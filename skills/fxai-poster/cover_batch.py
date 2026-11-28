# -*- coding: utf-8 -*-
"""按项目配置出封面（薄入口，不含尺寸、版式、提示词或生图实现）。

尺寸与站点规格 → cover_sizes.json（唯一真源）
版式与渲染     → cover_styles.py（STYLES / render / render_matrix）
留白侧判定     → cover_side.py（detect / resolve）
版式→提示词    → cover_prompt.py（六段骨架，第三段写足排版说明）
底图/成图生图  → poster.gen_image() → fxai-scene-gen → fxai-workflow 的 HTTP 接口
                  （本地 GPU 与云端容器同一个接口，服务寻址与启停不归本处）

🔴 两条路线，**文字生在图上（--gen-cover）是默认首选**：
   千问2.1 文字渲染是强项，封面上的字应当和画面同一次生成（`10` 册第一句）。
   只有「要一次出多尺寸 / 后续要改字」才退回「无字底图 + HTML 叠加层」。
   「画面无任何文字」只在叠加层路线成立，不许写成通用生图口径。

用法：
  python cover_batch.py --project <项目>                       # 已有底图 → 多尺寸叠加层出图
  python cover_batch.py --project <项目> --gen-cover           # 首选：生图直接带字，一步成图
  python cover_batch.py --project <项目> --gen-cover --gen-size video_head \
      --video <成片.mp4>                                        # 片头卡：尺寸自动跟随成片
  python cover_batch.py --project <项目> --gen-cover --gen-size video_head \
      --video-size 1280x768                                     # 或显式传视频宽高
  python cover_batch.py --project <项目> --gen-base            # 叠加路线：生图出无字底图
  python cover_batch.py --project <项目> --only vert_9x16,horiz_16x9
  python cover_batch.py --project <项目> --side right
  python cover_batch.py --list-sizes / --sites
"""
from __future__ import annotations

import argparse
import inspect
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import cover_styles as CS       # noqa: E402
import cover_side as SIDE       # noqa: E402
import cover_prompt as CP       # noqa: E402
import video_size as VS         # noqa: E402


def _reg():
    return CS.load_sizes()


def _side_for_generation(cfg, cli_side):
    """生图前没有底图可测；auto 时按「主体放反侧」的默认落位。"""
    s = (cli_side or cfg.get("text_side", "auto")).strip().lower()
    if s in ("left", "right"):
        return s
    return "right"      # 主体默认偏左，文字贴右；与 cover_side 的兜底一致


def _qc_or_die(prompt, has_role, need_text, skip_qc, role=None):
    print("\n----- 生图提示词 -----\n" + prompt)
    verdict, fails, summary = CP.qc_prompt(prompt, has_role=has_role,
                                           need_text=need_text, role=role)
    print("----- 提示词质检：%s（%s）-----" % (verdict, summary))
    for f in fails:
        print("  FAIL " + f)
    if verdict == "FAIL" and not skip_qc:
        raise SystemExit("提示词未过质检门，先补要素；确要强出加 --skip-qc")
    print()


def _cast_count(proj, cfg):
    """画面声明的人物数：优先 gen_brief.cast_count，其次 制作.json 的 characters。"""
    brief = cfg.get("gen_brief") or {}
    if isinstance(brief.get("cast_count"), int) and brief["cast_count"] > 0:
        return brief["cast_count"]
    for name in ("制作.json", "封面.json"):
        p = pathlib.Path(proj) / name
        if not p.is_file():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        cast = d.get("characters")
        if isinstance(cast, list) and cast:
            return len(cast)
    return 0


def _role_gate(proj, cfg, role, skip_qc):
    """出图前硬校验：画面几人就必须传几张参考图（2026-11-27《风过长椅》坑）。"""
    n_refs = len([x for x in str(role or "").split(",") if x.strip()])
    n_cast = _cast_count(proj, cfg)
    if n_refs == 0:
        if n_cast >= 2 and not skip_qc:
            raise SystemExit(
                "FAIL 角色参考图门禁：项目声明 %d 位人物（男女主），但 --gen-role 未传任何参考图。\n"
                "  画面几人就传几张、逗号分隔：--gen-role \"song_charview/000.png,song_charview/001.png\"\n"
                "  不许用「逆光剪影 / 只留轮廓」规避第二张脸——那样男主会变成与正片不符的路人。"
                % n_cast)
        return
    if n_cast and n_refs < n_cast and not skip_qc:
        raise SystemExit(
            "FAIL 角色参考图门禁：项目声明 %d 位人物，--gen-role 只传了 %d 张（%s）。\n"
            "  少传的那位人物会因没有参考图被模型自由发挥，与正片形象对不上。\n"
            "  按画面人数补齐参考图（逗号分隔），主体描述用 <image1>/<image2> 逐一锁定。"
            % (n_cast, n_refs, role))
    print("[gate] 人物 %d 位 / 参考图 %d 张 —— 数量校验通过" % (n_cast, n_refs))


