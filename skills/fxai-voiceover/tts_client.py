# -*- coding: utf-8 -*-
# =====================================================================
# fxai-voiceover —— 数字人口播音频（TTS）客户端
#
# 只做一件事：把文字变成口播音频，全程走 HTTP API（POST /synthesize）。
# 音频由接口返回 base64，由本模块落盘到指定目录；不读服务端的 saved_path，
# 不扫服务/本机磁盘上的既有音频文件，本机与远端（云端）行为完全一样。
#
# 服务协议（CosyVoice3.0 的 apiv3，默认端口 8182）。只走两条：
#   GET  /health      探活
#   POST /synthesize  出音频：入 {text, mode:"cross_lingual"|"instruct", prompt_audio(base64),
#                                [instruct_text], speed, seed, stream:false}，出 {success, audio_base64, ...}
# 音频由接口返回 base64、由本模块落盘到指定目录；不读服务端的 saved_path，不扫本机磁盘找既有音频。
#
# 环境配置一律读用户级全局配置 ~/.workbuddy/fxai_global.json 的 tts 段，代码不写死地址。
# =====================================================================
import base64
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(os.path.dirname(_HERE), "fxai-common"),
           os.path.join(os.path.dirname(_HERE), "fxai-workflow")):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import fxai_common as fxc  # noqa: E402

fxc.allow_local()          # 本机服务必须绕过系统代理
_HTTPS = fxc.opener()

_GLOBAL_CFG_PATH = os.environ.get(
    "FXAI_GLOBAL_CONFIG", os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))

LOCAL_URL_DEFAULT = "http://127.0.0.1:8182"
_SPEAKER_FILE = os.path.join(_HERE, "speakers.json")

# 引擎名 → 默认本机端口（配置未给 engines 时按端口兜底）
ENGINE_PORTS = {"cosyvoice": 8182, "qwen3tts": 8086}
# 引擎别名 → 配置 engines 里的正式键名
ENGINE_ALIAS = {"cosy": "cosyvoice", "cosyvoice3": "cosyvoice", "cv3": "cosyvoice",
                "qwen": "qwen3tts", "qwen3": "qwen3tts", "qwen-tts": "qwen3tts"}

norm_url = fxc.norm_url

# prefer_local() 命中后，本进程内默认走本机（不影响全局配置文件）
_LOCAL_ON = {"on": False}
# 本地没起、机器上没有 torch 等依赖时，靠 ../fxai-workflow/xgy_guard.py 自动开机
_XGY_DIR = os.path.join(os.path.dirname(_HERE), "fxai-workflow")
if _XGY_DIR not in sys.path:
    sys.path.insert(0, _XGY_DIR)


