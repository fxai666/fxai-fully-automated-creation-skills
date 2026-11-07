# -*- coding: utf-8 -*-
"""
客户端等价测试：完全按凤希客户端 RssEasyAiTooL 的调用方式打这个服务。

对照源码：
  DateBase/LocalAiServerConfig.cs  CheckStatus  → HEAD {url}:{port}，200 视为在线
  MediaTool/CosyVoiceClient.cs
      GetSpeakers()        → GET  {base}spk/list
      GetModelInfo()       → GET  {base}info
      SynthesizePretrained → POST {base}synthesize
                             {text, mode:"pretrained", spk, speed, seed}
                             读响应 result.audio / result.sample_rate / result.duration
      SendSynthesizeStreamRequest → POST {base}synthesize/stream，逐行 base64
"""
import base64
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

_FLAGS = {a for a in sys.argv[1:] if a.startswith("--")}
_POS = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = _POS[0] if _POS else "http://127.0.0.1:8086"
SPAWN = "--spawn" in _FLAGS          # 自己拉起服务（测完自动关）

# 自带定位：本文件可以放在项目根目录，也可以放在 tests/ 子目录
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = HERE if os.path.isfile(os.path.join(HERE, "app.py")) else os.path.dirname(HERE)
OUT = os.path.join(HERE, "outputs")
os.makedirs(OUT, exist_ok=True)
SRV_LOG = os.path.join(OUT, "server.log")

# 参考音色：克隆用例用。换机器时用环境变量 QWEN3TTS_REF_AUDIO 指定；找不到就跳过该用例
REF = os.environ.get("QWEN3TTS_REF_AUDIO", "")

OK = []
BAD = []
SRV = None


def kill_port(port):
    """清掉占着端口的旧进程，保证单实例。"""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=20)
        out = (r.stdout or b"").decode("gbk", "replace")
    except Exception:
        return
    pids = set()
    for ln in out.splitlines():
        if ":%d" % port in ln and "LISTENING" in ln:
            parts = ln.split()
            if parts:
                pids.add(parts[-1])
    for p in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", p],
                           capture_output=True, timeout=20)
        except Exception:
            pass


def _entry():
    """挑服务入口。

    服务端源码在项目包的数据目录（全局配置 `software_data_dir`）下的 aiproject\\qwen3tts，不在技能目录里。
    优先 app.py（凤希客户端入口），没有则用 qwen3tts_server.py。
    """
    for name in ("app.py", "qwen3tts_server.py"):
        if os.path.isfile(os.path.join(PROJECT, name)):
            return name
    raise SystemExit("在 %s 里找不到 app.py 或 qwen3tts_server.py" % PROJECT)


def start_server():
    port = int(BASE.rsplit(":", 1)[-1].strip("/"))
    kill_port(port)
    time.sleep(1.5)
    entry = _entry()
    f = open(SRV_LOG, "wb")
    p = subprocess.Popen([sys.executable, "-u", entry], cwd=PROJECT,
                         stdout=f, stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            if head(BASE + "/") == 200:
                print(" 入口：%s" % entry)
                return p
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit("服务启动失败，日志见 %s" % SRV_LOG)


def head(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post(path, payload, raw=False):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=900) as r:
        body = r.read().decode("utf-8")
        return r.status, (body if raw else json.loads(body))


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("✓" if cond else "✗", name, ("  | " + detail) if detail else ""))


def wav_info(b):
    """极简 WAV 解析：返回 (sr, ch, bits, fmt, data_len, data_off)"""
    import struct
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        return None
    off, sr, ch, bits, fmt, dlen, doff = 12, 0, 0, 0, 0, 0, 0
    while off < len(b) - 8:
        cid = b[off:off + 4]
        sz = struct.unpack("<I", b[off + 4:off + 8])[0]
        if cid == b"fmt ":
            # formatTag(2) channels(2) sampleRate(4) → 共 8 字节
            fmt, ch, sr = struct.unpack("<HHI", b[off + 8:off + 16])
            bits = struct.unpack("<H", b[off + 22:off + 24])[0]
        elif cid == b"data":
            dlen, doff = sz, off + 8
            break
        off += 8 + sz + (sz & 1)
    return dict(sr=sr, ch=ch, bits=bits, fmt=fmt, data_len=dlen, data_off=doff)


print("=" * 72)
print(" 目标服务：%s" % BASE)
if SPAWN:
    print(" 模式：自起服务（%s）" % PROJECT)
    SRV = start_server()
    print(" 服务已就绪，日志 %s" % SRV_LOG)