def _fit_to_key(src, dst, w, h, native=None, size_key="", anchor="center"):
    """落盘到目标尺寸。

    🔴 video_head（片头卡）：生图在 32 倍数上，最终必须 == 视频实际尺寸，
       只按 crop_box 裁切，**绝不 resize**（缩放会把文字边缘抹糊）。
    其他平台 key：沿用原有「按 key 尺寸缩放落盘」行为。
    """
    from PIL import Image
    im = Image.open(src).convert("RGB")
    if size_key == "video_head":
        nw, nh = native or im.size
        if im.size != (nw, nh):
            raise SystemExit("片头卡生图尺寸异常：期望 %dx%d，实得 %dx%d"
                             % (nw, nh, im.size[0], im.size[1]))
        if (w, h) != (nw, nh):
            box = VS.crop_box((nw, nh), (w, h), anchor=anchor)
            print("片头卡只裁不缩：生图 %dx%d → 裁 %s → 成片 %dx%d"
                  % (nw, nh, box, w, h))
            im = im.crop(box)
    elif im.size != (w, h):
        im = im.resize((w, h), Image.LANCZOS)
    im.save(dst)
    return dst


def _generate_cover(cfg, size_key, side, role, outdir, custom_prompt, skip_qc,
                    force=False, native=None, anchor="center"):
    """路线 A：版式排版说明写进提示词 → 生图直接把字画在画面上 → 不叠字。"""
    import poster
    reg = _reg()
    spec = reg["sizes"].get(size_key)
    if not spec:
        raise SystemExit("未知尺寸 key：%s" % size_key)
    tw, th = int(spec["w"]), int(spec["h"])
    gw, gh = CP.GEN_PIXELS[size_key]
    outdir.mkdir(parents=True, exist_ok=True)

    prompt = custom_prompt or CP.build_prompt(cfg, size_key, side=side, role=role or None)
    _qc_or_die(prompt, has_role=bool(role), need_text=True, skip_qc=skip_qc, role=role)

    raw = outdir / ("_gen_raw_%dx%d.png" % (gw, gh))
    if size_key == "video_head":
        print("🔴 片头卡尺寸跟随成片：最终 %dx%d，原生生图 %dx%d（只裁不缩）"
              % (tw, th, gw, gh))
    print("生图直接带字：%dx%d → 成品 %dx%d  role=%s  文字侧=%s"
          % (gw, gh, tw, th, role or "纯场景", side))
    poster.gen_image(str(raw), gw, gh, prompt, subdir="cover_gen",
                     tag=CP.prompt_tag(prompt, cfg["title"], "gen_cover"),
                     role=role or None, force=force)
    if not raw.is_file():
        raise SystemExit("生图未产出文件：%s" % raw)
    out = outdir / ("封面_%s_%dx%d_生图带字.png" % (cfg["title"], tw, th))
    _fit_to_key(raw, out, tw, th, native=(gw, gh), size_key=size_key, anchor=anchor)
    print("成品（不叠字，文字由生图渲染）：%s" % out)
    print("🔴 逐字核对文案，错字漏字重出，不修补（10 册硬规则）")
    return out


def _generate_base(cfg, size_key, side, role, outdir, custom_prompt, skip_qc, force=False):
    """路线 B（例外）：无字底图，供多尺寸叠加层复用。"""
    import poster
    gw, gh = CP.GEN_PIXELS[size_key]
    outdir.mkdir(parents=True, exist_ok=True)
    prompt = custom_prompt or CP.build_prompt(cfg, size_key, side=side,
                                              no_text=True, role=role or None)
    _qc_or_die(prompt, has_role=bool(role), need_text=False, skip_qc=skip_qc, role=role)
    dst = outdir / ("_base_generated_%dx%d.png" % (gw, gh))
    print("叠加层专用无字底图：%dx%d  role=%s（本路线才写「画面无任何文字」）"
          % (gw, gh, role or "纯场景"))
    poster.gen_image(str(dst), gw, gh, prompt, subdir="cover_base",
                     tag=CP.prompt_tag(prompt, cfg["title"], "base"),
                     role=role or None, force=force)
    if not dst.is_file():
        raise SystemExit("生图未产出文件：%s" % dst)
    return str(dst)


