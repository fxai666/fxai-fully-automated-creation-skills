#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""场景 prompt 清单 → scene_rels.json（数字人视频场景映射）。

── 规范（已落地）──
· 空实景、只写环境、禁文字、禁主角本人（铁律 #3）；按节拍语义焦点配景。
· scenes.txt：一行一条场景描述（顺序 = 节拍序）。
· --scene-refs：可选，服务端场景图相对路径（如 dh_scene/000.png,...），
  与 scenes.txt 一一对应；不传则只落 desc。

用法：
  python scripts/gen_scenes.py --project <项目> --prompts scenes.txt \
      --scene-refs "dh_scene/000.png,dh_scene/001.png,..."
"""
import argparse, json, os


def gen_scenes(project, prompts, scene_refs):
    lines = [l.strip() for l in open(os.path.join(project, prompts), encoding="utf-8") if l.strip()]
    refs = [r.strip() for r in scene_refs.split(",")] if scene_refs else []
    rels = []
    for i, desc in enumerate(lines):
        rel = {"beat": i + 1, "desc": desc}
        if i < len(refs):
            rel["rel"] = refs[i]
        rels.append(rel)
    with open(os.path.join(project, "scene_rels.json"), "w", encoding="utf-8") as f:
        json.dump(rels, f, ensure_ascii=False, indent=1)
    print("场景映射 %d 条 -> %s/scene_rels.json" % (len(rels), project))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--prompts", default="scenes.txt")
    ap.add_argument("--scene-refs", default="")
    a = ap.parse_args()
    gen_scenes(a.project, a.prompts, a.scene_refs)


if __name__ == "__main__":
    main()