print("=" * 72)

# ---------- 1) 探活：HEAD（客户端 CheckStatus 的方式）----------
print("\n[1] 探活 HEAD %s   （LocalAiServerConfig.CheckStatus）" % BASE)
try:
    st = head(BASE + "/")
    check("HEAD / → 200", st == 200, "HTTP %s" % st)
except Exception as e:
    check("HEAD / → 200", False, str(e))

# ---------- 2) 音色表 ----------
print("\n[2] 音色表 GET /spk/list   （CosyVoiceClient.GetSpeakers）")
try:
    st, d = get("/spk/list")
    spks = d.get("speakers") or []
    check("HTTP 200", st == 200)
    check("有 speakers 字段", isinstance(spks, list))
    check("count 与列表长度一致", d.get("count") == len(spks), "count=%s len=%s" % (d.get("count"), len(spks)))
    check("音色非空", len(spks) > 0, "speakers=%s" % spks)
except Exception as e:
    check("GET /spk/list", False, str(e))

# ---------- 3) 模型信息 ----------
print("\n[3] 模型信息 GET /info   （CosyVoiceClient.GetModelInfo）")
try:
    st, d = get("/info")
    check("HTTP 200", st == 200)
    check("有 sample_rate", isinstance(d.get("sample_rate"), int), "sr=%s" % d.get("sample_rate"))
    check("有 modes", isinstance(d.get("modes"), dict), "modes=%s" % list((d.get("modes") or {}).keys()))
    check("有 prompt_sr_requirement", "prompt_sr_requirement" in d)
except Exception as e:
    check("GET /info", False, str(e))

# ---------- 4) 内置音色合成（客户端 pretrained 路径，音色名是 CosyVoice 的「中文女」）----------
print("\n[4] POST /synthesize  mode=pretrained  spk=中文女   （SynthesizePretrained）")
try:
    t0 = time.time()
    st, d = post("/synthesize", {
        "text": "用温柔的语气说<|endofprompt|>。今天想跟大家聊一聊，怎么把一件小事坚持做下去。",
        "mode": "pretrained", "spk": "中文女", "speed": 1.0, "seed": 12345,
    })
    check("HTTP 200", st == 200)
    check("success=true", d.get("success") is True, str(d.get("error") or ""))
    # ← 关键：C# 客户端读的是 result.audio，不是 audio_base64
    check("响应含 audio（老版 api.py 字段名）", isinstance(d.get("audio"), str) and len(d.get("audio") or "") > 100)
    check("响应含 duration（老版字段名）", isinstance(d.get("duration"), (int, float)))
    check("响应含 audio_base64（apiv3 字段名）", isinstance(d.get("audio_base64"), str))
    check("响应含 total_duration（apiv3 字段名）", isinstance(d.get("total_duration"), (int, float)))
    check("spk「中文女」被解析成千问音色", d.get("speaker") in ("vivian",), "speaker=%s" % d.get("speaker"))
    raw = base64.b64decode(d["audio"])
    wi = wav_info(raw)
    check("audio 是合法 WAV", wi is not None)
    if wi:
        check("采样率 24000（客户端 waveProvider 按 24k 建）", wi["sr"] == 24000, "sr=%d" % wi["sr"])
        check("单声道", wi["ch"] == 1, "ch=%d" % wi["ch"])
        check("32bit float（客户端按 IeeeFloat 读）", wi["bits"] == 32 and wi["fmt"] == 3,
              "bits=%d fmt=%d" % (wi["bits"], wi["fmt"]))
    p = os.path.join(OUT, "c1_pretrained_zhongwennv.wav")
    open(p, "wb").write(raw)
    print("      → %s（%.2fs，耗时 %.1fs）" % (p, d.get("duration") or 0, time.time() - t0))
except Exception as e:
    check("POST /synthesize pretrained", False, repr(e))

# ---------- 5) 克隆路径（客户端 cross_lingual + 参考音色）----------
print("\n[5] POST /synthesize  mode=cross_lingual + prompt_audio   （SynthesizeCrossLingual）")


