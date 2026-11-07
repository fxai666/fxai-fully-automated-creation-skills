# -*- coding: utf-8 -*-
# =====================================================================
# fxai-voiceover —— 数字人口播音频生成（技能主入口）
#
# 调 CosyVoice3.0 的 apiv3 接口（`POST /synthesize`，探活 `GET /health`），
# 把文字变成口播音频：单段 / 长文分句拼接 / 多段批量。
#
# 产物落盘：
#   --project <项目名> 时用 output_layout 建独立项目目录
#     <项目根>/<项目名>/music/   音频（wav 母版）
#     <项目根>/<项目名>/doc/     交付清单 delivery.json + 交付清单.md +（--script 时）分镜副本
#     <项目根>/<项目名>/video/   数字人口播视频（下一步的母版目录，本技能只占位不产出）
#   不带 --project 时退回到 --out-dir（手工目录），同样写 delivery.json。
#
# 音频一律由接口返回 base64、本脚本落盘；不扫磁盘找既有音频，不做本机产物搬运。
# 用法见 `python voice_gen.py --help` 与同目录 SKILL.md。
# =====================================================================
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_WF = os.path.join(os.path.dirname(_HERE), "fxai-workflow")
if _WF not in sys.path:
    sys.path.insert(0, _WF)
if os.path.join(os.path.dirname(_HERE), "fxai-common") not in sys.path:
    sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "fxai-common"))

import fxai_common as fxc  # noqa: E402
import tts_client as tc  # noqa: E402


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pad(n):
    return ("%02d" % n) if n < 100 else ("%03d" % n)


