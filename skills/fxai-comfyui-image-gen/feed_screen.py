# -*- coding: utf-8 -*-
"""视频信息流：把结构化 spec 按六段固定顺序拼成中文提示词并提交生图。

段序、UI 五区判据与验收项见 references/06-视频信息流.md。
"""
import sys
sys.dont_write_bytecode = True

import argparse
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from image_gen import generate_image

# 画布比例 → 像素（32 的整数倍，落 512~4096）
SIZE = {
    "9:16": (864, 1536),
    "2:3": (832, 1248),
    "3:4": (1080, 1440),
    "1:1": (1024, 1024),
    "4:3": (1440, 1080),
    "16:9": (1536, 864),
}

RATIO_CN = {
    "9:16": "竖幅九比十六",
    "2:3": "竖幅二比三",
    "3:4": "竖幅三比四",
    "1:1": "方形一比一",
    "4:3": "横幅四比三",
    "16:9": "横幅十六比九",
}

# UI 五区固定顺序（横排从左到右，竖排自上而下）
UI_ORDER = ("状态栏", "导航栏", "右侧互动栏", "底部导航", "左下文案")

SPEC_FIELDS = {
    "画布": "比例字符串，取 SIZE 的键，缺省 9:16",
    "质感": "摄影质感总纲：真实度 / 灰度 / 锐度 / 即时感，必填",
    "构图": "list[str]：主体方位、占画面比例、朝向、前中后三层各由什么填充",
    "界面": "dict：键取 UI_ORDER 之一，值为 list[str]，区内按屏幕实际顺序排",
    "主体": "dict：描述 / 发型 / 穿搭 必填，姿态 / 道具 / 性别 选填（性别取 woman/man/both/None）",
    "场景": "list[str]：地点、承托物材质与图案、邻位物件、窗外或远景",
    "光影": "list[str]：光源方向、受光面、阴影面、色温基调、氛围",
}


def _clause(items):
    """把列表或字符串收成以「；」分隔的一句。"""
    if isinstance(items, str):
        items = [items]
    return "；".join(str(x).strip() for x in items if str(x).strip())


def build_prompt(spec):
    """按六段固定顺序拼出提示词。"""
    ratio = str(spec.get("画布", "9:16"))
    if ratio not in SIZE:
        raise ValueError("画布取 %s 之一，收到 %r" % ("/".join(SIZE), ratio))

    texture = str(spec.get("质感", "")).strip()
    if not texture:
        raise ValueError("spec 缺【质感】：写真实度、灰度、锐度、即时感")

    subject = spec.get("主体") or {}
    for k in ("描述", "发型", "穿搭"):
        if not str(subject.get(k, "")).strip():
            raise ValueError("spec 缺【主体.%s】：%s" % (k, SPEC_FIELDS["主体"]))

    parts = ["%s手机应用全屏截图，%s。" % (RATIO_CN[ratio], texture)]

    # 二 构图落位
    comp = spec.get("构图")
    if comp:
        parts.append("【构图】%s。" % _clause(comp))

    # 三 UI 五区
    ui = spec.get("界面") or {}
    for key in UI_ORDER:
        if ui.get(key):
            parts.append("【UI·%s】%s。" % (key, _clause(ui[key])))

    # 四 主体人物
    subj = []
    if str(subject.get("描述", "")).strip():
        subj.append(str(subject["描述"]).strip())
    subj.append("【头型/发型】%s" % str(subject["发型"]).strip())
    subj.append("【穿搭/服装】%s" % str(subject["穿搭"]).strip())
    if str(subject.get("姿态", "")).strip():
        subj.append(str(subject["姿态"]).strip())
    if str(subject.get("道具", "")).strip():
        subj.append(str(subject["道具"]).strip())
    parts.append("【主体】%s。" % "；".join(subj))

    # 五 场景环境
    scene = spec.get("场景")
    if scene:
        parts.append("【场景】%s。" % _clause(scene))

    # 六 光影色调
    light = spec.get("光影")
    if light:
        parts.append("【光影】%s。" % _clause(light))

    return "".join(parts)


def generate_screen(spec, tag, save_dir=None, role=None, **kw):
    """拼装并提交，返回本地 PNG 绝对路径。role 缺省取 主体.性别，再缺省 woman。"""
    prompt = build_prompt(spec)
    width, height = SIZE[str(spec.get("画布", "9:16"))]
    if role is None:
        role = (spec.get("主体") or {}).get("性别", "woman")
    return generate_image(prompt, role=role, width=width, height=height,
                          tag=tag, save_dir=save_dir, **kw)


def main():
    ap = argparse.ArgumentParser(description="视频信息流：spec → 中文提示词 → 提交生图")
    ap.add_argument("spec", help="spec.json 路径")
    ap.add_argument("--tag", default=None, help="输出文件名（不含扩展名）")
    ap.add_argument("--save-dir", default=None, help="成品落点，缺省当前工作目录")
    ap.add_argument("--role", default=None, help="woman/man/both/None，缺省取 主体.性别")
    ap.add_argument("--print-only", action="store_true", help="只打印拼好的提示词，不提交")
    args = ap.parse_args()

    with open(args.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)

    if args.print_only:
        print(build_prompt(spec))
        return

    tag = args.tag or str(spec.get("画布", "9:16")).replace(":", "x")
    path = generate_screen(spec, tag=tag, save_dir=args.save_dir, role=args.role)
    print("OK:", path)


if __name__ == "__main__":
    main()
