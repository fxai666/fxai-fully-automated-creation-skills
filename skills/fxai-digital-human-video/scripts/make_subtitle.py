#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数字人口播字幕：调 fxai-lyric-subtitle 烧录（通用铁律 #8）。

── 规范（已落地）──
· 字幕必走 fxai-lyric-subtitle，不自写 ASS/SRT 样式（铁律 #8）。
· 有精确分段时长时用 --transcript 喂 _words.txt（时长轴，不跑 whisper）；
  _words.txt 格式每行：[起始->结束] 文字
· 输出：<out-dir>/成片_768x1280.mp4（带烧录字幕），与标题卡叠加同 timeline。

用法：
  python scripts/make_subtitle.py --project <项目> \
      --merged 合并_768x1280.mp4 --lyrics 口播文案.md --transcript _words.txt
"""
import argparse, os, subprocess, sys

_SKILLS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SUB_SKILL = os.path.join(_SKILLS, "fxai-lyric-subtitle", "scripts", "gen_lyric_subtitle.py")


def run(cmd, env=None):
    print("$", " ".join(str(c) for c in cmd[:6]), "...", flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    if p.returncode != 0:
        print(p.stdout[-1500:]); print(p.stderr[-2500:])
        raise SystemExit("命令失败: %s" % cmd[0])


def make_subtitle(project, merged, lyrics, transcript, out_dir, out_name):
    if not os.path.isfile(SUB_SKILL):
        sys.exit("找不到字幕技能 %s" % SUB_SKILL)
    env = dict(os.environ); env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    run([sys.executable, SUB_SKILL,
         "--video", merged, "--lyrics", lyrics, "--transcript", transcript,
         "--out-dir", out_dir, "--out-name", out_name], env=env)
    burned = os.path.join(out_dir, out_name + ".mp4")
    final = os.path.join(out_dir, "成片_768x1280.mp4")
    if os.path.isfile(burned):
        os.replace(burned, final)
    print("字幕烧录完成 ->", final)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--merged", default=None)
    ap.add_argument("--lyrics", default="口播文案.md")
    ap.add_argument("--transcript", default="_words.txt")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--out-name", default="成片")
    a = ap.parse_args()
    proj = a.project
    merged = a.merged or os.path.join(proj, "合并_768x1280.mp4")
    out_dir = a.out_dir or proj
    make_subtitle(proj, merged, os.path.join(proj, a.lyrics),
                  os.path.join(proj, a.transcript), out_dir, a.out_name)


if __name__ == "__main__":
    main()
