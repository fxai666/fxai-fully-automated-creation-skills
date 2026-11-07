# -*- coding: utf-8 -*-
# =====================================================================
# fxai-whisper-transcribe —— 语音转文本客户端（STT）
#
# 只做一件事：把音频变成文字。**全程走 HTTP API**，不跑本地 CLI 子进程：
#   GET  /health      探活
#   POST /transcribe  转写（JSON + base64 音频），出 {success, text, segments}
#
# 🔴 地址策略：**本机 API 优先 → 线上兜底**（与 TTS / ComfyUI 同一套）。
#   顺序：函数参数 url > 本机 local_url（探得通）> 线上 cloud_url > 本机地址兜底。
#   也就是说本机 8185 的 HTTP 服务只要在线就走它，探不通才走线上；force_cloud 可强制走线上。
#   全程 HTTP 接口，**不起本地进程、不 subprocess 调 whisper**（本地 CLI transcribe.py 仅供排障）。
# 两边都没起 → 报错提示人手动启动（本机服务的启停由人负责，技能只发 HTTP）。
#
# 环境配置一律读用户级全局配置 ~/.workbuddy/fxai_global.json 的 whisper 段，代码不写死地址：
#   whisper = {
#     "local_url": "http://127.0.0.1:8185",
#     "cloud_url": "https://...",
#     "prefer_local": true,
#     "default_language": "zh",
#     "default_beam": 5,
#     "timeout": 600
#   }
# =====================================================================
import base64
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(os.path.dirname(_HERE), "fxai-common"),
           os.path.join(os.path.dirname(_HERE), "fxai-workflow")):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import fxai_common as fxc  # noqa: E402

fxc.allow_local()                  # 本机地址绕过系统代理
_HTTPS = fxc.opener()

_GLOBAL_CFG_PATH = os.environ.get(
    "FXAI_GLOBAL_CONFIG", os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))

WHISPER_PORT = 8185
LOCAL_URL_DEFAULT = "http://127.0.0.1:%d" % WHISPER_PORT

norm_url = fxc.norm_url

# prefer_local() 命中后，本进程内默认走本机（不影响全局配置）
_LOCAL_ON = {"on": False}

# 云端自动开机（仙宫云）惰性导入
_XGY_DIR = os.path.join(os.path.dirname(_HERE), "fxai-workflow")


