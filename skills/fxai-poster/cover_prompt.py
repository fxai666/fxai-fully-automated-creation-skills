# -*- coding: utf-8 -*-
"""封面版式模板 → 生图提示词（六段骨架）。

为什么要有这一层：千问2.1 原生吃中文、文字渲染是强项（`10` 册第一句），
封面上的字**默认就该由生图直接写在画面上**，靠 HTML 叠加层补字是次优解。
`cover_styles.py` 里的 8 种版式模板描述的是「字怎么排」，这一层负责把同一套
版式语言翻译进提示词的第三段「文字区块」，让生图模型按版式把字画上去。

    cover_styles.py  STYLES / vert_title  ← 版式定义（HTML 叠加与生图共用同一套语义）
            ↓ 翻译
    cover_prompt.py  LAYOUTS / build_prompt()
            ↓ 六段骨架提示词（文案进双引号、位置/字号层级/字色字体/容器逐条写清）
    poster.gen_image() → fxai-scene-gen → fxai-workflow HTTP

🔴 只有走「叠加层」路线时才出无字底图（`no_text=True`）；那是例外，不是默认。
🔴 提示词写法只在 `references/10-千问2.1提示词规范.md` 定，本文件不另立规范。
🔴 技能包只管结构与版式，画面内容一律由项目 `封面.json` 的 `gen_brief` 提供。

用法：
    python cover_prompt.py --project <作品目录> --size vert_9x16 --side right
    python cover_prompt.py --project <作品目录> --size vert_9x16 --no-text  # 叠加层专用
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# 取自 `10` 册尺寸表：宽高必须是 32 的整数倍（1080 会被向下取整成 1056）
GEN_PIXELS = {
    "vert_9x16": (864, 1536),
    "vert_3x4": (1056, 1440),
    "square_1x1": (1024, 1024),
    "horiz_16x9": (1536, 864),
    "wechat_235": (1216, 512),
}

RATIO_WORD = {
    "vert_9x16": "9:16 竖版画幅",
    "vert_3x4": "3:4 竖版画幅",
    "square_1x1": "1:1 方形画幅",
    "horiz_16x9": "16:9 横版画幅",
    "wechat_235": "2.35:1 横向宽幅",
}


def _side_words(side):
    """文字侧 → 一组方位词。生图时人物放反侧。"""
    if side == "right":
        return dict(text="右侧", corner="右上角", foot="底部居右",
                    foot_corner="右下角", subject="偏左",
                    empty="画面右侧是连续纯净的空景，占整幅约三成，颜色均匀、纹理极少，"
                          "作为文字区；人物与主要物体全部安排在左侧",
                    band="色带只覆盖画面右侧约三成宽度", deco="主标题右侧紧贴")
    return dict(text="左侧", corner="左上角", foot="底部居左",
                foot_corner="左下角", subject="偏右",
                empty="画面左侧是连续纯净的空景，占整幅约三成，颜色均匀、纹理极少，"
                      "作为文字区；人物与主要物体全部安排在右侧",
                band="色带只覆盖画面左侧约三成宽度", deco="主标题左侧紧贴")


def _short(s, limit=14):
    """生图文案要短要准（10 册）：超长收敛到分隔符处，不擅自改写内容。"""
    s = (s or "").strip()
    if len(s) <= limit:
        return s
    for sep in (" · ", "｜", " | ", "，", "、"):
        if sep in s:
            cut = s.split(sep)[0].strip()
            if 0 < len(cut) <= limit:
                return cut
    return s[:limit].strip()


def gen_texts(cfg):
    """四条封顶：主标 / 眉标 / 副标 / 底栏。项目可用 gen_text 单独指定生图短文案。"""
    t = cfg.get("gen_text") or {}
    return dict(
        title=_short(t.get("title") or cfg.get("title", ""), 7),
        badge=_short(t.get("badge") or cfg.get("kicker", "")),
        sub=_short(t.get("sub") or cfg.get("subtitle", "")),
        footer=_short(t.get("footer") or cfg.get("footer", "")),
        en=(t.get("en") or cfg.get("title_en", "")).strip(),
    )


# ---- 版式模板 → 第三段「文字区块」------------------------------------------
# 每条都写清：引号文案 + 位置 + 字号层级 + 颜色 + 字体风格 + 排版容器。
# 引号内 ≤4 条、单条 ≤14 字，主标 ≤7 字（`10` 册硬规则）。

def _vert_title_texts(t, side, w, h):
    """竖排贴边样式：竖排主标 + 竖线装饰 + 眉标 + 副标 + 底栏。"""
    p = _side_words(side)
    lines = [
        '画面%s留白区，一条自上而下的竖排主标，用米白色宋体大字写"%s"，'
        '每个字竖排成一列，字号占画面高度的八分之一，字距均匀，笔画清晰端正；'
        % (p["text"], t["title"]),
        '%s一条细金色竖线作为装饰线，与主标等高，线宽约两个字宽的十分之一；'
        % p["deco"],
        '文字区垫一层自上而下渐深的深蓝黑半透明色带，让米白字与金色字在天空上清晰可读，'
        '%s，不遮住人物；' % p["band"],
    ]
    if t["badge"]:
        lines.append('画面%s一枚小字眉标，金色粗黑体横排一行写"%s"，'
                     '字号约为主标的四分之一，字母间距拉开，不折行；'
                     % (p["corner"], t["badge"]))
    if t["sub"]:
        lines.append('画面%s，浅米白色小字一行写"%s"，字号约为主标的一半，'
                     '与主标在同一条竖轴上对齐；' % (p["foot"], t["sub"]))
    if t["footer"]:
        lines.append('画面%s，金色最小号小字一行写"%s"，字号比副标再小一号，'
                     '作为底栏署名；' % (p["foot_corner"], t["footer"]))
    # 🔴 实测坑：装饰性英文字母若不写进引号给逐字文案，千问2.1 会自造乱码
    #    （本作曾出 "PUNUHGM TNE MNEKE"）。文案要上画面必须进引号，否则一律不写。
    #    竖版底部已有副标 + 底栏两行，再加一行只会挤糊，直接不生成。
    return lines


def _generic_texts(t, side, w, h, hint):
    """其余版式：按模板提示给主标/眉标/副标/底栏落位。"""
    p = _side_words(side)
    lines = [
        '画面%s，用%s写主标"%s"，字号最大，是画面里最醒目的文字；'
        % (hint.get("title_pos", "%s上方" % p["text"]),
           hint.get("title_font", "白色粗黑体大字"), t["title"]),
    ]
    if t["badge"]:
        lines.append('画面%s一枚%s徽章，内写"%s"，字号约为主标的四分之一，一行不折行；'
                     % (p["corner"], hint.get("badge_style", "圆角矩形深蓝底白色粗体"),
                        t["badge"]))
    if t["sub"]:
        lines.append('主标下方用%s写副标"%s"，字号比主标小一号；'
                     % (hint.get("sub_font", "浅米白色小字"), t["sub"]))
    if t["footer"]:
        lines.append('画面%s用%s写"%s"，字号最小，作为底栏署名；'
                     % (p["foot_corner"], hint.get("foot_font", "金色小字"), t["footer"]))
    lines.append(hint.get("container", "文字下方垫半透明深色色块，让字在复杂背景上清晰可读。"))
    return lines


LAYOUTS = {
    "vert_title": dict(cn="竖排贴边", fn=_vert_title_texts),
    "photo_big": dict(cn="写真大字", hint=dict(
        title_pos="画面上方", title_font="金色与白色两行立体粗黑体大字",
        badge_style="圆角胶囊深蓝底白色粗体", sub_font="白色小字", foot_font="浅灰白小字")),
    "closeup": dict(cn="人物特写", hint=dict(
        title_pos="画面下三分之一", title_font="白色粗黑体两行立体大字",
        badge_style="顶部小胶囊", sub_font="浅金色小字", foot_font="浅灰白小字")),
    "magazine": dict(cn="杂志白卡", hint=dict(
        title_pos="画面底部米白卡片内", title_font="黑色宋体无描边大字",
        badge_style="卡片上方细金线旁的深色小字", sub_font="深灰色小字",
        foot_font="深灰色小字")),
    "bold_block": dict(cn="撞色色块", hint=dict(
        title_pos="画面中部错位色块上", title_font="黑色综艺体三行等宽大字",
        badge_style="黄色色块内的黑色粗体", sub_font="白色小字", foot_font="黑色小字")),
    "split": dict(cn="上下分屏", hint=dict(
        title_pos="画面上下分屏的中央斜切线上", title_font="白色粗黑体大字",
        badge_style="橙金分割线旁的白色小字", sub_font="浅白色小字",
        foot_font="浅白色小字")),
    "list": dict(cn="清单三条", hint=dict(
        title_pos="画面上方", title_font="白色粗黑体一行大字",
        badge_style="右侧编号圆点", sub_font="浅米白小字", foot_font="浅灰小字")),
    "still": dict(cn="剧照压底", hint=dict(
        title_pos="画面下方暗区", title_font="白色粗黑体三行大字",
        badge_style="顶部胶囊", sub_font="浅米白小字", foot_font="浅灰小字")),
}


def text_block(cfg, style, side, w, h):
    lay = LAYOUTS.get(style)
    if not lay:
        raise SystemExit("未知版式 %s（可选 %s）" % (style, ", ".join(LAYOUTS)))
    t = gen_texts(cfg)
    if "fn" in lay:
        return lay["fn"](t, side, w, h)
    return _generic_texts(t, side, w, h, lay["hint"])


# ---- 六段骨架 --------------------------------------------------------------

BRIEF_KEYS = ("texture", "composition", "subject", "scene", "light")


def build_prompt(cfg, size_key, side="right", no_text=False, role=None):
    """拼六段骨架。no_text=True 只给「叠加层」路线用，出无字底图。"""
    if size_key not in GEN_PIXELS:
        raise SystemExit("未知尺寸 key：%s（可选 %s）" % (size_key, ", ".join(GEN_PIXELS)))
    w, h = GEN_PIXELS[size_key]
    brief = cfg.get("gen_brief") or {}
    missing = [k for k in BRIEF_KEYS if not str(brief.get(k, "")).strip()]
    if missing and not no_text:
        raise SystemExit("项目 封面.json 缺 gen_brief 字段：%s（画面内容由项目提供）"
                         % ", ".join(missing))
    style = cfg.get("style", "vert_title")
    p = _side_words(side)

    subject = str(brief.get("subject", "")).strip()
    if role and subject:
        subject = "<image1> 是画面中的人物，" + subject

    s1 = ["%s，%s" % (RATIO_WORD[size_key],
                      str(brief.get("texture", "写实摄影质感，电影级细腻胶片颗粒")).strip())]
    s2 = ["%s；" % str(brief.get("composition", "中景平视构图，主体占画面高度约七成")).strip(),
          "%s。" % p["empty"]]
    s3 = (["这一版是留给后期叠加文字的底图，画面为纯净的实景照片，"
           "没有任何文字、字母、水印或标识；文字区保持干净、颜色均匀、纹理极少。"]
          if no_text else text_block(cfg, style, side, w, h))
    s4 = [subject or "画面主体清晰、姿态自然。"]
    s5 = [str(brief.get("scene", "")).strip()]
    s6 = [str(brief.get("light", "")).strip()]

    parts = [
        "【画布与质感】" + "".join(s1),
        "【构图落位】" + "".join(s2),
        "【文字区块】" + "".join(s3),
        "【主体】" + "".join(s4),
        "【场景环境】" + "".join(s5),
        "【光影色调】" + "".join(s6),
    ]
    return "\n".join(parts)


def prompt_tag(prompt, title, kind="cover"):
    """🔴 tag 必须带提示词指纹：scene-gen 按 tag 缓存，改了提示词不换 tag 会复用旧图。"""
    import hashlib
    fp = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:8]
    return "%s_%s_%s" % (title, kind, fp)


def qc_prompt(text, has_role=False, need_text=True):
    """过 `qc.py` 的提示词门。返回 (verdict, 失败项, 摘要)。"""
    import qc
    r = qc.check_prompt(text, has_role=has_role, need_text=need_text)
    fails = [c["item"] + "：" + c["expect"] for c in r["checks"]
             if not c["ok"] and c["level"] == "fail"]
    return r["verdict"], fails, r["summary"]


def main() -> int:
    ap = argparse.ArgumentParser(description="封面版式 → 生图提示词")
    ap.add_argument("--project", required=True, help="作品目录（含 封面.json）")
    ap.add_argument("--size", default="vert_9x16", help="尺寸 key")
    ap.add_argument("--side", default="auto", help="left/right；auto 按配置的 text_side")
    ap.add_argument("--no-text", action="store_true", help="叠加层路线：出无字底图")
    ap.add_argument("--role", default="", help="角色参考图相对路径")
    a = ap.parse_args()

    cfg = json.loads((pathlib.Path(a.project) / "封面.json").read_text(encoding="utf-8"))
    side = a.side
    if side == "auto":
        side = cfg.get("text_side", "right")
        if side == "auto":
            side = "right"
    prompt = build_prompt(cfg, a.size, side=side, no_text=a.no_text, role=a.role or None)
    print(prompt)
    verdict, fails, summary = qc_prompt(prompt, has_role=bool(a.role),
                                        need_text=not a.no_text)
    print("\n--- 提示词质检：%s（%s）---" % (verdict, summary), file=sys.stderr)
    for f in fails:
        print("  FAIL " + f, file=sys.stderr)
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
