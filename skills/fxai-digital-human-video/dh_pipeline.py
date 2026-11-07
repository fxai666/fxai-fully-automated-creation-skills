# -*- coding: utf-8 -*-
# =====================================================================
# fxai-digital-human-video —— 数字人口播「整套任务」入口
#
# 一条命令跑完：原始口播文案 → 加情感/格式标签 → 逐段音频 → 合并音频
#            → 四视图 + 场景图 → 竖屏视频 → 烧字幕成片。
#
#   1) 标签   fxai-voiceover-script（skill 2）→ doc/口播文案_标签.md
#   2) 音频   fxai-voiceover        （skill 3）→ music/口播NN.wav…
#   3) 视频   dh_video.py           （本技能）→ 合并音频 / 四视图 / 场景图 / 视频 / 成片
#
# 产物全部落 <项目根>/<项目名>/（项目根 = 当前工作区）。
# 交付文件名见 doc/交付清单.md；视频带分辨率后缀，字幕不带。
# =====================================================================
import argparse
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILLS = os.path.dirname(_HERE)
for _dep in ("fxai-workflow", "fxai-audio-video", "fxai-scene-gen", "fxai-fourview-gen", "fxai-common"):
    _p = os.path.join(_SKILLS, _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)


AUDIO_EXT2 = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma")


def _step(n, total, title):
    print("\n" + "=" * 68, flush=True)
    print("[%d/%d] %s" % (n, total, title), flush=True)
    print("=" * 68, flush=True)


def _run(script, args, cwd=None):
    """跑一个技能脚本，失败即抛出（不吞错误）。"""
    cmd = [sys.executable, script] + args
    print("[dhp] $ %s %s" % (os.path.basename(script), " ".join(args)), flush=True)
    p = subprocess.run(cmd, cwd=cwd)
    if p.returncode != 0:
        raise SystemExit("[dhp] 失败（退出码 %d）：%s" % (p.returncode, os.path.basename(script)))


def build_parser():
    ap = argparse.ArgumentParser(
        description="数字人口播整套任务：文案加标签 → 音频 → 合并 → 四视图+场景图 → 竖屏视频 → 烧字幕")
    ap.add_argument("--project", required=True, help="项目名（产物落 <项目根>/<项目名>/）")
    ap.add_argument("--script", help="原始口播文案（不加标签的那份，如 口播文案.md）。不给就走「直接给音频」")
    ap.add_argument("--audio", action="append", default=[],
                    help="直接给音频（可多条），跳过加标签与合成；字幕内容由**语音识别**得出")
    ap.add_argument("--audio-dir", help="直接给音频目录（按序号排序），同上")
    ap.add_argument("--tag-file", help="标签文件（一行一个标签，按句逐句套用；不给则整篇默认标签）")
    ap.add_argument("--speaker", help="音色 id（默认取配置里第一个）")
    ap.add_argument("--char-desc", help="人物穿搭外貌（按文案内容设计；四视图用）")
    ap.add_argument("--role", default="woman", help="人物参考：woman|man|自定义相对路径")
    ap.add_argument("--scene-desc", help="场景描述，多张用 | 分隔；不给则按全文意境自由生成")
    ap.add_argument("--landscape", action="store_true", help="横屏输出（默认竖屏 768×1280）")
    ap.add_argument("--no-burn", action="store_true", help="不烧字幕（只出无字幕成片）")
    ap.add_argument("--no-charview", action="store_true", help="跳过四视图，直接用头像库作人物图")
    ap.add_argument("--no-local", action="store_true",
                    help="强制走线上容器（TTS 与视频都跳过本机探测，不碰本机盘）")
    ap.add_argument("--force", action="store_true", help="已有产物也重跑（默认复用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令，不实际运行")
    return ap


def _from_audio(a):
    """直接给音频的入口：合并 → 四视图 + 场景图 → 视频 → 烧字幕（字幕由语音识别）。

    没有文案，所以字幕文字走 whisper 识别；识别结果另存 `语音识别稿.md` 便于人工校对。
    """
    import output_layout as ol
    _step(1, 2, "直接给音频：跳过加标签与合成")
    root = ol.layout(a.project)["root"]
    auds = list(a.audio)
    if a.audio_dir:
        d = os.path.abspath(a.audio_dir)
        auds += [os.path.join(d, f) for f in sorted(os.listdir(d))
                 if f.lower().endswith(AUDIO_EXT2) and not f.startswith("口播音频")]
    auds = [os.path.abspath(p) for p in auds if os.path.isfile(p)]
    if not auds:
        raise SystemExit("[dhp] 没有可用音频（--audio / --audio-dir）")
    print("[dhp] 音频 %d 条：%s" % (len(auds), "、".join(os.path.basename(p) for p in auds[:5])), flush=True)

    _step(2, 2, "合并音频 → 四视图 + 场景图 → 竖屏视频 → 烧字幕（字幕走语音识别）")
    a3 = ["--project", a.project, "--mode", "multiref", "--role", a.role]
    for p in auds:
        a3 += ["--audio", p]
    if a.char_desc:
        a3 += ["--char-desc", a.char_desc]
    if a.scene_desc:
        a3 += ["--scene-desc", a.scene_desc]
    if a.landscape:
        a3 += ["--multiref-landscape"]
    if a.no_charview:
        a3 += ["--no-charview"]
    if not a.no_burn:
        a3 += ["--burn"]
    if a.no_local:
        a3 += ["--no-local"]
    if a.force:
        a3 += ["--force"]
    if a.dry_run:
        print("[dhp] $ dh_video.py %s" % " ".join(a3))
        return 0
    _run(os.path.join(_HERE, "dh_video.py"), a3)
    print("\n" + "=" * 68, flush=True)
    print("[dhp] 完成。产物在：%s" % root, flush=True)
    print("[dhp] 字幕文字来自语音识别，核对/改字请看 语音识别稿.md", flush=True)
    print("=" * 68, flush=True)
    return 0


