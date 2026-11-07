# -*- coding: utf-8 -*-
"""fxai-slide-video · 整套入口

文案 → 加情感标签 → 逐段配音 → 收音频进 `音频/` → 检查配图 → 拼接 → 排字幕 → 烧录成片。
配图按内容定制：配音跑完后检查 `配图/` 是否齐；不齐则提示补齐后重跑（已有产物复用）。
"""
import argparse, os, sys, subprocess, json, re, pathlib, shutil, datetime

_HERE = pathlib.Path(__file__).parent
_SKILLS = _HERE.parent
sys.path.insert(0, str(_HERE))

import sv_media
import slide_spec
import sv_layout          # 项目存储格式的唯一执行点：目录怎么分、清单怎么写，全在这里

# 参考模板预设（名字见 references/02-模板库.md 的「模板登记表」）
# 「按 <模板名> 做一套」= sv_pipeline.py --preset <模板名>，自动带上这组参数。
# 上中下分栏（旧名「栏目图文」）的整套参数：字幕压进中区下沿、中区缓慢推近做呼吸感。
# shell=True 标记「带壳版式」：sub_bottom 是竖屏画布算出来的值，横屏时由 sv_media 按中区槽位重算。
_SZ_SPLIT = {"anim": False, "fade": 0.15, "speed": 1.05,
             "zoom": 0.05, "sub_bottom": 455, "sub_size": 46,
             "sub_color": "#FFD84D", "trans": "push_left", "trans_dur": 0.55, "shell": True}

# 左中右分栏：竖屏画布中槽满高贴边（上下零边距）、横屏画布中槽是 16:9 横带，两翼竖排文字。
# 🔴 zoom 必须为 0 —— 竖屏中槽已是满高，推近会把上下裁掉，直接毁掉「视频上下百分之百显示」。
# 字幕压在中槽底部安全区（中槽宽约 62%，字幕居中不会盖到两翼）。
_SZ_TRIO = {"anim": False, "fade": 0.15, "speed": 1.05,
            "zoom": 0.0, "sub_bottom": 96, "sub_size": 44,
            "sub_color": "#FFD84D", "trans": "push_left", "trans_dur": 0.55, "shell": True}

PRESETS = {
    "快嘴爆款": {"anim": True,  "fade": 0.3, "speed": 1.12},  # 深色大花字 + 页内弹入 + 黑场换页 + 快嘴
    "教程讲解": {"anim": False, "fade": 0.0, "speed": 1.0},   # 浅色步骤卡 + 逐步展开 + 平速
    "穿搭展示": {"anim": True,  "fade": 0.3, "speed": 1.05},  # 人物图满屏 + 底部暗卡列单品
    "商品展示": {"anim": True,  "fade": 0.3, "speed": 1.05},  # 商品图完整不裁切 + 底部暗卡列规格
    "写真人像": {"anim": True,  "fade": 0.4, "speed": 1.0},   # 人像大图 + 少量文案
    "对比测评": {"anim": False, "fade": 0.2, "speed": 1.1},   # 并排对比卡（A vs B）
    "清单合集": {"anim": True,  "fade": 0.3, "speed": 1.1},   # 清单逐项点亮
    "数据说话": {"anim": True,  "fade": 0.3, "speed": 1.1},   # 结果/战绩驱动（stats 版式）
    "成交逼单": {"anim": True,  "fade": 0.3, "speed": 1.05},  # 聊天反馈拉信任（chat）+ 三档套餐报价（pricing）
    # 固定节目壳（上下两条带不换）+ 中区换素材（图片/视频/海报），硬切换图
    "上中下分栏": _SZ_SPLIT,
    "上中下分栏静态背景": _SZ_SPLIT,
    "栏目图文": _SZ_SPLIT,      # 旧名，与「上中下分栏」同一套参数
    # 同族的竖版版式：左栏竖排标题 + 中槽 + 右栏竖排落款。
    # 中槽形状看**槽里素材的方向**（不是画布）：竖素材满高贴边、横素材 16:9 横带居中。
    "左中右分栏": _SZ_TRIO,
    "左中右": _SZ_TRIO,
}

