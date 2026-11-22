# -*- coding: utf-8 -*-
"""编排入口：选人(角色库) + 选服装/姿势(登记表) → 提示词 → 场景工作流。

边界：本脚本只“组织”，不内嵌生图逻辑、不下载、不搬图。
- --dry-run：只打印将提交的提示词与参考图路径，不调用工作流（默认建议先跑）。
- 真出图：交给 fxai-scene-gen.gen_scene_image() → fxai-workflow(scene→img-qwen21-process.json)，
  下游 allow_no_outfit=True、尺寸走 default_max_side() 档位；这些是下游职责，这里只备好入参。
"""
import os
import sys
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import prompt_builder as PB

SKILLS = os.path.normpath(os.path.join(_HERE, "..", ".."))


def _lookup_asset(asset_id, field="图片"):
    """从登记表按 id 取路径；asset_id 也可直接是路径。"""
    if os.sep in asset_id or "/" in asset_id:
        return asset_id
    import csv
    p = os.environ.get("FXAI_POSE_COSTUME_DATA") or os.path.join(_HERE, "..", "data", "assets.csv")
    if os.path.isdir(p):
        p = os.path.join(p, "assets.csv")
    with open(p, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("id") == asset_id:
                return r.get(field, "")
    raise SystemExit("[compose] 登记表未找到 id: " + asset_id)


def compose(portrait_path, costume_id, pose_id, style, pose_name="", angle="",
            costume_name="", scene="", dry=True):
    costume_path = _lookup_asset(costume_id) if costume_id else ""
    pose_path = _lookup_asset(pose_id) if pose_id else ""

    portrait = {"source": "upload"} if portrait_path else None
    costume = {"source": "library", "服装名称": costume_name} if costume_path else None
    pose = {"source": "library", "姿势名称": pose_name, "角度": angle} if pose_path else None

    prompt = PB.build_recipe_prompt(
        portrait=portrait, costume=costume, pose=pose,
        style=style, extra_scene=scene,
    )

    refs = [x for x in (portrait_path, costume_path, pose_path) if x]
    plan = {
        "参考图顺序": refs,
        "风格": style,
        "提示词": prompt,
        "下游": "fxai-scene-gen.gen_scene_image → fxai-workflow(scene→img-qwen21-process.json)",
        "下游固定参数": {"allow_no_outfit": True, "max_side": "default_max_side()", "本机": 960},
    }

    if dry:
        print("===== DRY RUN：仅预览，不调用工作流 =====")
        for k, v in plan.items():
            if isinstance(v, list):
                v = "\n  ".join([""] + v)
            print("%s: %s" % (k, v))
        return plan

    # 真出图：不在本脚本实现，显式交给下游（保持职责边界）。
    raise SystemExit(
        "[compose] 真出图请调用 fxai-scene-gen.gen_scene_image(prompt, role=','.join(refs), ...)。"
        "本脚本只组织入参，不内嵌生图逻辑。"
    )


def _cli():
    ap = argparse.ArgumentParser(description="角色造型编排（dry-run 默认）")
    ap.add_argument("--人物", dest="portrait", default="", help="人物参考图路径（来自角色库）")
    ap.add_argument("--服装", dest="costume", default="", help="服装 id 或路径")
    ap.add_argument("--服装名", dest="costume_name", default="")
    ap.add_argument("--姿势", dest="pose", default="", help="姿势 id 或路径")
    ap.add_argument("--姿势名", dest="pose_name", default="")
    ap.add_argument("--角度", dest="angle", default="")
    ap.add_argument("--风格", dest="style", default="realistic")
    ap.add_argument("--场景", dest="scene", default="", help="卧姿承托场景行（仅环境，用户认可才加）")
    ap.add_argument("--dry-run", dest="dry", action="store_true")
    ap.add_argument("--run", dest="dry", action="store_false", help="真出图（交给下游实现）")
    ap.set_defaults(dry=True)
    a = ap.parse_args()
    compose(a.portrait, a.costume, a.pose, a.style, a.pose_name, a.angle,
            a.costume_name, a.scene, dry=a.dry)


if __name__ == "__main__":
    _cli()