# ---------- 配置 ----------
def global_cfg():
    """用户级全局部署配置（fxai_global.json）。"""
    try:
        with open(_GLOBAL_CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def whisper_cfg():
    """全局配置里的 whisper 段。"""
    c = global_cfg().get("whisper")
    return c if isinstance(c, dict) else {}


def local_url():
    return norm_url(whisper_cfg().get("local_url") or LOCAL_URL_DEFAULT)


def cloud_url():
    return norm_url(whisper_cfg().get("cloud_url") or "")


def whisper_url(override=""):
    """取用顺序：**函数参数 > 本机地址（本机优先，探得通即用）> 线上地址 > 本机地址兜底**。

    🔴 本机 API 优先：本机 8185 在线就走它；探不通才走 cloud_url。全程 HTTP，不起本地进程。
    """
    if norm_url(override):
        return norm_url(override)
    if _LOCAL_ON["on"]:
        return local_url()
    return cloud_url() or local_url()


def default_language():
    return str(whisper_cfg().get("default_language") or "zh").strip() or "zh"


def default_beam():
    try:
        return int(whisper_cfg().get("default_beam", 5))
    except Exception:
        return 5


def default_timeout():
    try:
        return int(whisper_cfg().get("timeout", 600))
    except Exception:
        return 600


def prefer_local(timeout=2.0, quiet=False):
    """探本机 whisper：探得通就把本进程默认地址切成本机。

    返回 True = 走本机；False = 本机没起，调用方照旧走线上。
    """
    lu = local_url()
    if not check(lu, timeout=timeout):
        if not quiet:
            print("[whisper] 本机 %s 未就绪，按配置走线上" % lu, flush=True)
        return False
    _LOCAL_ON["on"] = True
    if not quiet:
        print("[whisper] 本机已就绪，本次走本机（不计费）", flush=True)
    return True


# ---------- 服务连通 ----------
def _get(url, timeout=8):
    try:
        with _HTTPS.open(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        raise RuntimeError("请求失败 %s：%s" % (url, e))


def check(url=None, timeout=4):
    """探活：GET /health 返 status=healthy 即就绪（懒加载服务 model_loaded=False 也算就绪）。"""
    u = whisper_url(url)
    try:
        d = json.loads(_get(u + "/health", timeout=timeout))
    except Exception:
        return False
    if d.get("status") != "healthy":
        return False
    if "model_loaded" in d:
        return True
    return bool(d.get("model_loaded"))


def status(url=None, timeout=6):
    """服务与模型状态。

    返回 {url, online, state, busy, in_flight, model_loaded, device, idle_timeout, cloud, detail}。
    state：idle / loading / transcribing（正在识别）/ unloading。
    """
    u = whisper_url(url) or local_url()
    info = {"url": u, "online": False, "state": None, "busy": False, "in_flight": 0,
            "model_loaded": False, "device": None, "idle_timeout": None,
            "cloud": is_cloud(u), "features": [], "detail": ""}
    try:
        d = json.loads(_get(u + "/health", timeout=timeout))
    except Exception as e:
        info["detail"] = "服务不可达：%s" % (str(e)[:120] or type(e).__name__)
        return info
    info["online"] = d.get("status") == "healthy"
    info["state"] = d.get("state")
    info["busy"] = bool(d.get("busy"))
    info["in_flight"] = int(d.get("in_flight") or 0)
    info["model_loaded"] = bool(d.get("model_loaded"))
    info["device"] = d.get("device")
    info["idle_timeout"] = d.get("idle_timeout")
    info["features"] = d.get("features") or []
    return info


def _xgy():
    try:
        if _XGY_DIR not in sys.path:
            sys.path.insert(0, _XGY_DIR)
        import xgy_guard as x
        return x
    except Exception:
        return None


def is_cloud(url):
    """是不是仙宫云容器地址（只有这类才谈得上自动拉起实例）。"""
    x = _xgy()
    if not x:
        return False
    try:
        return bool(x.is_xgpu(url))
    except Exception:
        return x.XGPU_HOST in (url or "")


# ---------- 服务端能力 ----------
FEATURES = ("clip_seg", "condition", "passes")
_CAP = {}          # url -> set(features)
_WARNED = set()


def capabilities(url=None, timeout=6):
    """服务端支持的能力集（读 /health 的 features）。

    老版本没有 features 字段 → 返回空集（只能按最基础参数用），
    客户端据此提示「哪些参数在这台服务端上不生效」，不做静默降级。
    """
    u = norm_url(url) or whisper_url()
    if u in _CAP:
        return _CAP[u]
    caps = set()
    try:
        d = json.loads(_get(u + "/health", timeout=timeout))
        f = d.get("features")
        if isinstance(f, list):
            caps = set(str(x) for x in f)
    except Exception:
        pass
    _CAP[u] = caps
    return caps


def _warn_once(url, key, msg):
    tag = (url, key)
    if tag in _WARNED:
        return
    _WARNED.add(tag)
    print("[whisper] ⚠ %s（%s）" % (msg, url), flush=True)


def _boot_or_fail(u, allow_boot, wait):
    x = _xgy()
    if not x:
        raise RuntimeError("whisper 云端地址（%s）探不通：请手动启动本机 Whisper 服务（8185），"
                           "或确认云端实例在线。" % u)
    try:
        ok, msg = x.ensure_service(u, lambda a: check(a, timeout=8), wait=wait, name="Whisper")
        print("[whisper] 云端实例：%s" % msg, flush=True)
    except Exception as e:
        ok, msg = False, "云端开机异常：%s" % e
        print("[whisper] %s" % msg, flush=True)
    if ok:
        return u
    raise RuntimeError("whisper 云端实例已尝试自动开机但仍未就绪（%s）。" % u)


def ensure(url=None, allow_boot=True, wait=300):
    """开工前保证 whisper 服务在线：**本机优先 → 线上兜底 → 都起不来提示手动启动**。

    只有仙宫云容器地址才谈得上自动开机；本机地址探不通只提示人启动，不代管开关机。
    """
    if url:
        u = whisper_url(url)
        if check(u, timeout=4):
            return u
        if allow_boot and is_cloud(u):
            return _boot_or_fail(u, allow_boot, wait)
        raise RuntimeError("whisper 地址（%s）探不通：请确认服务已启动。" % u)

    lu = local_url()
    if check(lu, timeout=4):
        _LOCAL_ON["on"] = True
        return lu

    cu = cloud_url()
    if cu:
        if check(cu, timeout=4):
            return cu
        if allow_boot and is_cloud(cu):
            return _boot_or_fail(cu, allow_boot, wait)

    raise RuntimeError(
        "whisper 服务两边都没起来：本机 %s 没启动，线上地址也探不通。"
        "请手动启动其一：① 本机在「凤希本地 AI 控制台」启动 Whisper（端口 8185，"
        "地址可在全局配置 whisper.local_url 改）；② 线上确认 whisper.cloud_url 已填且实例在线。"
        % lu)


# ---------- 转写 ----------
def _poll_task(base, tid, timeout):
    """轮询异步任务直到完成，返回结果 dict。"""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            d = json.loads(_get(base + "/task/" + tid, timeout=20))
        except Exception as e:
            raise RuntimeError("查询任务状态失败：%s" % e)
        st = d.get("state")
        if st != last:
            if st == "queued":
                print("[whisper] 排队中：前方 %d 个任务（队列共 %d 个）"
                      % (d.get("ahead") or 0, d.get("queue_len") or 0), flush=True)
            elif st == "running":
                print("[whisper] 轮到我了，开始转写…", flush=True)
            last = st
        if st == "done":
            try:
                return json.loads(_get(base + "/task/" + tid + "/result", timeout=180))
            except Exception as e:
                raise RuntimeError("取转写结果失败：%s" % e)
        if st == "error":
            raise RuntimeError("转写失败：%s" % (d.get("error") or "未知错误"))
        time.sleep(1)
    raise RuntimeError("等转写超时（%.0f 秒）：任务 %s 仍未完成，可在服务端 /task/%s 继续查"
                       % (timeout, tid, tid))


def _post_transcribe(u, payload, timeout):
    """提交转写：**优先异步任务**，老服务端没有该端点时退回同步长连接。

    🔴 为什么不直接同步：一段长音频要转几分钟，期间服务端一个字节都不发，
    nginx（60s）/Cloudflare（100s）会把这种静默连接当死连接掐掉，表现是 504
    或连接重置 —— 用户看到的是「失败」，其实任务还在跑。异步模式下每次 HTTP
    都是短请求，排多久都不掉线，所以服务端敢把并发排队放宽到分钟级。
    """
    base = u.rstrip("/")
    try:
        sub = fxc.http_post(base + "/transcribe/async", payload, 60, op=_HTTPS)
    except RuntimeError as e:
        msg = str(e)
        if "404" in msg or "405" in msg:
            return fxc.http_post(base + "/transcribe", payload, timeout=timeout, op=_HTTPS)
        raise
    tid = sub.get("task_id") or sub.get("id")
    if not tid:
        return fxc.http_post(base + "/transcribe", payload, timeout=timeout, op=_HTTPS)
    ahead = sub.get("ahead") or 0
    if ahead > 0:
        print("[whisper] 已入队：前方 %d 个任务（本机 %s 人可同时转写，任务号 %s）"
              % (ahead, sub.get("workers") or "?", tid), flush=True)
    return _poll_task(base, tid, timeout)


def transcribe_api(audio, url=None, language="zh", timestamps=False, vad=False,
                   beam=5, clip_seg=None, condition=True, timeout=600):
    """把音频推到 /transcribe（JSON + base64），返回文本（或带时间轴的片段列表）。"""
    u = norm_url(url) or whisper_url()
    if not u:
        raise RuntimeError("未配置 whisper 地址（local_url / cloud_url）")
    if not os.path.isfile(audio):
        raise RuntimeError("音频文件不存在：%s" % audio)
    caps = capabilities(u)
    if not condition and "condition" not in caps:
        _warn_once(u, "condition",
                   "服务端不支持 condition 参数，condition=False 不生效（歌曲幻觉会变多）")
    if clip_seg and "clip_seg" not in caps:
        _warn_once(u, "clip_seg",
                   "服务端不支持 clip_seg，硬切分段被忽略（多版交叉会退化成同一份整首结果）")
    with open(audio, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    payload = {
        "audio_base64": b64,
        "filename": os.path.basename(audio),
        "language": language,
        "timestamps": bool(timestamps),
        "vad": bool(vad),
        "beam": int(beam),
        "clip_seg": (float(clip_seg) if clip_seg else None),
        "condition": bool(condition),
    }
    print("[whisper] 转写中：%s（%s，超时 %ds）" % (os.path.basename(audio), u, timeout), flush=True)
    res = _post_transcribe(u, payload, timeout)
    if not (res.get("success") or res.get("ok")):
        raise RuntimeError("转写失败：%s" % res)
    if timestamps:
        return res.get("segments") or []
    return (res.get("text") or "").strip()


def transcribe(audio, language=None, timestamps=False, vad=False, beam=None,
               clip_seg=None, condition=True, url=None, allow_boot=True, timeout=None,
               force_cloud=False):
    """语音转文本主入口（全接口）。

    language    语言，缺省读配置 whisper.default_language（zh）；传 "auto" 让模型自判
    timestamps  True 返回 list[{start,end,text,words?}]，False 返回 str
    beam        beam size，缺省读配置 whisper.default_beam（5）
    clip_seg    按该秒数硬切分段转写（9/11/13），不同长度各跑一版即互为校验
    condition   是否 condition_on_previous_text（歌里关掉可减少幻觉复读）
    force_cloud True = 忽略本机，直接走线上地址
    """
    if not os.path.isfile(audio):
        raise RuntimeError("音频文件不存在：%s" % audio)
    language = language or default_language()
    beam = default_beam() if beam is None else beam
    timeout = int(timeout or default_timeout())

    if force_cloud and not url:
        url = cloud_url()
        if not url:
            raise RuntimeError("force_cloud 需要配置 whisper.cloud_url")
    if url is None and not _LOCAL_ON["on"]:
        prefer_local(quiet=True)

    u = ensure(url, allow_boot=allow_boot)
    return transcribe_api(audio, url=u, language=language, timestamps=timestamps,
                          vad=vad, beam=beam, clip_seg=clip_seg, condition=condition,
                          timeout=timeout)


def transcribe_segments(audio, **kw):
    """只要带时间轴的片段列表（含 words 时每段带逐词时间戳）。"""
    return transcribe(audio, timestamps=True, **kw)


def transcribe_passes(audio, passes, language=None, url=None, allow_boot=True,
                      timeout=None, force_cloud=False):
    """一次上传、服务端连跑多版（多版交叉校验用），返回每版结果列表。

    passes: [{"beam":5,"clip_seg":9,"word_ts":True,"condition":False}, ...]
    服务端不支持 passes 时自动退化为逐版单独请求（每版重传一次音频）。
    返回 [{text, segments, duration, ...}, ...]，顺序与 passes 一致。
    """
    if not os.path.isfile(audio):
        raise RuntimeError("音频文件不存在：%s" % audio)
    language = language or default_language()
    timeout = int(timeout or default_timeout())
    if force_cloud and not url:
        url = cloud_url()
        if not url:
            raise RuntimeError("force_cloud 需要配置 whisper.cloud_url")
    if url is None and not _LOCAL_ON["on"]:
        prefer_local(quiet=True)
    u = ensure(url, allow_boot=allow_boot)

    with open(audio, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    payload = {
        "audio_base64": b64,
        "filename": os.path.basename(audio),
        "language": language,
        "passes": [dict(p, language=(p.get("language") or language)) for p in passes],
    }
    print("[whisper] 多版转写：%s（%d 版，%s）" % (os.path.basename(audio), len(passes), u), flush=True)
    res = _post_transcribe(u, payload, timeout)
    if not (res.get("success") or res.get("ok")):
        raise RuntimeError("多版转写失败：%s" % res)
    got = res.get("passes")
    if isinstance(got, list) and len(got) == len(passes):
        return got
    # 老服务端不认识 passes：逐版单独请求兜底
    _warn_once(u, "passes", "服务端不支持 passes（多版一次上传），改为逐版请求，每版重传一次音频")
    out = []
    for p in passes:
        out.append({"text": transcribe_api(
            audio, url=u, language=language, timestamps=True,
            vad=bool(p.get("vad", False)), beam=int(p.get("beam", 5)),
            clip_seg=p.get("clip_seg"), condition=bool(p.get("condition", True)),
            timeout=timeout)})
    return out


def format_lines(segments, words=False, bounds_only=False):
    """把片段列表格式化成文本行（与本地 CLI 输出同格式，下游解析函数不用改）。

    words=True       每词一行 `[0.12->0.45] 词`
    bounds_only=True 每段一行 `[0.12->3.45]`（无文本）
    缺省             每段一行 `[0.1->3.4] 文本`
    """
    lines = []
    for s in segments or []:
        ws = s.get("words") or []
        if words:
            for w in ws:
                t = (w.get("word") or "").strip()
                if t:
                    lines.append("[%.2f->%.2f] %s" % (w["start"], w["end"], t))
        elif bounds_only:
            if ws:
                a, b = ws[0]["start"], ws[-1]["end"]
            else:
                a, b = s["start"], s["end"]
            lines.append("[%.2f->%.2f]" % (a, b))
        else:
            lines.append("[%.1f->%.1f] %s" % (s["start"], s["end"], (s.get("text") or "").strip()))
    return "\n".join(lines)


def write_transcript(audio, out_path, words=False, bounds_only=False, **kw):
    """跑接口并把结果按本地 CLI 同格式写盘（字幕类技能的统一入口）。

    words       逐词时间戳（每词一行）
    bounds_only 只要每段的起止（每段一行，无文本）
    kw 透传 transcribe（language / vad / beam / clip_seg / condition / url / timeout ...）
    返回写入的文本。
    """
    segs = transcribe(audio, timestamps=True, **kw)
    text = format_lines(segs, words=words, bounds_only=bounds_only)
    if out_path:
        d = os.path.dirname(os.path.abspath(out_path))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print("SAVED %s (chars=%d)" % (out_path, len(text)), flush=True)
    return text


# ---------- CLI ----------
def main():
    import argparse
    ap = argparse.ArgumentParser(description="语音转文本（全程走 HTTP 接口）")
    ap.add_argument("audio", nargs="?", default=None, help="音频文件路径（--status 时可省略）")
    ap.add_argument("--lang", default=None, help="语言，缺省读配置（zh）；auto 让模型自判")
    ap.add_argument("--ts", action="store_true", help="输出带时间轴")
    ap.add_argument("--vad", action="store_true", help="启用 VAD 静音过滤")
    ap.add_argument("--clip-seg", type=float, default=None, help="按秒数硬切分段")
    ap.add_argument("--beam", type=int, default=None)
    ap.add_argument("--cloud", action="store_true", help="强制走线上地址")
    ap.add_argument("--url", default=None, help="覆盖地址")
    ap.add_argument("--out", default=None, help="输出文本文件路径")
    ap.add_argument("--timeout", type=int, default=None)
    ap.add_argument("--status", action="store_true", help="只打印 whisper 服务状态")
    a = ap.parse_args()

    if a.status:
        for tag, u in (("本机", local_url()), ("线上", cloud_url())):
            if not u:
                print("%s：未配置" % tag)
                continue
            s = status(u)
            print("%s %s：online=%s state=%s busy=%s model_loaded=%s device=%s features=%s%s"
                  % (tag, u, s["online"], s["state"], s["busy"], s["model_loaded"], s["device"],
                     ",".join(s["features"]) or "（旧版，无声明）",
                     ("（%s）" % s["detail"]) if s["detail"] else ""))
        return

    lang = None if a.lang in ("auto", "none", "None") else a.lang
    text = transcribe(a.audio, language=lang, timestamps=a.ts, vad=a.vad,
                      beam=a.beam, clip_seg=a.clip_seg, url=a.url,
                      force_cloud=a.cloud, timeout=a.timeout)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            if isinstance(text, list):
                f.write("\n".join("[%.1f->%.1f] %s" % (s["start"], s["end"], s["text"]) for s in text))
            else:
                f.write(text)
        print("SAVED %s" % a.out)
    else:
        if isinstance(text, list):
            for s in text:
                print("[%.1f->%.1f] %s" % (s["start"], s["end"], s["text"]))
        else:
            print(text)


if __name__ == "__main__":
    main()
