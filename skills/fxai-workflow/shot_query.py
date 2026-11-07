# -*- coding: utf-8 -*-
# =====================================================================
# fxai-workflow —— 分镜进度查询（生成视频通用）
# 位置：公共目录 fxai-workflow/（非技能，被所有视频类技能复用）。
#
# 解决什么问题：
#   多段循环生成（口播短剧 / MV / 数字人 / 幻灯片视频…）中途断了（超时、关机、
#   报错、换机器），需要知道「已经生成到第几个分镜」，下次从断点接着生成，
#   而不是从头重跑（重跑 = 重新烧一遍 GPU 时长）。
#
# 判定口径（用户定的规矩，全技能统一）：
#   ★ 视频文件只要**不是合并目录**（merged）里的，文件名上带的那个文件号
#     = 已生成的分镜序号（000.mp4 = 第 1 镜，007.mp4 = 第 8 镜）。
#   合并片（video/merged/xxx.mp4）是整条成片，不算分镜，不计入。
#
# 数据从哪来（按可靠性依次尝试，**全部走 HTTP 接口**，不读 ComfyUI 本机目录）：
#   ① 广播台账 state.json（fxai-comfyui-watcher）：第 N/M 镜、已保存文件名、成片名
#   ② /fxai/tasks/result：该 prompt_id 的结果令牌（video/sucai|007.mp4）
#   ③ /history/<pid> 的 prompt[2]（提交时的完整工作流）→ 反查节点「保存目录」
#   ③.5 本地工作流快照（提交时自动落盘）→ 保存目录 + 总镜数 + 提交时刻
#      ↑ 没开监听进程、服务重启过 history 被清空时，这是**唯一**还能用的锚点：
#        保存目录、台词行数（=总镜数）、提交时间(time) 三样齐活，所以只给一个
#        prompt_id 就能算出「已生成到第几镜、还剩几镜、大约还要多久」。
#   ④ /fxai/files/list?dir=fxai&subdir=video/<保存目录> → 目录内全部视频文件（带 size/mtime）
#
# ⚠️ fxai-toolkit 节点**不写 /history 的 outputs**（实测为空），所以分镜清单以
#    ④ 的文件列表为准，① ② ③ ③.5 只用来定位「保存目录 / 总镜数 / 任务状态 / 任务起点」。
# ⚠️ 保存目录由各任务自己的工作流决定（短剧默认 sucai，其余技能各用己方目录），查询侧
#    不假定固定目录；目录被多个任务共用时，拿到任务起点(time)后**默认只数本任务落盘的**，
#    否则历史分镜会被算成本次成果。
#
# 用法（命令行）：
#   python shot_query.py <prompt_id|前缀|pid文件> [...多个]
#   python shot_query.py 8bc2f030               # 渲染中看进度：已生成 10/14 镜，还剩 4 镜
#   python shot_query.py <pid> --json            # 机器读（remaining / eta_seconds / next_index）
#   python shot_query.py <pid> --resume          # 只打印续跑索引
#   python shot_query.py <pid> --dir sucai       # 保存目录已知时直接指定
#   python shot_query.py <pid> --total 14        # 总镜数已知（算缺失/是否跑完）
#   python shot_query.py <pid> --url <地址>      # 指定服务器
#
# 用法（库，任何视频技能 import 复用）：
#   import shot_query as SQ
#   r = SQ.query(pid)
#   print(SQ.format_text(r))                        # 人读：第几镜 / 还剩几镜 / 约多久
#   if not r["finished"]:
#       wf = build_workflow(..., start_index=r["next_index"])   # 断点续跑
# =====================================================================
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# ---------- 本机代理绕过（127.0.0.1 直连，不经代理） ----------
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fxai-common"))
import fxai_common as fxc  # noqa: E402

fxc.allow_local()
_OPENER = fxc.opener()
norm_url = fxc.norm_url

LOCAL_URL = "http://127.0.0.1:8188"
GLOBAL_CFG = os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json")
HOME = os.path.join(os.path.expanduser("~"), ".workbuddy", "runtime", "comfy_ws")
STATE = os.path.join(HOME, "state.json")

# 视频后缀（分镜只认这些）
VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".gif")
# 合并目录/成片名的判别词（命中即不算分镜）
MERGED_WORDS = ("merged", "merge", "合并", "成片", "final", "concat", "完整", "output_merged")
# 工作流里「保存目录」字段名（按优先级）
SAVE_DIR_FIELDS = ("保存目录", "输出目录", "保存路径", "outputdir", "output_dir", "subdir")
# 兜底：字段名只是「目录」时，只有这类节点才算「保存到哪」
SAVE_NODE_HINTS = ("Save", "Manager", "Video", "Merger", "Output")


