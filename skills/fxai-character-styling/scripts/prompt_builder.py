# -*- coding: utf-8 -*-
"""提示词构建器：逐字对应 aiGenerator.ts 的 buildRecipePrompt()。

用户硬规矩：严格照抄仓库写法，不自行发挥。
- 不写穿搭文字；服装靠参考图，名称只在括号作辅助说明。
- 图1/图2/图3 索引按实际传入顺序动态生成。
- 选项 extra_scene 是本地认可的卧姿承托补丁（场景≠穿搭），默认关闭。
"""
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
STYLES_PATH = os.path.join(_HERE, "..", "data", "styles.json")

PHOTO_STYLES = {"realistic", "fashion", "film"}


def _load_styles():
    with open(STYLES_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_recipe_prompt(portrait=None, costume=None, pose=None, style="realistic",
                        extra_images=None, workflow_images=None, extra_scene=""):
    """对应 buildRecipePrompt(recipe, stylePreset, workflowImages)。

    portrait / costume / pose 均为 dict 或 None：
      portrait: {source:'upload'|'library', 发长, 发型名称, 标题}
      costume : {source:'upload'|'library', 服装名称}
      pose    : {source:'upload'|'library', 姿势名称, 角度}
    extra_images / workflow_images: [{name, role}]
    extra_scene: 卧姿承托场景行（仅环境，不含动作词），可选。
    """
    extra_images = extra_images or []
    workflow_images = workflow_images or []
    styles = _load_styles()
    style_info = styles.get(style) or styles["realistic"]
    is_photo = style in PHOTO_STYLES
    faceless = (not portrait) and not any(
        im.get("role") == "character_sheet" for im in workflow_images
    ) and bool(costume and pose)

    clauses = []
    idx = 1
    costume_idx = 0

    for _ in [im for im in workflow_images if im.get("role") == "character_sheet"]:
        clauses.append(
            "• 角色设定参考：提供的设定集展示同一角色的多个视角与细节，不是多个不同人物。"
            "保持角色的脸型、五官、发型、发色、肤色及服装配饰一致；只把它作为角色参考，"
            "成品布局遵循当前工作流的输出设置。"
        )
        idx += 1

    if portrait:
        hair_name = portrait.get("发型名称") or ""
        hair_desc = portrait.get("标题") if hair_name and hair_name != "标准基础款" else portrait.get("发长", "")
        skin = ("人物的肤色必须与图一人物一致，所有裸露皮肤（包括服装镂空处露出的皮肤）"
                "必须与脸部肤色统一，统一后的肤色与质感按风格要求呈现。")
        outfit = "" if costume else "保留图1人物原有可见服装与配饰，不复制姿势参考图的服装或白模外观。"
        if portrait.get("source") == "upload":
            clauses.append(f"• 人物：图{idx}中的人物，保持其脸型、五官、发型和发色一致。{skin}{outfit}")
        else:
            clauses.append(
                f"• 人物：图{idx}中的人物，保持其脸型、五官、发型（{hair_desc}）和发色一致。{skin}{outfit}"
            )
        idx += 1

    if costume:
        costume_idx = idx
        name = costume.get("服装名称", "")
        if costume.get("source") == "upload":
            clauses.append(
                f"• 服装：让人物穿上图{idx}中展示的整套服装和配饰，"
                f"每件单品的颜色、款式、面料与配饰都以图{idx}为准。"
            )
        else:
            clauses.append(
                f"• 服装：让人物穿上图{idx}中展示的整套服装和配饰（参考名称：{name}），"
                f"每件单品的颜色、款式、面料与配饰都以图{idx}为准，名称仅作辅助说明。"
            )
        idx += 1

    if pose:
        wear = f"，穿着图{costume_idx}的服装" if costume_idx else ""
        if is_photo:
            body = f"最终画面中的人物是真人，皮肤真实自然{wear}"
        else:
            body = f"人物的肤色与质感按风格要求呈现{wear}"
        pname = pose.get("姿势名称", "")
        angle = pose.get("角度", "")
        if pose.get("source") == "upload":
            clauses.append(
                f"• 姿势：按照图{idx}中的人物动作和镜头角度摆姿势；"
                f"仅参考姿势与构图，不复制图{idx}人物的外观与服装，{body}。"
            )
        else:
            clauses.append(
                f"• 姿势：按照图{idx}中人体模型的动作和镜头角度摆姿势（{pname}，{angle}）；"
                f"图{idx}只是动作示意，{body}。"
            )
        idx += 1

    if faceless:
        clauses.append(
            "• 无脸展示：生成一位穿着参考服装的成年展示人物，脸部保持无五官的自然肤色表面，"
            "不添加眼睛、鼻子、嘴巴或具体人物身份；全身裸露皮肤的颜色统一，"
            "不照搬服装图或姿势图的灰白人台材质。"
        )

    for reference in [im for im in workflow_images if im.get("role") != "character_sheet"]:
        rname = reference.get("name") or "补充参考"
        clauses.append(f"• 工作流参考：参考图{idx}（{rname}）中的相关元素，具体用途以工作流要求为准。")
        idx += 1

    for extra in extra_images:
        ename = extra.get("name") or "补充元素"
        clauses.append(f"• 附加参考：融合图{idx}（{ename}）中的元素、配饰、背景或光影细节。")
        idx += 1

    if not clauses:
        return "请先选择人像/服装/姿势或上传附加参考图。"

    char_sheet = any(im.get("role") == "character_sheet" for im in workflow_images)
    ref_phrase = "提供的角色设定参考" if char_sheet else f"提供的 {idx - 1} 张参考图"
    subject = "人物穿搭展示图" if faceless else ("真实人物照片" if is_photo else "人物图像")
    lines = [f"请根据{ref_phrase}，生成一张完整的{subject}：", *clauses]

    if style_info.get("prompt"):
        tail = ("人物比例协调，手部自然，脸部无五官，光影统一。" if faceless
                else "人物比例协调，手部与五官自然，光影统一。")
        lines.append(f"• 风格：{style_info['prompt']} {tail}")
    if extra_scene:
        # 卧姿承托补丁：仅环境描述，不含动作词。用户认可后才传。
        lines.append(f"• 场景：{extra_scene}")
    lines.append("画面中没有任何文字、标志或水印。")
    return "\n".join(lines)


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="照抄仓库写法的提示词构建器")
    ap.add_argument("--人物", dest="portrait", action="store_true", help="有人物参考图（图1）")
    ap.add_argument("--人物发长", dest="hair", default="")
    ap.add_argument("--人物标题", dest="hair_title", default="")
    ap.add_argument("--服装", dest="costume", action="store_true", help="有服装参考图（图2）")
    ap.add_argument("--服装名", dest="costume_name", default="")
    ap.add_argument("--姿势", dest="pose", action="store_true", help="有姿势参考图（图3）")
    ap.add_argument("--姿势名", dest="pose_name", default="")
    ap.add_argument("--角度", dest="angle", default="")
    ap.add_argument("--风格", dest="style", default="realistic")
    ap.add_argument("--场景", dest="scene", default="", help="卧姿承托场景行（仅环境）")
    a = ap.parse_args()

    portrait = ({"source": "upload", "发长": a.hair, "标题": a.hair_title}
                if a.portrait else None)
    costume = ({"source": "library", "服装名称": a.costume_name} if a.costume else None)
    pose = ({"source": "library", "姿势名称": a.pose_name, "角度": a.angle} if a.pose else None)
    print(build_recipe_prompt(portrait, costume, pose, a.style, extra_scene=a.scene))


if __name__ == "__main__":
    _cli()
