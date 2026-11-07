# -*- coding: utf-8 -*-
"""sv_bgm —— 幻灯片/口播片的**背景配乐自动生成**（`--bgm auto` 的执行点）。

产物取法完全走公共件 `fxai-workflow/comfy_client.py`（与生图/生乐技能一致）：
  占位符工作流 → POST /prompt → 轮询 `/fxai/tasks/result` 拿 url 令牌 → GET `/fxai/down/file` 下载。
**不读 ComfyUI 本地 output 目录**（服务端可能不在本机，本机路径不可靠）。

音乐规格：**默认纯器乐**（铺垫不抢口播），也可按内容要带唱——带不带由内容定，不做强制工序。
  默认 `minimax3`（`music-minimax3-api.json`，MiniMax Music 3，输出 32kHz 立体声）。
  备选 `yue2`（`music-yue2-api.json`）。

两档人声：
  `--vocal none`（默认）= 纯器乐。Caption 的 Vocal Details 明写「纯器乐、无人声」+ lyrics `[Instrumental]`，
     M3 不写这句人声会漂，写了才稳 —— 这是**提示词层面**的保证，无需再靠转写抽检。
  `--vocal "轻柔女声哼唱"` = 带唱。Vocal Details 换成这句描述，须同时给 `--lyrics` / `--lyrics-file`
     （有词的唱才有意义；没给歌词直接报错，不静默退化成器乐）。

用法（独立跑，或由 sv_media / sv_pipeline 的 `--bgm auto` 调）：
  python sv_bgm.py --out-dir <项目>/配乐 --duration 60
  python sv_bgm.py --out-dir <项目>/配乐 --duration 90 --style "温暖钢琴，慢速，克制"
  python sv_bgm.py --out-dir <项目>/配乐 --vocal "低音男声轻唱" --lyrics-file 配乐/歌词.txt

输出：stdout 打一行 `RESULT {json}`（wav / remote / model / duration / sample_rate / vocal）。
"""
import argparse
import json
import pathlib
import sys
import time
import wave

_HERE = pathlib.Path(__file__).resolve().parent
_SKILLS = _HERE.parent
_WF = _SKILLS / "fxai-workflow"

# 默认 caption 按 MiniMax Music 3 的三段式写（Global / Vocal / Arrangement）。
# 🔴 纯器乐时 Vocal Details 必须明写「纯器乐」——M3 不写人声就会漂，写了才稳。
_CAP_GLOBAL = (
    "Global Metadata: bpm 92, 明亮克制的现代电子流行 / corporate pop。情绪上进但不张扬，"
    "全程铺底，中频留空不与人声抢位。场景：效率工具、创作工作台、城市通勤。"
    "混音干净，低频收敛，动态不大。\n"
)
_CAP_VOCAL_NONE = "Vocal Details: 纯器乐，全程无人声演唱，无和声、无呼吸声、无念白。\n"
_CAP_ARRANGE = (
    "Arrangement: 钢琴与合成器琶音从第一小节贯穿全曲；轻鼓点与贝斯在主段进入；弦乐长音铺底。"
    "每八小节轻微起伏一次，结尾自然收束不硬切。"
)
DEFAULT_CAPTION = _CAP_GLOBAL + _CAP_VOCAL_NONE + _CAP_ARRANGE

DEFAULT_YUE2_STYLE = (
    "纯器乐，无人声演唱，现代氛围电子，92 BPM，钢琴与合成器琶音铺底，轻鼓点，克制不抢人声"
)

INSTRUMENTAL_LYRICS = "[Instrumental]"

# 只给了歌词、没写人声描述时的兜底人声（气声轻唱、靠后，最不容易抢口播）
DEFAULT_VOCAL = "轻柔人声轻唱，气声，人声靠后，不抢正面口播"

MAX_DURATION = {"minimax3": 300}     # 模型上限（yue2 工作流把 max_duration 锁在 360，无需传）

# 带唱配乐默认音量更低：有词会和口播抢注意力，垫底要更靠后
VOL_INSTRUMENTAL = 0.20
VOL_VOCAL = 0.15


def is_vocal(vocal):
    """`--vocal` 是否要求带唱（'none' / 空 / 未给 = 纯器乐）。"""
    v = (vocal or "").strip()
    return bool(v) and v.lower() not in ("none", "off", "no", "instrumental", "纯器乐", "无人声")