def _fmt_sec(s):
    """秒 -> m:ss（交付清单里看时长比浮点舒服）。"""
    try:
        s = float(s or 0)
    except Exception:
        return "-"
    return "%d:%02d" % (int(s // 60), int(round(s % 60)))


def _size_kb(p):
    try:
        return round(os.path.getsize(p) / 1024.0, 1)
    except Exception:
        return None


def _limit(args):
    """分句阈值：--max-chars 优先，缺省取配置 tts.max_chars_per_chunk（80），0 = 不分句。"""
    v = args.max_chars if args.max_chars is not None else tc.tts_cfg().get("max_chars_per_chunk", 80)
    return int(v or 0)


def _segments_of(text, args):
    """长文分句：阈值见 _limit。"""
    limit = _limit(args)
    if limit <= 0:
        return [text.strip()] if text.strip() else []
    parts = tc.split_text(text, limit)
    for i, p in enumerate(parts):
        print("[tts] 第%d/%d 段：%s" % (i + 1, len(parts), p[:40]), flush=True)
    return parts


def _voice_of(args, sp):
    """参考音色 + 情感语气指令。命令行显式传的优先于音色库。

    音色二选一（服务端按「有没有 ref_audio」路由）：
      有 ref_audio → 走克隆（服务端会忽略文本里的情绪描述）
      只有 speaker（千问内置音色）→ 走内置音色 + 情绪描述

    取值顺序：--ref-audio > --qwen-speaker（且没显式 --speaker）> 音色库条目。
    """
    if args.ref_audio:
        return {"ref_audio": args.ref_audio, "speaker": args.qwen_speaker or "",
                "instruct_text": args.instruct_text,
                "prompt_text": args.prompt_text or "", "mode": "",
                "speed": args.speed}
    if args.qwen_speaker and not args.speaker:
        return {"ref_audio": "", "speaker": args.qwen_speaker,
                "instruct_text": args.instruct_text, "mode": "cross_lingual",
                "speed": args.speed}
    if not sp:
        raise SystemExit("! 缺音色：--ref-audio <参考音色> / --speaker <id>（先看 --list）"
                         " / --qwen-speaker <千问内置音色>")
    ra = sp.get("ref_audio") or ""
    spk = sp.get("qwen_speaker") or args.qwen_speaker or ""
    if not ra and not spk:
        raise SystemExit("! 音色「%s」既没配 ref_audio（走克隆），也没配 qwen_speaker（走内置音色）"
                         % sp.get("id"))
    d = {"ref_audio": ra, "speaker": spk,
         "instruct_text": sp.get("instruct_text"),
         "prompt_text": args.prompt_text or sp.get("prompt_text") or "",
         "mode": sp.get("mode") or "", "speed": sp.get("speed")}
    if args.instruct_text:
        d["instruct_text"] = args.instruct_text
    if args.speed:                      # 命令行 --speed 覆盖音色库里的语速
        d["speed"] = args.speed
    return d


def _one(text, sp, args, out_path, index=0, instruct=None):
    """合成一段写到 out_path（文件已存在且有效、且语速一致则复用，不重复烧 GPU）。

    instruct 给了就只这一段用（口播脚本里逐段带 `用…的语气说<|endofprompt|>` 标签），
    没给就整条用音色的默认语气指令。
    """
    v = _voice_of(args, sp)
    want_speed = float(v.get("speed") or 1.0)
    # 复用要连语速一起核：本地变速是**原地**改文件，改了语速只能重合成（拉伸过的波形回不去）
    if (os.path.isfile(out_path) and os.path.getsize(out_path) > 1024 and not args.force
            and abs(tc.applied_speed(out_path) - want_speed) < 1e-3):
        print("[tts] 复用已有音频：%s" % out_path, flush=True)
        return {"index": index, "ok": True, "path": os.path.abspath(out_path),
                "duration": tc.wav_seconds(out_path), "mode": "cross_lingual",
                "reused": True, "text": text}
    # 🔴 走合成 = 波形换了新的，旧的响度归一标记必须当场失效。
    # 标记还在的话 loudnorm_all() 直接 continue，新音频停在原始响度（实测 -29.8 LUFS），
    # 比其余已归一的段低十几 dB —— 补录某一段后，整片就那一段突然变小声。
    _mk = out_path + ".norm"
    if os.path.isfile(_mk):
        try:
            os.remove(_mk)
        except OSError:
            pass
    meta = tc.synthesize(text,
                         ref_audio=v["ref_audio"],
                         speaker=v.get("speaker") or "",
                         mode=v["mode"],
                         prompt_text=v.get("prompt_text") or "",
                         instruct_text=instruct or v["instruct_text"],
                         speed=v["speed"],
                         seed=args.seed,
                         url=args.url,
                         out_path=out_path,
                         engine=args.engine)
    meta["index"] = index
    return meta


# ---------- 项目目录 + 交付清单 ----------
def _open_project(args):
    """按 --project 建项目目录；没给就退回 --out-dir。返回 (项目名, 音频目录, doc 目录, 项目根)。"""
    name = (args.project or "").strip()
    if not name:
        d = os.path.abspath(args.out_dir)
        os.makedirs(d, exist_ok=True)      # 音频 + 交付清单都落这里，先建好
        return "", d, d, d
    import output_layout as ol
    L = ol.layout(name)          # 建 <项目根>/<项目名>/{image,_tmp}（image 放图片，其余交付物在根）
    # 交付物扁平化：音频与清单一律放**项目根目录**，不建 music/ doc/ 分层
    print("[tts] 项目目录（交付物在根目录）：%s" % L["root"], flush=True)
    return name, L["root"], L["root"], L["root"]


def _rel(root, p):
    """交付清单里一律记相对项目根的路径，换台机器看清单不会因为盘符对不上。"""
    try:
        return os.path.relpath(p, root).replace("\\", "/")
    except Exception:
        return p


def _item_of(root, r):
    """一条合成结果 -> 清单条目（带相对路径 / 时长 / 大小 / 字数）。"""
    p = r.get("path") or ""
    return {
        "index": r.get("index"),
        "type": "audio",
        "file": _rel(root, p) if p else "",
        "path": os.path.abspath(p) if p else "",
        "duration_sec": r.get("duration"),
        "duration": _fmt_sec(r.get("duration")),
        "size_kb": _size_kb(p) if p else None,
        "sample_rate": r.get("sample_rate"),
        "mode": r.get("mode"),
        "reused": bool(r.get("reused")),
        "chars": len(r.get("text") or ""),
        "text": r.get("text") or "",
    }


def _write_delivery(args, name, root, doc_dir, items, voice, video_note, joined=None, svc_url=""):
    """写交付清单：机器读 delivery.json + 人读 交付清单.md，放进项目 doc/。"""
    total = sum(float(i.get("duration_sec") or 0) for i in items)
    doc = {
        "project": name,
        "project_root": os.path.abspath(root),
        "generated_at": _now(),
        "service": {"api": "apiv3 (CosyVoice3.0)", "endpoint": "POST /synthesize",
                    "url": svc_url or voice.pop("url", "") or ""},
        "voice": voice,
        "summary": {"count": len(items), "total_seconds": round(total, 2),
                    "total_duration": _fmt_sec(total), "merged": bool(joined),
                    "merged_file": _rel(root, joined) if joined else ""},
        "items": items,
        # 下一步做数字人口播视频时，成片落在 video/，清单里先占位
        "video": {"dir": "video/", "count": 0, "items": [],
                  "note": "数字人口播视频下一步产出，落在本项目 video/ 下"},
    }
    os.makedirs(doc_dir, exist_ok=True)
    jp = os.path.join(doc_dir, "delivery.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    mp = os.path.join(doc_dir, "交付清单.md")
    lines = ["# 交付清单 —— %s" % (name or "口播"),
             "",
             "- 生成时间：%s" % doc["generated_at"],
             "- 服务：apiv3（CosyVoice3.0）`POST /synthesize` @ %s" % (doc["service"]["url"] or "-"),
             "- 音色：%s" % json.dumps(voice, ensure_ascii=False) if voice else "",
             "- 音频：%d 条，合计 %s（%.2f 秒）" % (len(items), _fmt_sec(total), total),
             ""]
    if joined:
        lines.append("- 已拼成一条：%s" % _rel(root, joined))
    lines += ["", "## 音频", "", "| # | 文件 | 时长 | 大小 | 字数 | 说明 |",
              "|---|------|------|------|------|------|"]
    for i in items:
        lines.append("| %s | %s | %s | %s KB | %d | %s |"
                     % (i["index"], i["file"], i["duration"],
                        i["size_kb"] if i["size_kb"] is not None else "-",
                        i["chars"], "（复用已有）" if i["reused"] else ""))
    lines += ["", "## 数字人口播视频", "",
              "（暂无；下一步产出后落在 `video/`，会补进这里）", ""]
    with open(mp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[tts] 交付清单：%s / %s" % (jp, mp), flush=True)
    return jp


def _do_check(args):
    names = tc.engines_list() or ["cosyvoice"]
    print("已登记引擎：%s（默认 %s）" % (", ".join(names), tc.default_engine()))
    for name in names:
        lu, cu = tc.local_url(name), tc.cloud_url(name)
        print("[%s] 本机 %s -> %s" % (name, lu, "在线" if tc.check(lu) else "未启动"))
        if cu:
            print("[%s] 线上 %s -> %s" % (name, cu, "在线" if tc.check(cu) else "未启动"))
    print("默认路由：%s优先" % ("本机" if tc.tts_cfg().get("prefer_local", True) else "线上"))
    sps = tc.speakers()
    if not sps:
        print("音色库为空：把参考音色 + 情感语气指令填进本目录 speakers.json，或 fxai_global.json 的 tts.speakers")
    for sp in sps:
        print("音色 %s | %s | 参考音色 %s | 语气指令 %s | 千问内置音色 %s"
              % (sp.get("id"), sp.get("name"), sp.get("ref_audio"), sp.get("instruct_text"),
                 sp.get("qwen_speaker") or "-"))
    return 0


def _do_list(args):
    sps = tc.speakers()
    if not sps:
        print("（音色库为空：把参考音色 + 情感语气指令填进本目录 speakers.json，或 fxai_global.json 的 tts.speakers）")
    for sp in sps:
        print("%-12s %-10s %-24s %-28s %s"
              % (sp.get("id"), sp.get("name"), sp.get("ref_audio"), sp.get("instruct_text"),
                 sp.get("qwen_speaker") or ""))
    return 0


# ---------- 分段响度归一化（EBU R128 线性增益，拉齐各段听感音量） ----------
def _ffmpeg_bin():
    """ffmpeg：全局配置 software_install_dir → <目录>/ffmpeg/ffmpeg.exe，否则走 PATH。"""
    inst = tc.global_cfg().get("software_install_dir") or ""
    if inst:
        for nm in ("ffmpeg.exe", "ffmpeg"):
            c = os.path.join(inst, "ffmpeg", nm)
            if os.path.isfile(c):
                return c
    import shutil as _sh
    return _sh.which("ffmpeg") or "ffmpeg"


def _loudness_measure(path):
    """EBU R128 测量：返回 (整合响度 I (LUFS), 峰值 dBFS/dBTP)；测不到 I 返回 (None, 0)。"""
    r = subprocess.run([_ffmpeg_bin(), "-hide_banner", "-nostats", "-i", path,
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = r.stderr or ""
    m_i = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", txt)
    m_pk = (re.findall(r"True peak:\s*(-?[\d.]+)", txt)
            or re.findall(r"Peak:\s*(-?[\d.]+)\s*dB", txt))
    return (float(m_i[-1]) if m_i else None, float(m_pk[-1]) if m_pk else 0.0)


def loudnorm_all(paths, target=None):
    """全部段生成完：逐段测量整合响度，线性增益统一拉齐（默认 -16 LUFS，全局键 audio_loudness 可改）。
    不压动态、防削波；处理过写 <名>.wav.norm 标记（记目标值），重跑直接跳过。"""
    target = float(target if target is not None
                   else tc.global_cfg().get("audio_loudness", -16.0))
    for p in paths:
        if not p or not os.path.isfile(p) \
                or not p.lower().endswith((".wav", ".mp3", ".flac", ".m4a")):
            continue
        marker = p + ".norm"
        if os.path.isfile(marker):
            try:
                if json.load(open(marker, encoding="utf-8")).get("target") == target:
                    continue
            except Exception:
                pass
        i, peak = _loudness_measure(p)
        if i is None:
            continue
        gain = target - i
        if gain > 0 and peak + gain > -1.0:          # 提升会削波 → 限到峰值 -1 dBFS
            gain = max(0.0, -1.0 - peak)
        if abs(gain) < 0.1:
            with open(marker, "w", encoding="utf-8") as f:
                json.dump({"target": target, "gain": 0.0}, f)
            continue
        tmp = p + ".norm_tmp.wav"
        r = subprocess.run([_ffmpeg_bin(), "-y", "-i", p,
                            "-af", "volume=%.2fdB" % gain, "-c:a", "pcm_s16le", tmp],
                           capture_output=True, text=True)
        if r.returncode == 0 and os.path.isfile(tmp) and tc.wav_seconds(tmp) > 0.1:
            shutil.move(tmp, p)
            with open(marker, "w", encoding="utf-8") as f:
                json.dump({"target": target, "gain": round(gain, 2)}, f)
            print("[tts] 响度归一 %s  I=%.1f LUFS → %.1f（增益 %+.1f dB）"
                  % (os.path.basename(p), i, target, gain), flush=True)
        elif os.path.isfile(tmp):
            os.remove(tmp)


def _build_parser():
    ap = argparse.ArgumentParser(
        description="数字人口播音频生成（CosyVoice3 apiv3 API · 本机优先 · 产物落项目目录）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="用法：\n"
               "  python voice_gen.py --check\n"
               "  python voice_gen.py --list\n"
               "  python voice_gen.py --project 一个人的队伍 --text '大家好，今天这期讲三件事' --speaker xiaoyun\n"
               "  python voice_gen.py --project 一个人的队伍 --text-file 口播稿.txt --join-all\n"
               "  python voice_gen.py --project 一个人的队伍 --script 分镜.txt\n"
               "  python voice_gen.py --project 一个人的队伍 --text 'Hello world' "
               "--ref-audio voices/ref.wav --instruct-text '请用四川话表达。'\n")
    ap.add_argument("--check", action="store_true", help="只探测接口（本机/线上）与音色库")
    ap.add_argument("--list", action="store_true", help="列音色库（本机登记的音色）")
    ap.add_argument("--project", help="项目名：独立建 <项目根>/<项目名>/ 目录，产物存进去")
    ap.add_argument("--text", help="口播文本（单段）")
    ap.add_argument("--text-file", help="把整个文件当一段口播稿（按分句阈值切成多段再拼）")
    ap.add_argument("--script", help="多段脚本：空行分段，逐段各出一条音频 + 交付清单（副本存 doc/）；"
                                     "段里带 `用…的语气说<|endofprompt|>` 就逐段用这个语气")
    ap.add_argument("--speaker", help="音色 id（见 --list）")
    ap.add_argument("--qwen-speaker",
                    help="千问内置音色 id（vivian/serena/eric/dylan/uncle_fu…）："
                         "不传参考音色、走内置音色 + 情绪描述")
    ap.add_argument("--ref-audio", help="参考音色（相对软件数据目录或绝对路径）；给了就走克隆")
    ap.add_argument("--prompt-text", dest="prompt_text",
                    help="参考音频的原文（走克隆必填；也可写进音色库条目的 prompt_text）")
    ap.add_argument("--instruct-text", dest="instruct_text", help="情感语气指令，覆盖音色库的")
    ap.add_argument("--speed", type=float, help="语速 0.5~2.0，覆盖音色/配置")
    ap.add_argument("--seed", type=int, help="随机种子（同文本同种子 = 同一条音频）")
    ap.add_argument("--out", help="输出路径（单段口播；给了就忽略项目目录默认位置）")
    ap.add_argument("--out-dir", default=".", help="不建项目目录时的输出目录（默认当前目录）")
    ap.add_argument("--name", default="口播", help="文件名前缀（自动补序号；项目目录下默认取项目名）")
    ap.add_argument("--ext", default=".wav", help="输出扩展名（接口只出 wav，默认 .wav）")
    ap.add_argument("--max-chars", type=int, default=None, help="分句长度阈值（默认配置 80，0=不分句）")
    ap.add_argument("--join-all", action="store_true", help="多段合成后拼成一条（分句口播默认自动拼）")
    ap.add_argument("--manifest", action="store_true", help="不建项目目录时也写 manifest.json（等价于交付清单）")
    ap.add_argument("--force", action="store_true", help="输出文件已存在也重新合成（默认复用）")
    ap.add_argument("--no-local", action="store_true", help="跳过本机探测，直接走线上")
    ap.add_argument("--url", help="显式指定服务地址（参数优先于配置）")
    ap.add_argument("--engine", help="语音引擎：cosyvoice / qwen3tts（不传取 tts.default_engine）")
    ap.add_argument("--json", action="store_true", help="末尾输出 JSON（供其它技能解析）")
    return ap


def main():
    args = _build_parser().parse_args()

    if args.check:
        return _do_check(args)
    if args.list:
        return _do_list(args)

    # 本机优先：开工前先探本机，探不通落到线上（本机 GPU 不计费）
    # 显式传了 --url 就不探了：地址已由调用方定死，再探本机只会打印误导性的「走线上」
    if not args.no_local and not args.url:
        tc.prefer_local(engine=args.engine)

    name, audio_dir, doc_dir, root = _open_project(args)
    ext = args.ext or ".wav"
    prefix = args.name if args.name != "口播" else (name or args.name)

    # 不指定音色就用配置里的第一条（fxai_global.json 的 tts.speakers 首位 = 女主000）
    sp = tc.find_speaker(args.speaker) if args.speaker else (tc.speakers() or [None])[0]
    tasks = []          # [(说话内容, 输出路径, 语气指令)]
    auto_join = False   # 分句口播：各段生成后自动拼成一条
    if args.text:
        text = args.text.strip()
        if not text:
            raise SystemExit("! --text 是空的")
        parts = _segments_of(text, args)
        final = args.out or os.path.join(audio_dir, prefix + ext)
        if len(parts) == 1:
            tasks = [(parts[0], final, None)]
        else:
            auto_join = True
            tasks = [(p, os.path.join(audio_dir, prefix + _pad(i + 1) + ext), None)
                     for i, p in enumerate(parts)]
    elif args.text_file:
        with open(args.text_file, encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            raise SystemExit("! %s 是空的" % args.text_file)
        parts = _segments_of(text, args)
        base = os.path.splitext(os.path.basename(args.text_file))[0]
        final = args.out or os.path.join(audio_dir, base + ext)
        if len(parts) == 1:
            tasks = [(parts[0], final, None)]
        else:
            auto_join = True
            tasks = [(p, os.path.join(audio_dir, base + _pad(i + 1) + ext), None)
                     for i, p in enumerate(parts)]
    elif args.script:
        with open(args.script, encoding="utf-8") as f:
            blocks = [b.strip() for b in re.split(r"\n\s*\n", f.read()) if b.strip()]
        if not blocks:
            raise SystemExit("! 脚本里没有分段（空行分段）")
        # 提交前质检：带标签的段查格式与正文长度，整段没标签的按普通分镜放行、只查长度
        lim = _limit(args)
        bad = fxc.check_script(blocks, lim or 10 ** 9, require_tag=False)
        if bad:
            raise SystemExit("! 脚本质检没过，不提交合成：\n" + fxc.format_bad(bad))
        print("[tts] 质检通过：%d 段，格式合规%s"
              % (len(blocks), ("、正文均 <%d 字（含分隔句号）" % lim) if lim else ""), flush=True)
        # 分镜副本存 doc/，交付时别人只看项目目录也能知道这段音频哪来的
        # （脚本本来就在 doc/ 下时跳过，否则同文件复制会报 WinError 32）
        _dst = os.path.join(doc_dir, os.path.basename(args.script))
        if os.path.normcase(os.path.abspath(args.script)) != os.path.normcase(os.path.abspath(_dst)):
            shutil.copy2(args.script, _dst)
        # 段里带 `用…的语气说<|endofprompt|>` 就逐段用这个语气，没带就整条用音色默认语气
        # 序号从 01 开始（start=1 直接用 i；再加 1 会让文件名从 02 起）
        # cross_lingual：语气/方言标签必须留在正文里一起送（后端只补 You are a helpful assistant. 前缀）。
        # 拆出去当 instruct_text 单独传，cross_lingual 下会被 synthesize 清空，等于白写。
        tasks = []
        for i, (instruct, txt) in enumerate([fxc.parse_line(b) for b in blocks], start=1):
            if instruct:
                txt = instruct + fxc.MARK + "。" + txt   # 分隔句号必带：缺了首字发音会飘
            tasks.append((txt, os.path.join(audio_dir, prefix + _pad(i) + ext), instruct))
    else:
        raise SystemExit("! 给活干：--text / --text-file / --script（或先用 --check / --list 看环境）")

    results = []
    for i, (txt, path, instruct) in enumerate(tasks, start=1):
        try:
            results.append(_one(txt, sp, args, path, index=i, instruct=instruct))
        except Exception as e:
            print("[tts] ✗ %s 失败：%s" % (path, e), flush=True)
            results.append({"ok": False, "path": os.path.abspath(path), "text": txt, "error": str(e)})

    # 分段响度归一化：全部生成完先拉齐各段音量，再拼接/交付（已处理的带 .norm 标记跳过）
    loudnorm_all([r["path"] for r in results if r.get("ok")])

    joined = None
    if (auto_join or args.join_all) and len(results) > 1 and all(r.get("ok") for r in results):
        joined = tc.join_wavs([r["path"] for r in results],
                              args.out or os.path.join(audio_dir, prefix + ext))
        total = tc.wav_seconds(joined)
        results.append({"index": 0, "ok": True, "path": joined, "joined": True,
                        "duration": total, "segments": len(results), "text": ""})
        print("[tts] 已拼成一条：%s（%.2fs）" % (joined, total or 0), flush=True)

    # 失败的那条不进交付清单（清单只记能交付的东西）
    items = [_item_of(root, r) for r in results if r.get("ok")]
    voice = {"speaker_id": args.speaker or "",
             "mode": "cross_lingual",
             "ref_audio": (args.ref_audio or (sp or {}).get("ref_audio") or ""),
             "qwen_speaker": (args.qwen_speaker or (sp or {}).get("qwen_speaker") or ""),
             "route": ("voice_clone" if (args.ref_audio or (sp or {}).get("ref_audio"))
                       else "custom_voice"),
             "instruct_text": args.instruct_text or (sp or {}).get("instruct_text") or "",
             "speed": args.speed or (sp or {}).get("speed") or tc.tts_cfg().get("default_speed", 1.0),
             "speed_mode": tc.engine_speed_mode(args.engine),
             "seed": args.seed if args.seed is not None else tc.tts_cfg().get("default_seed")}
    if items:
        svc_url = next((r.get("url") for r in results if r.get("ok")), "")
        if name:
            _write_delivery(args, name, root, doc_dir, items, voice, None, joined, svc_url)
        elif args.manifest or args.script:
            mf = os.path.join(audio_dir, "manifest.json")
            with open(mf, "w", encoding="utf-8") as f:
                json.dump({"voice": voice, "items": items}, f, ensure_ascii=False, indent=2)
            print("[tts] manifest：%s" % mf, flush=True)

    ok_n = sum(1 for r in results if r.get("ok"))
    print("[tts] 完成 %d/%d，音频 %d 条" % (ok_n, len(results), len(items)), flush=True)
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    if ok_n != len(results):
        raise SystemExit(2)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[tts] 已中断", flush=True)
        sys.exit(130)