# ---------- 配置 ----------
def global_cfg():
    """用户级全局部署配置（fxai_global.json）。"""
    try:
        with open(_GLOBAL_CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def tts_cfg():
    """全局配置里的 tts 段：`tts = {default_engine, engines, local_url, cloud_url, ...}`。"""
    c = global_cfg().get("tts")
    return c if isinstance(c, dict) else {}


def engines_cfg():
    """tts 段里的 engines 分节：`{cosyvoice: {local_url, cloud_url}, qwen3tts: {...}}`。"""
    e = tts_cfg().get("engines")
    return e if isinstance(e, dict) else {}


def engines_list():
    """已登记的引擎名（配置顺序）。"""
    return [k for k in engines_cfg() if isinstance(engines_cfg()[k], dict)]


def default_engine():
    """默认引擎名：tts.default_engine，缺省取 engines 第一个，登记为空时用 cosyvoice。"""
    name = norm_engine(tts_cfg().get("default_engine") or "")
    if name:
        return name
    lst = engines_list()
    return lst[0] if lst else "cosyvoice"


def norm_engine(name):
    """引擎名归一：去空格转小写、别名归到正式键名。"""
    n = str(name or "").strip().lower()
    return ENGINE_ALIAS.get(n, n)


def engine_cfg(engine=""):
    """某个引擎的地址配置：`{local_url, cloud_url}`。

    engine 传空 → 默认引擎。配置无该引擎 → 空 dict。
    """
    e = engines_cfg()
    key = norm_engine(engine) or default_engine()
    c = e.get(key)
    return c if isinstance(c, dict) else {}


def engine_default_mode(engine=""):
    """某引擎的默认合成模式：engines.<名>.default_mode → tts.default_mode → zero_shot。"""
    m = str(engine_cfg(engine).get("default_mode") or "").strip()
    if m:
        return m
    return str(tts_cfg().get("default_mode") or "zero_shot").strip() or "zero_shot"


def engine_speed_mode(engine=""):
    """语速由谁实现（`tts.engines.<名>.speed_mode`）：

      server（默认）= 服务端/模型管线**原生**实现，speed 原样透传，客户端不碰波形；
      local         = 服务端不做变速，客户端拿模型原生音频后用 ffmpeg 本地变速。

    依据：CosyVoice3 在 `CosyVoice3Model.token2wav()` 里做变速——对 flow 出来的 mel
    沿时间轴线性插值（`F.interpolate`）后再进 HiFT 声码器，属模型管线内部，故 server。
    千问 Qwen3-TTS 的 `generate_voice_clone` / `generate_custom_voice` **没有任何语速
    入口**，只能靠客户端本地变速，故 local。
    """
    m = str(engine_cfg(engine).get("speed_mode") or "").strip().lower()
    return m if m in ("server", "local") else "server"


def engine_url(engine="", local=True):
    """取某引擎的本机/线上地址；该引擎没登记就用引擎默认端口兜底。"""
    c = engine_cfg(engine)
    if local:
        v = norm_url(c.get("local_url") or "")
        if v:
            return v
        key = norm_engine(engine) or default_engine()
        return "http://127.0.0.1:%d" % ENGINE_PORTS.get(key, 8182)
    return norm_url(c.get("cloud_url") or "")


def local_url(engine=""):
    """本机地址。传 engine 取该引擎；不传取默认引擎。
    engines 未登记该引擎时回落顶层 tts.local_url（兼容旧配置）。
    """
    if engines_cfg():
        return engine_url(engine, local=True)
    return norm_url(tts_cfg().get("local_url") or LOCAL_URL_DEFAULT)


def cloud_url(engine=""):
    """线上地址。传 engine 取该引擎；不传取默认引擎。
    engines 未登记该引擎时回落顶层 tts.cloud_url（兼容旧配置）。
    """
    if engines_cfg():
        return engine_url(engine, local=False)
    return norm_url(tts_cfg().get("cloud_url") or "")


def tts_url(override="", engine=""):
    """TTS 地址取用顺序：**函数参数 > prefer_local 命中> 线上地址 > 本机地址**。

    engine 指定引擎（qwen3tts / cosyvoice）；不传取 tts.default_engine。
    同一条任务要打不同服务器时传 override；服务器切换只走这个参数。
    """
    if norm_url(override):
        return norm_url(override)
    if _LOCAL_ON["on"]:
        return local_url(engine)
    c = cloud_url(engine)
    return c or local_url(engine)


def prefer_local(timeout=2.0, quiet=False, engine=""):
    """探本机 TTS：探得通就把本进程默认地址切成本机。

    返回 True = 走本机；False = 本机没起，调用方照旧走线上（云端会按需自动开机）。
    每次合成都先调它（用户定调：能在本机跑的一律本机，本机 GPU 不计费）。
    """
    lu = local_url(engine)
    if not check(lu, timeout=timeout):
        if not quiet:
            print("[tts] 本机 TTS 未就绪，按配置走线上", flush=True)
        return False
    _LOCAL_ON["on"] = True
    if not quiet:
        print("[tts] 本机 TTS 已就绪，本次走本机", flush=True)
    return True


# ---------- 服务连通 ----------
def _get(url, timeout=8):
    try:
        with _HTTPS.open(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        raise RuntimeError("请求失败 %s：%s" % (url, e))


def check(url=None, timeout=4):
    """探活：`GET /health` 通了就算就绪。

    服务分两种：
      - 懒加载（响应带 model_loaded 字段）：进程活着即可，模型在首次合成时自动进显存（约 13 秒）。
      - 启动即加载（响应不带该字段）：必须 model_loaded 为真，否则按未就绪处理、等一会儿再试。
    """
    u = tts_url(url)
    try:
        raw = _get(u + "/health", timeout=timeout)
        d = json.loads(raw)
    except Exception:
        return False
    if d.get("status") != "healthy":
        return False
    if "model_loaded" in d:
        return True
    return bool(d.get("model_loaded"))


def _xgy():
    """xgy_guard 惰性导入：它要读 token，导入失败不该影响非云端调用。"""
    try:
        if _XGY_DIR not in sys.path:
            sys.path.insert(0, _XGY_DIR)
        import xgy_guard as x
        return x
    except Exception:
        return None


def is_cloud(url):
    """是不是仙宫云容器地址。只有这类地址才谈得上「自动拉起实例」。

    判定走 xgy_guard.is_xgpu，跟 comfy_client.require_comfyui 同一套判断，不另写域名规则。
    """
    x = _xgy()
    if not x:
        return False
    try:
        return bool(x.is_xgpu(url))
    except Exception:
        return x.XGPU_HOST in (url or "")


def status(url=None, timeout=6, engine=""):
    """服务与模型状态。

    返回 {url, online, model_loaded, sample_rate, idle_timeout, cloud, detail}。
    online=服务进程通不通；model_loaded=模型是否已进显存。
    懒加载服务刚起来时 model_loaded=False 属正常，首次合成会自动加载。
    """
    u = tts_url(url, engine) or local_url(engine)
    info = {"url": u, "online": False, "model_loaded": False, "sample_rate": None,
            "idle_timeout": None, "cloud": is_cloud(u), "detail": ""}
    try:
        d = json.loads(_get(u + "/health", timeout=timeout))
    except Exception as e:
        info["detail"] = "服务不可达：%s" % (str(e)[:120] or type(e).__name__)
        return info
    info["online"] = d.get("status") == "healthy"
    info["model_loaded"] = bool(d.get("model_loaded"))
    info["sample_rate"] = d.get("sample_rate")
    info["idle_timeout"] = d.get("idle_timeout")
    return info


def _xgy_instance_id():
    """全局配置里的仙宫云实例 ID（用于失败提示里告诉用户去开哪台）。"""
    return str(global_cfg().get("xgy_instance_id") or "").strip()


def ensure(url=None, allow_boot=True, wait=300, engine=""):
    """开工前保证 TTS 服务在线。顺序（用户定调）：**本机优先 → 云端兜底 → 都起不来提示手动启动**。

    engine  指定引擎（qwen3tts / cosyvoice）；不传取 tts.default_engine。

    1. 显式传了 url：只认这个地址（探通即用；不通按云端/本机规则处理）。
    2. 没传 url：先探该引擎的本机地址；本机通 → 用本机（本机 GPU 不计费）。
    3. 本机不通 → 云端地址（*.container.x-gpu.com）：探不通就自动拉起实例，等服务就绪后用云端。
    4. 云端也拉不起来（或没配云端地址）→ 抛错明确告知「本机与云端都没起来，请手动启动」。
    """
    # —— 显式 override：只认它 ——
    if url:
        u = tts_url(url)
        if check(u, timeout=4):
            return u
        return _boot_or_fail(u, allow_boot, wait)

    # —— 本机优先 ——
    lu = local_url(engine)
    if check(lu, timeout=4):
        _LOCAL_ON["on"] = True
        return lu

    # —— 云端兜底 ——
    cu = cloud_url()
    if cu:
        if check(cu, timeout=4):
            return cu
        if allow_boot and is_cloud(cu):
            return _boot_or_fail(cu, allow_boot, wait)

    # —— 两边都起不来：明确提示手动启动 ——
    iid = _xgy_instance_id()
    raise RuntimeError(
        "TTS 服务两边都没起来：本机 8182 没启动，云端实例也拉不起来。"
        "请手动启动其一：① 本机在「凤希本地 AI 控制台」把 CosyVoice 起在 8182"
        "（地址可在全局配置 tts.local_url 改）；② 云端在仙宫云控制台开机实例 %s，"
        "并确认全局配置 tts.cloud_url 已填。"
        % (iid or "（实例 ID 见仙宫云控制台）"))


def _boot_or_fail(u, allow_boot, wait):
    """云端实例自动开机 + 等服务就绪；成功返回地址，失败抛错（提示手动启动）。

    TTS 只认 8182 /health 就绪（与 ComfyUI 解耦）；懒加载服务 model_loaded=False 也算就绪，
    首次合成会自动进显存。
    """
    x = _xgy()
    if not x:
        raise RuntimeError(
            "TTS 云端地址（%s）探不通，本机也没起；云端自动开机模块缺失（xgy_guard 导入失败），"
            "请手动启动本机 CosyVoice（8182）或云端实例。" % u)
    try:
        # 只借 xgy 拉起云端实例；TTS 就绪只看 8182 /health
        ok, msg = x.ensure_service(u, lambda a: check(a, timeout=8), wait=wait, name="TTS")
        print("[tts] 云端实例：%s" % msg, flush=True)
    except Exception as e:
        ok, msg = False, "云端开机异常：%s" % e
        print("[tts] %s" % msg, flush=True)
    if ok:
        return u
    raise RuntimeError(
        "TTS 云端实例已尝试自动开机但仍未就绪（%s）。请去仙宫云控制台看开机状态与端口，"
        "或手动启动本机 CosyVoice（8182）。" % u)


# ---------- 音色库 ----------
def speakers():
    """音色列表 = 技能包 speakers.json ∪ 全局配置 tts.speakers（后者同名覆盖前者）。

    每项：{id, name, ref_audio, instruct_text, speed}
    ref_audio 是参考音色（输入素材，不是产物），读成 base64 发给接口。
    """
    out = []
    if os.path.isfile(_SPEAKER_FILE):
        try:
            with open(_SPEAKER_FILE, encoding="utf-8") as f:
                out.extend(json.load(f) or [])
        except Exception as e:
            print("[tts] speakers.json 读取失败（忽略）：%s" % e, flush=True)
    cfg_sp = tts_cfg().get("speakers") or []
    if isinstance(cfg_sp, list):
        out.extend(cfg_sp)
    elif isinstance(cfg_sp, dict):
        out.extend([dict(v, id=k) if isinstance(v, dict) else v for k, v in cfg_sp.items()])
    return out


def find_speaker(sid):
    for sp in speakers():
        if str(sp.get("id") or sp.get("name") or "") == str(sid):
            return sp
    raise RuntimeError("找不到音色「%s」（已登记：%s）；先看 `python voice_gen.py --list`"
                       % (sid, ", ".join(str(s.get("id")) for s in speakers()) or "无"))


# ---------- 合成 ----------
def _ffmpeg_bin():
    """ffmpeg 可执行文件：全局配置 software_install_dir 优先，其次 PATH。"""
    inst = str(global_cfg().get("software_install_dir") or "")
    if inst:
        for sub in ("ffmpeg", ""):
            for name in ("ffmpeg.exe", "ffmpeg"):
                p = os.path.join(inst, sub, name) if sub else os.path.join(inst, name)
                if os.path.isfile(p):
                    return p
    for name in ("ffmpeg", "ffmpeg.exe"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def _to_16k_mono(path):
    """不是 16kHz 单声道 PCM 就转一份，返回新文件路径；本来符合返回空串。

    ffmpeg 缺失或转换失败时返回空串，按原文件送（由服务端报错）。
    """
    try:
        info = _parse_wav(path)
    except Exception:
        return ""
    if info.get("sr") == 16000 and info.get("ch") == 1 and info.get("bits") == 16:
        return ""
    exe = _ffmpeg_bin()
    if not exe:
        return ""
    tag = "%dx%d_%dbit" % (info.get("ch", 0), info.get("sr", 0), info.get("bits", 0))
    key = hashlib.sha1(("%s|%s|%s" % (os.path.abspath(path), tag,
                                      os.stat(path).st_mtime_ns)).encode()).hexdigest()[:16]
    out = os.path.join(tempfile.gettempdir(), "fxai_tts_ref_%s.wav" % key)
    if os.path.isfile(out) and os.path.getsize(out) > 1024:
        return out
    try:
        subprocess.run([exe, "-y", "-loglevel", "error", "-i", path,
                        "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", out],
                       check=True, timeout=120)
    except Exception:
        return ""
    return out if os.path.isfile(out) and os.path.getsize(out) > 1024 else ""


def _parse_wav(path):
    """手工解析 RIFF 头，返回 {fmt, ch, sr, bits, data}。

    接口吐的是 IEEE float（fmt=3）wav，标准库 wave 只认 PCM（fmt=1），解析不了；
    这里连 fmt=3 一起读，拼接与采样率校验都能对上任意一路 wav。
    """
    with open(path, "rb") as f:
        blob = f.read()
    if blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise RuntimeError("不是 wav 文件：%s" % path)
    pos, fmt, data = 12, None, b""
    while pos + 8 <= len(blob):
        cid, sz = blob[pos:pos + 4], struct.unpack("<I", blob[pos + 4:pos + 8])[0]
        body = blob[pos + 8:pos + 8 + sz]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        pos += 8 + sz + (sz & 1)
    if fmt is None:
        raise RuntimeError("wav 缺 fmt 块：%s" % path)
    return {"fmt": fmt[0], "ch": fmt[1], "sr": fmt[2], "bits": fmt[5], "data": data}


def _write_wav(path, info):
    fmt_body = struct.pack("<HHIIHH", info["fmt"], info["ch"], info["sr"],
                           info["sr"] * info["ch"] * info["bits"] // 8,
                           info["ch"] * info["bits"] // 8, info["bits"])
    data = info["data"]
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_body)) + fmt_body
            + b"data" + struct.pack("<I", len(data)) + data)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)