def _cc():
    """延迟导入公共件（只在真要生成时才碰 ComfyUI 依赖）。"""
    if not (_WF / "comfy_client.py").is_file():
        raise SystemExit("找不到公共件 fxai-workflow（应与本技能同级）：%s" % _WF)
    p = str(_WF)
    if p not in sys.path:
        sys.path.insert(0, p)
    import comfy_client
    return comfy_client


def _probe(path):
    """返回 (秒, 采样率, 声道, 位深)；读不出来抛错。"""
    with wave.open(str(path), "rb") as w:
        rate, ch, bits = w.getframerate(), w.getnchannels(), w.getsampwidth() * 8
        secs = w.getnframes() / float(rate or 1)
    return round(secs, 2), rate, ch, bits


def build_caption(model="minimax3", style=None, vocal=None):
    """按「纯器乐 / 带唱」两档拼提示词。纯器乐一律显式写无人声（M3 不写会漂出唱）。"""
    model = (model or "minimax3").strip().lower()
    vocal_txt = (vocal or "").strip()
    if is_vocal(vocal_txt):
        base = style or (
            "bpm 88，温暖克制的现代流行 / 城市民谣。情绪松弛、有呼吸感，"
            "人声靠后、不抢正面口播。混音干净，低频收敛，动态不大。"
        )
        if "Vocal Details" in base:
            return base
        if model == "minimax3":
            return ("Global Metadata: " + base + "\n"
                    "Vocal Details: " + vocal_txt + "\n" + _CAP_ARRANGE)
        return base + "；" + vocal_txt + "；人声靠后不抢口播"
    if style and "Vocal Details" in style:
        return style
    if style:
        return ((_CAP_GLOBAL + "风格倾向：" + style + "。\n" + _CAP_VOCAL_NONE + _CAP_ARRANGE)
                if model == "minimax3"
                else (style + "；纯器乐，无人声演唱，克制不抢人声"))
    return DEFAULT_CAPTION if model == "minimax3" else DEFAULT_YUE2_STYLE


def gen_bgm(out_dir, duration=60, style=None, model="minimax3", seed=0,
            name=None, timeout=5400, vocal=None, lyrics=None):
    """生成一首配乐并下载到 out_dir，返回结果 dict（wav 为绝对路径）。

    vocal：None / "none" = 纯器乐（默认）；给描述文本（如「轻柔女声哼唱」）= 带唱，须配 lyrics。
    lyrics：歌词文本；纯器乐时为 `[Instrumental]`。带唱却没给歌词 → 直接报错。
    duration 只是「请求上限」，模型可提前收尾；短于成片由混音端 loop 补足。
    """
    cc = _cc()
    model = (model or "minimax3").strip().lower()
    if model not in ("minimax3", "yue2"):
        raise SystemExit("--bgm-model 只支持 minimax3 / yue2，收到：%s" % model)

    vocal_txt = (vocal or "").strip()
    lyrics_txt = (lyrics or "").strip()
    has_lyrics = bool(lyrics_txt) and lyrics_txt != INSTRUMENTAL_LYRICS
    # 给了人声描述、或给了歌词，都按带唱处理（只给歌词没写人声 → 用默认描述，不静默忽略歌词）
    want_vocal = is_vocal(vocal_txt) or has_lyrics
    if want_vocal:
        if not has_lyrics:
            raise SystemExit(
                "带唱配乐必须给歌词：--bgm-lyrics \"…\" 或 --bgm-lyrics-file 配乐/歌词.txt\n"
                "  只要背景铺陈就别带唱（默认 --bgm-vocal none 即纯器乐）。"
            )
        if not is_vocal(vocal_txt):
            vocal_txt = DEFAULT_VOCAL
    else:
        lyrics_txt = INSTRUMENTAL_LYRICS

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = name or ("bgm_%s" % time.strftime("%Y%m%d_%H%M%S"))
    wf = _WF / ("music-minimax3-api.json" if model == "minimax3" else "music-yue2-api.json")
    if not wf.is_file():
        raise SystemExit("找不到音乐工作流：%s（fxai-workflow 是否完整分发？）" % wf)

    caption = build_caption(model, style, vocal_txt)

    # caption：纯器乐走内置三段式；带唱把 Vocal Details 换成用户给的人声描述
    params = {"fxai_prompt": caption, "fxai_lyrics": lyrics_txt,
              "fxai_seed": int(seed or 0)}
    cap = MAX_DURATION.get(model)
    want = int(duration or 0) or 60
    if cap:
        params["fxai_music_duration"] = int(max(15, min(want, cap)))

    cc.require_comfyui()
    print("[bgm] 提交配乐任务（%s，请求 %ss，%s）…"
          % (model, want, ("带唱：" + vocal_txt) if want_vocal else "纯器乐"), flush=True)
    url = cc.submit_workflow(str(wf), params, tag="bgm:" + tag, kind="music", timeout=timeout)
    dst = (out_dir / (tag + ".wav")).resolve()
    cc._download(url, str(dst))
    secs, rate, ch, bits = _probe(dst)
    remote = cc.url_to_rel(url)
    print("[bgm] DONE %s（%.1fs，%dHz/%dch/%dbit，令牌 %s）" % (dst, secs, rate, ch, bits, remote),
          flush=True)
    if secs + 0.5 < want * 0.8:
        print("[bgm][warn] 生成时长 %.1fs 明显短于请求 %ss —— 混音端会循环补足；"
              "想更长可换 --bgm-model yue2 或拆两段" % (secs, want), flush=True)
    return {"ok": True, "wav": str(dst), "remote": remote, "model": model,
            "duration": secs, "sample_rate": rate, "channels": ch, "bits": bits,
            "requested": want, "vocal": (vocal_txt if want_vocal else "none"),
            "lyrics": ("自定义 %d 字" % len(lyrics_txt)) if want_vocal else INSTRUMENTAL_LYRICS,
            "style": caption[:120]}


