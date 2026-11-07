# -*- coding: utf-8 -*-
"""ComfyUI 任务进度广播监听（零依赖，仅标准库手写 RFC6455 客户端）。

一个常驻进程订阅 <server>/ws，接收**所有**任务的执行事件，按 prompt_id 路由落盘。
任何项目、任何任务都读同一份广播，不必再轮询 /history。

## 通道选择（重要）
ComfyUI 自带的 executing / progress / executed 事件是**定向**发给「提交该任务的客户端」
（server.client_id），旁观客户端收不到。所以本监听的主通道是 fxai-toolkit 自己广播的事件：

    fxai:progress     {prompt_id, event, data}
                      event=scene_executing → data{current,total,duration,frames,message}
                      event=scene_saved     → data{scene_index,scene_count,path,message}
                      event=upscale_start / upscale_done → data{...}
    fxai:task_saved   {prompt_id, url:"video/merged|xxx.mp4", time}  ← 任务完成，产物落库

通用事件（status/executing/executed/execution_*）仍会解析，用于兜底与节点级计数。

## 运行时目录（固定 ~/.workbuddy/runtime/comfy_ws）
    state.json        全局任务快照：谁在跑、第几镜、成没成、产物文件名
    events.jsonl      全量事件流，一行一事件，带 prompt_id
    tasks/<pid>.log   单任务人类可读进度
    watcher.pid       监听进程 pid（stop 用）
    watcher.log       监听自身的连接/断线日志

## 子命令
    watch    常驻监听（--url 指定服务器，--detach 自脱离）
    top      打印当前活跃 + 最近结束的任务
    progress 增量读某个任务的新进度行（读本地游标文件，不问服务器）
    next     等「全部保存完毕」就退出并播报（纯事件，不写文件；--log 才留历史）
    status   打印某个 prompt_id 的进度、产物、最近日志
    shots    查「已生成到第几个分镜」（生成视频通用：短剧/MV/数字人/幻灯片视频）
             用法：comfy_watch.py shots <prompt_id> [--dir 保存目录] [--total 总镜数]
                  [--json] [--resume] [--files] [--url 服务器]
    resume   **断了之后一条命令接着跑**（生成视频通用）：
             查该任务已生成到第几镜 → 从 /history 取回上次提交的完整工作流 →
             把「开始索引」改到下一镜 → 重新 POST /prompt
             用法：comfy_watch.py resume <prompt_id> [--wait] [--dry-run] [--dir 目录]
                  [--total N] [--timeout 秒] [--url 地址]
             --dry-run 只算不提交；--wait 提交后就地等到终态
    wait     等某个 prompt_id 出终态（台账里没记录就自连 WS），退出码 0=success 1=error 2=超时
             用法：comfy_watch.py wait <prompt_id> [--timeout 秒] [--url 服务器]
    stop     按 pid 文件停掉监听
    sid      打印监听自己的 clientId（提交任务时带上它可额外收到定向的节点级事件）
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HOME = os.path.join(os.path.expanduser("~"), ".workbuddy", "runtime", "comfy_ws")
TASKS_DIR = os.path.join(HOME, "tasks")
STATE = os.path.join(HOME, "state.json")
EVENTS = os.path.join(HOME, "events.jsonl")
PIDFILE = os.path.join(HOME, "watcher.pid")
WLOG = os.path.join(HOME, "watcher.log")
TERMINAL = ("success", "error", "interrupted")
MAX_TASKS = 200
GLOBAL_CFG = os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json")
LOCAL_URL = "http://127.0.0.1:8188"

# 本机/云端 ComfyUI 一律直连：环境变量里有 http_proxy 会把请求绕进代理网关，
#表现为 502 Bad Gateway 或超时（公共件 fxai_common 里统一处理，别手写 urlopen）。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "fxai-common"))
import fxai_common as fxc  # noqa: E402

fxc.allow_local()
_OPENER = fxc.opener()
norm_url = fxc.norm_url


def http_get_json(url, timeout=8):
    with _OPENER.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _server_alive(url, timeout=1.5):
    try:
        with _OPENER.open(url.rstrip("/") + "/system_stats", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def resolve_server(explicit="", probe_local=True):
    """解析本次要连的 ComfyUI 地址，**任何一处都不写死、也不读环境变量**：

    显式参数（--url / 库调用传进来的，该任务在哪台就传哪台）
      > 探活本机 127.0.0.1:8188（用户定调「本机优先」，本机在跑就走本机）
      > 全局配置 fxai_global.json 的 comfy_url（通常是仙宫云）
      > 本机默认兜底

    ⚠️ 全局配置里的地址**可能指向云端**，
    所以每轮都要先探活本机再取配置，本机任务才不会连到云上去空等。
    """
    s = norm_url(explicit)
    if s:
        return s
    if probe_local and _server_alive(LOCAL_URL):
        return LOCAL_URL
    try:
        with open(GLOBAL_CFG, encoding="utf-8") as f:
            s = norm_url(json.load(f).get("comfy_url"))
        if s:
            return s
    except Exception:
        pass
    return LOCAL_URL


def now():
    return time.time()


def stamp(ts=None):
    return time.strftime("%H:%M:%S", time.localtime(ts or now()))


def read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(state):
    """原子写台账。并发要点（两个监听同时刷也不会出事）：

    1) 临时文件名带 pid —— 否则多个进程抢同一个 `state.json.tmp` 会被独占锁拒绝；
    2) `os.replace` 在 Windows 上也会被并发占用挡住（PermissionError 13），要退避重试；
    3) 失败不抛给调用方（flush 里已捕获并记账），这里只清理临时文件后原样抛出。
    """
    os.makedirs(TASKS_DIR, exist_ok=True)
    tmp = "%s.tmp.%d" % (STATE, os.getpid())
    data = json.dumps(state, ensure_ascii=False, indent=1)
    last = None
    for attempt in range(5):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, STATE)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.05 * (attempt + 1))
        except Exception as e:
            last = e
            break
    try:
        os.remove(tmp)
    except Exception:
        pass
    raise last


def task_log(pid):
    return os.path.join(TASKS_DIR, "%s.log" % pid)


def _same_id(a, b):
    """prompt_id 匹配：完整 uuid 对完整 uuid，或对任意长度的前缀（wait 允许只给前 8 位）。"""
    if not b:
        return True
    return a == b or a.startswith(b) or b.startswith(a)


def append(path, line):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- WS 客户端

def ws_connect(url, timeout=3600):
    """连 <url>/ws?clientId=... 并完成 RFC6455 握手，返回 socket。"""
    u = urllib.parse.urlparse(url)
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme in ("https", "wss") else 80)
    cid = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
    path = "/ws?clientId=" + cid
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(None)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)
    )
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = sock.recv(4096)
        if not d:
            raise ConnectionError("握手期间连接被关闭")
        buf += d
    head = buf.split(b"\r\n\r\n")[0].decode(errors="ignore")
    if " 101 " not in head.split("\r\n")[0] + " ":
        raise ConnectionError("握手失败：%s" % head.splitlines()[0])
    return sock, cid


def _readn(sock, n):
    out = b""
    while len(out) < n:
        d = sock.recv(n - len(out))
        if not d:
            raise ConnectionError("连接已关闭")
        out += d
    return out


def ws_recv(sock):
    """收一条完整消息（自动处理分片与 ping）；连接关闭返回 None。"""
    chunks = []
    while True:
        b0, b1 = _readn(sock, 2)
        op = b0 & 0x0F
        masked = bool(b1 & 0x80)
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", _readn(sock, 2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", _readn(sock, 8))[0]
        mask = _readn(sock, 4) if masked else None
        payload = _readn(sock, ln) if ln else b""
        if mask:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        if op == 0x8:
            return None
        if op == 0x9:
            sock.sendall(bytes([0x8A, len(payload)]) + payload)   # ping -> pong
            continue
        if op == 0xA:
            continue
        chunks.append(payload)
        if b0 & 0x80:
            return b"".join(chunks).decode(errors="ignore")


# ---------------------------------------------------------------- 事件路由

class Broadcaster(object):
    def __init__(self, server, only=None, log=False):
        """log=False（默认）= **纯事件模式**：只在内存里记账，用来当场播报，不写任何文件。

        只有需要跨会话查询（status/top/事后翻台账）时才传 log=True 落盘。
        """
        self.server = resolve_server(server)
        self.log = log
        self.state = (read_state() if log else None) or {"server": self.server, "tasks": {}}
        self.state["server"] = self.server
        self.state.setdefault("tasks", {})
        self.only = only            # 只处理该 prompt_id 的事件（wait 模式）
        self.dirty = False
        self.last_flush = 0.0

    def task(self, pid):
        t = self.state["tasks"].get(pid)
        if t is None:
            t = {"prompt_id": pid, "first_seen": now(), "status": "running",
                 "server": self.server, "executed_nodes": 0, "scene_current": None,
                 "scene_total": None, "scenes_done": 0, "message": "", "outputs": [],
                 "merged": None, "error": None}
            self.state["tasks"][pid] = t
            if self.log:
                append(task_log(pid), "[%s] 任务开始 %s（服务器 %s）" % (stamp(), pid, self.server))
        return t

    def _msg(self, t, text):
        t["message"] = text
        if self.log:
            append(task_log(t["prompt_id"]), "[%s] %s" % (stamp(), text))

    def on_event(self, mtype, data):
        if not _same_id(data.get("prompt_id") or "(server)", self.only):
            return
        if mtype == "fxai:progress":
            self._on_fxai_progress(data)
        elif mtype == "fxai:task_saved":
            self._on_task_saved(data)
        else:
            self._on_core(mtype, data)

    # -- fxai:progress：语义化阶段事件 ------------------------------------
    def _on_fxai_progress(self, data):
        pid = data.get("prompt_id") or "(server)"
        ev = data.get("event") or ""
        d = data.get("data") or {}
        t = self.task(pid)
        t["updated"] = now()
        if ev == "scene_executing":
            t["scene_current"] = d.get("current")
            t["scene_total"] = d.get("total")
            self._msg(t, d.get("message") or
                      "开始第 %s/%s 个场景（%s 秒 / %s 帧）"
                      % (d.get("current"), d.get("total"), d.get("duration"), d.get("frames")))
        elif ev == "scene_saved":
            t["scenes_done"] = d.get("scene_count") or t.get("scenes_done", 0) + 1
            if d.get("path"):
                t["outputs"] = sorted(set(t["outputs"] + [d["path"]]))
            self._msg(t, d.get("message") or ("第 %s 个场景已生成" % d.get("scene_index")))
        else:
            self._msg(t, d.get("message") or ("%s %s" % (ev, json.dumps(d, ensure_ascii=False)[:160])))
        self.dirty = True
        if self.log:
            append(EVENTS, json.dumps({"ts": t["updated"], "type": "fxai:progress", "prompt_id": pid,
                                        "event": ev, "data": d}, ensure_ascii=False))

    # -- fxai:task_saved：有文件被保存（过程事件） -------------------------
    def _on_task_saved(self, data):
        """有文件被保存 = **过程**事件：一次工作流会来很多条（音频/图片/每个分镜视频/合并片）。

        本身不判终局；"全部保存完毕"由 `execution_success`（自己提交时能收到），
        或"最后一次保存后静默 settle 秒"（别人提交时的兜底）来判定。
        """
        pid = data.get("prompt_id") or "(server)"
        t = self.task(pid)
        t["updated"] = now()
        url = data.get("url") or ""
        parts = url.split("|")
        cat_sub = parts[0]                      # 形如 "image/shortdrama_qjz" 或 "video"
        seg = cat_sub.split("/", 1)
        category = seg[0]
        subdir = seg[1] if len(seg) > 1 else ""
        files = [f for f in parts[1:] if f]
        t["category"] = category
        t["subdir"] = subdir
        t["saved"] = True
        counts = t.setdefault("saved_counts", {})
        counts[category] = counts.get(category, 0) + (len(files) or 1)
        t["outputs"] = sorted(set(t["outputs"] + files))
        if category == "video" and subdir == "merged" and files:
            t["merged"] = files[0]
        self._msg(t, "保存：%s/%s → %s" % (category, subdir, ", ".join(files) or "-"))
        self.dirty = True
        if self.log:
            append(EVENTS, json.dumps({"ts": t["updated"], "type": "fxai:task_saved",
                                        "prompt_id": pid, "url": url}, ensure_ascii=False))

    # -- ComfyUI 通用事件：兜底 + 节点计数 ----------------------------------
    def _on_core(self, mtype, data):
        pid = data.get("prompt_id")
        if not pid:
            if mtype == "status":
                st = data.get("status") or {}
                self.state["queue_running"] = st.get("queue_running")
                self.state["queue_pending"] = st.get("queue_pending")
                self.dirty = True
            return
        t = self.task(pid)
        t["updated"] = now()
        if mtype == "executing":
            if data.get("node") is None:
                if t.get("status") == "running":
                    t["status"] = "success"
                    t["finished"] = now()
                    self._msg(t, "执行结束（executing=None）")
            else:
                t["node"] = data.get("node")
                t["node_name"] = data.get("node_name")
        elif mtype == "executed":
            t["executed_nodes"] = t.get("executed_nodes", 0) + 1
            if isinstance(data.get("output"), dict):
                for v in data["output"].values():
                    if not isinstance(v, list):
                        continue
                    for x in v:
                        if not isinstance(x, dict) or not x.get("filename"):
                            continue
                        # 只认 type=output 的正式产物。type=temp 是 PreviewImage /
                        # 预览类节点写进 temp 目录的临时图（文件名形如
                        # ComfyUI_temp_xxxxx_00001_.png），不是成品，混进 outputs 后
                        # 会被当成下载令牌，取不到文件。预览图另存 previews 备查。
                        if (x.get("type") or "output") != "output":
                            t.setdefault("previews", [])
                            if x["filename"] not in t["previews"]:
                                t["previews"].append(x["filename"])
                            continue
                        sub = (x.get("subfolder") or "").replace("\\", "/").strip("/")
                        # 记录时保留裸文件名：下载令牌是「类别/子目录|文件名」，
                        # 文件名里再带一段目录会让令牌拼错。子目录另记一份。
                        t["outputs"] = sorted(set(t["outputs"] + [x["filename"]]))
                        if sub:
                            t.setdefault("output_subdirs", {})
                            t["output_subdirs"][x["filename"]] = sub
        elif mtype == "execution_success":
            if t.get("status") == "running":
                t["status"] = "success"
                t["finished"] = now()
                self._msg(t, "执行成功（execution_success）")
        elif mtype == "execution_error":
            t["status"] = "error"
            t["finished"] = now()
            t["error"] = (data.get("exception_message") or "")[:300]
            self._msg(t, "出错：%s" % t["error"])
        elif mtype == "execution_interrupted":
            t["status"] = "interrupted"
            t["finished"] = now()
            self._msg(t, "被中断")
        else:
            return
        self.dirty = True
        if self.log:
            append(EVENTS, json.dumps({"ts": t["updated"], "type": mtype, "prompt_id": pid,
                                        "node": t.get("node"), "node_name": t.get("node_name"),
                                        "status": t.get("status")}, ensure_ascii=False))

    def flush(self, force=False):
        if not self.log or not self.dirty:
            return                       # 纯事件模式：不落盘
        if not force and now() - self.last_flush < 0.4:
            return
        tasks = self.state["tasks"]
        if len(tasks) > MAX_TASKS:
            for pid, _ in sorted(tasks.items(), key=lambda kv: kv[1].get("updated", 0))[:-MAX_TASKS]:
                tasks.pop(pid, None)
        # 多个监听可能同时在跑：写之前先并入磁盘上的最新记录，后写就不会覆盖先写
        disk = read_state()
        for pid, rec in (disk.get("tasks") or {}).items():
            mine = tasks.get(pid)
            if mine is None or rec.get("updated", 0) > mine.get("updated", 0):
                tasks[pid] = rec
        self.state["connected"] = True
        self.state["updated"] = now()
        try:
            write_state(self.state)
        except Exception as e:
            append(WLOG, "[%s] 台账写入失败（忽略）: %r" % (stamp(), e))
        self.last_flush = now()
        self.dirty = False


# ---------------------------------------------------------------- 子命令

def cmd_watch(argv):
    server = ""
    detach = "--detach" in argv
    for i, a in enumerate(argv):
        if a == "--url" and i + 1 < len(argv):
            server = argv[i + 1]
    if not detach and len(argv) and not argv[0].startswith("--"):
        server = argv[0]
    server = resolve_server(server)
    os.makedirs(TASKS_DIR, exist_ok=True)
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))

    if detach:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "watch", "--url", server],
                         creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        print("监听已脱离启动（server=%s）" % server)
        return 0

    bc = Broadcaster(server)
    backoff = 1.0
    beat = now()
    while True:
        try:
            sock, cid = ws_connect(server)
            bc.state["client_id"] = cid
            append(WLOG, "[%s] 已连接 %s sid=%s" % (stamp(), server, cid))
            print("[%s] 已连接 %s，全局广播监听中（clientId=%s）"
                  % (stamp(), server, cid), flush=True)
            bc.state["connected"] = True
            bc.flush(force=True)
            backoff = 1.0
            sock.settimeout(30)
            while True:
                try:
                    raw = ws_recv(sock)
                except socket.timeout:
                    bc.flush(force=True)
                    if now() - beat >= 30:
                        beat = now()
                        append(WLOG, "[%s] 心跳" % stamp())
                    continue
                if raw is None:
                    raise ConnectionError("服务端关闭连接")
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                bc.on_event(msg.get("type"), msg.get("data") or {})
                bc.flush()
        except KeyboardInterrupt:
            bc.state["connected"] = False
            bc.flush(force=True)
            return 0
        except Exception as e:
            bc.state["connected"] = False
            bc.flush(force=True)
            append(WLOG, "[%s] 断开：%s" % (stamp(), e))
            print("[%s] 断开：%s，%.0fs 后重连" % (stamp(), e, backoff), flush=True)
            time.sleep(backoff)
            backoff = min(30.0, backoff * 2)


class Listener(object):
    """库用法：先连上拿到自己的 clientId → 提交时带上它 → 再等这个任务。

    这样两条通道同时到位：定向的 executing/execution_error（能第一时间发现报错）
    加上 fxai 的全局广播（第 N/M 镜开始、产物名）。
    """

    def __init__(self, url="", log=False):
        self.url = resolve_server(url)
        self.log = log
        self.sock, self.cid = ws_connect(self.url)
        self.sock.settimeout(8)
        self.bc = Broadcaster(self.url, log=log)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def _pump(self):
        """收一条事件；8 秒没消息就返回 False。事件处理出错只记账，不打断等待。"""
        try:
            raw = ws_recv(self.sock)
        except socket.timeout:
            self.bc.flush(force=True)
            return False
        if raw is None:
            raise ConnectionError("服务端关闭连接")
        try:
            msg = json.loads(raw)
        except Exception:
            return False
        try:
            self.bc.on_event(msg.get("type"), msg.get("data") or {})
        except Exception as e:
            append(WLOG, "[%s] 事件处理异常（已忽略）: %r" % (stamp(), e))
        self.bc.flush()
        return True

    def _in_queue(self, pid):
        """问服务器这个 pid 还在不在队列里（排队等 GPU 时会长时间没有任何事件）。"""
        try:
            q = http_get_json(self.url.rstrip("/") + "/queue", timeout=6)
        except Exception:
            return None                      # 问不到就当不知道，继续等
        for key in ("queue_running", "queue_pending"):
            for item in q.get(key) or []:
                cand = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item
                if isinstance(cand, str) and (cand == pid or cand.startswith(pid) or pid.startswith(cand)):
                    return True
        return False

    def wait(self, pid, timeout=7200, verbose=True, tag="", idle_fallback=90, settle=30):
        """等该 prompt_id 出终态，返回终态任务字典。

        终态判据（全局统一，不看具体工作流保存了什么）：
          ① `execution_success` —— 工作流跑完，所有保存动作已结束（自己提交时收得到）；
          ② 静默兜底：最后一次 `task_saved`（有文件保存）之后安静 `settle` 秒 = 也算跑完
             （监听别人提交的任务时收不到定向事件，走这条）。

        长时间没有任何信号时先问服务器「它还在不在队列里」：还在就继续等，
        确实不在了才返回 None 让调用方回落轮询。
        """
        self.bc.only = pid
        t0, last_sig, last_sig_at = now(), None, now()
        last_event_at = now()
        while now() - t0 < timeout:
            got = self._pump()
            if got:
                last_event_at = now()
            t = None
            for cand in (self.bc.state.get("tasks") or {}).values():
                if _same_id(cand["prompt_id"], pid):
                    t = cand
                    break
            if t:
                sig = (t.get("status"), t.get("scene_current"), t.get("scenes_done"), t.get("message"))
                if sig != last_sig:
                    if verbose:
                        print("  [%s] %s ｜ %s" % (tag, t.get("status"), _progress_text(t)), flush=True)
                    last_sig, last_sig_at = sig, now()
                if t.get("status") in TERMINAL:
                    return t
                if t.get("saved") and settle and now() - last_event_at > settle:
                    t["status"] = "success"          # 保存完且无新动静 = 全部保存完毕
                    t["finished"] = now()
                    self.bc._msg(t, "全部保存完毕（保存后静默 %ds）" % settle)
                    self.bc.flush(force=True)
                    return t
            if got and t:
                last_sig_at = now()
            if idle_fallback and now() - last_sig_at > idle_fallback:
                if self._in_queue(pid):
                    if verbose:
                        print("  [%s] 仍在队列中等待（GPU 占用），继续等…" % tag, flush=True)
                    last_sig_at = now()
                    continue
                return None
        return None


def _age(ts):
    if not ts:
        return "?"
    d = int(now() - ts)
    if d < 90:
        return "%ds前" % d
    if d < 5400:
        return "%d分钟前" % (d // 60)
    return "%d小时前" % (d // 3600)


def _find(state, pid):
    tasks = state.get("tasks") or {}
    if pid in tasks:
        return tasks[pid]
    cands = [k for k in tasks if k.startswith(pid)]
    return tasks[cands[0]] if len(cands) == 1 else None


def _progress_text(t):
    cur, tot = t.get("scene_current"), t.get("scene_total")
    if cur and tot:
        return "第%s/%s镜（已出%s）" % (cur, tot, t.get("scenes_done", 0))
    if t.get("scenes_done"):
        return "已出%s个场景" % t["scenes_done"]
    return t.get("message") or "-"


def cmd_top():
    st = read_state()
    tasks = st.get("tasks") or {}
    print("服务器 %s ｜ 连接 %s ｜ 队列 运行%s/等待%s ｜ 记录 %d 条"
          % (st.get("server"), "正常" if st.get("connected") else "断开",
             st.get("queue_running"), st.get("queue_pending"), len(tasks)))
    act = {k: v for k, v in tasks.items() if v.get("status") not in TERMINAL}
    fin = sorted((v for v in tasks.values() if v.get("status") in TERMINAL),
                 key=lambda v: v.get("finished", 0), reverse=True)[:8]
    if act:
        print("-- 执行中 --")
        for pid, t in sorted(act.items(), key=lambda kv: kv[1].get("first_seen", 0)):
            print("  %s  %-8s %-28s %s" % (pid[:8], t.get("status"),
                                            _progress_text(t)[:28], _age(t.get("updated"))))
    if fin:
        print("-- 最近结束 --")
        for t in fin:
            print("  %s  %-11s %-40s %s" % (t["prompt_id"][:8], t.get("status"),
                                           (t.get("message") or "")[:40], _age(t.get("finished"))))
    return 0


def _shot_line(t):
    """status 里的「已生成到第几镜」一行（查不到就给原因，不刷异常）。"""
    sq = _load_shot_query()
    if sq is None:
        return "-（未找到 fxai-workflow/shot_query.py）"
    try:
        r = sq.query(t["prompt_id"], url=t.get("server") or "",
                     save_dir=t.get("subdir") or "")
    except Exception as e:
        return "-（查询失败：%r）" % (e,)
    if not r.get("shots"):
        return "0 镜%s" % ("（%s）" % r["note"] if r.get("note") else
                           "（保存目录 %s 里还没有分镜视频）" % (r.get("save_dir") or "未知"))
    nxt = ("，下一镜 第%d镜（续跑 start_index=%d）"
           % (r["next_shot"], r["next_index"])) if r.get("next_index") is not None else ""
    miss = ("，缺失第%s镜" % "、".join(str(i + 1) for i in r["missing"][:8])) if r.get("missing") else ""
    return "已生成 %d 镜（第%d~第%d镜）%s%s" % (
        r["shot_count"], r["shots"][0]["shot"], r["shots"][-1]["shot"], miss, nxt)


def cmd_status(pid):
    t = _find(read_state(), pid)
    if not t:
        # 台账里没有（没开监听 / 服务重启过）→ 交给 shot_query：它能从提交快照自取
        # 保存目录、总镜数、提交时刻，再数目录里的分镜，照样能报「第几镜 / 还剩几镜」。
        sq = _load_shot_query()
        if sq is None:
            print("未找到该 prompt_id 的记录，也没找到 fxai-workflow/shot_query.py")
            return 2
        try:
            r = sq.query(_resolve_pid(pid, LOCAL_URL), url=LOCAL_URL)
        except Exception as e:
            print("查询失败：%r" % (e,))
            return 2
        print(sq.format_text(r))
        print("（台账里没有这条记录——开了监听才有逐镜开始/结束时刻）")
        return 0
    print("prompt_id : %s" % t["prompt_id"])
    print("服务器    : %s" % t.get("server"))
    print("状态      : %s" % t.get("status"))
    print("进度      : %s" % _progress_text(t))
    print("场景      : 开始第%s/%s，已完成%s，节点已执行%s"
          % (t.get("scene_current"), t.get("scene_total"),
             t.get("scenes_done"), t.get("executed_nodes")))
    print("最近消息  : %s" % t.get("message"))
    print("开始/更新 : %s / %s" % (_age(t.get("first_seen")), _age(t.get("updated"))))
    if t.get("error"):
        print("错误      : %s" % t["error"])
    counts = t.get("saved_counts") or {}
    if counts:
        print("各类保存  : %s" % "、".join("%s %d" % (k, v) for k, v in sorted(counts.items())))
    label, fname = final_artifact(t)
    if fname:
        print("%-9s: %s" % (label, fname))
    print("分镜进度  : %s" % _shot_line(t))
    outs = t.get("outputs") or []
    print("产物      : %d 个" % len(outs))
    for o in outs[:12]:
        print("           %s" % o)
    p = task_log(t["prompt_id"])
    if os.path.exists(p):
        print("-- 最近日志 --")
        with open(p, encoding="utf-8") as f:
            for line in f.readlines()[-8:]:
                print("  " + line.rstrip())
    return 0


def _resolve_pid(pid, url):
    """把前缀补成完整 prompt_id：先看队列，再看历史（结果表只认完整 id）。"""
    if pid.count("-") == 4:
        return pid
    base = url.rstrip("/")
    try:
        q = http_get_json(base + "/queue", timeout=8)
        for key in ("queue_running", "queue_pending"):
            for item in q.get(key) or []:
                cand = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item
                if isinstance(cand, str) and cand.startswith(pid):
                    return cand
    except Exception:
        pass
    try:
        h = http_get_json(base + "/history", timeout=30)
        for k in h:
            if k.startswith(pid):
                return k
    except Exception:
        pass
    return pid


def _fxai_result(pid, url):
    """问服务端结果表：这个 prompt_id 有没有落库的产物（连晚了也能查回来）。"""
    full = _resolve_pid(pid, url)
    try:
        req = urllib.request.Request(
            url.rstrip("/") + "/fxai/tasks/result",
            data=json.dumps({"prompt_ids": full}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with _OPENER.open(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        item = data.get(full)
        return item.get("url") if item else None
    except Exception:
        return None


def cmd_wait(pid, timeout=7200, url=""):
    """等某个 prompt_id 出终态。

    地址解析：命令行 --url > 该任务台账里记的服务器（跨机时不会查错机器）
    > 探活本机 > 全局配置 > 本机默认。
    台账里已有终态就直接回；否则自连 WS 守着（只收这个任务的事件），收到终态即退出，
    因此能触发会话通知——这是替掉 sleep 轮询的关键。等不到时兜底查一次服务端结果表，
    覆盖「任务在连上之前就完成了」的情况。
    """
    t = _find(read_state(), pid)
    url = resolve_server(url or (t or {}).get("server") or "")
    if t and t.get("status") in TERMINAL:
        print("终态 %s ｜ 产物 %d 个%s"
              % (t["status"], len(t.get("outputs") or []),
                 (" ｜ 成片 " + t["merged"]) if t.get("merged") else ""))
        return 0 if t["status"] == "success" else 1
    print("[%s] 台账无终态，自连 %s 等待 %s" % (stamp(), url, pid), flush=True)
    try:
        lis = Listener(url)
    except Exception as e:
        print("连接失败：%s" % e)
        return _wait_fallback(pid, url, "连不上广播监听（%s）" % e)
    try:
        t = lis.wait(pid, timeout=timeout, verbose=True, tag="wait", idle_fallback=90)
    except KeyboardInterrupt:
        return 2
    except Exception as e:
        print("连接异常：%s" % e)
        return _wait_fallback(pid, url, "连接异常（%s）" % e)
    finally:
        lis.close()
    if not t:
        return _wait_fallback(pid, url, "等满 %ds 仍无终态广播" % timeout)
    print("终态 %s ｜ 产物 %d 个%s"
          % (t["status"], len(t.get("outputs") or []),
             (" ｜ 成片 " + t["merged"]) if t.get("merged") else ""))
    return 0 if t["status"] == "success" else 1


def _in_server_queue(pid, url):
    """查服务端 /queue：这个 prompt_id 是否仍在执行或排队中。"""
    full = _resolve_pid(pid, url)
    try:
        with _OPENER.open(url.rstrip("/") + "/queue", timeout=10) as r:
            q = json.loads(r.read().decode())
        for key in ("queue_running", "queue_pending"):
            for item in q.get(key) or []:
                if isinstance(item, (list, tuple)) and len(item) > 1 \
                        and str(item[1]).startswith(full[:8]):
                    return True
    except Exception:
        pass
    return False


def _wait_fallback(pid, url, why):
    """广播没等到终态时的兜底：查服务端结果表（任务可能早已完成）。"""
    token = _fxai_result(pid, url)
    if token:
        print("兜底查到结果（%s）：%s" % (why, token))
        return 0
    if _in_server_queue(pid, url):
        print("兜底查到任务仍在服务端队列中执行（%s）。wait 到此超时退出，"
              "重新执行 wait 可继续等。" % why)
        return 2
    print("兜底未查到结果（%s）。服务端队列和结果表里都没有该 prompt_id——"
          "多见于别人提交的任务或服务端已清队列。" % why)
    return 2


def _log_rows(pid):
    p = task_log(pid)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().splitlines()
    except Exception:
        return []


def _load_shot_query():
    """载入公共层的分镜进度查询（fxai-workflow/shot_query.py），失败返回 None。"""
    import importlib.util
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(base, "fxai-workflow", "shot_query.py")
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("fxai_shot_query", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def cmd_resume(argv):
    """断了之后一条命令接着跑：查已生成到第几镜 → 改开始索引 → 重新提交（生成视频通用）。

    参数原样透传（--wait / --dry-run / --dir / --total / --timeout / --url …）。
    """
    sq = _load_shot_query()
    if sq is None:
        print("未找到公共层 fxai-workflow/shot_query.py，无法续跑")
        return 2
    return sq.main(["resume"] + list(argv or []))


def cmd_shots(argv):
    """查「已经生成到第几个分镜」（生成视频通用：短剧/MV/数字人/幻灯片视频都走这条）。

    参数原样透传给 shot_query.py（--dir/--total/--json/--resume/--files …）。
    """
    sq = _load_shot_query()
    if sq is None:
        print("未找到公共层 fxai-workflow/shot_query.py，无法查分镜进度")
        return 2
    return sq.main(argv or [])


def final_artifact(t):
    """按任务类型给出「最终产物」：(标签, 文件名 或 None)。

    视频类（短剧/MV）以合并片为准；图片类 / 纯音频类没有合并环节，
    那次保存的就是最终产物（多产物时取第一个）。
    """
    t = t or {}
    if t.get("merged"):
        return "成片", t["merged"]
    outs = t.get("outputs") or []
    counts = t.get("saved_counts") or {}
    if outs:
        if counts.get("video"):
            return "视频", outs[0]
        if counts.get("audio") and not counts.get("image"):
            return "音频", outs[0]
        return "图片", outs[0]
    return None, None


def _cursor_path(pid):
    return os.path.join(HOME, "cursor_%s.txt" % pid[:8])


def _read_cursor(pid):
    try:
        with open(_cursor_path(pid), encoding="utf-8") as f:
            return int((f.read().strip() or "0"))
    except Exception:
        return 0


def _write_cursor(pid, n):
    try:
        with open(_cursor_path(pid), "w", encoding="utf-8") as f:
            f.write(str(n))
    except Exception:
        pass


def cmd_next(pid, timeout=5400, log=False):
    """等「全部保存完毕」就退出（纯事件模式：只在内存里记账，不写任何文件）。

    判据：`execution_success`（工作流跑完，所有保存动作结束）→ 终态；
    收不到定向事件时用「最后一次保存后静默 settle 秒」兜底；报错/被中断同样立即退出。
    退出时直接打印内存里的结果：各类保存计数 + 最终产物 + 进度。
    要留历史给 `status/top` 事后查，加 `--log`。
    """
    st = read_state()
    cands = [k for k in (st.get("tasks") or {}) if _same_id(k, pid)]
    full = cands[0] if cands else pid
    url = resolve_server(((st.get("tasks") or {}).get(full) or {}).get("server") or "")
    try:
        lis = Listener(url, log=log)
    except Exception as e:
        print("连接失败：%s" % e)
        return 2
    print("[%s] 等 %s 全部保存完毕…（%s）" % (stamp(), full[:8], url), flush=True)
    status = "timeout"
    try:
        # 复用 Listener.wait 的判据（execution_success / 保存后静默 / 报错 / 中断），
        # 不在这里另写一套——否则会漏掉静默兜底，永远等不到终态。
        t = lis.wait(full, timeout=timeout, verbose=False, idle_fallback=0, settle=30)
        status = (t or {}).get("status") or "timeout"
    except KeyboardInterrupt:
        lis.close()
        return 2
    except Exception as e:
        lis.close()
        print("连接异常：%s" % e)
        return 2
    finally:
        lis.close()
    full_task = _find(lis.bc.state, full) or {}
    counts = full_task.get("saved_counts") or {}
    if counts:
        print("各类保存：%s" % "、".join("%s %d" % (k, v) for k, v in sorted(counts.items())))
    label, fname = final_artifact(full_task)
    print("%s：%s" % (label, fname) if fname else "产物：0 个（没有保存记录）")
    if full_task.get("error"):
        print("错误：%s" % full_task["error"])
    print("｜ 状态 %s ｜ %s" % (status, _progress_text(full_task)))
    return 0 if status == "success" else 1


def cmd_progress(pid, lines=8):
    """增量读某个任务的新进度行（对话里播报进度用）。

    每次只返回上次之后**新出现**的行，并记一个游标，所以反复调用不会重复刷屏。
    """
    t = _find(read_state(), pid)
    if not t:
        print("台账里还没有 %s 的记录（任务刚开始时可能还没事件，先跑 wait 挂着）" % pid)
        return 2
    log = task_log(t["prompt_id"])
    prev = _read_cursor(t["prompt_id"])
    rows = _log_rows(t["prompt_id"])
    new = rows[prev:]
    _write_cursor(t["prompt_id"], len(rows))
    print("%s ｜ %s" % (t["prompt_id"][:8], _progress_text(t)))
    for r in new[-lines:]:
        print("  " + r)
    if not new:
        print("  （暂无新进度）")
    print("｜ 新增 %d 行 / 累计 %d 行" % (len(new), len(rows)))
    return 0


def _alive():
    try:
        with open(PIDFILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def cmd_stop():
    try:
        with open(PIDFILE) as f:
            pid = int(f.read().strip())
    except Exception:
        print("没有 pid 文件，监听未启动过")
        return 0
    try:
        os.kill(pid, 15)
        print("已停止监听（pid=%d）" % pid)
    except Exception as e:
        print("停止失败：%s" % e)
    try:
        os.remove(PIDFILE)
    except Exception:
        pass
    return 0


USAGE = __doc__


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        return 1
    sub, rest = sys.argv[1], sys.argv[2:]
    if sub == "watch":
        return cmd_watch(rest)
    if sub == "top":
        return cmd_top()
    if sub == "shots":
        return cmd_shots(rest)
    if sub == "resume":
        return cmd_resume(rest)
    if sub == "status":
        return cmd_status(rest[0] if rest else "")
    if sub == "wait":
        to, pid, url = 7200, "", ""
        i = 0
        while i < len(rest):
            if rest[i] == "--timeout":
                to = int(rest[i + 1])
                i += 2
                continue
            if rest[i] == "--url":
                url = rest[i + 1]
                i += 2
                continue
            pid = rest[i]
            i += 1
        return cmd_wait(pid, to, url)
    if sub == "stop":
        return cmd_stop()
    if sub == "sid":
        print((read_state().get("client_id") or "") + "\t"
              + ("监听在跑" if _alive() else "监听未运行"))
        return 0
    if sub == "progress":
        n = 8
        if "--lines" in rest:
            n = int(rest[rest.index("--lines") + 1])
            rest = [a for a in rest if a != "--lines" and a != str(n)]
        return cmd_progress(rest[0] if rest else "", n)
    if sub == "next":
        to = 5400
        if "--timeout" in rest:
            to = int(rest[rest.index("--timeout") + 1])
            rest = [a for a in rest if a != "--timeout" and a != str(to)]
        lg = "--log" in rest
        rest = [a for a in rest if a != "--log"]
        return cmd_next(rest[0] if rest else "", to, lg)
    print(USAGE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
