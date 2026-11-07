# -*- coding: utf-8 -*-
"""
fxai-short-drama · 多平台发布交付套件生成器

新建一部短剧、渲好某一集后，为该集一键产出各短视频平台投稿所需的「交付物」：
  - 【每集独立一套，落在该集目录里，随集走】每平台按投稿表单真实需要的图片数量出图（封面图 + 该平台额外要的头像/小图等），不重复、不漏
  - 该集封面只生【一版】最大竖版主图（走生图技能按该集 COVER_PROMPT 生成，封面文字由模型画入），
    各平台封面尺寸由本地工具等比裁切/缩放产出，每个尺寸只做本地裁切
  - 该集一份《剧情介绍.md》（剧名/简介/角色/标签/AI声明，各平台直接复制；内容由 project_def 自动带全）
  - 每平台一份《展示页.md》（投稿表单字段预填，可直接复制粘贴）
  - 该集一份《本集交付清单.md》；系列根《发布/总览_交付清单.md》为跨集总览
  - 共享底图/抽帧缓存放《发布/_base/》，主海报全系列只生一次
注意：交付物按集出，渲好一集就为那一集跑一次，不必一次跑全剧。

设计：
  - 封面底图一律走生图技能（fxai-comfyui-image-gen.generate_image），按短剧自己的角色形象图锚定
    （server_subdir/00N.png，禁用 avatar 品牌头像库）。
  - 🔴 单版口径（用户 2026-10-12 定）：每张封面【只生一版】最大尺寸竖版主图（1080×1920），
    其余平台尺寸（1200×1600 / 770×1080 / 1920×1080 / 头像小图等）全部由本地 _center_crop_resize
    等比裁切产出，每个尺寸只做本地裁切（生图慢且同提示词随机性大）。
  - 🔴 封面【全部文字】（剧名/集名/钩子句/标签/印章）由生图模型一次画入，排版指令写进提示词；
    PIL 层只做裁切/缩放，不叠任何字（用户 2026-10-12 定）。竖版不加黑色蒙版。
  - 每集封面：读该集分镜脚本的 COVER_PROMPT/COVER_ROLE（AST 安全提取）；无提示词或生图失败，
    退回主海报 / 视频最亮帧。
  - 主海报：_series_cover_prompt 从 project_def 派生；卖点用 cover_hook（≤24 字，长句模型会编乱字）；
    ComfyUI 不可用或图近黑（亮度<15）时，退回已生成海报原图（同朝向优先）/ 系列最亮场景角色图。
  - 图片按 platform_publish_specs.json 的 required_images 出：
    role=poster 用单版主图本地裁切；role=avatar 裁女主角色参考图头肩区；role=clean（榜单禁字图）
    用无文字专用底图 gen_rank_base。

用法：
  python platform_publish.py --project-root "<系列根>" --episodes 1                 # 只给第1集出交付物
  python platform_publish.py --project-root <系列根> --episodes 1,2                  # 出第1、2集
  python platform_publish.py --project-root <系列根>                                 # 默认全部分集
  python platform_publish.py --project-root <系列根> --platforms 红果短剧,快手        # 限定平台
  python platform_publish.py --project-root <系列根> --no-comfyui                    # 主海报用现有最亮图
  python platform_publish.py --project-root <系列根> --regen-poster                  # 强制重跑 ComfyUI 主海报

落点（每集独立、自包含）：
  <系列根>/<集目录>/发布/<平台>/图片_*.jpg + <平台>_展示页.md
  <系列根>/<集目录>/发布/剧情介绍.md、本集交付清单.md
  <系列根>/发布/_base/…（共享底图与抽帧缓存）、发布/总览_交付清单.md
"""

