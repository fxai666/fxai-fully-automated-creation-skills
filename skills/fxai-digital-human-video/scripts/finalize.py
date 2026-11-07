#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S4 · 标题卡（可选）+ 信息贴片（可选）+ 写实封面（可选）+ 拼接 → 成片_最终_{W}x{H}.mp4

──── 为什么有这个脚本 ────
`dh_pipeline.py` / `dh_video.py` 只跑到「合并 + 烧字幕」，产出 成片_{W}x{H}.mp4。
那是**中间成片**：没有标题卡、没有封面。`_beat_times.json` 由本脚本从逐段音频时长算。
本脚本把「节拍时间码 → 标题卡 → 信息贴片 → 封面 → 拼接」串成一条命令，作为固定执行标准 S4。

──── 三层叠加（互不影响，各自可选）────
  · 字幕（底部，跟语音逐句）       —— 烧录步骤（S3）已完成，非本脚本职责
  · 标题卡（顶部，跟节拍）         —— 独立技能 fxai-title-card；image/cards.json 存在才叠
  · 信息贴片（任意位置，跟节奏）   —— 独立技能 fxai-infocard；image/infocards.json 存在才叠
    半透明形状（气泡/胶囊/小方块/方块卡片）的**辅助性补充内容**，内容另外写，
    不是从口播文案提炼的；带淡入/滑入/弹入 + 停留 + 淡出。
本脚本只在对应 json 存在时才叠；没有就跳过。

──── 三层的时间基准 ────
标题卡与信息贴片的 `t` 都按**口播起点**算（不含封面）；封面在本脚本最后拼，
拼完所有图层统一后移封面段时长，音画字三层仍然同步。

想强制不叠：
  --no-titles     不叠顶部标题卡
  --no-infocards  不叠信息贴片
  --no-cover      不拼封面

用法：
  python scripts/finalize.py --project <项目>
  python scripts/finalize.py --project <项目> --no-titles          # 不叠顶部字
  python scripts/finalize.py --project <项目> --no-infocards       # 不叠信息贴片
  python scripts/finalize.py --project <项目> --no-cover           # 只叠字不拼封面
  python scripts/finalize.py --project <项目> --cover-bg image/scene_01.png --force

