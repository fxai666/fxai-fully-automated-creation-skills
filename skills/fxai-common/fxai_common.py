# -*- coding: utf-8 -*-
"""fxai 公共件：地址规范化 + 直连 HTTP。

被 fxai-workflow / fxai-voiceover / fxai-comfyui-watcher 等技能共用，
实现只有这一份，各技能直接 import。

服务地址一律经本模块出口，规则统一：反斜杠换正斜杠、压掉重复斜杠、去尾斜杠。
"""
import json
import os
import urllib.error
import urllib.request

UA = "fxai/1.0"


def norm_url(s):
    """地址规范化。

    ① Windows 路径/手抄地址混进反斜杠（`http://host:8188\\`）→ 全换成正斜杠；
    ② 基址末尾带斜杠再拼端点变成 `//`，网关会把 `//` 当路径的一部分——
       表现为「接口偶发 404 / 空响应」。
    """
    s = str(s or "").strip().replace("\\", "/")
    if "://" in s:
        scheme, _, tail = s.partition("://")
        while "//" in tail:
            tail = tail.replace("//", "/")
        s = scheme + "://" + tail
    return s.rstrip("/")


def allow_local(trusted=("127.0.0.1", "localhost")):
    """把本机地址加进 NO_PROXY / no_proxy，否则本机 127.0.0.1 会走系统代理、
    表现为连得上但请求卡死或返回代理页。"""
    joined = ",".join(trusted)
    for key in ("NO_PROXY", "no_proxy"):
        cur = os.environ.get(key, "")
        if not any(t in cur.split(",") for t in trusted):
            os.environ[key] = joined if not cur else cur + "," + joined


def opener():
    """不过代理的 opener（本机服务必须走直连），带上 fxai 的 User-Agent。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    op.addheaders = [("User-Agent", UA)]
    return op


MARK = "<|endofprompt|>"      # 口播规范：语气指令和说话内容的分界


def parse_line(line):
    """口播一行 → (语气指令, 说话内容)。

    行格式 `用<语言>、<情感>、<情绪>的语气说<|endofprompt|>。<说话内容>`：
    带 MARK 的按格式拆开；没带 MARK 的整行就是说话内容，语气指令留空由调用方兜底。
    """
    line = str(line or "").strip()
    if MARK not in line:
        return "", line
    tag, content = line.split(MARK, 1)
    return tag.strip().rstrip("。").strip(), content.strip().lstrip("。").strip()


def check_script(lines, limit=80, require_tag=False):
    """逐段质检口播稿，返回问题清单 [(段号, 原因, 片段)]，空列表 = 全过。

    查三件事：
    ① 段里带没带分隔符 MARK（require_tag=True 时，缺分隔符直接算问题）；
    ② 带分隔符的段，情感语气标签和说话内容是不是都非空；
    ③ 说话内容长度有没有到 limit 字（标签不计入，分隔句号占 1 个字）。

    require_tag=False 用于普通分镜：整段没带 MARK 当作正常正文放行，只查长度。
    """
    bad = []
    for i, line in enumerate(lines or [], 1):
        line = str(line or "").strip()
        if not line:
            continue
        if MARK not in line:
            if require_tag:
                bad.append((i, "缺分隔符 %s" % MARK, line[:20]))
            elif len(line) > limit:
                bad.append((i, "正文 %d 字 > %d" % (len(line), limit), line[:20]))
            continue
        tag, content = parse_line(line)
        if require_tag and not tag:
            bad.append((i, "缺情感语气标签", content[:20]))
        if not content:
            bad.append((i, "缺说话内容", tag[:20]))
        elif len(content) + 1 > limit:      # +1 = 分隔句号，必带且占一个字
            bad.append((i, "正文 %d 字（含分隔句号 %d）≥ %d" % (len(content), len(content) + 1, limit),
                        content[:20]))
    return bad


def format_bad(bad):
    """问题清单拼成给人看的多行文本。"""
    return "\n".join("  第 %d 段：%s（%s…）" % (i, why, frag) for i, why, frag in bad)


def check_wait_policy(block, timeout_sec, task_id="", source="后台任务", max_block_sec=60):
    """等长任务时校验等待参数，**违规直接抛 RuntimeError**，不当事后提醒。

    规矩（违规即抛）：
      · `block=True` 时 `timeout_sec` 不得超过 `max_block_sec`（默认 60 秒）；
      · `block=False` 一律放行（非阻塞查询交给调用方自己的预算控制）。

    用法：
        from fxai_common import check_wait_policy
        check_wait_policy(block=True, timeout_sec=600)      # → 抛错
        check_wait_policy(block=False, task_id="iTJj7I")    # → OK

    收到「仍在运行 / 你会被自动通知 / do NOT poll」提示后，正确做法是
    **停止查询、去干别的、等 `<task-notification>`**，而不是再调一次等待。
    """
    if not block:
        return
    try:
        t = float(timeout_sec)
    except (TypeError, ValueError):
        return
    if t > float(max_block_sec):
        raise RuntimeError(
            "拒绝阻塞等待 %s：block=True 时 timeout_sec 不得超过 %s 秒（当前 %s）。"
            "长任务完成后会自动通知，请停止查询去干别的；"
            "「界面没动静」不等于「任务卡住」。"
            % (source, max_block_sec, timeout_sec))


def wait_policy_guard(max_block_sec=60, max_polls=1):
    """返回 `check(block, timeout_sec=None, task_id="", seen=0)` 闭包，
    供长任务循环里复用同一套预算（把 max_block_sec / max_polls 固定住）。

    典型用法（批量生图 / 批量渲染的收尾等待）：
        guard = wait_policy_guard()
        for i, pid in enumerate(pids):
            guard(block=False, task_id=pid, seen=i)   # 同一任务只查一次
    """
    def _check(block, timeout_sec=None, task_id="", seen=0):
        if block:
            check_wait_policy(True, timeout_sec if timeout_sec is not None else max_block_sec,
                              task_id, "后台任务", max_block_sec)
        if task_id and int(seen) > int(max_polls):
            raise RuntimeError(
                "同一任务已查询 %s 次（上限 %s）：别再轮询了，完成会自动通知。"
                % (seen, max_polls))
    return _check


def _detail(e):
    try:
        return json.loads(e.read().decode("utf-8", "replace")).get("detail", "") or str(e)
    except Exception:
        return str(e)


def http_get(url, timeout=8, op=None):
    """GET 返回文本；失败抛 RuntimeError（带上接口给的 detail）。"""
    try:
        with (op or opener()).open(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        raise RuntimeError("请求失败 %s：%s" % (url, e))


def http_post(url, payload, timeout=60, op=None):
    """POST json 并返回解析后的 dict；HTTP 错误码带上接口的 detail 一起抛。"""
    body = json.dumps(payload).encode("utf-8") if not isinstance(payload, (bytes, str)) else payload
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    try:
        with (op or opener()).open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("接口返回 %s：%s" % (e.code, _detail(e)))
    except Exception as e:
        raise RuntimeError("接口调用失败 %s：%s" % (url, e))
