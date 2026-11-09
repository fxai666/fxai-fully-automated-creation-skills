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
  python cover_batch.py --project <项目> --gen-base            # 叠加路线：生图出无字底图
  python cover_batch.py --project <项目> --only vert_9x16,horiz_16x9
  python cover_batch.py --project <项目> --side right
  python cover_batch.py --list-sizes / --sites
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import cover_styles as CS       # noqa: E402
import cover_side as SIDE       # noqa: E402
import cover_prompt as CP       # noqa: E402


def _reg():
    return CS.load_sizes()


def _side_for_generation(cfg, cli_side):
    """生图前没有底图可测；auto 时按「主体放反侧」的默认落位。"""
    s = (cli_side or cfg.get("text_side", "auto")).strip().lower()
    if s in ("left", "right"):
        return s
    return "right"      # 主体默认偏左，文字贴右；与 cover_side 的兜底一致


def _qc_or_die(prompt, has_role, need_text, skip_qc):
    print("\n----- 生图提示词 -----\n" + prompt)
    verdict, fails, summary = CP.qc_prompt(prompt, has_role=has_role, need_text=need_text)
    print("----- 提示词质检：%s（%s）-----" % (verdict, summary))
    for f in fails:
        print("  FAIL " + f)
    if verdict == "FAIL" and not skip_qc:
        raise SystemExit("提示词未过质检门，先补要素；确要强出加 --skip-qc")
    print()


def _fit_to_key(src, dst, w, h):
    """生图尺寸是 32 倍数（10 册），按 key 尺寸等比缩放后落盘。"""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    if im.size != (w, h):
        im = im.resize((w, h), Image.LANCZOS)
    im.save(dst)
    return dst


def _generate_cover(cfg, size_key, side, role, outdir, custom_prompt, skip_qc, force=False):
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
    _qc_or_die(prompt, has_role=bool(role), need_text=True, skip_qc=skip_qc)

    raw = outdir / ("_gen_raw_%dx%d.png" % (gw, gh))
    print("生图直接带字：%dx%d → 成品 %dx%d  role=%s  文字侧=%s"
          % (gw, gh, tw, th, role or "纯场景", side))
    poster.gen_image(str(raw), gw, gh, prompt, subdir="cover_gen",
                     tag=CP.prompt_tag(prompt, cfg["title"], "gen_cover"),
                     role=role or None, force=force)
    if not raw.is_file():
        raise SystemExit("生图未产出文件：%s" % raw)
    out = outdir / ("封面_%s_%dx%d_生图带字.png" % (cfg["title"], tw, th))
    _fit_to_key(raw, out, tw, th)
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
    _qc_or_die(prompt, has_role=bool(role), need_text=False, skip_qc=skip_qc)
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
    ap.add_argument("--gen-role", default="", help="角色参考图相对路径；留空=不含参考图")
    ap.add_argument("--gen-prompt", default="", help="自定义提示词全文，覆盖自动拼装")
    ap.add_argument("--skip-qc", action="store_true", help="跳过提示词质检门（不推荐）")
    ap.add_argument("--force", action="store_true", help="忽略生图缓存，强制重出")

    ap.add_argument("--list-sizes", action="store_true", help="列出尺寸注册表")
    ap.add_argument("--sites", action="store_true", help="列出各站封面规格")
    a = ap.parse_args()

    reg = _reg()

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

    # ---- 路线 A：生图直接带字，出完就结束，绝不叠第二层字 ----
    if a.gen_cover:
        side = _side_for_generation(c, a.side)
        _generate_cover(c, a.gen_size, side, a.gen_role, outdir,
                        a.gen_prompt, a.skip_qc, a.force)
        return 0

    # ---- 路线 B / 已有底图：叠加层出多尺寸 ----
    bg = a.bg or c.get("base_image", "")
    if a.gen_base:
        side = _side_for_generation(c, a.side)
        bg = _generate_base(c, a.gen_size, side, a.gen_role, outdir,
                            a.gen_prompt, a.skip_qc, a.force)
    if not pathlib.Path(bg).is_file():
        raise SystemExit("底图不存在：%s" % bg)

    configured = a.side or c.get("text_side", "auto")
    side, info = SIDE.resolve_side(bg, configured)
    print(SIDE.describe(side, info))

    theme = c.get("theme", {})
    kw = dict(t1=c["title"], en=c.get("title_en", ""), kick=c.get("kicker", ""),
              sub=c.get("subtitle", ""), brand=c.get("footer", ""),
              gold=theme.get("gold", "#d8c39a"), paper=theme.get("paper", "#f7f2e9"),
              side=side)
    style = c.get("style", "vert_title")
    if style not in CS.STYLES:
        raise SystemExit("未知样式 %s（可选 %s）" % (style, ", ".join(CS.STYLES)))

    keys = [x.strip() for x in a.only.split(",") if x.strip()] or c.get("sizes") or None
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
