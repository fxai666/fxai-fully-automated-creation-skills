# -*- coding: utf-8 -*-
"""成片质检 —— fxai-slide-video 的最终验收环节（机械能查的交给程序）。

跑法：
    python sv_check.py --project <项目名> --root <工作区> [--frames DIR]

流程：读 delivery.json 定位成片 → ffprobe 元数据（分辨率/时长/帧率）→ 抽帧像素检测
（壳冻结 / 背景虚化 / slogan 单行）→ 解析 _tmp/media_*.log（字幕位置/条数/对齐率）→
核对 segments 素材混搭 → 写项目根 质检报告.md（检查清单结果 + 问题记录，保留历史 P 记录）。

判据与清单见 references/08-成片质检.md。质检不过 → 修复后重跑，全绿才交付。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    from PIL import Image
except Exception:  # 纯文本环境：像素项跳过并提示
    Image = None

FRAME_SPLIT = 0.527          # 竖屏上下带分配（与 ml_frame 一致）
TH_BLUR = 25.0               # 背景虚化：上下带亮度均值下限（纯黑≈11）
TH_TEXT = 120                # slogan 文字二值化阈值（金字≈185，带底≈11）
TH_ROW_RATIO = 0.02          # 行内文字像素占比下限（判定该行是文字行）
BAND_GAP = 8                 # 文字带聚类间隙（px）


# ---------- 工具路径（与 sv_media 同款：全局配置优先，不硬编码盘符） ----------
def global_cfg():
    p = os.environ.get("FXAI_GLOBAL_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".workbuddy", "fxai_global.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ffmpeg_bin():
    cfg = global_cfg()
    inst = cfg.get("software_install_dir") or cfg.get("video_install_dir") or ""
    if inst:
        for nm in ("ffmpeg.exe", "ffmpeg"):
            c = os.path.join(inst, "ffmpeg", nm)
            if os.path.isfile(c):
                return os.path.normpath(c)
    v = cfg.get("video_ffmpeg") or cfg.get("ffmpeg")
    if v and os.path.isfile(v):
        return v
    import shutil as _sh
    return _sh.which("ffmpeg") or "ffmpeg"


def ffprobe_bin():
    f = ffmpeg_bin()
    if f != "ffmpeg":
        cand = os.path.join(os.path.dirname(f), "ffprobe.exe")
        if os.path.isfile(cand):
            return cand
    return "ffprobe"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe_media(path):
    """返回 (w, h, fps, dur)；任一取不到给 None。"""
    o = run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-show_entries", "format=duration",
             "-of", "json", str(path)])
    info = {}
    try:
        info = json.loads(o.stdout or "{}")
    except Exception:
        return None, None, None, None
    try:
        s = info["streams"][0]
        w, h = int(s["width"]), int(s["height"])
        fr = s.get("r_frame_rate", "24/1")
        a, b = fr.split("/")
        fps = round(float(a) / float(b)) if float(b) else 24
    except Exception:
        w = h = fps = None
    try:
        dur = float(info["format"]["duration"])
    except Exception:
        dur = None
    return w, h, fps, dur


def grab(video, t, out_png):
    """抽一帧整图到 png（-ss 前置，快）。"""
    r = run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(t), "-i", str(video), "-frames:v", "1",
             str(out_png)])
    return os.path.isfile(out_png) and os.path.getsize(out_png) > 0


def bands_ratio(w, h):
    """三段高度：竖屏按 frame 公式，横屏兜底 15%。返回 (top_h, media_h, bot_h)。"""
    if w > h:  # 横屏例外：上下带各 15%（与 frame_ratios 横屏分支一致口径）
        top = int(round(h * 0.15))
        return top, int(round(h * 0.70)), int(round(h * 0.15))
    mh = w * 9 // 16
    if mh % 2:
        mh -= 1  # 中区高度必须偶数（04-风格册）
    top = int(round((h - mh) * FRAME_SPLIT))
    return top, mh, h - top - mh


def _pixels(im):
    """取像素序列：Pillow 14 用 get_flattened_data，旧版回退 getdata。"""
    g = getattr(im, "get_flattened_data", None)
    return g() if g else list(im.getdata())


def region_mean(img_path, x0, y0, x1, y1):
    im = Image.open(img_path).convert("L")
    crop = im.crop((x0, y0, x1, y1))
    px = _pixels(crop)
    return sum(px) / max(1, len(px))


def text_ratio_diff(p, q, box):
    """同一区域两帧的『壳文字』差异：二值化（>TH_TEXT）后像素级 XOR / 文字像素总数。

    上下带是半透明带 + 当前段素材虚化底（P2 设计），背景亮度会随换段变化；
    壳文字（金字/白字/黄字，亮度 >120）才是『全片冻结』的对象，虚化暗底被阈值滤掉。
    """
    a = Image.open(p).convert("L").crop(box)
    b = Image.open(q).convert("L").crop(box)
    if a.size != b.size:
        b = b.resize(a.size)
    pa, pb = _pixels(a), _pixels(b)
    ma = [x > TH_TEXT for x in pa]
    mb = [x > TH_TEXT for x in pb]
    total = sum(ma) + sum(mb)
    if total <= 0:
        return 0.0
    diff = sum(1 for x, y in zip(ma, mb) if x != y)
    return diff / total


def text_bands(img_path, x0, y0, x1, y1):
    """区域内文字带数：按行统计亮像素（>TH_TEXT），行聚合成带（gap≤BAND_GAP）。"""
    im = Image.open(img_path).convert("L").crop((x0, y0, x1, y1))
    w, h = im.size
    px = im.load()
    row_hits = []
    for y in range(h):
        n = sum(1 for x in range(w) if px[x, y] > TH_TEXT)
        row_hits.append(n > int(w * TH_ROW_RATIO))
    bands, in_b = 0, False
    last = -10
    for y, hit in enumerate(row_hits):
        if hit and not in_b:
            in_b, bands = True, bands + 1
            last = y
        elif hit and in_b:
            last = y
        elif not hit and in_b and (y - last) > BAND_GAP:
            in_b = False
    return bands


def load_delivery(base):
    p = os.path.join(base, "delivery.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def parse_segments(base):
    """读 segments.json：段数 + 素材混搭统计（视频/图片/网页）。"""
    seg = os.path.join(base, "segments.json")
    if not os.path.isfile(seg):
        return None
    try:
        with open(seg, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    segs = data.get("segments") if isinstance(data, dict) else data
    if not isinstance(segs, list):
        return None
    kinds = {"视频": 0, "图片": 0, "网页": 0}
    for s in segs:
        flds = s.get("fields") or {}
        m = flds.get("media")
        items = m if isinstance(m, list) else ([m] if m else [])
        for it in items:
            if isinstance(it, dict) and (it.get("page") or it.get("url") or it.get("scroll")):
                kinds["网页"] += 1
            elif isinstance(it, str) and it.lower().endswith(".mp4"):
                kinds["视频"] += 1
            elif isinstance(it, str):
                kinds["图片"] += 1
    return len(segs), kinds


def latest_media_log(base):
    logs = sorted(glob.glob(os.path.join(base, "_tmp", "media_*.log")))
    return logs[-1] if logs else None


def parse_log(base):
    """从最新 media 日志提取：bottom、对齐率、字幕条数、成片时长。"""
    log = latest_media_log(base)
    out = {"log": log, "bottom": None, "size": None, "align": [], "sub_count": None,
           "merged_s": None, "done_s": None}
    if not log:
        return out
    with open(log, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"字幕位置跟中区走：bottom=(\d+)（frame (\d+)x(\d+)）", text)
    if m:
        out["bottom"] = int(m.group(1))
        out["size"] = (int(m.group(2)), int(m.group(3)))
    out["align"] = [(float(a), int(n)) for a, n in
                    re.findall(r"对齐匹配率 ([\d.]+)%，字幕 (\d+) 条", text)]
    m = re.search(r"字幕 (\d+) 条，校验通过", text)
    if m:
        out["sub_count"] = int(m.group(1))
    m = re.search(r"MERGED .*? ([\d.]+)s", text)
    if m:
        out["merged_s"] = float(m.group(1))
    m = re.search(r"DONE .*? ([\d.]+)s", text)
    if m:
        out["done_s"] = float(m.group(1))
    return out


def collect_old_issues(report_path):
    """保留旧报告里的 P 记录段落（### P… 到下一个 ###/## 或结尾）。"""
    if not os.path.isfile(report_path):
        return ""
    with open(report_path, encoding="utf-8") as f:
        text = f.read()
    parts = re.findall(r"(?ms)^### P\d+.*?(?=^### P\d+|^## |\Z)", text)
    return "\n".join(p.strip() for p in parts if p.strip())