# ---------- 地址解析（与 fxai-comfyui-watcher 完全同一套口径） ----------
def _load_watcher():
    """载入广播监听模块（复用它的地址解析与台账读取），失败返回 None。"""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "fxai-comfyui-watcher", "scripts", "comfy_watch.py")
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("fxai_comfy_watch", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


_W = _load_watcher()


def _server_alive(url, timeout=1.5):
    try:
        with _OPENER.open(url.rstrip("/") + "/system_stats", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def resolve_server(explicit=""):
    """显式传入 > 探活本机 > 全局配置 comfy_url > 本机默认。"""
    if _W is not None:
        try:
            return _W.resolve_server(explicit)
        except Exception:
            pass
    s = norm_url(explicit)
    if s:
        return s
    if _server_alive(LOCAL_URL):
        return LOCAL_URL
    try:
        with open(GLOBAL_CFG, encoding="utf-8") as f:
            s = norm_url(json.load(f).get("comfy_url"))
        if s:
            return s
    except Exception:
        pass
    return LOCAL_URL


# ---------- HTTP ----------
# 铁律：只走接口，绝不扫本机磁盘（ComfyUI 可能跑在另一台机器上，
# 本机磁盘 ≠ 服务端磁盘）。DEBUG=True 时把每次真实请求打出来，供核对。
DEBUG = False


def _log_call(method, url, body=None):
    if DEBUG:
        tail = ("  body=%s" % json.dumps(body, ensure_ascii=False)[:200]) if body else ""
        print("[接口] %s %s%s" % (method, url, tail), file=sys.stderr, flush=True)


def _get_json(url, timeout=20):
    _log_call("GET", url)
    with _OPENER.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post_json(url, body, timeout=20):
    _log_call("POST", url, body)
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _age(ts):
    if not ts:
        return "?"
    d = int(time.time() - ts)
    if d < 90:
        return "%ds前" % d
    if d < 5400:
        return "%d分钟前" % (d // 60)
    if d < 86400 * 2:
        return "%d小时前" % (d // 3600)
    return "%d天前" % (d // 86400)


# ---------- 文件名 → 分镜序号 ----------
def shot_index(name):
    """从文件名取分镜序号（0-based，与文件号一致）。取不到返回 None。

    规则（覆盖实测到的几种命名）：
      000.mp4 / 007.mp4        → 纯数字文件名，直接取整
      scene_003.mp4            → 尾部 _数字
      fxai_00003.mp4           → 尾部 _数字
      ComfyUI_00012_.mp4       → 尾部 _数字
      fxai_20261014_162835.mp4 → 时间戳，长数字（≥6 位）不算分镜号，返回 None
    """
    stem = os.path.splitext(os.path.basename(str(name or "")))[0]
    stem = stem.rstrip("_")
    if not stem:
        return None
    if re.fullmatch(r"\d+", stem):
        return int(stem)
    m = re.search(r"[_\-](\d+)$", stem)
    if m:
        return int(m.group(1))
    nums = re.findall(r"\d+", stem)
    if nums:
        last = nums[-1]
        if len(last) >= 6:      # 20261014 / 162835 这类时间戳不是分镜号
            return None
        return int(last)
    return None


def is_merged(name, subdir="", merged_dir="merged"):
    """是不是合并片（合并目录 / 成片名）。命中就不算分镜。"""
    sd = (subdir or "").replace("\\", "/").strip("/").lower()
    if sd and (sd == str(merged_dir).lower() or sd.endswith("/" + str(merged_dir).lower())):
        return True
    low = os.path.basename(str(name or "")).lower()
    return any(w in low for w in MERGED_WORDS)


def _is_video(name):
    return os.path.splitext(str(name or ""))[1].lower() in VIDEO_EXT


# ---------- 各数据源 ----------
def _ledger_task(pid):
    """读广播台账里这条任务的记录（支持前缀）。None = 台账里没有。"""
    try:
        if _W is not None:
            return _W._find(_W.read_state(), pid)
    except Exception:
        pass
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return None
    tasks = st.get("tasks") or {}
    if pid in tasks:
        return tasks[pid]
    cands = [k for k in tasks if k.startswith(pid)]
    return tasks[cands[0]] if len(cands) == 1 else None


def resolve_pid(pid, url):
    """前缀 → 完整 prompt_id（先查队列，再查历史）。已经是完整 id 原样返回。"""
    if pid.count("-") == 4:
        return pid
    base = url.rstrip("/")
    try:
        q = _get_json(base + "/queue", timeout=8)
        for key in ("queue_running", "queue_pending"):
            for item in q.get(key) or []:
                cand = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item
                if isinstance(cand, str) and cand.startswith(pid):
                    return cand
    except Exception:
        pass
    try:
        h = _get_json(base + "/history", timeout=30)
        for k in h:
            if k.startswith(pid):
                return k
    except Exception:
        pass
    return pid


def history_entry(pid, url):
    """GET /history/<pid> → 该条记录（含 status / prompt / outputs）。"""
    base = url.rstrip("/")
    try:
        h = _get_json(base + "/history/" + pid, timeout=15)
        if isinstance(h, dict) and h.get(pid):
            return h[pid]
    except Exception:
        pass
    try:
        h = _get_json(base + "/history", timeout=30)
        if isinstance(h, dict):
            return h.get(pid)
    except Exception:
        pass
    return None


def workflow_of(entry):
    """/history 的 prompt 是 [序号, pid, API工作流, extra, outputs]；取出第 3 项。"""
    p = (entry or {}).get("prompt")
    if isinstance(p, list):
        for x in p:
            if isinstance(x, dict) and any(
                    isinstance(v, dict) and "class_type" in v for v in x.values()):
                return x
        for x in p:
            if isinstance(x, dict):
                return x
    if isinstance(p, dict):
        return p
    return None


def save_dirs_of_workflow(wf):
    """从提交过的工作流里反查「保存到哪个目录」。返回 [(class_type, 字段名, 值)]。"""
    out = []
    if not isinstance(wf, dict):
        return out
    for _nid, node in wf.items():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "")
        for field, val in (node.get("inputs") or {}).items():
            if not isinstance(val, str) or not val.strip():
                continue
            if field in SAVE_DIR_FIELDS:
                out.append((ct, field, val.strip()))
            elif field == "目录" and any(h in ct for h in SAVE_NODE_HINTS):
                out.append((ct, field, val.strip()))
    return out


def task_result(pid, url):
    """POST /fxai/tasks/result → 结果令牌（如 video/sucai|007.mp4）。"""
    try:
        data = _post_json(url.rstrip("/") + "/fxai/tasks/result",
                          {"prompt_ids": pid}, timeout=15)
        item = data.get(pid) if isinstance(data, dict) else None
        return (item or {}).get("url") or None
    except Exception:
        return None


def list_video_dir(subdir, url):
    """GET /fxai/files/list?dir=fxai&subdir=video/<subdir> → 文件列表 [{name,size,mtime}]。"""
    sub = str(subdir or "").replace("\\", "/").strip("/")
    if sub.startswith("video/"):
        sub = sub[6:]
    path = "video/" + sub if sub else "video"
    u = (url.rstrip("/") + "/fxai/files/list?dir=fxai&subdir="
         + urllib.parse.quote(path, safe="/"))
    data = _get_json(u, timeout=20)
    if not isinstance(data, dict):
        return []
    items = (data.get("video") or data.get("videos") or [])
    out = []
    for x in items:
        if isinstance(x, dict) and x.get("name"):
            out.append({"name": x["name"], "size": int(x.get("size") or 0),
                        "mtime": float(x.get("mtime") or 0)})
    return out


def running_pids(url):
    """当前正在执行的任务 pid 列表（ComfyUI 队列串行，通常就 1 个）。

    任务是排队执行的，所以「现在跑到第几镜」＝「当前 running 这个 pid 跑到第几镜」，
    不需要用户记住 prompt_id。页面里显示的是队列顺序，不是镜号，别混。
    """
    try:
        q = _get_json(url.rstrip("/") + "/queue", timeout=10)
    except Exception:
        return []
    out = []
    for it in (q.get("queue_running") or []):
        pid = it[1] if isinstance(it, (list, tuple)) and len(it) > 1 else it
        if isinstance(pid, str) and pid:
            out.append(pid)
        elif isinstance(pid, dict) and pid.get("prompt_id"):
            out.append(pid["prompt_id"])
    return out


def pending_count(url):
    """队列里还有几个在排队（不含正在跑的那个）。"""
    try:
        q = _get_json(url.rstrip("/") + "/queue", timeout=10)
    except Exception:
        return 0
    return len(q.get("queue_pending") or [])


def parse_token(token):
    """'video/sucai|007.mp4' → ('video', 'sucai', ['007.mp4'])。"""
    if not token:
        return "", "", []
    parts = str(token).split("|")
    seg = parts[0].split("/", 1)
    cat = seg[0]
    sub = seg[1] if len(seg) > 1 else ""
    return cat, sub, [f for f in parts[1:] if f]


# ---------- 主查询 ----------
def query(pid, url="", save_dir="", expect_total=None, merged_dir="merged",
          only_this_task=False, verbose=False, debug=False):
    """查一个 prompt_id 已生成到第几个分镜。返回结构化字典（见文件头）。"""
    global DEBUG
    if debug:
        DEBUG = True
    pid = str(pid or "").strip()
    t = _ledger_task(pid) if pid else None
    url = resolve_server(url or (t or {}).get("server") or "")
    full = resolve_pid(pid, url) if pid else ""
    res = {
        "prompt_id": full, "input": pid, "server": url,
        "status": None, "category": None, "save_dir": "", "merged": None,
        "scene_total": None, "scene_current": None, "shot_count": 0,
        "single_shot": None, "running": False,
        "shots": [], "done": [], "missing": [], "last_index": None,
        "next_index": None, "next_shot": None, "finished": False,
        "sources": [], "files_all": [], "note": "",
    }
    if not full:
        # 不给 pid 也能查：只按保存目录（新提交前的续跑门禁就走这条路）
        res["note"] = "未给 prompt_id，按保存目录查"

    if full:
        # ① 台账：状态 / 第 N 镜 / 已保存文件名 / 成片名
        if t:
            res["status"] = t.get("status")
            res["scene_total"] = t.get("scene_total")
            res["scene_current"] = t.get("scene_current")
            res["category"] = t.get("category")
            if t.get("subdir"):
                res["save_dir"] = t["subdir"]
            if t.get("merged"):
                res["merged"] = t["merged"]
            res["sources"].append("台账")

    # ② 结果表：最后落库的那一个产物（令牌里带类别与目录）
    token = task_result(full, url) if full else None
    if token:
        cat, sub, files = parse_token(token)
        res["category"] = res["category"] or cat or "video"
        if sub and sub != merged_dir and not res["save_dir"]:
            res["save_dir"] = sub
        if sub == merged_dir and files:
            res["merged"] = files[0]
        res["sources"].append("结果表")
        res["last_token"] = token

    # ③ /history：任务状态 + 反查保存目录
    entry = history_entry(full, url) if full else None
    create_ms = None
    if entry:
        st = (entry.get("status") or {})
        if st.get("status_str"):
            res["status"] = res["status"] or st.get("status_str")
        for m in st.get("messages", []) or []:
            if isinstance(m, (list, tuple)) and len(m) >= 2 and isinstance(m[1], dict):
                if m[1].get("exception_message"):
                    res["error"] = m[1]["exception_message"][:300]
        wf = workflow_of(entry)
        cands = save_dirs_of_workflow(wf)
        if cands:
            res["save_dir_candidates"] = ["%s（%s.%s）" % (v, ct, f) for ct, f, v in cands]
            if not res["save_dir"]:
                res["save_dir"] = cands[0][2]
        # outputs（fxai 节点通常为空，有就收）
        outs = entry.get("outputs") or {}
        if isinstance(outs, dict):
            for _nid, node in outs.items():
                if not isinstance(node, dict):
                    continue
                for _k, v in node.items():
                    if isinstance(v, list):
                        for x in v:
                            if isinstance(x, dict) and x.get("filename"):
                                sub2 = str(x.get("subfolder") or "")
                                nm = x["filename"]
                                if _is_video(nm) and is_merged(nm, sub2, merged_dir):
                                    res["merged"] = res["merged"] or nm
                                elif _is_video(nm) and not res["save_dir"]:
                                    res["save_dir"] = sub2
        extra = None
        if isinstance(entry.get("prompt"), list):
            for x in entry["prompt"]:
                if isinstance(x, dict) and "create_time" in x:
                    extra = x
        if extra:
            create_ms = extra.get("create_time")
        res["sources"].append("history")

    # ③.5 快照兜底：台账里没有（没开监听进程）、history 里也没有（fxai 节点不入 history）
    # 时，只剩「提交时落的工作流快照」这一条路。它同时能给出保存目录、总镜数、提交时刻，
    # 所以一条 `shot_query.py <pid>` 就能算出渲染到第几镜、还剩几镜。
    snap_meta = None
    if full:
        try:
            _snap_wf, snap_meta = load_snapshot(full)
        except Exception:
            _snap_wf, snap_meta = None, None
        if _snap_wf:
            if not res["save_dir"]:
                _dirs = save_dirs_of_workflow(_snap_wf)
                if _dirs:
                    res["save_dir"] = _dirs[0][2]
                    res["sources"].append("快照目录")
            # 索引区间：开始 == 结束 且 >= 0 → 单镜重渲任务（只跑那一镜）。
            # 这种任务结束索引就是「这一镜」，总镜数按单镜口径跳过，否则会报成 1/5 镜。
            _st_i = _end_i = None
            for _n, _c, _f, _v in index_fields_of(_snap_wf):
                try:
                    _iv = int(_v)
                except (TypeError, ValueError):
                    continue
                if _f in ("开始索引", "start_index"):
                    _st_i = _iv
                elif _f in ("结束索引", "end_index"):
                    _end_i = _iv
            if _st_i is not None and _st_i >= 0 and _st_i == _end_i:
                res["single_shot"] = _st_i + 1
                res["sources"].append("快照单镜区间")
            if not res["single_shot"] and not res["scene_total"]:
                if _end_i is not None and _end_i >= 0:
                    res["scene_total"] = _end_i + 1   # 结束索引是 0 基，总数 = n+1
                    res["sources"].append("快照镜数")
                else:
                    # 整集渲染时结束索引写的是 -1（"跑到末尾"），这时总镜数看台词行：
                    # lines_data 一镜一行，行数就是本集镜数。
                    _lid, _rows = lines_data_of(_snap_wf)
                    if _rows:
                        res["scene_total"] = len(_rows)
                        res["sources"].append("快照台词行数")

    if save_dir:                      # 显式指定最可靠
        res["save_dir"] = save_dir
        res["sources"].append("指定--dir")

    # 状态兜底：还在执行队列里就是 running（history 要跑完才写，别把在跑的报成「未知」）
    if full and not res["status"]:
        try:
            if full in running_pids(url):
                res["running"] = True
                res["status"] = "running"
                res["sources"].append("队列")
        except Exception:
            pass

    # ④ 列保存目录 → 分镜清单（主判据）
    sub = str(res["save_dir"] or "").strip("/")
    if not res["category"]:
        res["category"] = "video"
    if sub:
        try:
            files = list_video_dir(sub, url)
        except Exception as e:
            files = []
            res["note"] = "列目录失败：%r" % (e,)
        if files:
            res["sources"].append("文件列表")
        # 保存目录本身就是合并目录 → 目录里最新的那个就是成片，没有分镜可查
        if str(sub).strip("/").lower() == str(merged_dir).lower() and files:
            newest = max(files, key=lambda x: x["mtime"] or 0)
            res["merged"] = res["merged"] or newest["name"]
            res["category"] = "video"
        # 本次任务窗口：mtime 不早于任务开始
        # 锚点优先级：台账 first_seen > history create_time > 提交时落的工作流快照 time
        # 没有监听进程时台账是空的、fxai 节点又不写 history，只剩快照这一条——必须补上，
        # 否则共用保存目录（如 sucai）会把历史分镜全算成本次成果。
        begin = (t or {}).get("first_seen") or (
            create_ms / 1000.0 if create_ms else None)
        if not begin and snap_meta and snap_meta.get("time"):
            try:
                begin = float(snap_meta["time"])
                res["sources"].append("快照时间")
            except (TypeError, ValueError):
                begin = None
        res["begin_ts"] = begin
        # 只要拿到了锚点就默认只数「本次任务」落盘的分镜：保存目录可能被多个任务共用，
        # 不过滤会把历史分镜算成本次成果。无锚点时才退化成数整个目录。
        _use_win = bool(begin)
        items = []
        for f in files:
            nm = f["name"]
            if not _is_video(nm):
                continue
            if is_merged(nm, sub, merged_dir):
                continue
            idx = shot_index(nm)
            in_win = True
            if _use_win and f["mtime"]:
                in_win = f["mtime"] >= begin - 60
            items.append({"name": nm, "index": idx, "size": f["size"],
                          "mtime": f["mtime"], "in_window": in_win})
        res["files_all"] = sorted(items, key=lambda x: ((x["index"] is None),
                                                        x["index"] or 0, x["name"]))
        shots = [x for x in res["files_all"] if x["index"] is not None and x["size"] > 0]
        if _use_win:
            # 有锚点就是有本任务起点：窗口里 0 个文件 = 真的是 0 镜（刚提交、还没落盘），
            # 不得回落成整个目录，否则会把历史分镜误报成本次已生成。
            shots = [x for x in shots if x["in_window"]]
        if res["single_shot"]:
            # 单镜重渲只认那一镜：同一目录里连续跑多个单镜任务时，时间窗会把
            # 别人的产物也框进来（前一个任务刚写完，mtime 也在窗内），必须按镜号裁掉。
            shots = [x for x in shots if x["index"] == res["single_shot"] - 1]
        # 同一序号多文件（重跑）只保留最新的那个
        by_idx = {}
        for x in sorted(shots, key=lambda y: y["mtime"] or 0):
            by_idx[x["index"]] = x
        res["shots"] = [dict(by_idx[i], token="video/%s|%s" % (sub, by_idx[i]["name"]),
                             shot=i + 1)
                        for i in sorted(by_idx)]
        if res["shots"]:
            res["category"] = "video"   # 目录里确实列出分镜视频 → 按视频任务算
    else:
        res["note"] = (res["note"] + "；" if res["note"] else "") + \
            "未定位到保存目录（台账/结果表/history 都没给），请加 --dir <目录名>"

    idxs = [s["index"] for s in res["shots"]]
    res["done"] = idxs
    res["shot_count"] = len(idxs)
    res["last_index"] = max(idxs) if idxs else None

    # 平均每镜耗时：相邻分镜**落盘时刻**间隔的中位数（抗单镜抖动，重跑乱序也不影响）
    res["avg_shot_seconds"] = None
    _by_time = sorted((x for x in res["shots"] if x.get("mtime")),
                      key=lambda x: x["mtime"])
    _gaps = [b["mtime"] - a["mtime"] for a, b in zip(_by_time, _by_time[1:])
             if b["mtime"] > a["mtime"]]
    if _gaps:
        _gaps.sort()
        res["avg_shot_seconds"] = _gaps[len(_gaps) // 2]

    # 总镜数：显式 --total > 台账 scene_total > 快照推出
    # 单镜重渲任务没有「一共几镜 / 还剩几镜」，这几项一律不填，免得报成 1/5 镜
    total = expect_total or res["scene_total"]
    if res["single_shot"]:
        total = None
    res["scene_total"] = total

    if total:
        res["missing"] = [i for i in range(total) if i not in set(idxs)]
        res["finished"] = not res["missing"] and bool(res["merged"] or res["shots"])
        # 还剩几镜 / 预计还要多久（渲染中看进度就是看这两个数）
        res["remaining"] = len(res["missing"])
        if res["remaining"] and res.get("avg_shot_seconds"):
            res["eta_seconds"] = int(res["remaining"] * res["avg_shot_seconds"])
        else:
            res["eta_seconds"] = None
    if res["missing"]:
        res["next_index"] = res["missing"][0]
    elif res["last_index"] is not None:
        res["next_index"] = res["last_index"] + 1
    elif total:
        res["next_index"] = 0
    if res["next_index"] is not None:
        res["next_shot"] = res["next_index"] + 1
    return res


def format_text(r):
    """人类可读输出。"""
    L = []
    pid = r["prompt_id"]
    L.append("分镜进度 · %s（%s）" % (pid[:8] + ("…" if len(pid) > 8 else ""), r["server"]))
    _st = r.get("status") or "未知"
    if _st == "running":
        _st = "运行中"
    L.append("  状态     : %s%s" % (_st,
                                    "（第 %s/%s 镜）" % (r["scene_current"], r["scene_total"])
                                    if r.get("scene_current") and r.get("scene_total") else ""))
    if r.get("single_shot"):
        L.append("  任务类型 : 单镜重渲（只跑第 %d 镜，不整集重投）" % r["single_shot"])
    if r.get("save_dir"):
        L.append("  保存目录 : video/%s" % r["save_dir"])
    else:
        L.append("  保存目录 : （未定位）")
    if r["shots"]:
        first, last = r["shots"][0], r["shots"][-1]
        _tot = r.get("scene_total")
        L.append("  已生成   : %s（第 %d ~ 第 %d 镜，文件 %s … %s）"
                 % ("%d/%d 镜" % (r["shot_count"], _tot) if _tot
                    else "%d 镜" % r["shot_count"],
                    first["shot"], last["shot"],
                    first["name"], last["name"]))
        L.append("  分镜号   : %s" % "、".join(str(s["shot"]) for s in r["shots"][:24])
                 + (" …" if r["shot_count"] > 24 else ""))
    else:
        if r.get("single_shot"):
            L.append("  已生成   : 第 %d 镜还没落盘（该镜在生成中，或还在队列里排着）"
                     % r["single_shot"])
        else:
            L.append("  已生成   : 0 镜（还没落盘任何分镜视频）")
    L.append("  成片     : %s" % (r["merged"] or "未合并"))
    if r["scene_total"]:
        L.append("  总镜数   : %d 镜%s" % (r["scene_total"],
                                        " ｜ 缺失第 %s 镜" % "、".join(str(i + 1) for i in r["missing"])
                                        if r["missing"] else " ｜ 无缺失"))
    _rem = r.get("remaining")
    if _rem:
        _eta = r.get("eta_seconds")
        _avg = r.get("avg_shot_seconds")
        L.append("  还剩     : %d 镜%s%s"
                 % (_rem,
                    "（约 %d 分钟）" % max(1, int(round(_eta / 60.0))) if _eta else "",
                    "，单镜均耗约 %d 分" % max(1, int(round(_avg / 60.0))) if _avg else ""))
    if r["next_index"] is not None and not r.get("single_shot"):
        L.append("  下一镜   : 第 %d 镜（索引 %d）" % (r["next_shot"], r["next_index"]))
        L.append("  续跑参数 : start_index=%d  # 从第 %d 镜继续生成"
                 % (r["next_index"], r["next_shot"]))
    if r.get("error"):
        L.append("  错误     : %s" % r["error"])
    if r.get("sources"):
        L.append("  数据来源 : %s" % " + ".join(r["sources"]))
    if r.get("note"):
        L.append("  提示     : %s" % r["note"])
    return "\n".join(L)


# ---------- 一键续跑：断了之后一条命令接着跑 ----------
def index_fields_of(wf):
    """在工作流里找「开始索引 / 结束索引」字段：[(node_id, class_type, 字段名, 当前值)]。"""
    out = []
    if not isinstance(wf, dict):
        return out
    for nid, node in wf.items():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "")
        for field, val in (node.get("inputs") or {}).items():
            if field in ("开始索引", "结束索引", "start_index", "end_index"):
                out.append((nid, ct, field, val))
    return out


def lines_data_of(wf):
    """取工作流里的 lines_data（每个镜头一行），返回 (node_id, rows)。rows 为 list 或 None。"""
    if not isinstance(wf, dict):
        return None, None
    for nid, node in wf.items():
        if not isinstance(node, dict):
            continue
        for field, val in (node.get("inputs") or {}).items():
            if field == "lines_data":
                rows = None
                if isinstance(val, str):
                    try:
                        rows = json.loads(val)
                    except Exception:
                        rows = None
                elif isinstance(val, list):
                    rows = val
                return nid, rows
    return None, None


# ---------- 工作流快照（服务重启 /history 清空后的续跑兜底） ----------
# ComfyUI 的 /history 是**内存态**：实测本机只留最近 10 条、全是 0.4 天内的任务，
# 而服务端输出目录里存着 23 天前的分镜——那些任务的原始工作流早就查不回来了。
# 所以每次提交都顺手落一份快照到台账同目录，resume 时 history 取不到就用它。
SNAP_DIR = os.path.join(HOME, "snapshots")


def save_snapshot(pid, wf, server="", also_dir=None):
    """提交成功后存一份工作流快照。also_dir 可再存一份到项目目录（跟着项目走）。"""
    os.makedirs(SNAP_DIR, exist_ok=True)
    data = {"prompt_id": pid, "server": server, "time": time.time(), "workflow": wf}
    path = os.path.join(SNAP_DIR, "%s.json" % pid)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    if also_dir:
        try:
            os.makedirs(also_dir, exist_ok=True)
            with open(os.path.join(also_dir, "%s.workflow.json" % pid), "w",
                      encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass
    return path


def load_snapshot(pid):
    """按 prompt_id（可给前缀）读本地快照 → (workflow, meta)；没有返回 (None, None)。"""
    if not os.path.isdir(SNAP_DIR):
        return None, None
    path = os.path.join(SNAP_DIR, "%s.json" % pid)
    if not os.path.exists(path):
        hits = [f for f in os.listdir(SNAP_DIR) if f.startswith(pid) and f.endswith(".json")]
        if not hits:
            return None, None
        hits.sort(key=lambda f: os.path.getmtime(os.path.join(SNAP_DIR, f)), reverse=True)
        path = os.path.join(SNAP_DIR, hits[0])
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, None
    return data.get("workflow"), {k: v for k, v in data.items() if k != "workflow"}


def list_snapshots(limit=10):
    """最近落的快照（给 status / 排障看）。"""
    if not os.path.isdir(SNAP_DIR):
        return []
    rows = []
    for f in os.listdir(SNAP_DIR):
        if not f.endswith(".json"):
            continue
        p = os.path.join(SNAP_DIR, f)
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        rows.append({"pid": d.get("prompt_id") or f[:-5], "server": d.get("server"),
                     "time": d.get("time"), "mtime": os.path.getmtime(p)})
    rows.sort(key=lambda r: r.get("time") or r["mtime"], reverse=True)
    return rows[:limit]


def workflow_from_history(pid, url="", resolve=True):
    """从 /history/<pid> 取回「上次提交时的完整工作流」（API 格式，可直接重投）。

    这是续跑的关键：断了之后只剩一个 prompt_id，也能原样恢复全部镜头参数
    （每行提示词/时长/参考图、尺寸、帧率、音频模式、保存目录），不用重新准备分镜数据。
    """
    url = resolve_server(url or ((_ledger_task(pid) or {}).get("server") or ""))
    full = resolve_pid(pid, url) if resolve else pid
    entry = history_entry(full, url)
    wf = workflow_of(entry) if entry else None
    return full, url, wf, entry


def resume(pid, url="", wait=False, timeout=7200, dry_run=False, end_index=-1,
           only_this_task=None, save_dir="", expect_total=None, wf_file=None):
    """一键续跑：查已生成到第几镜 → 把工作流的「开始索引」改到下一镜 → 重新提交。

    返回 dict：{old_pid, new_pid, start_index, shot_count, total, save_dir,
                action, reason, submitted, wf_meta}
    ⚠️ 与"整集重投"的差别：只改开始索引，lines_data 保留全部镜头，
       合并节点照旧合并整集（已生成的镜头命中节点缓存时会直接复用）。
    """
    full, srv, wf, _entry = workflow_from_history(pid, url)
    wf_src = "history"
    # history 查不到（ComfyUI 重启 / 历史被滚动清掉）→ 退到 --wf 指定的文件，再退到本地快照
    if not wf and wf_file:
        try:
            with open(wf_file, encoding="utf-8") as f:
                obj = json.load(f)
            wf = obj.get("workflow") if isinstance(obj, dict) else obj
            wf_src = "指定文件"
        except Exception:
            wf = None
    if not wf:
        wf, _meta = load_snapshot(full or pid)
        if wf:
            wf_src = "本地快照"
    out = {"old_pid": full, "server": srv, "new_pid": None, "submitted": False,
           "action": "blocked", "reason": "", "start_index": None, "wf_source": wf_src,
           "shot_count": 0, "total": None, "save_dir": save_dir, "wf_meta": {}}
    if not wf:
        out["reason"] = ("拿不到这条任务的原始工作流：服务端 /history 已查不到（ComfyUI 重启或历史被清），"
                         "本机也没有提交快照。退路：① 用 --wf <工作流json> 指定；"
                         "② 按原脚本重新提交并显式给 start_index（进度用 shots 查）")
        return out
    # 1) 本任务计划多少镜：工作流 lines_data 的行数最权威（显式 --total > 台账 > 行数）
    _nid, rows = lines_data_of(wf)
    total = expect_total or ((_ledger_task(full) or {}).get("scene_total")) \
        or (len(rows) if rows else None)
    out["total"] = total
    out["wf_meta"] = {"lines_data_node": _nid, "lines_rows": len(rows) if rows else None}
    # 2) 查进度（默认按「本任务时间窗」算；取不到任务开始时间时自动退化为整个目录）
    r = query(full, url=srv, save_dir=save_dir, expect_total=total,
              only_this_task=(True if only_this_task is None else only_this_task))
    out["shot_count"] = r["shot_count"]
    out["save_dir"] = r.get("save_dir") or save_dir
    out["merged"] = r.get("merged")
    out["missing"] = r.get("missing") or []
    # 3) 断点索引：本任务窗口优先；窗口内没文件但目录里有 → 按目录最大号往下接
    idx = r.get("next_index")
    all_idx = [f["index"] for f in (r.get("files_all") or []) if f.get("index") is not None]
    dir_next = (max(all_idx) + 1) if all_idx else 0
    if not r["shot_count"] and dir_next:
        idx = dir_next
        out["reason_note"] = ("本任务窗口内没找到文件，按目录最大文件号往下接（第 %d 镜）；"
                              "若该目录被多个任务共用，请加 --dir 指定专用目录" % (dir_next + 1))
    if idx is None:
        idx = 0
    out["dir_next"] = dir_next
    if total:
        # 目录里文件比本任务镜数还多 = 目录被共用、又查不到本任务时间窗，无法归属
        if r["shot_count"] > total:
            out["action"] = "blocked"
            out["reason"] = ("保存目录 video/%s 里有 %d 个分镜文件，多于本任务镜数 %d，"
                             "又查不到本任务的开始时间（台账/history 都没记录），"
                             "分不清哪些是本任务生成的" % (out["save_dir"], r["shot_count"], total))
            out["hint"] = ("两条安全做法：① 换一个本任务专用保存目录重投（--dir <新目录>）；"
                           "② 先 shots --files 人工核对，再用 --total 明确本任务镜数")
            return out
        if idx > total:
            idx = total
    out["start_index"] = idx
    if total and idx >= total and not r.get("missing"):
        out["action"] = "done"
        out["reason"] = "已生成 %d/%d 镜且不缺镜%s，无需续跑" % (
            r["shot_count"], total, "（成片已出：%s）" % r["merged"] if r.get("merged") else "")
        return out
    out["action"] = "resume"
    out["reason"] = "已生成 %d 镜%s，从第 %d 镜接着跑" % (
        r["shot_count"], "/%d" % total if total else "", idx + 1)
    # 4) 改工作流的开始/结束索引
    fields = index_fields_of(wf)
    changed = []
    for nid, ct, field, _val in fields:
        if field in ("开始索引", "start_index"):
            wf[nid]["inputs"][field] = idx
            changed.append("%s.%s=%d" % (ct or nid, field, idx))
        elif field in ("结束索引", "end_index"):
            wf[nid]["inputs"][field] = end_index
            changed.append("%s.%s=%s" % (ct or nid, field, end_index))
    out["changed"] = changed
    out["workflow"] = wf
    if not fields:
        out["action"] = "blocked"
        out["reason"] = ("工作流里没有「开始索引/结束索引」字段（不是多镜控制器类工作流），"
                         "无法按分镜续跑——这种工作流只能整条重跑")
        return out
    if dry_run:
        return out
    # 5) 提交（带上监听的 clientId，等的时候能收定向事件）
    body = {"prompt": wf}
    try:
        if _W is not None:
            lis = _W.Listener(srv)
            body["client_id"] = lis.cid
            lis.close()
    except Exception:
        pass
    data = _post_json(srv.rstrip("/") + "/prompt", body, timeout=30)
    new_pid = (data or {}).get("prompt_id")
    if not new_pid:
        out["action"] = "error"
        out["reason"] = "提交失败：%s" % json.dumps(data, ensure_ascii=False)[:300]
        return out
    out["new_pid"] = new_pid
    out["submitted"] = True
    # 6) 可选：就地等（不再另起命令）
    if wait and _W is not None:
        try:
            lis = _W.Listener(srv)
            t = lis.wait(new_pid, timeout=timeout, verbose=True, tag="续跑")
            lis.close()
            out["wait_status"] = (t or {}).get("status")
        except Exception as e:
            out["wait_status"] = "等待异常：%r" % (e,)
    return out


# ---------- CLI ----------
USAGE = """查「已生成到第几个分镜」（生成视频通用：短剧 / MV / 数字人 / 幻灯片视频）

用法:
  python shot_query.py <prompt_id|前缀|pid文件> [...多个] [选项]
  python shot_query.py now              # 查此刻正在跑的那个（取 /queue 的 running，任务串行）
  （参数也可以是 pid 文件路径，如 ep01_pid.txt，文件里每行一个 pid）

一键续跑（断了 / 服务器挂了之后，一条命令接着跑）：
  python shot_query.py resume <prompt_id> [--wait] [--timeout 秒] [--dry-run]
         [--dir 保存目录] [--total 总镜数] [--end-index -1] [--url 地址] [--debug]
  原理：从 /history/<pid> 取回上次提交的完整工作流 → 查已生成到第几镜 →
        把「开始索引」改到下一镜 → 重新 POST /prompt（lines_data 保留全部镜头，
        合并节点照旧合并整集；已生成镜头命中节点缓存时直接复用）。
        --dry-run 只算不提交；--wait 提交后就地等到终态。
  （也可：comfy_watch.py resume <pid> …）

选项（查询模式）：
  --dir <保存目录>     该任务保存分镜的目录（如 sucai）；不传则自动定位
  --total <总镜数>     已知总镜数；不传则从提交快照的台词行数/结束索引自动取
  --url <地址>         指定 ComfyUI 服务器（不传：本机 > 全局配置）
  --files              打印目录内文件清单（分镜号/大小/时间）
  --json               机器读（流水线取 next_index / remaining / eta_seconds）
  --resume             只出一行：<pid>\\t<start_index>\\t<已生成镜数>
  --this-task-only     强制只算本次任务时间窗（拿到提交锚点时本来就会自动过滤）
  --debug              把每次真实 HTTP 请求打到 stderr，供逐条核对

渲染中看进度：直接给 prompt_id 即可，其余全部自动——保存目录、总镜数、提交时刻
都从「提交时落的工作流快照」里读，再数保存目录里本任务落盘的分镜，最后算出
「已生成 N/M 镜 · 还剩 K 镜 · 约 X 分钟」（单镜均耗取相邻落盘间隔的中位数）。
  python shot_query.py 8bc2f030        # → 已生成 10/14 镜，还剩 4 镜（约 22 分钟）

全部结论只由接口回答（不扫本机磁盘）：台账 → 结果表 → history → 提交快照 → 文件列表。

判定口径：视频文件只要不在合并目录（merged）里，文件名上的文件号 = 分镜序号
（000.mp4 = 第 1 镜，007.mp4 = 第 8 镜）；合并片（成片）不算分镜。
"""


def _read_pid_file(path):
    """参数是已存在的文件 → 读里面的 prompt_id（每行一个，忽略空行与 # 注释）。"""
    try:
        with open(path, encoding="utf-8") as f:
            rows = [x.strip() for x in f.read().splitlines()]
    except Exception:
        return []
    return [x for x in rows if x and not x.startswith("#")]


def format_resume(r):
    out = []
    out.append("续跑 · 原任务 %s（%s）" % ((r["old_pid"] or "-")[:8], r["server"]))
    out.append("  工作流来源: %s" % r.get("wf_source"))
    if r["action"] == "blocked":
        out.append("  无法续跑：%s" % r["reason"])
        if r.get("shot_count"):
            out.append("  已生成   : %d 镜（保存目录 %s）" % (r["shot_count"], r.get("save_dir")))
        return "\n".join(out)
    out.append("  已生成   : %d 镜%s" % (r["shot_count"],
                                      " / 共 %s 镜" % r["total"] if r.get("total") else ""))
    if r.get("missing"):
        out.append("  缺失     : 第 %s 镜" % "、".join(str(i + 1) for i in r["missing"][:12]))
    if r["action"] == "done":
        out.append("  结论     : %s" % r["reason"])
        return "\n".join(out)
    out.append("  保存目录 : video/%s" % (r.get("save_dir") or "?"))
    out.append("  续跑起点 : 第 %d 镜（开始索引 %d）" % (r["start_index"] + 1, r["start_index"]))
    out.append("  改动     : %s" % ("、".join(r.get("changed") or []) or "-"))
    if r.get("submitted"):
        out.append("  ✅ 已提交 : %s" % r["new_pid"])
        out.append("  接着等   : comfy_watch.py wait %s --timeout 5400" % r["new_pid"])
        if r.get("wait_status"):
            out.append("  等待结果 : %s" % r["wait_status"])
    else:
        out.append("  （--dry-run：只算不提交，去掉该参数即真提交）")
    return "\n".join(out)


def main(argv=None):
    global DEBUG
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        print("库调用：import shot_query as SQ; r = SQ.query(pid); "
              "r['next_index'] 即断点续跑的 start_index")
        return 0
    opt = {"dir": "", "total": None, "url": "", "json": False, "resume": False,
           "files": False, "only": None, "wait": False, "timeout": 7200,
           "dry": False, "end_index": -1, "wf": None}
    mode = "shots"
    if argv and argv[0] == "resume":
        mode = "resume"
        argv = argv[1:]
    elif argv and argv[0] in ("now", "running"):
        mode = "now"
        argv = argv[1:]
    pids = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dir" and i + 1 < len(argv):
            opt["dir"] = argv[i + 1]; i += 2; continue
        if a == "--total" and i + 1 < len(argv):
            try:
                opt["total"] = int(argv[i + 1])
            except ValueError:
                pass
            i += 2; continue
        if a == "--url" and i + 1 < len(argv):
            opt["url"] = argv[i + 1]; i += 2; continue
        if a == "--json":
            opt["json"] = True; i += 1; continue
        if a == "--resume":
            opt["resume"] = True; i += 1; continue
        if a == "--files":
            opt["files"] = True; i += 1; continue
        if a == "--this-task-only":
            opt["only"] = True; i += 1; continue
        if a == "--debug":
            DEBUG = True; i += 1; continue
        if a == "--wait":
            opt["wait"] = True; i += 1; continue
        if a == "--dry-run":
            opt["dry"] = True; i += 1; continue
        if a == "--timeout" and i + 1 < len(argv):
            try:
                opt["timeout"] = int(argv[i + 1])
            except ValueError:
                pass
            i += 2; continue
        if a == "--end-index" and i + 1 < len(argv):
            try:
                opt["end_index"] = int(argv[i + 1])
            except ValueError:
                pass
            i += 2; continue
        if a == "--wf" and i + 1 < len(argv):
            opt["wf"] = argv[i + 1]; i += 2; continue
        if os.path.isfile(a):          # pid 文件（如 ep01_pid.txt）
            pids.extend(_read_pid_file(a))
        else:
            pids.append(a)
        i += 1
    if mode == "now" and not pids:
        _u = resolve_server(opt["url"])
        pids = running_pids(_u)
        if not pids:
            print("当前没有正在执行的视频任务（队列空闲）；要查已完成的任务就直接给 prompt_id。")
            return 0
        if not opt["json"]:
            print("当前正在执行：%s（队列里还排着 %d 个）\n"
                  % (pids[0], pending_count(_u)))
    if not pids:
        print("缺少 prompt_id")
        return 1

    if mode == "resume":
        outs = []
        for pid in pids:
            r = resume(pid, url=opt["url"], wait=opt["wait"], timeout=opt["timeout"],
                       dry_run=opt["dry"], end_index=opt["end_index"],
                       save_dir=opt["dir"], expect_total=opt["total"],
                       only_this_task=opt["only"], wf_file=opt["wf"])
            if opt["json"]:
                r.pop("workflow", None)      # 整个工作流太大，不进 json
            outs.append(r)
            if not opt["json"]:
                print(format_resume(r))
                print()
        if opt["json"]:
            print(json.dumps(outs if len(outs) > 1 else outs[0], ensure_ascii=False, indent=1))
        return 0 if all(o.get("submitted") or o.get("action") == "done" for o in outs) else 1

    out = []
    for pid in pids:
        r = query(pid, url=opt["url"], save_dir=opt["dir"],
                  expect_total=opt["total"], only_this_task=(opt["only"] or False))
        out.append(r)
    if opt["json"]:
        print(json.dumps(out if len(out) > 1 else out[0], ensure_ascii=False, indent=1))
        return 0
    for r in out:
        if opt["resume"]:
            print("%s\t%d\t%d" % (r["prompt_id"],
                                  r["next_index"] if r["next_index"] is not None else -1,
                                  r["shot_count"]))
            continue
        print(format_text(r))
        if opt["files"] and r["files_all"]:
            print("  -- 目录内文件 --")
            for f in r["files_all"][:40]:
                tag = "第%d镜" % (f["index"] + 1) if f["index"] is not None else "非序号"
                print("     %-28s %-8s %8.1fMB  %s"
                      % (f["name"], tag, f["size"] / 1048576.0, _age(f["mtime"])))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