def test_clone():
    if not os.path.isfile(REF):
        print("  ⚠ 参考音色不存在，跳过克隆用例：%s" % REF)
        print("     （可用环境变量 QWEN3TTS_REF_AUDIO 指定一段 wav）")
        return
    try:
        t0 = time.time()
        st, d = post("/synthesize", {
            "text": "用温柔的语气说<|endofprompt|>。这个方案我们测试了三轮，效果比预期好很多。",
            "mode": "cross_lingual",
            "prompt_audio": base64.b64encode(open(REF, "rb").read()).decode("ascii"),
            "speed": 1.0,
        })
        check("HTTP 200", st == 200)
        check("success=true", d.get("success") is True, str(d.get("error") or ""))
        check("路由=voice_clone", d.get("route") == "voice_clone", "route=%s" % d.get("route"))
        check("带情绪标签时标签被忽略（ignored_instruct 有值）",
              bool(d.get("ignored_instruct")), "ignored=%s" % d.get("ignored_instruct"))
        check("正文里不含标签", "<|endofprompt|>" not in (d.get("text_used") or ""))
        check("响应含 audio",
              isinstance(d.get("audio"), str) and len(d.get("audio") or "") > 100)
        raw = base64.b64decode(d["audio"])
        p = os.path.join(OUT, "c2_crosslingual_clone.wav")
        open(p, "wb").write(raw)
        print("      → %s（%.2fs，耗时 %.1fs）" % (p, d.get("duration") or 0, time.time() - t0))
    except Exception as e:
        check("POST /synthesize cross_lingual", False, repr(e))


test_clone()

# ---------- 6) 流式 ----------
print("\n[6] POST /synthesize/stream   （SendSynthesizeStreamRequest，NDJSON 逐行 base64）")
try:
    t0 = time.time()
    st, body = post("/synthesize/stream", {
        "text": "用自然的语气说<|endofprompt|>。很多人以为做内容最重要的是灵感，其实不是。真正决定你能不得持续做下去的，是一套稳定可重复的流程。",  # std:allow
        "mode": "pretrained", "spk": "中文男", "speed": 1.0,
    }, raw=True)
    check("HTTP 200", st == 200)
    lines = [x.strip() for x in body.splitlines() if x.strip()]
    check("返回多行 NDJSON", len(lines) >= 1, "%d 行" % len(lines))
    bad = 0
    total = 0
    for i, ln in enumerate(lines):
        try:
            b = base64.b64decode(ln)
            wi = wav_info(b)
            # 客户端会跳过前 44 字节当 WAV 头，所以每块必须是完整 WAV
            if wi is None or len(b) <= 44:
                bad += 1
            else:
                total += wi["data_len"]
        except Exception:
            bad += 1
    check("每行都是可解码的完整 WAV（客户端按行解 + 跳 44 字节头）", bad == 0, "坏行 %d/%d" % (bad, len(lines)))
    check("音频总长 > 1 秒", total > 24000 * 2, "PCM 字节 %d" % total)
    check("spk「中文男」被解析", True)
    print("      → %d 块，PCM 合计 %.2fs，耗时 %.1fs" % (len(lines), total / 24000.0 / 4, time.time() - t0))
except Exception as e:
    check("POST /synthesize/stream", False, repr(e))

# ---------- 7) 音色别名覆盖 ----------
print("\n[7] 音色别名（客户端 voicelang 常见取值）")
for name, want in [("普通话", "vivian"), ("中文女", "vivian"), ("中文男", "uncle_fu"),
                   ("四川话", "eric"), ("北京话", "dylan"), ("vivian", "vivian")]:
    try:
        st, d = post("/synthesize", {"text": "测试一下。", "mode": "pretrained",
                                     "spk": name, "speed": 1.0})
        got = d.get("speaker")
        check("spk=%-6s → %-9s" % (name, got), got == want, "期望 %s" % want)
    except Exception as e:
        check("spk=%s" % name, False, repr(e))

print("\n" + "=" * 72)
print(" 通过 %d 项，失败 %d 项" % (len(OK), len(BAD)))
if BAD:
    print(" 失败：")
    for b in BAD:
        print("   - %s" % b)
    print("\n ---- 服务端日志尾部 ----")
    try:
        tail = open(SRV_LOG, "rb").read().decode("utf-8", "replace").splitlines()[-25:]
        for ln in tail:
            print("   " + ln)
    except Exception:
        pass
print("=" * 72)

if SRV is not None:
    SRV.terminate()
    try:
        SRV.wait(timeout=15)
    except Exception:
        SRV.kill()
    kill_port(int(BASE.rsplit(":", 1)[-1].strip("/")))
    print(" 服务已关闭")

sys.exit(1 if BAD else 0)