_LOG_PATH = None
def set_log(path):
    global _LOG_PATH
    _LOG_PATH = path

def run(script, args):
    cmd = [sys.executable, str(script)] + [str(x) for x in args]
    print("$ %s %s" % (pathlib.Path(script).name, " ".join(cmd[2:])), flush=True)
    # 子进程输出同时写进项目 _tmp 日志（流水线自身打印由 sys.stdout 的 Tee 负责）
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       encoding="utf-8", errors="replace")
    out = r.stdout or ""
    print(out, end="", flush=True)
    if _LOG_PATH:
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as _f:
                _f.write(out)
        except Exception:
            pass
    if r.returncode != 0:
        raise SystemExit("失败：%s" % pathlib.Path(script).name)

def bgm_volume(vocal=None, lyrics=None, lyrics_file=None, given=None):
    """→ (音量, 是否带唱)。显式给音量优先；否则按人声档：纯器乐 0.20 / 带唱 0.15。"""
    import sv_bgm
    has_vocal = sv_bgm.is_vocal(vocal or "") or bool((lyrics or "").strip() or lyrics_file)
    if given is not None:
        return float(given), has_vocal
    return (sv_bgm.VOL_VOCAL if has_vocal else sv_bgm.VOL_INSTRUMENTAL), has_vocal