前置（缺一即报错，不猜）：
  · <项目>/成片_{W}x{H}.mp4       中间成片（S3 产物）
  · <项目>/image/cards.json       标题卡文字（可选，一节拍一条，条数 = 音频段数）
  · <项目>/image/infocards.json   信息贴片（可选，每条 = 补充文字+形状+时间+位置）
  · <项目>/image/cover_bg.png     封面背景（没有则自动取 image/scene_01.png）
  · 逐段音频                       <项目>/music/*.wav 或项目根 *.wav
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_cover        # noqa: E402
import overlay_titles   # noqa: E402

SKIP_AUDIO = {"口播音频.wav"}


def bind_cardkit(scripts_dir):
    """把指定技能目录下的 cardkit.py 绑定为 `cardkit`。

    fxai-title-card 与 fxai-infocard 各带一份 cardkit.py，模块名相同、接口不同。
    sys.path 只决定先后，`import cardkit` 会命中先加载的那份并被缓存；
    切换技能前调用本函数，按目录显式重载，后续 `import cardkit` 即命中本次绑定。
    """
    import importlib.util
    p = os.path.join(scripts_dir, "cardkit.py")
    if not os.path.isfile(p):
        return
    spec = importlib.util.spec_from_file_location("cardkit", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cardkit"] = mod
    spec.loader.exec_module(mod)


def natkey(name):
    """自然排序：dh-vo2 < dh-vo10"""
    import re
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", os.path.basename(name))]


def find_segments(project):
    """找逐段音频：music/ 优先，其次项目根；排除合并音频与 _ 前缀临时件。"""
    cands = []
    for d in (os.path.join(project, "music"), project):
        if not os.path.isdir(d):
            continue
        got = [os.path.join(d, f) for f in os.listdir(d)
               if f.lower().endswith(".wav")
               and f not in SKIP_AUDIO
               and not f.startswith("_")]
        if got:
            cands = got
            break
    if not cands:
        sys.exit("找不到逐段音频（<项目>/music/*.wav 或项目根 *.wav）；"
                 "算不出节拍时间码，标题卡无法定位")
    return sorted(cands, key=natkey)


def audio_dur(path):
    """秒。优先 wave（PCM 直读），失败回落 ffprobe。"""
    try:
        import wave
        with wave.open(path, "rb") as w:
            fr = w.getframerate()
            if fr:
                return w.getnframes() / float(fr)
    except Exception:
        pass
    ff = overlay_titles.ffmpeg_bin()
    probe = os.path.join(os.path.dirname(ff), "ffprobe.exe")
    if not os.path.isfile(probe):
        probe = "ffprobe"
    import subprocess
    p = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except Exception:
        sys.exit("读不出音频时长：%s" % path)


def build_beats(project, segs, keep=False):
    """算 _beat_times.json。已有且 --keep-beats 时直接用。"""
    out = os.path.join(project, "_beat_times.json")
    if keep and os.path.isfile(out):
        print("[beat] 复用已有 %s" % out)
        return out
    beats, t = [], 0.0
    for i, p in enumerate(segs, 1):
        d = audio_dur(p)
        beats.append({"i": i, "start": round(t, 3), "end": round(t + d, 3),
                      "dur": round(d, 3), "audio": os.path.basename(p)})
        t += d
    json.dump({"total": round(t, 3), "beats": beats},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[beat] %d 个节拍，总时长 %.2fs -> %s" % (len(beats), t, out))
    return out


def pick_cover_bg(project, given):
    """封面背景：显式指定 > image/cover_bg.png > 第一张场景图 > 兜底渐变（None）。"""
    img = os.path.join(project, "image")
    if given:
        p = given if os.path.isabs(given) else os.path.join(project, given)
        if not os.path.isfile(p):
            sys.exit("指定的封面背景不存在：%s" % p)
        return p
    p = os.path.join(img, "cover_bg.png")
    if os.path.isfile(p):
        return p
    for name in ("scene_01.png", "scene_1.png"):
        q = os.path.join(img, name)
        if os.path.isfile(q):
            import shutil
            shutil.copyfile(q, p)
            print("[cover] 用场景图作封面背景：%s -> cover_bg.png" % name)
            return p
    print("[cover] 没有可用的封面背景（场景图也没有），交给 gen_cover 走兜底渐变")
    return None


def load_cover_cfg(project):
    cfg = dict(gen_cover.DEFAULTS)
    for name in ("cover.json", os.path.join("image", "cover.json")):
        p = os.path.join(project, name)
        if os.path.isfile(p):
            cfg.update(json.load(open(p, encoding="utf-8")))
            print("[cover] 读到 %s" % p)
            break
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--size", default="768x1280", help="成片尺寸，默认 768x1280")
    ap.add_argument("--burned", default=None, help="中间成片，默认 <项目>/成片_{size}.mp4")
    ap.add_argument("--cover-bg", default=None)
    ap.add_argument("--out", default=None, help="默认 <项目>/成片_最终_{size}.mp4")
    ap.add_argument("--force", action="store_true", help="重出标题卡与封面")
    ap.add_argument("--keep-beats", action="store_true", help="复用已有 _beat_times.json")
    ap.add_argument("--no-titles", action="store_true", help="不叠标题卡（本片不需要顶部字）")
    ap.add_argument("--card-position", default="auto",
                    choices=["auto", "top", "center", "bottom", "left", "right"],
                    help="标题卡位置：auto=竖版压顶/横版贴右竖排（横版默认，避开人物面部）")
    ap.add_argument("--no-infocards", action="store_true", help="不叠信息贴片")
    ap.add_argument("--no-cover", action="store_true", help="不拼封面")
    a = ap.parse_args()

    proj = a.project
    if not os.path.isdir(proj):
        sys.exit("项目目录不存在：%s" % proj)
    img = os.path.join(proj, "image")
    burned = a.burned or os.path.join(proj, "成片_%s.mp4" % a.size)
    out = a.out or os.path.join(proj, "成片_最终_%s.mp4" % a.size)

    if not os.path.isfile(burned):
        sys.exit("缺中间成片 %s —— 先跑 S3（dh_pipeline.py / dh_video.py + 烧字幕）" % burned)

    # ① 节拍时间码
    segs = find_segments(proj)
    beats_path = build_beats(proj, segs, keep=a.keep_beats)
    beats = json.load(open(beats_path, encoding="utf-8"))["beats"]

    # ② 标题卡（可选；实现走独立技能 fxai-title-card）
    wt = burned
    cards_path = os.path.join(img, "cards.json")
    do_titles = (not a.no_titles) and os.path.isfile(cards_path)
    if a.no_titles:
        print("[cards] --no-titles：本片不叠顶部字")
    elif not os.path.isfile(cards_path):
        print("[cards] 没有 %s：本片不叠标题卡，只拼封面" % cards_path)
    else:
        cards = json.load(open(cards_path, encoding="utf-8"))
        if len(cards) != len(beats):
            sys.exit("cards.json 有 %d 条，音频 %d 段 —— 必须一一对应"
                     % (len(cards), len(beats)))
        sys.path.insert(0, overlay_titles.title_card_scripts())
        bind_cardkit(overlay_titles.title_card_scripts())
        import gen_cards as TC_gen      # noqa: E402  ← 来自 fxai-title-card
        import overlay_cards as TC_ov   # noqa: E402
        W, H = [int(x) for x in a.size.lower().split("x")]
        # 🔴 必须先把文字合进 beats，再出图；顺序反了会全被判成空卡
        for i, b in enumerate(beats, 1):
            b.setdefault("i", i)
            c = cards[i - 1]
            b.setdefault("title", c.get("title", ""))
            b.setdefault("sub", c.get("sub", ""))
            b.setdefault("foot", c.get("foot", ""))
        # cards.json 比任何 title_NN.png 新 → 重出（自动跟上文案修改）
        # 位置/尺寸变了也要重出，否则旧卡（比如旧的顶部横排）会顶着新参数混进来
        cards_mtime = os.path.getmtime(cards_path) if os.path.isfile(cards_path) else 0
        first_card = os.path.join(img, "title_01.png")
        meta_path = os.path.join(img, "_cards_meta.json")
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
        except Exception:
            meta = {}
        layout_stale = (meta.get("position") != a.card_position or meta.get("size") != a.size)
        cards_stale = (layout_stale or not os.path.isfile(first_card)
                       or os.path.getmtime(first_card) < cards_mtime)
        if a.force or cards_stale:
            if layout_stale and not a.force:
                print("[cards] 位置/尺寸变了（%s → %s），重出"
                      % (meta.get("position"), a.card_position))
            elif cards_stale and not a.force:
                print("[cards] cards.json 比标题卡新，重出")
            # 🔴 横版别用 top：人物居中出镜时顶部横字正压脸上 → 交给 auto（横版走侧边竖排）
            TC_gen.make_cards(beats, W, H, img, a.card_position)
            json.dump({"position": a.card_position, "size": a.size},
                      open(meta_path, "w", encoding="utf-8"), ensure_ascii=False)
        else:
            print("[cards] 标题卡已存在，跳过（--force 重出）")
        wt = os.path.join(proj, "_tmp", "with_titles.mp4")
        os.makedirs(os.path.join(proj, "_tmp"), exist_ok=True)
        TC_ov.overlay(burned, img, beats, wt)

    # ②' 信息贴片（可选；实现走独立技能 fxai-infocard）
    # 与标题卡并列的一层：半透明形状（气泡/胶囊/小方块/方块卡片）的**辅助性补充内容**，
    # 出现在画面合适位置，淡入/滑入/弹入 + 停留 + 淡出。内容另外写，不是从口播文案提炼。
    infocards_path = os.path.join(img, "infocards.json")
    if a.no_infocards:
        print("[infocards] --no-infocards：本片不叠信息贴片")
    elif not os.path.isfile(infocards_path):
        print("[infocards] 没有 %s：本片不叠信息贴片" % infocards_path)
    else:
        sys.path.insert(0, overlay_titles.infocard_scripts())
        bind_cardkit(overlay_titles.infocard_scripts())
        import make_infocards as IC_mk      # noqa: E402  ← 来自 fxai-infocard
        import overlay_infocards as IC_ov   # noqa: E402
        W, H = [int(x) for x in a.size.lower().split("x")]
        ic_dir = os.path.join(proj, "_tmp", "infocards")
        IC_mk.make_infocards(infocards_path, W, H, ic_dir)
        wi = os.path.join(proj, "_tmp", "with_infocards.mp4")
        IC_ov.overlay(wt, os.path.join(ic_dir, "render.json"), wi)
        wt = wi

    # ③ 封面（可选）
    if a.no_cover:
        print("[cover] --no-cover：本片不拼封面")
        if os.path.abspath(wt) != os.path.abspath(out):
            import shutil
            shutil.copyfile(wt, out)
        print("\n✅ S4 完成（无封面）-> %s" % out)
    else:
        cover = os.path.join(img, "cover.png")
        if a.force or not os.path.isfile(cover):
            bg = pick_cover_bg(proj, a.cover_bg)
            gen_cover.gen_cover(proj, load_cover_cfg(proj), bg)
        else:
            print("[cover] 封面已存在，跳过（--force 重出）")
        overlay_titles.stitch_cover(proj, wt, cover, out)
        print("\n✅ S4 完成 -> %s" % out)

    print("   下一步：python scripts/check_delivery.py --project %s --size %s" % (proj, a.size))


if __name__ == "__main__":
    main()