import os
import sys
import re
import json
import glob
import shutil
import subprocess
import datetime

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (SKILL_DIR,
           os.path.join(SKILL_DIR, "..", "fxai-workflow"),
           os.path.join(SKILL_DIR, "..", "fxai-comfyui-image-gen")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drama_pipeline as DP

try:
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance
except ImportError:
    sys.stderr.write("缺少 Pillow：请在运行环境装 pip install Pillow\n")
    raise

SPECS_PATH = os.path.join(SKILL_DIR, "platform_publish_specs.json")

def _font_dir():
    """字体目录按环境解析：FXAI_FONT_DIR > %WINDIR%/Fonts（系统盘符不写死）。"""
    return os.environ.get("FXAI_FONT_DIR") or os.path.join(
        os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows", "Fonts")


_FONT_CANDIDATES = [
    os.path.join(_font_dir(), "simhei.ttf"),
    os.path.join(_font_dir(), "msyh.ttc"),
]


def _font_path():
    for f in _FONT_CANDIDATES:
        if os.path.isfile(f):
            return f
    return None


_FONT = _font_path()


# ------------------------- ffmpeg 关键帧抽取 -------------------------
def _ffmpeg():
    if hasattr(DP, "_resolve_bin"):
        p = DP._resolve_bin("ffmpeg.exe")
        if p and os.path.isfile(p):
            return p
    import shutil as _sh
    p = _sh.which("ffmpeg")
    if p:
        return p
    raise RuntimeError("找不到 ffmpeg（请确认软件安装目录下的 ffmpeg 已就绪）。")


def _ffprobe():
    if hasattr(DP, "_resolve_bin"):
        p = DP._resolve_bin("ffprobe.exe")
        if p and os.path.isfile(p):
            return p
    import shutil as _sh
    p = _sh.which("ffprobe")
    if p:
        return p
    return None


def _duration(mp4):
    fp = _ffprobe()
    if not fp:
        return 60.0
    try:
        out = subprocess.run([fp, "-v", "error", "-show_entries",
                               "format=duration", "-of", "default=nw=1:nk=1", mp4],
                              capture_output=True, text=True, timeout=30).stdout.strip()
        d = float(out)
        return d if d > 0 else 60.0
    except Exception:
        return 60.0


def extract_frame(mp4, out_png):
    """抽多个位置的最亮帧，避开黑场；返回生成的 png 路径或 None。"""
    ff = _ffmpeg()
    dur = _duration(mp4)
    best, best_lum = None, -1
    tmps = []
    for pos in (0.12, 0.28, 0.44, 0.6, 0.76, 0.9):
        t = max(0.0, min(dur - 0.1, dur * pos))
        tf = "%s.f%d.png" % (out_png, int(pos * 100))
        r = subprocess.run([ff, "-y", "-ss", "%.2f" % t, "-i", mp4,
                            "-frames:v", "1", "-q:v", "3", tf],
                           capture_output=True, timeout=120)
        if os.path.isfile(tf):
            tmps.append(tf)
            lum = _brightness(tf)
            if lum > best_lum:
                best_lum, best = lum, tf
    if best:
        shutil.copyfile(best, out_png)
    for f in tmps:
        try:
            os.remove(f)
        except OSError:
            pass
    return out_png if best else None


def _ep_base(series_root, ep_label, hero, cache_dir, ep_base=None):
    """某集封面底图：--ep-base 指定帧 > 该集 raw 原片最亮帧 > 成片 _sub > 主海报。

    ep_base: {"9": "<本地干净帧路径>"} 或 {"9、公司要倒闭？": "..."}，键可以是集号或集目录名。
    用于抽帧自动选到带乱码道具文字/构图不佳的帧时，手工指定一张干净底图。
    """
    if ep_base:
        k = ep_label.split("、")[0]
        for key in (ep_label, k):
            path = ep_base.get(key)
            if path and os.path.isfile(path):
                out_png = os.path.join(cache_dir, "ep_%s_base.png" % k)
                shutil.copyfile(path, out_png)
                return out_png
    epd = os.path.join(series_root, ep_label)
    if not os.path.isdir(epd):
        return hero
    raws = sorted(glob.glob(os.path.join(epd, "fxai_ep*_raw.mp4")))
    subs = sorted(glob.glob(os.path.join(epd, "成片*_sub.mp4")))
    src = raws[0] if raws else (subs[0] if subs else None)
    if not src:
        return hero
    key = os.path.join(cache_dir, "ep_%s_frame.png" % ep_label.split("、")[0])
    if os.path.isfile(key):
        return key
    f = extract_frame(src, key)
    return f or hero


def _series_role_refs(pd):
    """从 project_def 派生「男女主同框」封面参考图（服务端相对路径，短剧角色图）。

    🔴 封面/海报必须用短剧【自己的角色形象图】（server_subdir/00N.png，即渲染管线用的
    同一套四视图），不得用全局头像库 avatar/000、001——那是凤希AI 品牌头像（女主/用户
    本人证件照），与剧中男主形象完全对不上，模型会直接忽略参考图（2026-10-12 封面事故）。

    顺序固定：男主在前（提示词里 = <image1>）、女主在后（= <image2>），
    与 check_image_refs 的标签编号规则一致。
    识别约定（凤希AI 项目生态）：server_index=0 → 女主（凤希）；=1 → 男主；
    拿不到时返回 None，由调用方决定退回默认 role。
    """
    sub = pd.get("server_subdir")
    ch = pd.get("characters", {}) or {}
    if not sub:
        return None
    by_idx = {}
    for _k, v in ch.items():
        i = (v or {}).get("server_index")
        if isinstance(i, int):
            by_idx[i] = "%s/%03d.png" % (sub, i)
    woman = by_idx.get(0)
    man = by_idx.get(1)
    if man and woman:
        return man + "," + woman
    return None


def _cover_prompt_guwu(pd):
    """模板一：古装权谋竖版海报（默认）。男女主同框、画面上方留负空间压标题。
    角色外貌从 project_def.characters[].prompt 派生。封面文字由生图模型画入。
    引用标签铁律：role 传 2 张参考图，提示词必须含 <image1>/<image2>。"""
    title = pd.get("title", "未命名短剧")
    ch = pd.get("characters", {}) or {}
    season_text = _season_text(pd)
    # 卖点优先用封面专用短钩子（project_def.cover_hook，≤24 字，模型才画得完整）；
    # 没有才退回 one_liner 截断——长句模型会画崩甚至自行编字（2026-10-12 海报乱字事故）
    one_liner = (pd.get("cover_hook") or pd.get("one_liner") or "").strip()
    if len(one_liner) > 24:
        one_liner = one_liner[:23] + "…"
    category = pd.get("category") or ""

    def _p(k, fb):
        return (ch.get(k, {}) or {}).get("prompt", "") or fb

    woman = _p("fengxi", "古装中国年轻女子，黑发高挽、着素色纱裙")
    man = _p("xieyunlan", "古装中国年轻男子，束发冠、玄色长袍")
    return ("竖版古装中国权谋短剧电影海报，一张排版完整的中文海报。"
            "<image1> 是画面右侧的%s；"
            "<image2> 是画面左侧的%s。"
            "两人并肩而立、隔一段距离冷冷对视，神情克制、暗流涌动，站在画面下方三分之一处。"
            "画面上方三分之二留出深色朦胧的负空间用于压标题，"
            "负空间里用魏碑书法体写着海报文字：最顶部居中写着剧名「%s」四个大字；"
            "剧名下方写着分类「%s」；画面中部是最大的一行金色卖点文案「%s」；"
            "画面最底部往上约百分之十五处写着规格「%s」（避开画面最底部边缘的安全禁区）。"
            "所有中文必须清晰可读、笔画完整、无错字无乱码；剧名为金色（上亮下暗渐变）、"
            "其余文字为月白色，所有文字带深色描边保证可读。"
            "背景是夜色里的古中国宫殿飞檐与成排宫灯暖黄光、空中薄雾流动、几片飘落的花瓣。"
            "冷青灰调为底、宫灯暖黄是唯一暖光源，强明暗对比、体积光、浅景深、电影级调色、胶片颗粒质感。"
            "画面里除上述海报文字外没有任何其他文字、字幕、可读汉字、现代物。"
            % (man, woman, title, category or "古风 · 权谋", one_liner or "一段倾国倾城的复仇之路", season_text))


def _cover_prompt_niandai(pd):
    """模板二·农村年代剧（外挂/成长/温情题材）竖版海报。
    主角（server_index 0 角色）居中捧一件发光「外挂」物件，农村年代场景（土坯房/田埂/老槐树/煤油灯），
    剧名/分类/卖点/规格由生图模型画入。role 传主角一张参考图，提示词必须含 <image1>。"""
    title = pd.get("title", "未命名短剧")
    ch = pd.get("characters", {}) or {}
    season_text = _season_text(pd)
    one_liner = (pd.get("cover_hook") or pd.get("one_liner") or "").strip()
    if len(one_liner) > 24:
        one_liner = one_liner[:23] + "…"
    category = pd.get("category") or ""
    # 主角外貌从 server_index=0 角色的 desc 派生（本模板主角即剧集第一主角，不套"女主/男主"生态约定）
    main_prompt = ""
    for _k, v in ch.items():
        if (v or {}).get("server_index") == 0:
            main_prompt = (v or {}).get("desc", "") or ""
            break
    if not main_prompt:
        main_prompt = "一个乡村孩子，衣着朴素，神情带着不甘与渴望"
    main_prompt = main_prompt.split("形象锚点")[0].split("台词风格")[0].strip()
    return ("竖版90年代中国北方农村短剧电影海报，一张排版完整的中文海报。"
            "<image1> 是画面下方居中的主角：%s；他双手捧着一件发着淡淡金光的「外挂」物件"
            "（卡片/卷轴/药瓶等，物件上是神秘发光纹路、没有可读文字），神情带着不甘与渴望，不是网红脸明星脸、保持本人身份。"
            "画面上方三分之二留出深色朦胧的负空间用于压标题。"
            "负空间里用魏碑书法体写着海报文字：最顶部居中写着剧名「%s」；"
            "剧名下方写分类「%s」；画面中部是最大的一行金色卖点文案「%s」；"
            "画面最底部往上约百分之十五处写着规格「%s」（避开画面最底部边缘的安全禁区）。"
            "所有中文必须清晰可读、笔画完整、无错字无乱码；剧名为金色（上亮下暗渐变）、"
            "其余文字为月白色，所有文字带深色描边保证可读。"
            "背景是夜色里的土坯房、田埂与远处一棵老槐树剪影，土房窗户漏出一点煤油灯暖黄光。"
            "冷青灰调为底、煤油灯暖黄是唯一暖光源，强明暗对比、体积光、浅景深、电影级调色、胶片颗粒质感。"
            "画面里除上述海报文字外没有任何其他文字、字幕、可读汉字、现代物。"
            % (main_prompt, title, category or "年代 · 成长", one_liner or "一个渴望外挂的人", season_text))


def _series_cover_prompt(pd):
    """全剧作品封面生图提示词——按题材路由到模板（见下路由表）：
      · 古装/权谋/武侠   → 模板一·古装权谋（cover_style='guwu'，缺省）
      · 现代/年代/农村   → 模板二·农村年代剧（cover_style='niandai'）
    project_def['cover_style'] 显式指定；缺省回退古装（模板一）。
    角色参考图由 _series_cover_role 同步路由。"""
    style = (pd.get("cover_style") or "guwu").strip().lower()
    if style in ("niandai", "xiangcun", "nongcun", "xiandai"):
        return _cover_prompt_niandai(pd)
    return _cover_prompt_guwu(pd)


def _series_cover_role(pd):
    """全剧封面参考图——按模板路由：
      模板二（年代剧）→ 主角单张（server_index 0 角色图，role 单图）；
      模板一（古装）→ 男女主双图（沿用 _series_role_refs）。"""
    style = (pd.get("cover_style") or "guwu").strip().lower()
    if style in ("niandai", "xiangcun", "nongcun", "xiandai"):
        sub = pd.get("server_subdir")
        if sub:
            for _k, v in (pd.get("characters", {}) or {}).items():
                if (v or {}).get("server_index") == 0:
                    return "%s/000.png" % sub
        return None
    return _series_role_refs(pd) or "both"


def _load_cover_meta(ep_dir):
    """从集目录的 run_qsl1_ep*.py 里【安全提取】封面三件套（AST 解析，不执行脚本，
    规避触发渲染提交）：
      COVER_PROMPT  封面生图提示词（必填，短剧执行规范：设计分镜之前定好、写进脚本）
      COVER_ROLE    角色锚定：woman / man / both / None（None=纯场景空镜）
      COVER_HOOK    封面钩子句（可选，发布时压在封面底部）
    返回 dict(prompt=..., role=..., hook=...)。"""
    import ast
    var_map = {"COVER_PROMPT": "prompt", "COVER_ROLE": "role", "COVER_HOOK": "hook"}
    meta = {"prompt": None, "role": None, "hook": None}
    for path in glob.glob(os.path.join(ep_dir, "run_qsl1_ep*.py")):
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id in var_map:
                        if isinstance(node.value, ast.Constant):
                            v = node.value.value
                            if t.id == "COVER_ROLE":
                                meta["role"] = (v.strip() or None) if isinstance(v, str) else None
                            elif isinstance(v, str) and v.strip():
                                meta[var_map[t.id]] = v.strip()
    return meta


def _load_cover_prompt(ep_dir):
    """兼容旧名：只取 COVER_PROMPT。"""
    return _load_cover_meta(ep_dir)["prompt"]


def gen_ep_cover(series_root, ep_label, pd, cache_dir, use_comfyui=True, reuse=True,
                 size=(1080, 1920)):
    """某集封面底图：用该集分镜脚本里的 COVER_PROMPT 走生图技能生成（一张该集专属封面），
    COVER_ROLE 决定锚定哪些角色形象图；无提示词或生图失败返回 None，由调用方决定退回。
    封面上的【全部文字】由生图模型一次画入（排版指令写进提示词），PIL 层只做裁切。
    size=(w,h)：单版口径下只传最大竖版 (1080,1920)，其余平台尺寸由调用方本地裁切；
    若显式传横版尺寸，提示词会自动改横版构图（文字集中左侧）。返回 png 路径或 None。"""
    k = ep_label.split("、")[0]
    w, h = size
    out = os.path.join(cache_dir, "ep_%s_gen_%dx%d.png" % (k, w, h))
    if reuse and os.path.exists(out):
        return out
    meta = _load_cover_meta(os.path.join(series_root, ep_label))
    prompt = meta.get("prompt")
    if not prompt:
        return None
    if not (use_comfyui and DP.check_comfyui()):
        return None
    if w > h:  # 横版：构图指令整体镜像到左右方向，文字集中左侧
        prompt = prompt.replace("画面上方三分之二留出深色朦胧的负空间用于压标题",
                                "画面左侧二分之一留出深色朦胧的负空间，所有文字集中在左侧")
        prompt = prompt.replace("站在画面下方三分之一处", "站在画面右侧二分之一处")
    try:
        import image_gen
        role = meta.get("role")
        print("[封面] 第%s集 %dx%d 提交生图技能（role=%s）：%s" % (k, w, h, role or "空镜", prompt[:36]))
        dst = image_gen.generate_image(prompt, role=role, width=w, height=h,
                                       tag="cover_ep%s_%dx%d" % (k, w, h), save_dir=cache_dir,
                                       timeout=600, allow_no_outfit=True)
        if dst and os.path.exists(dst):
            shutil.copyfile(dst, out)
            return out
    except Exception as e:
        print("[封面] 第%s集 %dx%d 生图失败（%s），退回主海报/抽帧。" % (k, w, h, e))
    return None


# ------------------------- 封面底图：ComfyUI 主海报 -------------------------
def gen_hero_poster(series_root, pd, out_dir, use_comfyui=True, regen=False,
                    size=(1080, 1920)):
    """生成整剧主海报底图（平台必传的『作品封面』）。ComfyUI 在线则走生图技能生成；否则退回系列最亮图。返回本地图片路径。
    单版口径：只生最大竖版 (1080,1920) 一版，其余平台尺寸由调用方本地裁切。"""
    w, h = size
    cache = os.path.join(out_dir, "cover_main_base_%dx%d.png" % (w, h))
    if os.path.exists(cache) and not regen:
        return cache

    if use_comfyui and DP.check_comfyui():
        try:
            import image_gen
            prompt = _series_cover_prompt(pd)
            if w > h:
                prompt = prompt.replace("画面上方三分之二留出深色朦胧的负空间用于压标题",
                                        "画面左侧二分之一留出深色朦胧的负空间，所有文字集中在左侧")
                prompt = prompt.replace("站在画面下方三分之一处", "站在画面右侧二分之一处")
            # 🔴 用短剧自己的角色形象图（server_subdir/00N.png），不用 avatar 品牌头像库
            role = _series_cover_role(pd)
            print("[海报] %dx%d 提交生图技能（role=%s，男女主同框）：%s" % (w, h, role, prompt[:40]))
            dst = image_gen.generate_image(prompt, role=role, width=w, height=h,
                                            tag="cover_main_%dx%d" % (w, h), save_dir=out_dir,
                                            timeout=600, allow_no_outfit=True)
            if os.path.exists(dst) and _brightness(dst) >= 15:
                # 门禁只拦近黑失败图：夜戏海报实测亮度 26~46 均属正常，勿用高阈值误杀
                shutil.copyfile(dst, cache)
                print("[海报] ComfyUI 生图成功（亮度达标）。")
                return cache
            print("[海报] ComfyUI 图过暗（疑似失败图），退回最亮现有图作底图。")
        except Exception as e:
            print("[海报] ComfyUI 生图失败（%s），退回最亮现有图作底图。" % e)

    # 退回 1：已生成过的海报原图（同朝向优先，且须过亮度门禁）——比白底四视图 sheet 更适合当海报底图
    raws = [p for p in glob.glob(os.path.join(out_dir, "cover_main_*.png"))
            if not os.path.basename(p).startswith("cover_main_base")
            and _brightness(p) >= 15]
    if raws:
        def _is_wide(p):
            return Image.open(p).size[0] > Image.open(p).size[1]
        same = [p for p in raws if _is_wide(p) == (w > h)]
        pick = (same or raws)[0]
        shutil.copyfile(pick, cache)
        print("[海报] 用已生成的海报原图作底图：%s" % pick)
        return cache

    # 退回 2：场景图优先（更亮更贴剧情），其次角色图；挑整体最亮一张
    cands = []
    for grp in ("scenes", "characters"):
        for _k, info in pd.get(grp, {}).items():
            local = os.path.join(series_root, info.get("local", ""))
            if os.path.exists(local):
                cands.append(local)
    if not cands:
        raise RuntimeError("无任何可用底图（角色/场景图缺失且 ComfyUI 起不来）。")
    best = max(cands, key=_brightness)
    shutil.copyfile(best, cache)
    print("[海报] 用现有最亮底图（候选 %d 张）：%s" % (len(cands), best))
    return cache


def _brightness(path):
    """平均亮度（0-255），用于在候选图里挑最亮的一张（避开黑场/暗图）。"""
    im = Image.open(path).convert("L").resize((64, 64))
    return sum(im.getdata()) / len(im.getdata())


def _rank_base_prompt(pd):
    """榜单封面无文字底图提示词：横版电影感剧照，画面里【没有任何文字】。
    角色外貌从 project_def.characters[].prompt 派生，不写死、不另编。
    引用标签铁律：role 传 2 张参考图，提示词必须含 <image1>/<image2>（check_image_refs 硬拦）。"""
    ch = pd.get("characters", {}) or {}

    def _p(k, fb):
        return (ch.get(k, {}) or {}).get("prompt", "") or fb

    woman = _p("fengxi", "古装中国年轻女子，黑发高挽、着素色纱裙")
    man = _p("xieyunlan", "古装中国年轻男子，束发冠、玄色长袍")
    return ("横版古装中国权谋短剧电影剧照，画面里没有任何文字、字幕、水印、招牌。"
            "<image1> 是画面右侧的%s；"
            "<image2> 是画面左侧的%s。"
            "两人在夜色里的古中国宫殿长廊中隔一段距离冷冷对视，神情克制、暗流涌动。"
            "廊下宫灯暖黄光、青砖地面反着冷月色、空中薄雾流动。"
            "冷青灰调为底、宫灯暖黄是唯一暖光源，强明暗对比、体积光、浅景深、电影级调色、胶片颗粒质感。"
            % (man, woman))


def gen_rank_base(series_root, pd, cache_dir, use_comfyui=True, size=(2450, 800)):
    """火龙漫剧榜单封面底图：平台规格明确『禁用带任何标题文字，只出底图』。
    单独生一张【无文字】横版剧照（缓存 cover_rank_base_WxH.png，全系列共用）；
    ComfyUI 不可用或生图不合格时返回 None，由调用方退回 hero 裁切。"""
    w, h = size
    cache = os.path.join(cache_dir, "cover_rank_base_%dx%d.png" % (w, h))
    if os.path.exists(cache):
        return cache
    if not (use_comfyui and DP.check_comfyui()):
        return None
    try:
        import image_gen
        role = _series_role_refs(pd) or "both"
        print("[榜单底图] %dx%d 提交生图技能（无文字纯底图，role=%s）" % (w, h, role))
        dst = image_gen.generate_image(_rank_base_prompt(pd), role=role, width=w, height=h,
                                       tag="cover_rank_%dx%d" % (w, h), save_dir=cache_dir,
                                       timeout=600, allow_no_outfit=True)
        if dst and os.path.exists(dst) and _brightness(dst) >= 15:
            shutil.copyfile(dst, cache)
            print("[榜单底图] 无文字底图就绪 -> %s" % cache)
            return cache
        print("[榜单底图] 生图结果不合格，退回 hero 裁切。")
    except Exception as e:
        print("[榜单底图] 生图失败（%s），退回 hero 裁切。" % e)
    return None


def _center_crop_resize(base, w, h, bias_top=False):
    """把底图等比放大到覆盖 (w,h) 并裁切成 (w,h)，整图保持明亮。
    bias_top=True 时裁切窗口偏上（单版竖版主图裁横版时用：保住画面上方的标题文字与人物上半身）。"""
    base = ImageEnhance.Brightness(base).enhance(1.15)
    bw, bh = base.size
    scale = max(w / bw, h / bh)
    base = base.resize((int(bw * scale), int(bh * scale)), Image.LANCZOS)
    lw = (base.width - w) // 2
    slack = base.height - h
    th = int(slack * 0.30) if (bias_top and slack > 0) else slack // 2
    return base.crop((lw, th, lw + w, th + h))


# ------------------------- PIL 合成（海报式排版） -------------------------
# 字体栈：标题用古风魏/楷（海报主字体），副文用中宋/雅黑
_FONT_TITLE_CANDIDATES = [
    os.path.join(_font_dir(), "STXINWEI.TTF"),   # 华文新魏：古风武侠海报主字体
    os.path.join(_font_dir(), "STXINGKA.TTF"),   # 华文行楷
    os.path.join(_font_dir(), "STKAITI.TTF"),    # 华文楷体
    os.path.join(_font_dir(), "simhei.ttf"),
]
_FONT_BODY_CANDIDATES = [
    os.path.join(_font_dir(), "STZHONGS.TTF"),   # 华文中宋：副标题/正文
    os.path.join(_font_dir(), "msyh.ttc"),
    os.path.join(_font_dir(), "simhei.ttf"),
]


def _first_font(cands):
    for f in cands:
        if os.path.isfile(f):
            return f
    return None


_FONT_TITLE = _first_font(_FONT_TITLE_CANDIDATES) or _FONT
_FONT_BODY = _first_font(_FONT_BODY_CANDIDATES) or _FONT

# 海报配色：古金（标题/装饰）、月白（正文）、朱砂（印章）、墨（描边/投影）
GOLD = (212, 172, 104)
GOLD_HI = (247, 228, 174)
GOLD_DEEP = (146, 104, 44)
MOON = (240, 243, 246)
CINNABAR = (168, 44, 36)
INK = (16, 16, 20)

# 封面标签行里不出现的泛用词
_TAG_SKIP = {"短剧", "AI生成", "AI", "高能卡点"}


def _cover_tags(tags, n=4):
    """取前 n 个非泛用标签，用于封面底部标签行。"""
    out = [t for t in (tags or []) if t not in _TAG_SKIP]
    return out[:n] or ["古风", "权谋"]


def _fit_font(text, max_w, max_font, min_font=26, font_path=None):
    """从大到小试字号，返回 (ImageFont, 行列表)。中文按字符换行，最多 3 行。"""
    fp = font_path or _FONT_BODY
    for size in range(max_font, min_font - 1, -2):
        f = ImageFont.truetype(fp, size) if fp else ImageFont.load_default()
        lines, cur = [], ""
        for ch in text:
            test = cur + ch
            if f.getlength(test) > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = test
        if cur:
            lines.append(cur)
        if len(lines) <= 3:
            return f, lines
    f = ImageFont.truetype(fp, min_font) if fp else ImageFont.load_default()
    lines, cur = [], ""
    for ch in text:
        test = cur + ch
        if f.getlength(test) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return f, lines


# ---------- 海报绘制原语 ----------
def _char_w(font):
    return font.getlength("国")


def _spaced_width(text, font, spacing=0):
    """带字间距的一段文字总宽。"""
    if not text:
        return 0
    return len(text) * _char_w(font) + max(0, len(text) - 1) * spacing


def _draw_spaced(d, x, y, text, font, fill, spacing=0, stroke_width=0, stroke_fill=None):
    """从左到右逐字绘制（字间距 spacing 像素），每字以 lm 锚点对齐同一基线。"""
    cw = _char_w(font)
    for i, ch in enumerate(text):
        d.text((x + i * (cw + spacing), y), ch, font=font, fill=fill, anchor="lm",
               stroke_width=stroke_width, stroke_fill=stroke_fill)
    return x + _spaced_width(text, font, spacing)


def _draw_spaced_c(d, cx, y, text, font, fill, spacing=0, stroke_width=0, stroke_fill=None):
    """水平居中于 cx 的逐字绘制。"""
    return _draw_spaced(d, cx - _spaced_width(text, font, spacing) / 2.0, y,
                        text, font, fill, spacing, stroke_width, stroke_fill)


def _draw_grad_text(img, cx, y_mid, text, font, c_top, c_bottom, spacing=0,
                    stroke_width=0, stroke_fill=INK, shadow=True):
    """海报主标题：渐变填充（上亮下沉）+ 深色描边 + 轻投影，水平居中于 cx。
    img 必须是 RGBA。返回文字高度。"""
    tw = _spaced_width(text, font, spacing)
    x0 = cx - tw / 2.0
    asc, desc = font.getmetrics()
    th = asc + desc
    off = max(2, stroke_width // 2) if stroke_width else 2

    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        _draw_spaced(ImageDraw.Draw(sh), x0 + off, y_mid + off, text, font,
                     (0, 0, 0, 140), spacing)
        img.alpha_composite(sh)
    if stroke_width > 0:
        st = Image.new("RGBA", img.size, (0, 0, 0, 0))
        _draw_spaced(ImageDraw.Draw(st), x0, y_mid, text, font,
                     tuple(stroke_fill) + (255,), spacing, stroke_width,
                     tuple(stroke_fill) + (255,))
        img.alpha_composite(st)
    # 渐变填充：遮罩取无描边的字心，渐变按文字 bbox 垂直铺
    mask = Image.new("L", img.size, 0)
    _draw_spaced(ImageDraw.Draw(mask), x0, y_mid, text, font, 255, spacing)
    gw, gh = int(tw) + 8, int(th) + 10
    grad = Image.new("RGB", (max(1, gw), max(1, gh)))
    gd = ImageDraw.Draw(grad)
    for i in range(grad.height):
        t = i / max(1, grad.height - 1)
        gd.line([(0, i), (grad.width, i)],
                fill=tuple(int(c_top[k] + (c_bottom[k] - c_top[k]) * t) for k in range(3)))
    gx, gy = int(x0) - 4, int(y_mid - th / 2.0) - 5
    region = mask.crop((gx, gy, gx + grad.width, gy + grad.height))
    img.paste(grad, (gx, gy), region)
    return th


def _vgrad_scrim(size, top_h, top_a, bot_h, bot_a, color=(0, 0, 0)):
    """上下渐变压暗层（RGBA）：顶部 top_h 内由 top_a 渐隐到 0，底部同理。"""
    w, h = size
    col = Image.new("RGBA", (1, h), (0, 0, 0, 0))
    cp = col.load()
    for y in range(h):
        if y < top_h:
            a = int(top_a * (1 - y / max(1, top_h - 1)))
        elif y >= h - bot_h:
            a = int(bot_a * ((y - (h - bot_h)) / max(1, bot_h - 1)))
        else:
            a = 0
        cp[0, y] = color + (a,)
    return col.resize((w, h))


def _hgrad_panel(w, h, frac, a_left, a_right, color=(0, 0, 0)):
    """横向渐变面板（RGBA）：左侧 frac 宽度内由 a_left 渐隐到 0，用于横版封面。"""
    row = Image.new("RGBA", (w, 1), (0, 0, 0, 0))
    rp = row.load()
    for x in range(w):
        if x < w * frac:
            a = int(a_left * (1 - x / max(1.0, w * frac)))
        else:
            a = int(a_right)
        rp[x, 0] = color + (a,)
    return row.resize((w, h))


def _draw_rule(d, cx, y, half_w, color=GOLD, thick=2):
    """居中装饰线：─── ✦ ───"""
    gap = max(6, int(half_w * 0.26))
    d.line([(cx - half_w, y), (cx - gap, y)], fill=color, width=thick)
    d.line([(cx + gap, y), (cx + half_w, y)], fill=color, width=thick)
    r = max(3, thick + 1)
    d.ellipse([cx - r, y - r, cx + r, y + r], outline=color, width=thick)


def _draw_seal(img, cx, cy, box, text, font):
    """朱红印章：圆角方章 + 阴文白字（四字两行两列，其余单字居中）。"""
    d = ImageDraw.Draw(img)
    x0, y0 = int(cx - box / 2), int(cy - box / 2)
    d.rounded_rectangle([x0, y0, x0 + box, y0 + box], radius=int(box * 0.12),
                        fill=CINNABAR + (255,))
    m = int(box * 0.07)
    d.rounded_rectangle([x0 + m, y0 + m, x0 + box - m, y0 + box - m],
                        radius=int(box * 0.07), outline=MOON + (220,),
                        width=max(1, int(box * 0.018)))
    chs = [c for c in text if c.strip()]
    if len(chs) >= 4:
        cell = (box - 2 * m) / 2.0
        fs = int(cell * 0.78)
        f = ImageFont.truetype(_FONT_TITLE, fs)
        for i, c in enumerate(chs[:4]):
            px = x0 + m + cell * (i % 2) + cell / 2
            py = y0 + m + cell * (i // 2) + cell / 2
            d.text((px, py), c, font=f, fill=MOON + (255,), anchor="mm")
    else:
        fs = int((box - 2 * m) * 0.72)
        f = ImageFont.truetype(_FONT_TITLE, fs)
        d.text((cx, cy), chs[0] if chs else "印", font=f, fill=MOON + (255,), anchor="mm")


def _wrap_hook(hook, max_w, max_font, min_font=18):
    """钩子句按宽度换行（最多 2 行），返回 (font, lines)。"""
    f, lines = _fit_font(hook, max_w, max_font, min_font, _FONT_BODY)
    return f, lines[:2]


# 🔴 封面文字层已全部移交生图模型（排版指令写进 COVER_PROMPT，模型一次出图带字）；
# 本模块只按平台尺寸裁切底图（_center_crop_resize），文字由生图模型画入。
# 旧 poster_cover/composite_cover（PIL 叠字）已删除。

def _lead_ref_path(series_root, pd):
    """女主角色参考图本地路径（头像/剧照小图裁切用）；取不到返回 None。"""
    ch = pd.get("characters", {}) or {}
    for _k, v in ch.items():
        if (v or {}).get("server_index") == 0:
            loc = os.path.join(series_root, (v or {}).get("local", ""))
            return loc if os.path.exists(loc) else None
    return None


def crop_ref_face(ref_path, w, h):
    """角色参考图（白底多视图 sheet）裁头像：取最左一幅（正身/锚脸特写）的头肩区。
    版式约定：正身居最左；960×1280 三视图每幅约 1/3 宽、2168×1280 四视图每幅约 1/4 宽
    （按总宽估每幅宽；裁不动的版式退化为中心裁切）。"""
    im = Image.open(ref_path).convert("RGB")
    W, H = im.size
    vw = W / 3.0 if W <= 1500 else W / 4.0
    side = min(vw, H * 0.5)
    if vw < 8 or side < 8:
        return _center_crop_resize(im, w, h)
    return _center_crop_resize(im.crop((0, 0, int(vw), int(side))), w, h)


def composite_avatar(base_path, w, h):
    """头像/小图：中心裁切海报、保持明亮、无文字（账号头像或封面小图用）。"""
    return _center_crop_resize(Image.open(base_path).convert("RGB"), w, h)


# ------------------------- 自动文案（project_def 缺省时兜底，不出现 ⚠️） -------------------------
def _auto_synopsis(title):
    return "《%s》是一部由 AI 全流程生成的短剧，欢迎关注。" % title


def _auto_tags():
    return ["短剧", "AI生成"]


def _auto_category():
    return "短剧"


# ------------------------- 共享剧情介绍 -------------------------
def _ep_count_text(pd):
    """对外文案里的集数：如「20集」。只给集数，不带制作进度/连载状态。"""
    total = pd.get("total_episodes") or len(pd.get("episodes", []) or [])
    return "%d集" % total


def _season_text(pd):
    """封面副标题用的剧集规格：如「第一季 · 20集」。project_def 的 status（制作进度）不进任何交付文案。"""
    season = pd.get("season") or 1
    label = pd.get("season_label") or ("第一季" if season == 1 else "第%d季" % season)
    return "%s · %s" % (label, _ep_count_text(pd))


def _render_intro(out, title, pd, specs):
    """生成一份该集共用的剧情介绍（各平台简介/描述栏直接复制）。内容全部从 project_def 带全。"""
    synopsis = pd.get("synopsis") or _auto_synopsis(title)
    one_liner = pd.get("one_liner") or synopsis
    tags = pd.get("tags") or _auto_tags()
    category = pd.get("category") or _auto_category()
    ep_text = _ep_count_text(pd)

    lines = []
    lines.append("# 《%s》· 剧情介绍（投稿物料）\n" % title)
    lines.append("> 本文件为该集投稿共用的剧情介绍，复制到各平台「简介/描述/摘要」栏即可；完整角色表见下方。\n")
    lines.append("## 基础信息\n")
    lines.append("- 剧名：**%s**" % title)
    lines.append("- 总集数：**%s**" % ep_text)
    lines.append("- 一句话简介：%s" % one_liner)
    lines.append("- 剧情简介：%s" % synopsis)
    lines.append("- 标签：%s" % "、".join(tags))
    lines.append("- 分类：%s" % category)
    lines.append("")
    lines.append("## 角色介绍\n")
    lines.append("| 角色 | 性别 | 简介 | 角色图 |")
    lines.append("|------|------|------|--------|")
    for _k, info in pd.get("characters", {}).items():
        nm = info.get("name", _k)
        gender = info.get("gender", "男")
        desc = info.get("desc") or ("《%s》中登场的角色。" % title)
        local = info.get("local", "")
        lines.append("| %s | %s | %s | %s |" % (nm, gender, desc, local))
    lines.append("")
    lines.append("## AI 生成声明\n")
    lines.append("本剧由 AI 全流程生成（文生图 / 图生视频 / 自动剪辑），角色与场景均为拟人化动物卡通形象，不含真人出镜。发布时请按各平台要求勾选「内容由AI生成」。\n")
    lines.append("## 各平台简介字数限制速查\n")
    for pname, pinfo in specs["platforms"].items():
        rfs = "、".join(pinfo.get("required_fields", []))
        lines.append("- **%s**：%s%s" % (pname, rfs,
                                        "（须勾选内容由AI生成）" if pinfo.get("ai_declare") else ""))
    lines.append("")
    path = os.path.join(out, "剧情介绍.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ------------------------- 表单字段预填 -------------------------
def _field_value(field, title, pd, synopsis, one_liner, tags, category, ep_text):
    if "角色" in field:
        return "见下方角色信息表 / 剧情介绍.md"
    if "集数" in field or "总集" in field:
        return ep_text
    if "剧名" in field or "标题" in field or "作品名称" in field:
        return title
    if "简介" in field or "描述" in field or "摘要" in field:
        # 带字数上限的简介栏用短句（one_liner），否则用完整简介
        if re.search(r"[≤<]", field):
            return one_liner if len(one_liner) <= 90 else one_liner[:90]
        return synopsis
    if "标签" in field:
        return "、".join(tags)
    if "分类" in field:
        return category
    if "AI" in field:
        return "是（内容由AI生成）"
    return "—"


# ------------------------- 主流程 -------------------------
def _resolve_episodes(all_eps, episodes):
    """把 --episodes 的入参（集号 / 集目录名 / 完整集名）映射到项目定义里的集目录名。"""
    if not episodes:
        return list(all_eps)
    want = []
    for one in episodes if isinstance(episodes, (list, tuple)) else episodes.split(","):
        one = str(one).strip()
        if not one:
            continue
        hit = [e for e in all_eps if e == one or e.split("、")[0] == one]
        if not hit:
            raise RuntimeError("项目定义里没有这一集：%s（可选：%s）" %
                               (one, "、".join(all_eps[:5]) + ("…" if len(all_eps) > 5 else "")))
        if hit[0] not in want:
            want.append(hit[0])
    return want


def _ep_index(ep_label):
    """真实集号 = 集目录名开头的数字（「2、假意庇护」-> 2）。

    只跑其中一集时列表序号不作集号：本次列表只有 1 项，序号恒为 1，
    会把 ep2 的封面写成「封面_ep1_*」、文案写成「第1集」。
    """
    head = str(ep_label).split("、", 1)[0].strip()
    return int(head) if head.isdigit() else None


def gen_publish_kit(project_root, platforms=None, cover_art=None,
                    use_comfyui=True, regen_poster=False, ep_base=None,
                    episodes=None):
    """为 project_root 所在系列的指定分集生成多平台发布交付物。

    每集一套、落在集目录里；共享底图/抽帧缓存放 <系列根>/发布/_base。
    episodes: 集号或集目录名的列表/逗号串；None 或空 = 全部分集。返回系列级发布根目录。
    """
    series_root, pd = DP.load_project_def(project_root)
    if not pd:
        raise RuntimeError("未找到 project_def.json（向上查找失败）。")
    specs = _load_specs()
    plat_specs = specs["platforms"]
    if platforms:
        want = [p.strip() for p in platforms.split(",") if p.strip()]
        plat_specs = {k: v for k, v in plat_specs.items() if k in want}
        if not plat_specs:
            raise RuntimeError("指定的平台都不在规格表内：%s" % platforms)

    title = pd.get("title", "未命名短剧")
    all_eps = list(pd.get("episodes", []) or [])
    episodes = _resolve_episodes(all_eps, episodes)
    made = len(all_eps)
    ep_text = _ep_count_text(pd)
    season_text = _season_text(pd)
    synopsis = pd.get("synopsis") or _auto_synopsis(title)
    one_liner = pd.get("one_liner") or synopsis
    tags = pd.get("tags") or _auto_tags()
    category = pd.get("category") or _auto_category()

    out = os.path.join(series_root, "发布")
    os.makedirs(out, exist_ok=True)
    cache_dir = os.path.join(out, "_base")
    os.makedirs(cache_dir, exist_ok=True)

    hero = cover_art or gen_hero_poster(series_root, pd, cache_dir, use_comfyui, regen_poster)

    # 封面底图（文字全部由生图模型画入，PIL 只裁切）：
    #   · 单版口径（用户定）：每张封面【只生一版】最大竖版主图（1080×1920），
    #     其余平台尺寸全部本地等比裁切（_center_crop_resize），每个尺寸只做本地裁切；
    #   · 每集优先该集脚本 COVER_PROMPT+COVER_ROLE 走生图技能；失败退回主海报/抽帧。
    master_size = (1080, 1920)
    poster_sizes = sorted({(i["w"], i["h"]) for p in plat_specs.values()
                           for i in p["required_images"] if i["role"] == "poster"},
                          key=lambda s: s[0] * s[1], reverse=True)

    hero_map = {s: hero for s in poster_sizes}   # 全尺寸共用一版主图，本地裁切
    base_map, hook_map = {}, {}
    for ep_label in episodes:
        ep_gen = gen_ep_cover(series_root, ep_label, pd, cache_dir, use_comfyui,
                              size=master_size)
        ep_src = ep_gen or _ep_base(series_root, ep_label, hero, cache_dir, ep_base)
        base_map[ep_label] = {s: ep_src for s in poster_sizes}
        hook_map[ep_label] = _load_cover_meta(os.path.join(series_root, ep_label)).get("hook")

    manifest = {}
    for pos, ep_label in enumerate(episodes, 1):
        idx = _ep_index(ep_label) or pos      # 集号取目录名里的数字，取不到才退回排序位
        ep_out = os.path.join(series_root, ep_label, "发布")
        os.makedirs(ep_out, exist_ok=True)
        _render_intro(ep_out, title, pd, specs)

        files_all = []
        for pname, pinfo in plat_specs.items():
            pdir = os.path.join(ep_out, pname)
            os.makedirs(pdir, exist_ok=True)
            files = []
            # 平台投稿表单所需图片（封面/头像/榜单等）
            for img in pinfo["required_images"]:
                w, h = img["w"], img["h"]
                if img["role"] == "clean":
                    # 榜单图平台禁用带任何标题文字：用无文字专用底图（退回 hero 裁切）
                    rank_src = gen_rank_base(series_root, pd, cache_dir, use_comfyui, (w, h)) or hero
                    im = _center_crop_resize(Image.open(rank_src).convert("RGB"), w, h)
                elif img["role"] == "avatar":
                    # 头像/剧照小图：优先裁女主角色参考图的头肩区（平台规范亦建议角色参考图，且无文字）
                    ref = _lead_ref_path(series_root, pd)
                    im = crop_ref_face(ref, w, h) if ref else composite_avatar(hero, w, h)
                else:  # poster：文字已由生图模型画入底图，这里只按尺寸裁切
                    im = _center_crop_resize(Image.open(hero_map[(w, h)]).convert("RGB"), w, h,
                                             bias_top=(w > h))
                fn = os.path.join(pdir, "图片_%s_%dx%d.jpg" % (img["key"], w, h))
                im.save(fn, "JPEG", quality=92)
                files.append((img, os.path.basename(fn)))

            # 本集独立封面（role=poster 且 per_episode=true 的图片，每集各出一份）
            posters = [i for i in pinfo["required_images"]
                       if i["role"] == "poster" and i.get("per_episode", True)]
            for poster in posters:
                ew, eh = poster["w"], poster["h"]
                base = base_map[ep_label][(ew, eh)]
                # 文字已由生图模型画入底图，这里只按平台尺寸裁切
                im = _center_crop_resize(Image.open(base).convert("RGB"), ew, eh,
                                         bias_top=(ew > eh))
                efn = os.path.join(pdir, "封面_ep%d_%dx%d.jpg" % (idx, ew, eh))
                im.save(efn, "JPEG", quality=92)
                files.append(({"key": "ep%d" % idx, "label": "第%d集封面" % idx,
                               "role": "poster", "w": ew, "h": eh,
                               "ratio": poster["ratio"]}, os.path.basename(efn)))

            md = _render_page(pname, pinfo, title, pd, ep_text, synopsis, one_liner,
                              tags, category, files, specs, ep_idx=idx, ep_label=ep_label)
            with open(os.path.join(pdir, "%s_展示页.md" % pname), "w", encoding="utf-8") as f:
                f.write(md)
            files_all.append((pname, files))
            print("  [%s] 第%d集 %s：%d 张图 + 展示页" % (ep_label, idx, pname, len(files)))

        _render_ep_overview(ep_out, specs, title, pd, ep_text, idx, ep_label, files_all)
        manifest[ep_label] = files_all
        print("[%s] 第%d集 发布物就绪（%d 张图）" % (ep_label, idx,
              sum(len(f) for _, f in files_all)))

    _render_overview(out, specs, title, pd, ep_text, len(manifest), len(plat_specs),
                     manifest, all_eps)
    print("\n== %d 集的发布交付物已生成 ==" % len(manifest))
    print("   落点：<系列根>/<集目录>/发布/，本次覆盖 %d 个平台、%d 集；共享缓存与总览：<系列根>/发布/"
          % (len(plat_specs), len(manifest)))
    return out


def _render_page(pname, pinfo, title, pd, ep_text, synopsis, one_liner, tags, category, files, specs,
                 ep_idx=None, ep_label=None):
    ai = "是（发布时记得勾选『内容由AI生成』）" if pinfo.get("ai_declare") else "—"
    who = "第%d集《%s》" % (ep_idx, ep_label.split("、", 1)[1]) if (ep_idx and ep_label) else "全剧"
    lines = []
    lines.append("# %s · 发布交付页 — 《%s》 %s\n" % (pname, title, who))
    lines.append("> 本页为《%s》%s在【%s】的投稿/展示材料，字段已按 %s 要求预填，可直接复制粘贴。剧情介绍见 `剧情介绍.md`。\n"
                 % (title, who, pname, pname))
    lines.append("## 一、投稿表单字段（直接复制粘贴）\n")
    for field in pinfo.get("required_fields", []):
        val = _field_value(field, title, pd, synopsis, one_liner, tags, category, ep_text)
        lines.append("- **%s**：%s" % (field, val))
    lines.append("")
    lines.append("## 二、角色信息表\n")
    lines.append("| 角色 | 性别 | 简介 | 角色图 |")
    lines.append("|------|------|------|--------|")
    for _k, info in pd.get("characters", {}).items():
        nm = info.get("name", _k)
        gender = info.get("gender", "男")
        desc = info.get("desc") or ("《%s》中登场的角色。" % title)
        local = info.get("local", "")
        lines.append("| %s | %s | %s | %s |" % (nm, gender, desc, local))
    lines.append("")
    lines.append("## 三、封面/图片（已生成，直接上传）\n")
    for img, fn in files:
        lines.append("- **%s**（%dx%d，%s）：`%s`" % (img["label"], img["w"], img["h"], img["ratio"], fn))
    lines.append("")
    lines.append("## 四、特别要求（%s）\n" % pname)
    for s in pinfo.get("special", []):
        lines.append("- %s" % s)
    lines.append("")
    lines.append("> 通用视频规格：%s / %s / %s；单集时长下限 %s 秒。封面尺寸以本页文件名标注的像素为准。" %
                  (specs["video_common"]["format"], specs["video_common"]["resolution"],
                   specs["video_common"]["ratio"], specs["video_common"].get("min_duration", 60)))
    return "\n".join(lines)


def _render_ep_overview(ep_out, specs, title, pd, ep_text, ep_idx, ep_label, files_all):
    """每集一份交付清单（放在该集 发布/ 目录下）。"""
    lines = []
    lines.append("# 《%s》 第%d集《%s》· 本集交付清单\n" % (title, ep_idx, ep_label.split("、", 1)[1]))
    lines.append("生成时间：%s\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("本集交付物全部在 `<集目录>/发布/` 下，各平台子目录可直接上传；剧情介绍与表单字段均在该目录内。\n")
    lines.append("## 各平台交付物\n")
    for pname, files in files_all:
        lines.append("### %s（发布/%s/）\n" % (pname, pname))
        for img, fn in files:
            lines.append("- %s：%s（%dx%d，%s）" % (img["label"], fn, img["w"], img["h"], img.get("ratio")))
        lines.append("- 必填：%s" % "、".join(specs["platforms"][pname].get("required_fields", [])))
        if specs["platforms"][pname].get("ai_declare"):
            lines.append("- 须勾选『内容由AI生成』")
        lines.append("- 投稿表单：`%s_展示页.md`\n" % pname)
    lines.append("## 上传方式\n")
    lines.append("1. 视频用本集目录下的成片（`成片_epN_xxx_sub.mp4`）。")
    lines.append("2. 图片按上面清单对应上传：封面用 `封面_epN_WxH.jpg`，无文字小图用 `图片_*.jpg`。")
    lines.append("3. 简介/标签/性别简介等文案复制 `剧情介绍.md`。\n")
    lines.append("## 系列总览\n")
    lines.append("- 跨集总览、共享底图缓存：`<系列根>/发布/总览_交付清单.md`、`发布/_base/`\n")
    with open(os.path.join(ep_out, "本集交付清单.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _render_overview(out, specs, title, pd, ep_text, delivered, n_plat, manifest, all_eps):
    lines = []
    lines.append("# 《%s》· 多平台发布交付总览\n" % title)
    lines.append("生成时间：%s\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("剧集规格：**%s** ｜ 已生成交付物分集：**%d** ｜ 本次平台数：**%d**\n" % (_season_text(pd), delivered, n_plat))
    lines.append("\n## 通用视频规格\n")
    lines.append("- 格式：%s；分辨率：%s；比例：%s；音频：%s；水印：%s\n" %
                  (specs["video_common"]["format"], specs["video_common"]["resolution"],
                   specs["video_common"]["ratio"], specs["video_common"]["audio"],
                   specs["video_common"]["watermark"]))
    lines.append("- 单集时长下限：**%s 秒**（所有平台通用，短于 1 分钟不交付）\n" %
                  specs["video_common"].get("min_duration", 60))
    lines.append("\n## 分集交付物（每集一套，落在集目录里）\n")
    for idx, ep_label in enumerate(all_eps, 1):
        if ep_label not in manifest:
            continue
        n_img = sum(len(f) for _, f in manifest[ep_label])
        lines.append("- **第%d集《%s》**：`%s/发布/`（%d 张图 + 每平台展示页 + `本集交付清单.md`）" %
                     (idx, ep_label.split("、", 1)[1] if "、" in ep_label else ep_label, ep_label, n_img))
    lines.append("\n## 各平台交付物（按投稿表单所需图片数量出图，每集各出一张独立封面）\n")
    for ep_label, per_plat in manifest.items():
        for pname, files in per_plat:
            pinfo = specs["platforms"][pname]
            lines.append("### %s · %s\n" % (pname, ep_label))
            for img, fn in files:
                lines.append("- %s：%s（%dx%d，%s）" % (img["label"], fn, img["w"], img["h"], img["ratio"]))
            lines.append("- 必填：%s" % "、".join(pinfo.get("required_fields", [])))
            if pinfo.get("ai_declare"):
                lines.append("- 须勾选『内容由AI生成』")
            lines.append("- 投稿表单：`<集目录>/发布/%s/%s_展示页.md`\n" % (ep_label, pname))
    lines.append("\n## 通用剧情介绍\n")
    lines.append("- 每集一份：`发布/剧情介绍.md`（各平台简介/描述栏直接复制）\n")
    lines.append("\n## 分集封面说明\n")
    lines.append("- 每集封面存于 `<集目录>/发布/<平台>/封面_epN_WxH.jpg`，由生图技能按该集 COVER_PROMPT 生成（剧名/集名/钩子等文字全部由模型画入），生图失败才回退主海报/视频最亮帧。\n")
    lines.append("\n## 使用说明（渲好一集，就为那一集跑一次）\n")
    lines.append("1. 交付物按集落盘：`<集目录>/发布/<平台>/` 里是该平台所需图片（jpg，按真实尺寸）+ 展示页.md（投稿表单字段已预填，可复制），投稿时整目录直接用。")
    lines.append("2. `剧情介绍.md` 为该集共用的剧情文案，复制到各平台简介栏即可；剧名/简介/标签/角色性别简介均由 project_def.json 自动带全。")
    lines.append("3. 每集 `本集交付清单.md` 列清该集各平台要传什么，投稿逐条对照。")
    lines.append("4. 在 project_def.json 维护 `synopsis`/`one_liner`/`tags`/`category` 及各角色 `gender`/`desc`，可让所有文案自动带全。")
    lines.append("5. 重跑某集：`python platform_publish.py --project-root <系列根> --episodes <集号>`；强制重生成主海报加 `--regen-poster`。")
    lines.append("6. 只出交付物不重跑主海报：保持 `发布/_base/cover_main_base.png` 存在即可（已存在则复用）。")
    with open(os.path.join(out, "总览_交付清单.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _load_specs():
    with open(SPECS_PATH, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="fxai-short-drama 多平台发布交付套件生成器")
    ap.add_argument("--project-root", required=True, help="短剧系列根目录（含 project_def.json）")
    ap.add_argument("--platforms", default=None, help="逗号分隔平台名子集，如 抖音,快手（默认全部）")
    ap.add_argument("--cover-art", default=None, help="指定整剧主封面底图（本地图片路径），跳过 ComfyUI 生图")
    ap.add_argument("--no-comfyui", action="store_true", help="不调用 ComfyUI，主海报用已生成海报原图/现有最亮图")
    ap.add_argument("--regen-poster", action="store_true", help="强制重跑 ComfyUI 主海报")
    ap.add_argument("--ep-base", default=None, help='按集指定封面底图，格式 "集号=图片路径"（可逗号分隔多集），如 "9=<封面底图路径>"')
    ap.add_argument("--episodes", default=None,
                    help="只给指定分集出交付物（集号或集目录名，逗号分隔，如 \"1\" 或 \"1,2\"）；不给则全部分集")
    args = ap.parse_args()
    ep_base = {}
    if args.ep_base:
        for one in args.ep_base.split(","):
            if "=" not in one:
                raise SystemExit("--ep-base 格式应为 集号=图片路径，例：9=<封面底图路径>")
            k, v = one.split("=", 1)
            ep_base[k.strip()] = v.strip()
    gen_publish_kit(args.project_root, platforms=args.platforms, cover_art=args.cover_art,
                    use_comfyui=not args.no_comfyui, regen_poster=args.regen_poster,
                    ep_base=ep_base or None, episodes=args.episodes)