def main():
    ap = argparse.ArgumentParser(description="配图口播视频整套任务")
    ap.add_argument("--project", required=True, help="项目名（产物落 <工作区>/<项目名>/）")
    ap.add_argument("--root", default=None, help="工作区根目录（默认当前工作区）")
    ap.add_argument("--script", help="口播文案（无标签原文）")
    ap.add_argument("--spec", help="分段规格 JSON：每段 {text, template, fields}，单一事实源同时定义文案与配图")
    ap.add_argument("--anim", action="store_true",
                    help="营销模板：渲染镜头内动画（元素分层弹入），拼接时用动画素材")
    ap.add_argument("--rec", action="store_true",
                    help="动画改用「CDP 实时录制」（更顺滑、更快，单段只启 1 次 Chrome）；与 --anim 连用")
    ap.add_argument("--rec-fps", type=int, default=None,
                    help="录制片段归一化帧率（默认跟 --fps；录制时浏览器原生 30~60fps）")
    ap.add_argument("--rec-wait", type=int, default=150,
                    help="录制前等待(ms)：页面 load 后再等这么久才开录，避开白屏/半渲染（默认 150）")
    ap.add_argument("--fps", type=int, default=None,
                    help="成片输出帧率（默认 24；营销快节奏建议 30，与 --rec-fps 一致最好）")
    ap.add_argument("--size", default=None,
                    help="画布尺寸预设：竖屏(768×1280，默认) / 竖屏1366(768×1366) / 竖屏高清(1080×1920) / "
                         "横屏(1366×768) / 横屏小(1280×720) / 横屏高清(1920×1080)；也可直写 1366x768")
    ap.add_argument("--w", type=int, default=None,
                    help="画布/成片宽度（覆盖 --size）。页面按此宽度锁死出图")
    ap.add_argument("--h", type=int, default=None,
                    help="画布/成片高度（覆盖 --size）。内容超出此高度时，录制端自动模拟上滚")
    ap.add_argument("--no-scroll", action="store_true",
                    help="关闭「内容超出一屏自动上滚」（默认开；由录制端判断高度并模拟滚动）")
    ap.add_argument("--tag-file", help="标签文件（一行一个标签，按段顺序）")
    ap.add_argument("--speaker", help="音色 id（默认取配置第一个）")
    ap.add_argument("--fade", type=float, default=None,
                    help="换页淡入淡出时长(秒)：>0 则每页视频首尾淡到黑，实现黑场换页过渡（音频不淡）")
    ap.add_argument("--sub-bottom", type=int, default=None,
                    help="字幕距画面底部像素（栏目图文压进中区下沿，preset 已给 455）")
    ap.add_argument("--sub-size", type=int, default=None,
                    help="字幕字号（栏目图文媒体区矮，preset 给 46）")
    ap.add_argument("--sub-color", default=None,
                    help="字幕主色 #RRGGBB（栏目图文的黄字黑边，preset 给 #FFD84D）")
    ap.add_argument("--zoom", type=float, default=None,
                    help="栏目图文中区缓慢推近倍数（0=不推；preset 给 0.05 做呼吸感）")
    ap.add_argument("--trans", default=None,
                    help="段内多图转场（push_left 左右推移 / push_up 上下切换 / fade / cut / mix）")
    ap.add_argument("--trans-dur", type=float, default=None,
                    help="转场时长秒（preset 给 0.55；0.3 更利落）")
    ap.add_argument("--bgm", default=None,
                    help="背景配乐：给文件路径，或给 auto 让音乐生成器按成片时长现场生成（落 配乐/，"
                         "走 ComfyUI 接口取产物）。拼接后、烧录前混入，铺满整片、口播不降。")
    ap.add_argument("--bgm-volume", dest="bgm_volume", type=float, default=None,
                    help="BGM 音量（不给则自动：纯器乐 0.20、带唱 0.15；留白多的片可给 0.28）")
    ap.add_argument("--bgm-style", dest="bgm_style", default=None,
                    help="--bgm auto 的风格描述（默认内置 caption：明亮克制、中频留空）")
    ap.add_argument("--bgm-vocal", dest="bgm_vocal", default=None,
                    help="--bgm auto 的人声档：none（默认，纯器乐）或描述如「轻柔女声哼唱」"
                         "（带唱须同时给 --bgm-lyrics / --bgm-lyrics-file）")
    ap.add_argument("--bgm-lyrics", dest="bgm_lyrics", default=None,
                    help="--bgm auto 带唱时的歌词文本")
    ap.add_argument("--bgm-lyrics-file", dest="bgm_lyrics_file", default=None,
                    help="--bgm auto 带唱时从 .txt 读歌词")
    ap.add_argument("--bgm-model", dest="bgm_model", default=None, choices=["minimax3", "yue2"],
                    help="--bgm auto 用哪个音乐模型（默认 minimax3）")
    ap.add_argument("--cover", default=None,
                    help="视频封面 PNG：烧完字幕就铺首帧。铺封面排在 BGM 之前，"
                         "BGM 最后一道从第 0 帧（封面第一帧）铺满整片 —— 顺序由管线保证")
    ap.add_argument("--cover-frames", dest="cover_frames", type=int, default=None,
                    help="封面段帧数（默认 10，按帧不按秒）")
    ap.add_argument("--speed", type=float, help="语速 0.5~2.0（营销快节奏建议 1.1~1.2）")
    ap.add_argument("--instruct-text", dest="instruct_text",
                    help="情感/方言语气指令（如 请用四川话表达。），透传给 voice_gen")
    ap.add_argument("--preset", choices=sorted(PRESETS.keys()),
                    help="参考模板预设（见 references/02-模板库.md）：自动带上一组 --anim/--fade/--speed")
    ap.add_argument("--theme", help="配图样式名（themes/<名>.css，默认 深夜蓝金）；见 references/07-样式集.md")
    ap.add_argument("--theme-file", help="自定义样式 CSS 文件路径（优先于 --theme）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    ap.add_argument("--mode", choices=["public", "private"], default="public",
                    help="合规判罚尺度：public=公域平台（默认）；private=私域版放行联系方式")
    ap.add_argument("--strict", action="store_true", help="合规预检出现 ERROR 即中断")
    ap.add_argument("--no-compliance", dest="compliance", action="store_false",
                    help="跳过合规预检")
    ap.add_argument("--no-title", dest="title", action="store_false",
                    help="跳过发布标题生成")
    ap.add_argument("--force-title", action="store_true", help="标题文件已存在也重新生成")
    ap.add_argument("--topic", help="发布标题的主题词（默认从首段文案提取）")
    ap.add_argument("--main-title", help="指定主推标题（脚本只做校验与落盘）")
    a = ap.parse_args()

    # 画布尺寸：--w/--h 显式 > --size 预设 > preset 里的 size > 默认竖屏 768×1280
    # 解析完 a.w / a.h 一定是具体数字，后面所有地方直接用，无需再写 `or 768`。
    a.w, a.h, _sz_src = sv_layout.resolve_size(
        a.size or PRESETS.get(a.preset or "", {}).get("size"), a.w, a.h)

    # 预设打底；显式给的参数优先覆盖
    _p = PRESETS.get(a.preset or "", {})
    _u_bottom, _u_size = a.sub_bottom, a.sub_size    # 用户显式给的值，横屏修正不许碰
    if _p.get("anim"):
        a.anim = True
    if a.rec:
        a.anim = True          # 录制必然要动画
    if a.fade is None:
        a.fade = _p.get("fade", 0.0)
    if a.speed is None:
        a.speed = _p.get("speed")
    if a.sub_bottom is None:
        a.sub_bottom = _p.get("sub_bottom")
    if a.sub_size is None:
        a.sub_size = _p.get("sub_size")
    if a.sub_color is None:
        a.sub_color = _p.get("sub_color")
    if a.zoom is None:
        a.zoom = _p.get("zoom")
    if a.trans is None:
        a.trans = _p.get("trans")
    if a.trans_dur is None:
        a.trans_dur = _p.get("trans_dur")

    # preset 里那套字幕位置是 768×1280 默认档的竖屏值（455 会掉进下带、96 会掉出中槽）：
    # 画布一变（竖屏高清 / 横屏 / 自定尺寸）就交回 sv_media 按中区槽位重算，字号按画布高度等比缩。
    # 只改 preset 来源的值，用户显式给的 --sub-bottom / --sub-size 一个字都不动。
    if not (a.w == 768 and a.h == 1280):
        if _u_bottom is None and a.sub_bottom and (_p.get("shell") or a.sub_bottom > a.h * 0.4):
            print("[size] 非默认画布：preset 字幕位置 %d 是 768 档竖屏值，改由中区槽位推算" % a.sub_bottom,
                  flush=True)
            a.sub_bottom = None
        if _u_size is None and a.sub_size and a.sub_size > 40:
            a.sub_size = max(24, int(round(a.sub_size * a.h / 1280.0)))
    if a.preset:
        print("[preset] %s → anim=%s fade=%s speed=%s zoom=%s trans=(%s/%.2fs) "
              "sub=(bottom=%s size=%s color=%s)"
              % (a.preset, a.anim, a.fade, a.speed, a.zoom, a.trans,
                 a.trans_dur or 0.0, a.sub_bottom, a.sub_size, a.sub_color), flush=True)

    # 🔴 项目存储格式唯一入口（sv_layout.py）：一个项目 = 一个独立文件夹。
    #    成品 / 文案 / 字幕 / 清单直接放项目根；成组的配图进 配图/、音频进 音频/。
    #    layout() 会把根写进 FXAI_PROJECTS_ROOT —— 子脚本（script_gen / voice_gen）按同一个根解析，
    #    杜绝「配图在工作区 A、音频在全局根 B」的产物分裂。
    L = sv_layout.layout(a.project, a.root)
    base, img_dir = L["root"], L["image"]
    print("[layout] 项目目录：%s" % base, flush=True)

    # 0) 产物归位：把散落在子目录的成品搬回项目根（在开日志 Tee 之前跑，规避占用中的日志文件被搬动）
    for s, t in sv_layout.migrate(a.project, a.root):
        print("[migrate] %s -> %s" % (s, t), flush=True)

    # 🔴 源文件归位：把 --spec / --script 拷进项目目录，使项目自包含
    #    （无论调用方把源文件放哪儿、从哪个 cwd 调用，项目目录始终是单一事实源；换文件夹也不散）
    def _ingest(path):
        if not path:
            return path
        p = pathlib.Path(path)
        if not p.is_absolute():
            p = pathlib.Path.cwd() / p
        dst = base / p.name
        if p.resolve() == dst.resolve():
            return str(dst)
        if p.exists():
            shutil.copy2(str(p), str(dst))
            print("[layout] 源文件已归入项目目录：%s" % dst.name, flush=True)
            return str(dst)
        return str(p)
    a.spec = _ingest(a.spec)
    a.script = _ingest(a.script)

    # 🔴 运行日志落项目 _tmp/（工作区根目录保持干净）
    _tmp = base / "_tmp"
    _tmp.mkdir(parents=True, exist_ok=True)
    _ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _log = _tmp / ("pipeline_%s.log" % _ts)
    class _Tee:
        def __init__(self, *s):
            self.s = s
        def write(self, x):
            for o in self.s:
                o.write(x)
        def flush(self):
            for o in self.s:
                o.flush()
    sys.stdout = _Tee(sys.stdout, open(str(_log), "a", encoding="utf-8", buffering=1))
    sys.stderr = _Tee(sys.stderr, open(str(_log), "a", encoding="utf-8", buffering=1))
    set_log(str(_log))
    print("[log] 运行日志 -> %s" % _log, flush=True)

    # 0) 分段规格：单一事实源，同时产出文案(=字幕)与配图，二者天然对应
    #    ⚠️ 配图「延后」到 TTS 之后再渲染 —— 这样能拿到每段音频时长，交给录制端按「说话进度」定滚动节奏。
    #    （两阶段解耦：页面设计阶段只管把内容按实际宽高排好；滚动由录制阶段自己判断+模拟。）
    specs, _titles = None, None
    if a.spec:
        specs = slide_spec.load_specs(a.spec)
        (base / "口播文案.md").write_text("\n\n".join(slide_spec.texts_of(specs)), encoding="utf-8")
        # 发布标题与文案同批产出；合规预检在文案阶段完成，不推迟到发布
        _titles = None
        if a.title:
            import sv_title
            _titles = sv_title.ensure(base, specs, project=a.project,
                                      spec_name=pathlib.Path(a.spec).name,
                                      topic=a.topic, main=a.main_title,
                                      force=a.force_title, dry=a.dry_run)
        if a.compliance:
            import sv_compliance
            print("[合规] mode=%s  共 %d 段" % (a.mode, len(specs)), flush=True)
            _iss = sv_compliance.check_specs(specs, mode=a.mode)
            if _titles is not None:
                _iss += sv_compliance.check_titles(_titles)
            _cnt = sv_compliance.report(_iss)
            if a.strict and _cnt["ERROR"]:
                raise SystemExit("[合规] strict 模式下存在 ERROR，改完再跑")
        if a.dry_run:
            print("[dry-run] 跳过渲染配图（%d 段）；将用：anim=%s record=%s rec_fps=%s 画布=%dx%d(%s)"
                  % (len(specs), a.anim, a.rec, a.rec_fps or a.fps or 30,
                     a.w, a.h, _sz_src), flush=True)

    s_script = _SKILLS / "fxai-voiceover-script" / "script_gen.py"
    s_voice = _SKILLS / "fxai-voiceover" / "voice_gen.py"

    # 1) 文案（加不加标签由引擎白名单决定：只有 CosyVoice 走标签格式）
    sys.path.insert(0, str(_SKILLS / "fxai-voiceover"))
    import tts_client as tc
    engine = tc.default_engine()

    plain = sv_layout.find(base, "口播文案.md")
    if a.script and (base / "口播文案_标签.md").exists() is False:
        a1 = ["--project", a.project, "--text-file", str(plain), "--engine", engine]
        if a.tag_file:
            a1 += ["--tag-file", str(base / "口播标签.txt")]
        if a.dry_run:
            print("$ script_gen.py", " ".join(a1))
        else:
            run(s_script, a1)
            # 文案归位：script_gen 写在 doc/，本技能要求文案在项目根
            in_doc = base / "doc" / "口播文案_标签.md"
            if in_doc.exists():
                shutil.move(str(in_doc), str(base / "口播文案_标签.md"))
                print("[layout] 口播文案_标签.md -> 项目根", flush=True)

    tagged = sv_layout.find(base, "口播文案_标签.md")
    if tagged is None:
        raise SystemExit("没找到 口播文案_标签.md，先跑加标签那一步")

    # 2) 配音
    a2 = ["--project", a.project, "--script", str(tagged)]
    if a.speaker:
        a2 += ["--speaker", a.speaker]
    if a.speed:
        a2 += ["--speed", str(a.speed)]
    if a.instruct_text:
        a2 += ["--instruct-text", a.instruct_text]
    if a.dry_run:
        print("$ voice_gen.py", " ".join(a2))
        return 0
    run(s_voice, a2)

    # 2.5) 段音频归位到 音频/（voiceover 默认落在项目根，成组收进子目录）
    music = L["music"]
    music.mkdir(exist_ok=True)
    for w in sorted(base.glob(f"{a.project}*.wav")):
        shutil.move(str(w), str(music / w.name))
        mk = base / (w.name + ".norm")       # 响度归一标记随音频一起归位（重跑免重测）
        if mk.exists():
            shutil.move(str(mk), str(music / mk.name))
        print("[music]", w.name, flush=True)

    # 2.55) 样式：命令行 > segments.json 顶层 theme > 默认（深夜蓝金）
    _theme = a.theme_file or a.theme
    if not _theme and a.spec:
        _theme = slide_spec.spec_theme(a.spec)
    if _theme:
        print("[theme] 样式：%s" % _theme, flush=True)

    # 2.6) 渲染配图（延后到这里：音频已就绪 → 每段时长交给录制端，滚动跟着说话进度）
    if specs is not None and not a.dry_run:
        try:
            import gpu
            gpu.report()          # 全局检测一次：浏览器渲染 / 视频编码（进程内缓存）
        except Exception:
            pass
        _auds = sorted(music.glob(f"{a.project}*.wav"),
                       key=lambda p: int(re.search(r"(\d+)\.wav$", p.name).group(1)))
        audio_durs = [sv_media.dur(p) for p in _auds]
        slide_spec.render_specs(specs, img_dir, tmp_dir=base / "_tmp", anim=a.anim,
                                record=a.rec, rec_fps=(a.rec_fps or a.fps or 30),
                                rec_wait=a.rec_wait, w=a.w, h=a.h,
                                scroll=("none" if a.no_scroll else "auto"),
                                audio_durs=audio_durs, theme=_theme)
        slide_spec.verify(specs, img_dir)
        print("[spec] 已按 %d 段规格渲染配图（滚动节奏按音频时长）；文案已写入 口播文案.md"
              % len(specs), flush=True)

    # 3) 检查配图
    auds = sorted(music.glob(f"{a.project}*.wav"),
                  key=lambda p: int(re.search(r"(\d+)\.wav$", p.name).group(1)))
    imgs = sorted(img_dir.glob("*.png"))
    print("\n[配图检查] 音频 %d 段 / 配图 %d 张" % (len(auds), len(imgs)), flush=True)
    if len(imgs) < len(auds):
        print("配图不够，还差 %d 张。用 slides.py 的版式模板补齐到 配图/ 后，再跑："
              % (len(auds) - len(imgs)))
        print('  python sv_media.py --project "%s" --root "%s"' % (a.project, a.root))
        return 1

    # 4) 拼接 + 字幕 + 烧录
    print("\n[合成] 开始拼接与烧录", flush=True)
    sys.argv = ["sv_media.py", "--project", a.project, "--fade", str(a.fade)]
    if a.sub_bottom is not None:
        sys.argv += ["--sub-bottom", str(int(a.sub_bottom))]
    if a.sub_size is not None:
        sys.argv += ["--sub-size", str(int(a.sub_size))]
    if a.sub_color:
        sys.argv += ["--sub-color", str(a.sub_color)]
    if a.zoom:
        sys.argv += ["--zoom", str(a.zoom)]
    if a.trans:
        sys.argv += ["--trans", str(a.trans)]
    if a.trans_dur:
        sys.argv += ["--trans-dur", str(a.trans_dur)]
    if a.bgm:
        if str(a.bgm).strip().lower() == "auto":
            sys.argv += ["--bgm", "auto"]          # 混音端现场生成（走音乐生成器接口）
        else:
            # 转绝对路径：混音端 cwd 是项目根，这里给的文件若相对当前目录会解析错
            _bgm = pathlib.Path(a.bgm)
            if not _bgm.is_absolute():
                _bgm = pathlib.Path(os.path.abspath(str(_bgm)))
                if not _bgm.is_file() and (base / pathlib.Path(a.bgm)).is_file():
                    _bgm = base / pathlib.Path(a.bgm)
            if not _bgm.is_file():
                raise SystemExit("找不到配乐文件：%s（想自动生成就写 --bgm auto，见 04 册「BGM 环节」）"
                                 % a.bgm)
            sys.argv += ["--bgm", str(_bgm)]
        _is_auto = str(a.bgm).strip().lower() == "auto"
        # 音量：显式给出优先；否则纯器乐 0.20、带唱 0.15（与混音端同一口径）
        _bgmvol, _bgm_is_vocal = bgm_volume(
            a.bgm_vocal if _is_auto else None,
            a.bgm_lyrics if _is_auto else None,
            a.bgm_lyrics_file if _is_auto else None,
            a.bgm_volume)
        if a.bgm_volume is not None:
            sys.argv += ["--bgm-volume", str(a.bgm_volume)]
        if a.bgm_style:
            sys.argv += ["--bgm-style", a.bgm_style]
        if a.bgm_vocal:
            sys.argv += ["--bgm-vocal", a.bgm_vocal]
        if a.bgm_lyrics:
            sys.argv += ["--bgm-lyrics", a.bgm_lyrics]
        if a.bgm_lyrics_file:
            sys.argv += ["--bgm-lyrics-file", str(a.bgm_lyrics_file)]
        if a.bgm_model:
            sys.argv += ["--bgm-model", a.bgm_model]
    if a.cover:
        # 相对路径按项目根补全：sv_media 的 cwd 是项目根，这里给相对路径会解析错
        _cv = pathlib.Path(a.cover)
        if not _cv.is_absolute() and (base / _cv).is_file():
            _cv = base / _cv
        if not _cv.is_file():
            raise SystemExit("找不到封面图：%s" % a.cover)
        sys.argv += ["--cover", str(_cv)]
        if a.cover_frames:
            sys.argv += ["--cover-frames", str(a.cover_frames)]
    if a.root:
        sys.argv += ["--root", str(a.root)]
    if a.fps:
        sys.argv += ["--fps", str(a.fps)]
    if a.w:
        sys.argv += ["--w", str(a.w)]
    if a.h:
        sys.argv += ["--h", str(a.h)]
    sv_media.main()

    # 5) 交付清单落项目根（人读 交付清单.md + 机器读 delivery.json），路径相对项目根
    try:
        _bgm_txt = "无配乐"
        if a.bgm:
            _voc = "，带唱" if _bgm_is_vocal else ""
            if str(a.bgm).strip().lower() == "auto":
                _bgm_txt = "含背景配乐（自动生成%s，vol %.2f）" % (_voc, _bgmvol)
            else:
                _bgm_txt = "%s（vol %.2f）" % (pathlib.Path(a.bgm).name, _bgmvol)
        _extra = {"publish": _titles, "bgm": _bgm_txt}
        if a.cover:
            _extra["cover"] = {"png": pathlib.Path(a.cover).name,
                               "frames": a.cover_frames or 10,
                               "note": "铺首帧排在 BGM 之前，配乐从封面第一帧起铺满"}
        sv_layout.write_delivery(a.project, a.root, extra=_extra,
                                 extra_md=(sv_title.delivery_block(_titles) if _titles else None))
    except Exception as e:
        print("[warn] 交付清单写入失败（成片本身已生成）：%s" % e, flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