def main():
    a = build_parser().parse_args()
    if not a.script and not a.audio and not a.audio_dir:
        raise SystemExit("[dhp] 给活干：--script <原始口播文案>（自动加标签+合成音频）"
                         " 或 --audio <音频> / --audio-dir <目录>（直接用现成音频）")
    if a.script and (a.audio or a.audio_dir):
        raise SystemExit("[dhp] --script 与 --audio/--audio-dir 只能给一种"
                         "（前者会自动合成音频，后者是你自己的音频）")

    # ===== 直接给音频：跳过加标签与合成，字幕内容由语音识别得出 =====
    if a.audio or a.audio_dir:
        return _from_audio(a)

    if not os.path.isfile(a.script):
        raise SystemExit("[dhp] 找不到文案：%s" % a.script)

    import output_layout as ol
    root = ol.layout(a.project)["root"]   # 交付物扁平：都在项目根
    doc = root

    # 原始文案归档到项目 doc/（按约定：口播文案.md = 无标签原文）
    raw = os.path.join(root, "口播文案.md")
    if os.path.abspath(a.script) != os.path.abspath(raw):
        with open(a.script, encoding="utf-8") as f:
            body = f.read()
        with open(raw, "w", encoding="utf-8") as f:
            f.write(body)
        print("[dhp] 原始文案已归档：%s" % raw, flush=True)

    s_script = os.path.join(_SKILLS, "fxai-voiceover-script", "script_gen.py")
    s_voice = os.path.join(_SKILLS, "fxai-voiceover", "voice_gen.py")
    s_video = os.path.join(_HERE, "dh_video.py")

    # ---------- 1) 文案（加不加标签由引擎白名单决定：只有 CosyVoice 走标签格式）----------
    sys.path.insert(0, os.path.join(_SKILLS, "fxai-voiceover"))
    import tts_client as tc
    engine = tc.default_engine()
    _step(1, 3, "口播文案（引擎 %s）" % engine)
    a1 = ["--project", a.project, "--text-file", raw, "--engine", engine]
    if a.tag_file:
        a1 += ["--tag-file", a.tag_file]
    if a.dry_run:
        print("[dhp] $ script_gen.py %s" % " ".join(a1))
    else:
        _run(s_script, a1)
    # 标签产物由 script_gen.py 写在 <项目根>/doc/（过程文件归 doc/），与它保持一致
    docdir = ol.layout(a.project)["doc"]
    tagged = os.path.join(docdir, "口播文案_标签.md")
    if not os.path.isfile(tagged):
        raise SystemExit("[dhp] 标签产物不存在：%s" % tagged)

    # ---------- 2) 音频 ----------
    _step(2, 3, "逐段生成口播音频（+ 交付清单）")
    a2 = ["--project", a.project, "--script", tagged]
    if a.speaker:
        a2 += ["--speaker", a.speaker]
    if a.no_local:
        a2 += ["--no-local"]
    if a.force:
        a2 += ["--force"]
    if a.dry_run:
        print("[dhp] $ voice_gen.py %s" % " ".join(a2))
    else:
        _run(s_voice, a2)

    # ---------- 3) 四视图 + 场景图 + 视频 + 字幕 ----------
    _step(3, 3, "合并音频 → 四视图 + 场景图 → 竖屏视频 → 烧字幕")
    # 分段音频按序号显式列出（排除合并产物 口播音频.wav，否则会被当成第 N 段重复合并）
    music = root
    segs = sorted(f for f in os.listdir(music)
                  if f.lower().endswith(AUDIO_EXT2) and not f.startswith("口播音频"))
    if not segs:
        raise SystemExit("[dhp] music/ 下没有分段音频，先跑第 2 步")
    a3 = ["--project", a.project, "--script", tagged, "--mode", "multiref",
          "--role", a.role]
    for f in segs:
        a3 += ["--audio", os.path.join(music, f)]
    if a.char_desc:
        a3 += ["--char-desc", a.char_desc]
    if a.scene_desc:
        a3 += ["--scene-desc", a.scene_desc]
    if a.landscape:
        a3 += ["--multiref-landscape"]
    if a.no_charview:
        a3 += ["--no-charview"]
    if not a.no_burn:
        a3 += ["--burn"]
    if a.no_local:
        a3 += ["--no-local"]
    if a.force:
        a3 += ["--force"]
    if a.dry_run:
        print("[dhp] $ dh_video.py %s" % " ".join(a3))
    else:
        _run(s_video, a3)

    print("\n" + "=" * 68, flush=True)
    print("[dhp] 全流程完成。产物在：%s" % ol.project(a.project), flush=True)
    print("[dhp] 视频/成片在 video/，音频在 music/，清单与提示词在 doc/", flush=True)
    print("=" * 68, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