def wav_seconds(path):
    """本地 wav 时长（秒）；读不出来返回 None。"""
    try:
        d = _parse_wav(path)
        return round(len(d["data"]) / float(d["sr"] * d["ch"] * d["bits"] // 8), 2)
    except Exception:
        return None


# ---------- 本地变速（只给 speed_mode=local 的引擎用） ----------
SPEED_MARK_SUFFIX = ".speed"


def applied_speed(path):
    """该音频当前实际生效的语速：有 `<path>.speed` 标记读标记，无标记 = 1.0（模型原生节奏）。"""
    try:
        with open(path + SPEED_MARK_SUFFIX, encoding="utf-8") as f:
            return float(json.load(f).get("speed", 1.0))
    except Exception:
        return 1.0


def _atempo_expr(speed):
    """ffmpeg atempo 表达式：单实例吃 0.5~2.0，超出范围按乘积串联多个。"""
    parts, s = [], float(speed)
    while s < 0.5 - 1e-9:
        parts.append(0.5)
        s /= 0.5
    while s > 2.0 + 1e-9:
        parts.append(2.0)
        s /= 2.0
    parts.append(s)
    return ",".join("atempo=%.6f" % p for p in parts)


def apply_speed_local(path, speed):
    """本地变速：ffmpeg `atempo`，**原地**改 `path`，并写 `<path>.speed` 标记。

    只给 `speed_mode=local` 的引擎用（服务端不做变速的那种）。
    ⛔ 本地变速一律用 atempo：librosa `time_stretch` 是波形域相位声码器，会在每个音节后留拖尾（回音）。
    `speed` 恰为 1.0 时不动文件、不写标记（= 模型原生节奏，零处理）。
    """
    speed = float(speed or 1.0)
    if abs(speed - 1.0) < 1e-3:
        return speed
    exe = _ffmpeg_bin()
    if not exe:
        raise RuntimeError("本地变速需要 ffmpeg：未找到（配全局 software_install_dir，或装进 PATH）")
    info = _parse_wav(path)
    codec = "pcm_f32le" if info.get("fmt") == 3 else "pcm_s16le"
    tmp = path + ".speed_tmp.wav"
    r = subprocess.run([exe, "-y", "-loglevel", "error", "-i", path,
                        "-filter:a", _atempo_expr(speed), "-c:a", codec,
                        "-ar", str(info.get("sr") or 24000), tmp],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 1024:
        if os.path.isfile(tmp):
            os.remove(tmp)
        raise RuntimeError("ffmpeg 本地变速失败（speed=%.3f）：%s"
                           % (speed, (r.stderr or "")[-300:]))
    shutil.move(tmp, path)
    with open(path + SPEED_MARK_SUFFIX, "w", encoding="utf-8") as f:
        json.dump({"speed": speed}, f)
    return speed


def data_dir():
    """软件数据目录（全局配置 software_data_dir）。客户装到哪就从哪读，代码里不写死本机盘符。"""
    return str(global_cfg().get("software_data_dir") or "").strip()


def resolve_ref_audio(path):
    """参考音频路径解析：绝对路径直接用；相对路径以**软件数据目录**为基准。

    音色库里只写相对路径（如 `voices/xiaoyun.wav`），技能换台机器 / 换个盘符照样能跑；
    全局配置的软件数据目录没填时才认相对当前目录的写法。
    """
    p = str(path or "").strip()
    if not p:
        raise RuntimeError("参考音频路径为空")
    if os.path.isabs(p) or os.path.isfile(p):
        return os.path.abspath(p)
    base = data_dir()
    if base:
        cand = os.path.join(base, p)
        if os.path.isfile(cand):
            print("[tts] 参考音频解析：%s -> %s" % (p, cand), flush=True)
            return os.path.abspath(cand)
        raise RuntimeError("参考音频不存在：%s（已按软件数据目录 %s 解析，文件应在 <软件数据目录>/%s）"
                           % (p, base, p))
    return os.path.abspath(p)


def encode_audio(path):
    """本机参考音频 → base64（接口的 prompt_audio 只收 base64 字符串）。

    统一转成 16kHz 单声道 WAV：服务端（CosyVoice3）要求 16k，
    高采样率或多声道的素材直接送会报卷积尺寸错误。
    """
    p = resolve_ref_audio(path)
    if not os.path.isfile(p):
        raise RuntimeError("参考音频不存在：%s" % p)
    try:
        sr = _parse_wav(p)["sr"]
    except Exception:
        sr = 0
    if sr and sr < 16000:
        raise RuntimeError("参考音频采样率 %dHz 低于接口要求的 16000Hz：%s" % (sr, p))
    conv = _to_16k_mono(p)
    if conv:
        with open(conv, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _busy_hint(u):
    """提交前探一眼有没有空位。

    实时排队提示走 WebSocket（_start_progress），但有些 Python 环境没装
    websockets 库，那时 WS 会静默跳过、用户就只能看到「卡住了」。这里补一条
    提交前的探测：副本全占满就先说一声，至少让人知道是在排队而不是卡死。
    """
    try:
        d = json.loads(_get(u.rstrip("/") + "/health", timeout=3))
    except Exception:
        return
    free = d.get("free")
    if isinstance(free, int) and free <= 0:
        print("[tts] 并发已满（%d 份副本都在合成），本次提交要排队，请稍候…"
              % int(d.get("workers") or 0), flush=True)


def _poll_task(base, tid, timeout):
    """轮询异步任务直到完成，返回结果 dict。"""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            d = json.loads(_get(base + "/task/" + tid, timeout=15))
        except Exception as e:
            raise RuntimeError("查询任务状态失败：%s" % e)
        st = d.get("state")
        if st != last:
            if st == "queued":
                print("[tts] 排队中：前方 %d 人（队列共 %d 个任务）"
                      % (d.get("ahead") or 0, d.get("queue_len") or 0), flush=True)
            elif st == "running":
                print("[tts] 轮到我了，开始合成（排队 %.1fs）"
                      % (d.get("waiting_seconds") or 0), flush=True)
            last = st
        if st == "done":
            try:
                return json.loads(_get(base + "/task/" + tid + "/result", timeout=120))
            except Exception as e:
                raise RuntimeError("取合成结果失败：%s" % e)
        if st == "error":
            raise RuntimeError("合成失败：%s" % (d.get("error") or "未知错误"))
        time.sleep(1)
    raise RuntimeError("等合成超时（%.0f 秒）：任务 %s 仍未完成，可在服务端 /task/%s 继续查"
                       % (timeout, tid, tid))


def _submit_task(base, payload, timeout):
    """异步任务模式：提交拿 task_id → 轮询 → 取结果。

    🔴 为什么不做同步长连接：排队期间 HTTP 响应一个字节都不发，nginx/Cloudflare
    会把这种静默连接当死连接掐掉（60s / 100s）。异步模式下每次请求都很短，
    排多久都不会断线，所以服务端敢把等副本的时间放宽到分钟级。

    旧版服务端没有 /synthesize/async → 返回 None，由调用方退回同步接口。
    """
    try:
        r = fxc.http_post(base + "/synthesize/async", payload, 60, _HTTPS)
    except RuntimeError as e:
        msg = str(e)
        if "404" in msg or "405" in msg:
            return None                      # 旧服务端，不支持异步任务
        if "503" in msg:
            # 队列也满了（FXAI_CV_QUEUE_MAX），等一轮再试
            print("[tts] 服务端排队人数过多，等待 8 秒后重试…", flush=True)
            time.sleep(8)
            r = fxc.http_post(base + "/synthesize/async", payload, 60, _HTTPS)
        else:
            raise
    tid = r.get("task_id")
    if not tid:
        return None
    ahead = r.get("ahead") or 0
    if ahead > 0:
        print("[tts] 已提交，排在队列里：前方 %d 人（任务 %s）" % (ahead, tid), flush=True)
    return _poll_task(base, tid, timeout)


def _post(url, payload, timeout, _retry=1):
    """POST 合成请求。

    优先走异步任务（排队也不断线）；服务端是旧版、没有这个端点时退回同步。
    同步模式下 N 个人正在合成时会拿到 503「资源不足…」，这里自动等一轮再试。
    """
    base = url.rsplit("/synthesize", 1)[0]
    try:
        res = _submit_task(base, payload, timeout)
        if res is not None:
            return res
    except RuntimeError as e:
        msg = str(e)
        if _retry > 0 and "资源不足" in msg:
            print("并发已满，等待 8 秒后自动重试一次…", flush=True)
            time.sleep(8)
            return _post(url, payload, timeout, _retry - 1)
        raise
    # 没有异步能力：退回同步长连接
    _busy_hint(base)
    try:
        return fxc.http_post(url, payload, timeout, _HTTPS)
    except RuntimeError as e:
        msg = str(e)
        if _retry > 0 and "503" in msg and "资源不足" in msg:
            print("并发已满，等待 8 秒后自动重试一次…", flush=True)
            time.sleep(8)
            return _post(url, payload, timeout, _retry - 1)
        raise


def _start_progress(u):
    """订阅服务端 /ws/progress，把模型加载/卸载进度打到标准输出。

    服务端没有这个端点、或本机缺 websockets 库时静默跳过，不影响合成。
    返回停止函数；不需要停止时返回 None。
    """
    try:
        import asyncio
        import ssl as _ssl
        import threading
        import websockets
    except Exception:
        return None

    base = u.rstrip("/").split("://", 1)[-1]
    ws_url = ("wss://" if u.startswith("https") else "ws://") + base + "/ws/progress"
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    stop = threading.Event()
    connected = threading.Event()

    async def run():
        try:
            async with websockets.connect(ws_url, ssl=ctx, open_timeout=6) as ws:
                connected.set()
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        return
                    try:
                        d = json.loads(raw)
                    except Exception:
                        continue
                    ev, st = d.get("event"), d.get("state")
                    if ev == "hello":
                        print("[tts] 模型%s" % ("已在显存" if d.get("model_loaded")
                                              else "未加载，首次合成会自动加载"), flush=True)
                    elif ev == "queue":
                        # 服务端副本全被占时推的排队提示。群发消息，所以只说「还有几个人在等」，
                        # 不断言是不是轮到自己了（别人拿到副本也会推同一条）。
                        if st == "waiting":
                            print("[tts] 并发已满（%d 份副本都在合成），排队中：前方 %d 人，"
                                  "最多等 %d 秒"
                                  % (int(d.get("workers") or 0), int(d.get("ahead") or 0),
                                     int(d.get("timeout") or 0)), flush=True)
                        elif st == "update":
                            left = int(d.get("waiting") or 0)
                            if left > 0:
                                print("[tts] 排队中：还有 %d 人在等" % left, flush=True)
                            else:
                                print("[tts] 队列已清空，副本腾出来了", flush=True)
                    elif st == "loading":
                        print("[tts] 模型正在加载中…", flush=True)
                    elif st == "ready":
                        print("[tts] 模型加载完毕（%.1fs）" % float(d.get("seconds") or 0), flush=True)
                    elif st == "unloading":
                        print("[tts] 正在卸载模型释放显存…", flush=True)
                    elif st == "unloaded":
                        print("[tts] 模型已释放显存", flush=True)
        except Exception:
            return
        finally:
            connected.set()

    threading.Thread(target=lambda: asyncio.run(run()), daemon=True).start()
    connected.wait(timeout=6)   # 等订阅建立完成再返回，保证合同时的加载事件已收齐
    return stop.set


def synthesize(text, ref_audio="", instruct_text="", speed=1.0, seed=None,
               url=None, timeout=None, out_path=None, mode=None, prompt_text="",
               speaker="", engine=""):
    """参考音色 → 一条口播音频（CosyVoice3 跨语种复刻 / 克隆）。

    text          要念的文本。cross_lingual 下语气标签与正文一起送：
                  `用…的语气说<|endofprompt|>。<正文>`（前缀 You are a helpful assistant. 由后端自动补，客户端只传这一段）
    ref_audio     参考音色（文件路径或已 base64 的字符串）。
                  **可以不给**：不给就只送 speaker，走服务端内置音色 + 情绪描述
                  （千问引擎支持这条；CosyVoice3 必须有参考音色，不给会由服务端报错）。
                  给了就走克隆，服务端会按规则忽略文本里的情绪描述。
    speaker       内置音色 id（千问 CustomVoice：vivian / serena / eric / dylan / uncle_fu …）。
                  CosyVoice3 不认这个字段，传了由服务端忽略。
    mode          合成模式，统一 cross_lingual（V3 跨语种复刻）：用参考音色复刻声音，
                  情感/情绪/语气/方言全靠 text 里的标签带；不走 zero_shot / instruct
    prompt_text   cross_lingual 不用（后端不读）
    instruct_text cross_lingual 不用（语气写在 text 里）
    speed         语速 0.5~2.0
    out_path      落盘路径
    返回 {ok, path, duration, sample_rate, mode, speed, url, text, speaker}
    """
    text = (text or "").strip()
    if not text:
        raise RuntimeError("口播文本为空")
    if not ref_audio and not speaker:
        raise RuntimeError("缺音色：给 --ref-audio / --speaker（参考音色走克隆），"
                           "或 --qwen-speaker（千问内置音色 + 情绪描述）")

    cfg = tts_cfg()
    # 模式：显式传入 > 该引擎的 default_mode > tts.default_mode
    # 参考音色克隆：CosyVoice3 走 zero_shot（cross_lingual 会报卷积错误）；千问走 cross_lingual
    mode = (mode or engine_default_mode(engine)).strip() or "zero_shot"
    if mode == "instruct":
        if not instruct_text:
            instruct_text = cfg.get("default_instruct_text") or ""
        if not instruct_text:
            raise RuntimeError("缺情感语气指令：给 --instruct-text，或填全局配置 tts.default_instruct_text")
        prompt_text = ""  # instruct 模式不传参考文本
    else:
        instruct_text = ""
        if not prompt_text:
            prompt_text = cfg.get("default_prompt_text") or ""
        if mode == "zero_shot" and not prompt_text:
            raise RuntimeError("缺参考文本：给 --prompt-text，或在音色库条目里配 prompt_text"
                               "（必须是参考音频的原文）")
    speed = float(speed if speed is not None else cfg.get("default_speed", 1.0))
    seed = seed if seed is not None else cfg.get("default_seed")
    timeout = int(timeout or cfg.get("timeout") or 300)

    # 语速由谁做，按引擎定（见 engine_speed_mode）：
    #   server → 模型管线原生实现，speed 原样透传给服务端
    #   local  → 服务端不做变速，只向它要模型原生节奏，拿到音频后本地 ffmpeg 变速
    smode = engine_speed_mode(engine)
    server_speed = 1.0 if smode == "local" else speed

    u = ensure(url, engine=engine)
    # 参考音色是可选的：不给就只送 speaker，由服务端走内置音色 + 情绪描述
    payload = {"text": text, "mode": mode, "speed": server_speed, "stream": False}
    if ref_audio:
        payload["prompt_audio"] = ref_audio if _is_b64(ref_audio) else encode_audio(ref_audio)
    if speaker:
        payload["speaker"] = speaker
    if mode == "instruct" and instruct_text:
        payload["instruct_text"] = instruct_text
    # 参考音色走克隆时，CosyVoice3 要求同时给参考音频文本（服务端用 <|endofprompt|> 分隔）
    if prompt_text:
        payload["prompt_text"] = prompt_text
    if seed:
        payload["seed"] = int(seed)

    print("[tts] 合成中：%d 字，模式 %s，语速 %.2f%s，音色 %s（%s）"
          % (len(text), mode, speed,
             "（客户端 ffmpeg 变速）" if smode == "local" and abs(speed - 1.0) >= 1e-3 else "",
             ("参考音色" if ref_audio else "内置 %s" % speaker), u), flush=True)
    stop_watch = _start_progress(u)
    try:
        res = _post(u.rstrip("/") + "/synthesize", payload, timeout)
    finally:
        if stop_watch:
            stop_watch()
    if not res.get("success"):
        raise RuntimeError("合成失败：%s" % res)
    # 两代服务字段名不同：apiv3 给 audio_base64，api.py 给 audio
    b64 = res.get("audio_base64") or res.get("audio") or ""
    if not b64:
        raise RuntimeError("接口没返回音频（audio_base64/audio 都为空）：%s" % res)
    data = base64.b64decode(b64)
    path = os.path.abspath(out_path or (os.environ.get("TTS_OUT") or _default_name(text)))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    dur = res.get("total_duration")
    if smode == "local" and abs(speed - 1.0) >= 1e-3:
        apply_speed_local(path, speed)      # 服务端不做变速 → 客户端 ffmpeg 本地做
        dur = wav_seconds(path) or dur
    meta = {"ok": True, "path": path, "duration": dur,
            "sample_rate": res.get("sample_rate"), "mode": mode,
            "speed": speed, "speed_mode": smode, "url": u, "text": text,
            "speaker": speaker or "",
            "route": res.get("route") or "", "engine": res.get("engine") or ""}
    print("[tts] ✓ 完成：%s（%.2fs，%.1f KB）"
          % (meta["path"], meta["duration"] or 0, len(data) / 1024.0), flush=True)
    return meta


def _is_b64(s):
    return isinstance(s, str) and len(s) > 200 and re.match(r"^[A-Za-z0-9+/=\s]+$", s)


def _default_name(text):
    safe = re.sub(r'[\\/*?:"<>|\s]+', "", text)[:12] or "口播"
    return safe + ".wav"


# ---------- 文本处理 / 落盘 / 拼接 ----------
def save_audio(data, path):
    """接口返回的 wav 字节 → 本机落盘。"""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def join_wavs(paths, out_path):
    """把多段 wav 拼成一条（保原采样格式，float 还是 float，不重采样）。

    各段采样率/声道/位深不一致时抛错，宁可让用户权衡也不静默产出错的时长。
    """
    if len(paths) == 1:
        return os.path.abspath(paths[0])
    infos, head = [], None
    for p in paths:
        d = _parse_wav(p)
        key = (d["fmt"], d["ch"], d["sr"], d["bits"])
        if head is None:
            head = key
        elif key != head:
            raise RuntimeError("分段音频格式不一致，无法拼接：%s 与 %s（%s vs %s）"
                               % (paths[0], p, head, key))
        infos.append(d)
    merged = dict(infos[0])
    merged["data"] = b"".join(d["data"] for d in infos)
    _write_wav(out_path, merged)
    return os.path.abspath(out_path)


def split_text(text, max_chars=80):
    """长文自动分句：换行是硬边界（各行独立成段，行内再按句末标点切），仍超长的句按逗号/顿号切，
    最后贪心合到 max_chars。默认 80 = 官方 split_paragraph 的 token_max_n（中文按字符数算）。

    接口一次念太长会衰减、断句生硬，所以口播长文默认分句后逐段合成再拼回一条；
    换行不丢（脚本/台本的一行就是一个镜头或一句停顿），别把段落边界吃掉。
    """
    text = re.sub(r"\r\n?", "\n", (text or "").strip())
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        out.extend(_merge_units(_split_sentences(line), max_chars))
    return out


def _split_sentences(s):
    """行内按句末标点切（保留标点在前一段末尾），用于分句前的预处理。"""
    return [u.strip() for u in re.split(r"(?<=[。！？!?；;\n])", s) if u.strip()]


def _merge_units(units, max_chars):
    out, buf = [], ""
    for u in units:
        if len(u) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            for sub in _split_by_comma(u, max_chars):
                out.append(sub)
            continue
        if buf and len(buf) + len(u) > max_chars:
            out.append(buf)
            buf = u
        else:
            buf = u if not buf else buf + u
    if buf:
        out.append(buf)
    return [x.strip() for x in out if x.strip()]


def _split_by_comma(s, max_chars):
    parts = [p.strip() for p in re.split(r"(?<=[，,、：:])", s) if p.strip()]
    out, buf = [], ""
    for p in parts:
        if len(p) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            out.append(p)
            continue
        if buf and len(buf) + len(p) > max_chars:
            out.append(buf)
            buf = p
        else:
            buf = p if not buf else buf + p
    if buf:
        out.append(buf)
    return out