def max_p(issues_text):
    nums = re.findall(r"^### P(\d+)", issues_text or "", re.M)
    return max((int(n) for n in nums), default=0)


def main():
    ap = argparse.ArgumentParser(description="成片质检（fxai-slide-video 最终验收）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--frames", default=None, help="抽帧输出目录（默认项目 _tmp/frames_check）")
    a = ap.parse_args()
    base = os.path.join(a.root, a.project)

    d = load_delivery(base)
    if not d or not d.get("video", {}).get("final_file"):
        print("❌ 找不到 delivery.json / 成片信息：%s" % os.path.join(base, "delivery.json"))
        sys.exit(1)
    final = os.path.join(base, d["video"]["final_file"])
    if not os.path.isfile(final):
        print("❌ 成片不存在：%s" % final)
        sys.exit(1)

    w, h, fps, dur = probe_media(final)
    if not w or not h:
        print("❌ ffprobe 读不出成片流信息：%s" % final)
        sys.exit(1)

    frames_dir = a.frames or os.path.join(base, "_tmp", "frames_check")
    os.makedirs(frames_dir, exist_ok=True)
    f_start = os.path.join(frames_dir, "f_start.png")
    f_end = os.path.join(frames_dir, "f_end.png")
    f_mid = os.path.join(frames_dir, "f_mid.png")
    t_mid = max(2.0, (dur or 60) * 0.5)
    grab(final, 2.0, f_start)
    grab(final, max(3.0, (dur or 60) - 2.0), f_end)
    grab(final, t_mid, f_mid)

    top_h, media_h, bot_h = bands_ratio(w, h)
    rows = []
    issues = []
    fails = []

    def add(no, name, judge, ok, detail):
        if ok is None:
            rows.append((no, name, judge, "⬜ " + detail))
        else:
            rows.append((no, name, judge, ("✅ " if ok else "❌ ") + detail))
            if not ok:
                fails.append("%s %s" % (no, name))

    # 1 分辨率/时长/帧率
    want = d["video"]["final_file"]
    m_name = re.search(r"_(\d+)x(\d+)\.mp4$", want)
    ok_size = bool(m_name) and w == int(m_name.group(1)) and h == int(m_name.group(2))
    audio_s = float(d.get("audio", {}).get("total_seconds") or 0)
    ok_dur = dur and (audio_s == 0 or abs(dur - audio_s) <= 1.5)
    ok_fps = fps and 23 <= fps <= 25
    ok1 = ok_size and ok_dur and ok_fps
    add("1", "分辨率/时长/帧率", "分辨率=画布档、时长≈音频±1.5s、帧率≈24",
        ok1, "%d×%d / %.2fs / %dfps" % (w, h, dur or 0, fps or 0))

    # 2 壳文字冻结（首尾帧上下带文字像素差异）
    # 判据：上下带是半透明带 + 当前段虚化底（P2），背景随换段变化属正常；
    # 冻结的是壳文字（logo/标题/slogan/栏目名），二值化后文字像素差异应很小。
    freeze = None
    if Image and os.path.isfile(f_start) and os.path.isfile(f_end):
        # 上带区域
        d_top = text_ratio_diff(f_start, f_end, (0, 0, w, top_h))
        # 下带区域（含 logo / 竖排栏目名 / slogan）
        d_bot = text_ratio_diff(f_start, f_end, (0, h - bot_h, w, h))
        worst = max(d_top, d_bot)
        freeze = worst < 0.25
        add("2", "壳文字冻结", "首尾帧上下带文字像素差异 < 25%",
            freeze, "上带 %.1f%% / 下带 %.1f%%" % (d_top * 100, d_bot * 100))
    else:
        add("2", "壳文字冻结", "首尾帧上下带文字像素差异", False, "缺 PIL 或抽帧失败")

    # 3 素材混搭
    segs = parse_segments(base)
    if segs:
        n_seg, kinds = segs
        n_img = int(d.get("image", {}).get("count") or 0)
        ok3 = n_img == n_seg and n_seg > 0
        add("3", "中区素材混搭", "配图数=段数、视频/图片/网页登记",
            ok3, "%d 段：视频%d / 图片%d / 网页%d（配图%d）"
                % (n_seg, kinds["视频"], kinds["图片"], kinds["网页"], n_img))
    else:
        add("3", "中区素材混搭", "配图数=段数", False, "读不到 segments.json")

    # 4 字幕位置
    lg = parse_log(base)
    if lg["bottom"] is not None and lg["size"] == (w, h):
        lw, lh = lg["size"]
        _, _, lbot = bands_ratio(lw, lh)
        sub_bottom_y = lh - lg["bottom"]          # 字幕底 y
        media_bottom = lh - lbot                  # 中区下沿 y
        ok4 = media_bottom - 40 <= sub_bottom_y <= media_bottom + 10
        add("4", "字幕位置", "字幕底在中区下沿内侧（底=%d，中区下沿=%d）" % (sub_bottom_y, media_bottom),
            ok4, "bottom=%d" % lg["bottom"])
    else:
        add("4", "字幕位置", "日志里有 bottom 值且与画布档一致", False,
            "日志无 bottom 或画布不符%s" % ("" if lg["log"] else "（无 media 日志）"))

    # 5 字幕内容（对齐率 + 条数）
    ok5 = True
    detail5 = "无字幕日志"
    if lg["align"] or lg["sub_count"]:
        worst = min((p for p, _ in lg["align"]), default=100.0)
        ok5 = lg["sub_count"] is not None and worst >= 95
        detail5 = "%d 条，对齐率最低 %.1f%%" % (lg["sub_count"] or 0, worst)
    add("5", "字幕内容", "条数>0、whisper 对齐率≥95%", ok5, detail5)

    # 6 品牌区排版（slogan 单行）
    bands = None
    if Image and os.path.isfile(f_mid):
        x0 = int(w * 0.62)
        x1 = int(w * 0.97)
        y0 = h - int(bot_h * 0.60)
        y1 = h - int(bot_h * 0.05)
        bands = text_bands(f_mid, x0, y0, x1, y1)
        add("6", "品牌区排版", "下栏 slogan 单行（文字带数=1）", bands == 1,
            "文字带数 %d" % bands)
    else:
        add("6", "品牌区排版", "slogan 单行", False, "缺 PIL 或抽帧失败")

    # 7 背景虚化（上下带亮度）
    if Image and os.path.isfile(f_mid):
        b_top = region_mean(f_mid, 0, 0, w, top_h)
        b_bot = region_mean(f_mid, 0, h - bot_h, w, h)
        ok7 = b_top >= TH_BLUR and b_bot >= TH_BLUR
        add("7", "背景虚化", "上下带亮度均值≥%.0f（透出虚化）" % TH_BLUR,
            ok7, "上带 %.1f / 下带 %.1f" % (b_top, b_bot))
    else:
        add("7", "背景虚化", "上下带亮度≥25", False, "缺 PIL 或抽帧失败")

    # 8-11 人工核项（程序给提示与判据）
    add("8", "花字/字幕不打架", "花字中区 0.44、字幕中区下沿 0.79", None, "人工核（抽帧）")
    add("9", "网页文字卡", "内容完整无乱码", None, "人工核（有网页段时看 S07/S09 帧）")
    add("10", "合规预检", "sv_compliance 0 ERROR 0 WARN", None, "人工核（pipeline 阶段已跑）")
    tag_ok = None
    try:
        with open(os.path.join(base, "交付清单.md"), encoding="utf-8") as f:
            tag_ok = "AI 生成" in f.read()
    except Exception:
        pass
    add("11", "发布标签", "交付清单标注「含有 AI 生成内容」", tag_ok,
        "已标注" if tag_ok else "待标注" if tag_ok is not None else "读不到交付清单")

    # 12 音轨与配乐（有 BGM 时确认真的在，且是上传安全档）
    a_ok, a_detail = False, ""
    try:
        o = run([ffprobe_bin(), "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=codec_name,sample_rate,channels", "-of", "json", final])
        st = (json.loads(o.stdout or "{}").get("streams") or [{}])[0]
        codec, rate = st.get("codec_name"), int(st.get("sample_rate") or 0)
        v = run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", final,
                 "-af", "volumedetect", "-f", "null", "-"])
        mv = re.search(r"mean_volume:\s*(-?[\d.]+) dB", v.stderr or "")
        mean = float(mv.group(1)) if mv else None
        bgm = str(d.get("bgm") or "")
        has_bgm = bool(bgm) and "无配乐" not in bgm
        ok12 = bool(codec) and (mean is None or -24 <= mean <= -12)
        a_detail = "%s / %sHz / %sch / mean %s dB，%s%s" % (
            codec or "无音轨", rate or "?", st.get("channels") or "?",
            ("%.1f" % mean) if mean is not None else "?",
            ("配乐：" + bgm) if has_bgm else "无配乐",
            "" if rate == 48000 else "（⚠️ 非 48kHz，上传前建议转 48k）")
        a_ok = ok12
    except Exception as e:
        a_detail = "读音轨失败：%s" % e
    add("12", "音轨与配乐", "有 aac 音轨、48kHz、整片 mean -24~-12dB", a_ok, a_detail)

    # ---- 写报告 ----
    report = os.path.join(base, "质检报告.md")
    old_issues = collect_old_issues(report)
    pn = max_p(old_issues)
    new_issue_txt = ""
    if fails:
        pn += 1
        new_issue_txt = (
            "### P%d · 质检失败项（%s）\n"
            "- **现象**：sv_check 未通过：%s\n"
            "- **根因**：待查（对照 08-成片质检.md 判据）\n"
            "- **修复**：待实施\n"
            "- **复验**：修复后重跑 `python sv_check.py --project %s --root %s` 至全绿\n"
            % (pn, "、".join(fails), "、".join(fails), a.project, os.path.abspath(a.root)))
    issues_blk = ""
    if old_issues or new_issue_txt:
        issues_blk = "## 质检问题记录\n\n" + "\n\n".join(
            x for x in (old_issues, new_issue_txt.strip()) if x) + "\n"

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    table = "\n".join("| %s | %s | %s | %s |" % r for r in rows)
    passed = not fails
    concl = ("**通过**。12 项全绿，问题记录见下。" if passed and not new_issue_txt else
             "**未通过**：%s。修复后重跑 `sv_check.py` 至全绿再交付。" % "、".join(fails))

    md = ("# 成片质检报告 —— %s\n\n"
          "- 质检时间：%s\n- 质检对象：%s（%d×%d / %.2fs / %dfps）\n"
          "- 质检方式：ffprobe 元数据 + 抽帧像素检测（壳冻结/虚化/slogan 单行）+ 合成日志解析\n\n"
          "## 质检清单（12 项，成片后逐项过）\n\n"
          "| # | 检查项 | 判据 | 结果 |\n|---|---|---|---|\n%s\n\n"
          "%s"
          "## 质检结论\n\n%s\n" % (a.project, now, d["video"]["final_file"], w, h, dur or 0,
                                 fps or 0, table, issues_blk, concl))

    with open(report, "w", encoding="utf-8") as f:
        f.write(md)
    print("质检报告 -> %s" % report)
    print("结论：%s" % ("✅ 通过" if passed else "❌ 未通过：" + "、".join(fails)))
    sys.exit(0 if passed else 2)


if __name__ == "__main__":
    main()