def ensure(out_dir, duration=60, style=None, model="minimax3", **kw):
    """给 sv_media / sv_pipeline 用的薄封装：失败时抛 SystemExit 并给中文提示。"""
    try:
        return gen_bgm(out_dir, duration=duration, style=style, model=model, **kw)
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(
            "配乐自动生成失败：%s\n"
            "  ① ComfyUI 是否在跑（%s）；② 模型 minimax3 是否已装（见 fxai-mv-pipeline 09 册）；\n"
            "  ③ 实在不行先手动放一首到 配乐/，改用 --bgm 配乐/xxx.wav。" % (e, _WF)
        )


def main():
    ap = argparse.ArgumentParser(
        description="幻灯片配乐自动生成（默认纯器乐，可按内容改为带唱；走 ComfyUI 接口取产物）")
    ap.add_argument("--out-dir", required=True, help="保存目录（一般 <项目>/配乐）")
    ap.add_argument("--duration", type=int, default=60, help="请求时长秒（上限 300，短了混音端会 loop）")
    ap.add_argument("--style", help="风格/气质描述（默认内置 caption；纯器乐时自动补「无人声」要求）")
    ap.add_argument("--vocal", default="none",
                    help="人声：none（默认，纯器乐）或描述如「轻柔女声哼唱」（须配 --lyrics）")
    ap.add_argument("--lyrics", help="带唱时的歌词文本")
    ap.add_argument("--lyrics-file", dest="lyrics_file", help="带唱时从文件读歌词（.txt，UTF-8）")
    ap.add_argument("--model", default="minimax3", choices=["minimax3", "yue2"])
    ap.add_argument("--seed", type=int, default=0, help="0 = 随机")
    ap.add_argument("--name", help="文件名前缀（默认 bgm_时间戳）")
    ap.add_argument("--timeout", type=int, default=5400, help="等待出结果的最长秒数")
    a = ap.parse_args()

    lyrics = a.lyrics
    if a.lyrics_file:
        lf = pathlib.Path(a.lyrics_file)
        if not lf.is_file():
            raise SystemExit("找不到歌词文件：%s" % a.lyrics_file)
        lyrics = lf.read_text(encoding="utf-8").strip()
    r = ensure(a.out_dir, duration=a.duration, style=a.style, model=a.model,
               seed=a.seed, name=a.name, timeout=a.timeout,
               vocal=a.vocal, lyrics=lyrics)
    print("RESULT " + json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
