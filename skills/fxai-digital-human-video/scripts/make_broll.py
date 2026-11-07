# -*- coding: utf-8 -*-
"""按分镜表里的 broll 项生成**动态**操作空镜（脚本只执行，不替模型做创意决策）。

分镜表每镜可以带一条 `broll`：
  {"what": "中景，桌前双手在键盘上快速敲击，屏幕命令行一行行上滚",
   "dynamics": "键帽随按下弹起，光标在屏上移动",
   "size": "中景", "start": 31.5, "dur": 4.5}

本脚本做四件事：
  1. 把 `what` 扩成生图提示词（中景构图、带持续动作、场景上下文完整，画面是正在操作的双手而非静物）
  2. 出图 -> 用 gen_video 的 single 模式（纯场景参考）把这张图**动起来**
  3. 配一段同长静音音频（最终成片用主音频覆盖，这里只是给 H3 一个输入）
  4. 汇总成 insert_broll 用的 spec

🔴 为什么走视频模型而不是 ffmpeg 滤镜：
之前用 make_material_seg 给静图加 zoom-in，那只是「一张静图 + 镜头缓推」，
手和物件都不会动，观众一眼看出是死图。要让键盘真的被敲、纸真的被翻，
必须走视频模型——与产线一致用 **multiref**，人物参考走默认人物库、场景参考用刚出的操作图，
提示词锁死「只有双手在桌面操作、身体头部在画面外」，multiref 注入的人物参考只作为
「手的主人」参考，不会画成整个人入场。

用法：
  python scripts/make_broll.py --project <项目> --storyboard <项目>/image/storyboard.json
"""
import argparse
import io
import json
import os
import subprocess
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.dirname(os.path.dirname(HERE))          # skills/
for p in (os.path.join(SK, "fxai-scene-gen"), os.path.join(SK, "fxai-audio-video"),
          os.path.join(SK, "fxai-digital-human-video")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scene_gen import gen_scene_image                # noqa: E402
import audio_video as av                             # noqa: E402


# 操作空镜的硬约束：中景、看得见场景上下文、动作在持续进行。
# 不写「人物」「站」「坐」这类会把人画出来的词——画面里只要手和场景。
BROLL_GUARD = ("，中景构图，画面里能看见桌面与周边设备和场景上下文，"
               "动作持续进行，画面里没有可读文字，没有出现完整的人脸和躯干")


def make_silence(path, dur, sr=24000):
    n = int(dur * sr)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)


def main():
    ap = argparse.ArgumentParser(description="按分镜表生成动态 B-roll 操作空镜")
    ap.add_argument("--project", required=True)
    ap.add_argument("--storyboard", required=True)
    ap.add_argument("--size", default="768x1280")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--tag", default="broll", help="缓存 tag 前缀")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    proj = a.project
    sb = json.load(io.open(a.storyboard, encoding="utf-8"))
    shots = sb.get("shots") or []
    items = [(s["i"], s["broll"]) for s in shots if s.get("broll")]
    if not items:
        print("[broll] 分镜表里没有 broll 项，跳过", flush=True)
        return

    img_dir = os.path.join(proj, "image")
    tmp_dir = os.path.join(proj, "_tmp")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    W, H = (int(x) for x in a.size.lower().split("x"))
    spec = []
    for n, (idx, b) in enumerate(items, 1):
        what = (b.get("what") or "").strip()
        if not what:
            print("[broll] 第 %s 镜 broll 没写 what，跳过" % idx, flush=True)
            continue
        # 生图提示词：what（已含中景与动作）+ 硬约束
        prompt = what.rstrip("。") + BROLL_GUARD
        tag = "%s_%02d" % (a.tag, n)
        print("[broll] 第 %s 镜：%s" % (idx, what), flush=True)
        try:
            p, rel = gen_scene_image(prompt, role=None, w=1080, h=1920,
                                     tag=tag, subdir="dh_broll", num=n - 1,
                                     timeout=1200, force=True)
        except Exception as e:
            print("[broll] 第 %s 镜出图失败：%s" % (idx, e), flush=True)
            continue
        img = os.path.join(img_dir, "broll_%02d.png" % n)
        import shutil
        shutil.copyfile(p, img)

        dur = float(b.get("dur") or 4.5)
        # 静音音频给 H3 当输入（最终成片用主音频覆盖，这里只是驱动生成）
        sil = os.path.join(tmp_dir, "broll_sil_%02d.wav" % n)
        make_silence(sil, dur + 0.4)

        # 🔴 动态段：multiref（与产线一致），人物参考走默认人物库，场景参考用刚出的操作图。
        # 提示词锁死「只有双手在桌面操作、身体头部在画面外」——multiref 注入的人物参考
        # 只作为「手的主人」参考，不会画成整个人入场。
        dyn = b.get("dynamics") or ""
        seg_prompt = (what.rstrip("。") + "；" + dyn
                      + "；画面里只有一双手在桌面操作，手臂从画面下缘伸入，"
                        "身体和头部都在画面外，不出现人脸和全身；动作持续进行")
        seg = os.path.join(tmp_dir, "broll_%02d.mp4" % n)
        try:
            av.gen_video("multiref",
                         aud_rel=os.path.abspath(sil),
                         char_rel=av.REF_WOMAN,
                         scene_ref=rel,
                         scene_desc=(b.get("size") or "中景") + "，" + what,
                         seg_prompts=[seg_prompt],
                         kind="speak",
                         tag="%s_%02d" % (a.tag, n),
                         timeout=1200,
                         allow_no_outfit=True,
                         out_dir=tmp_dir)
        except Exception as e:
            print("[broll] 第 %s 镜生成动态段失败：%s" % (idx, e), flush=True)
            continue
        spec.append({"video": seg, "start": float(b.get("start") or 0), "dur": dur})
        print("[broll] 第 %s 镜 -> %s（%.1fs）" % (idx, seg, dur), flush=True)

    out_spec = os.path.join(proj, "_tmp", "broll.json")
    io.open(out_spec, "w", encoding="utf-8").write(
        json.dumps(spec, ensure_ascii=False, indent=1))
    print("[broll] 共 %d 段，spec -> %s" % (len(spec), out_spec), flush=True)
    print("[broll] 下一步：python scripts/insert_broll.py --video <成片> "
          "--out <成片_b> --spec %s" % out_spec, flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