def main() -> int:
    ap = argparse.ArgumentParser(description="封面生成（文字生在图上 / 多尺寸叠加层）")
    ap.add_argument("--project", default="", help="作品目录（含 封面.json）")
    ap.add_argument("--only", default="", help="只出指定尺寸 key，逗号分隔")
    ap.add_argument("--outdir", default="", help="产物目录，默认 <项目>/封面")
    ap.add_argument("--side", default="", help="文字侧 left/right/auto；默认读项目 text_side")
    ap.add_argument("--bg", default="", help="直接指定底图路径；调版式时不必重新生图")

    ap.add_argument("--gen-cover", action="store_true",
                    help="【首选】生图直接把文字渲染在画面上，一步成图、不叠字")
    ap.add_argument("--gen-base", action="store_true",
                    help="【例外】生图出无字底图，供多尺寸叠加层复用")
    ap.add_argument("--gen-size", default="vert_9x16", help="生图尺寸 key，默认 vert_9x16")
    ap.add_argument("--video", default="",
                    help="成片 MP4；gen-size=video_head 时用 ffprobe 自动读宽高（首选）")
    ap.add_argument("--video-size", default="",
                    help="显式指定视频宽高 WxH（优先级高于 --video；无法读视频时用）")
    ap.add_argument("--crop-anchor", default="center",
                    choices=["center", "left", "right", "top", "bottom"],
                    help="片头卡从 32 倍数原生图裁回视频尺寸时的取景锚点，默认 center")
    ap.add_argument("--resolve-size", action="store_true",
                    help="只打印片头卡最终/原生尺寸并退出，不生图（供程序化校验）")
    ap.add_argument("--gen-role", default="", help="角色参考图相对路径；留空=不含参考图")
    ap.add_argument("--gen-prompt", default="", help="自定义提示词全文，覆盖自动拼装")
    ap.add_argument("--skip-qc", action="store_true", help="跳过提示词质检门（不推荐）")
    ap.add_argument("--force", action="store_true", help="忽略生图缓存，强制重出")

    ap.add_argument("--list-sizes", action="store_true", help="列出尺寸注册表")
    ap.add_argument("--list-styles", action="store_true", help="列出封面模板（常用/按主题两档）")
    ap.add_argument("--sites", action="store_true", help="列出各站封面规格")
    a = ap.parse_args()

    reg = _reg()

    if a.list_styles:
        CS.print_styles()
        return 0

    if a.list_sizes:
        pol = reg.get("policy", {})
        print("最小出图集：%s" % " + ".join(pol.get("minimal_set", [])))
        print("  理由：%s" % pol.get("minimal_set_why", ""))
        print("\n%-12s %-10s %-8s %-10s %-8s %s" %
              ("key", "尺寸", "比例", "版式", "锚点", "说明"))
        for k, v in reg.get("sizes", {}).items():
            print("%-12s %-10s %-8s %-10s %-8s %s" %
                  (k, "%dx%d" % (v["w"], v["h"]), v.get("ratio", ""),
                   v.get("layout", ""), v.get("anchor", ""), v.get("note", "")))
        return 0

    if a.sites:
        print("%-18s %-12s %-12s %-6s %-6s %s" %
              ("站点", "推荐", "最小尺寸", "可裁", "证据", "备注"))
        for k, v in reg.get("site_specs", {}).items():
            print("%-18s %-12s %-12s %-6s %-6s %s" %
                  (k, v.get("prefer", ""), "x".join(str(x) for x in v.get("min", [])),
                   "是" if v.get("crop") else "否", v.get("evidence", ""), v.get("note", "")))
        return 0

    if not a.project:
        raise SystemExit("缺 --project")
    if a.gen_cover and a.gen_base:
        raise SystemExit("--gen-cover 与 --gen-base 互斥：前者文字生在图上，后者是无字底图")
    if a.gen_size not in CP.GEN_PIXELS:
        raise SystemExit("未知 --gen-size：%s" % a.gen_size)

    proj = pathlib.Path(a.project)
    cfg_p = proj / "封面.json"
    if not cfg_p.is_file():
        raise SystemExit("缺配置文件：%s" % cfg_p)
    c = json.loads(cfg_p.read_text(encoding="utf-8"))
    outdir = pathlib.Path(a.outdir) if a.outdir else proj / "封面"

    # ---- 🔴 片头卡尺寸来源：视频实际宽高，不手填 cover_sizes.json ----
    requested_keys = [x.strip() for x in a.only.split(",") if x.strip()]
    project_keys = c.get("sizes") or []
    will_make_head = (a.gen_size == "video_head") or (
        not a.gen_cover and
        (("video_head" in requested_keys) if requested_keys else ("video_head" in project_keys)))
    native = None
    if will_make_head:
        fallback = (reg["sizes"]["video_head"]["w"], reg["sizes"]["video_head"]["h"])
        (vw, vh), source = VS.resolve(video=a.video or None, size_text=a.video_size or None,
                                      cfg=c, default=fallback)
        gw, gh = VS.native_size(vw, vh)
        spec = reg["sizes"]["video_head"]
        spec["w"], spec["h"] = vw, vh
        CP.GEN_PIXELS["video_head"] = (gw, gh)
        CP.RATIO_WORD["video_head"] = "%d×%d 视频首帧画幅，画面铺满不留黑边" % (vw, vh)
        native = (gw, gh)
        print("[video] 片头卡最终尺寸 %dx%d（来源：%s）；原生生图 %dx%d → 只裁不缩"
              % (vw, vh, source, gw, gh))
        if a.resolve_size:
            print("RESULT final=%dx%d native=%dx%d source=%s"
                  % (vw, vh, gw, gh, source))
            return 0
        if not a.gen_cover:
            raise SystemExit("FAIL 片头卡只能走 --gen-cover（文字生在图上、只裁不缩）。"
                             "叠加层路线会 scale 缩放，禁用；改用 "
                             "--gen-cover --gen-size video_head --video <成片.mp4>")
    elif a.resolve_size:
        raise SystemExit("--resolve-size 需配合 --gen-size video_head 使用")
    elif (a.video or a.video_size):
        print("WARN --video/--video-size 只对 gen-size=video_head 生效；"
              "平台双封面仍按 cover_sizes.json 的 vert_9x16 + horiz_16x9 出图。")

    # 生图路线先过角色参考图数量门（画面几人传几张），再进提示词门
    if a.gen_cover or a.gen_base:
        _role_gate(proj, c, a.gen_role, a.skip_qc)

    # ---- 路线 A：生图直接带字，出完就结束，绝不叠第二层字 ----
    if a.gen_cover:
        side = _side_for_generation(c, a.side)
        _generate_cover(c, a.gen_size, side, a.gen_role, outdir,
                        a.gen_prompt, a.skip_qc, a.force,
                        native=native, anchor=a.crop_anchor)
        return 0

    # ---- 路线 B / 已有底图：叠加层出多尺寸 ----
    bg = a.bg or c.get("base_image", "")
    style = c.get("style", "")
    if not style:                                    # 不写 style 就按标题类别自动选
        text = " ".join(str(c.get(x, "")) for x in ("title", "title2", "kicker", "subtitle"))
        style, score = CS.auto_style(text, has_bg=bool(bg))
        hit = "、".join("%s×%d" % (k, v) for k, v in
                        sorted(score.items(), key=lambda x: -x[1]) if v)
        print("未指定 style → 自动选 %s（%s）" % (style, hit or "无关键词命中，走兜底"))
    if style not in CS.STYLES:
        raise SystemExit("未知样式 %s（可选 %s）" % (style, ", ".join(CS.STYLES)))
    need_bg = CS.STYLES[style].get("need_bg", True)

    if a.gen_base:
        side = _side_for_generation(c, a.side)
        bg = _generate_base(c, a.gen_size, side, a.gen_role, outdir,
                            a.gen_prompt, a.skip_qc, a.force)
    if bg and not pathlib.Path(bg).is_file():
        raise SystemExit("底图不存在：%s" % bg)
    if need_bg and not bg:
        raise SystemExit("样式 %s 必须有底图（封面.json 的 base_image 或 --bg）" % style)

    if bg:
        side, info = SIDE.resolve_side(bg, a.side or c.get("text_side", "auto"))
        print(SIDE.describe(side, info))
    else:                                            # cream_capsule 这类不需要底图
        side = a.side or c.get("text_side", "auto")

    theme = c.get("theme", {})
    kw = dict(t1=c["title"], en=c.get("title_en", ""), kick=c.get("kicker", ""),
              sub=c.get("subtitle", ""), brand=c.get("footer", ""),
              gold=theme.get("gold", "#d8c39a"), paper=theme.get("paper", "#f7f2e9"),
              side=side,
              t2=c.get("title2", ""), cards=c.get("cards") or None,
              foot_left=c.get("foot_left", ""), foot_acc=c.get("foot_acc", ""),
              shot=c.get("shot", ""))
    # 各样式 fn 的签名不一样：只传它认的字段（空值照传，有些是必填位置参数）
    fn = CS.STYLES[style]["fn"]
    params = set(inspect.signature(fn).parameters) if fn else set()
    kw = {k: v for k, v in kw.items() if k in params}

    keys = requested_keys or project_keys or None
    name = "封面_%s" % c["title"]

    print("作品：%s   样式：%s   底图：%s" % (c["title"], style, pathlib.Path(bg).name))
    out = CS.render_matrix(style, outdir, name, bg=bg, keys=keys, **kw)
    for k, pth in out.items():
        v = reg["sizes"][k]
        print("  %-12s %4dx%-4d  %s" % (k, v["w"], v["h"], pathlib.Path(pth).name))
    print("输出目录：%s" % outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
