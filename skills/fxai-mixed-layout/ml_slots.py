# -*- coding: utf-8 -*-
"""fxai-mixed-layout · 素材槽（slot）核心

一条「槽里填什么」的公共实现：不管槽在整屏还是在栏目的中区，填进去的东西最终都要
归一成**一条 w×h 的无声视频**，交给外层去叠壳、叠字幕、拼接。

三种填法在这里被抹平：
    图片（单张 / 多张轮播）  → ffmpeg 归一化 + xfade 转场（build_reel）
    视频片段                 → 原样取用，只做尺寸与时长对齐
    网页（文字卡/长图/真网页）→ 由调用方注入 render_page 回调（录制端在技能里，不在这）

🔴 **本模块零业务依赖**：不 import 任何 fxai 技能，ffmpeg / 编码器 / 帧率由调用方
`configure()` 注入。方向只能是「技能 → 本模块」，反过来会让公共件绑死在某个技能上。
"""
import subprocess, pathlib

# ---------- 运行参数（调用方注入，不在这里猜环境） ----------
CFG = {
    "ffmpeg": "ffmpeg",     # 带 libass 的 ffmpeg 路径（技能侧从全局配置解析后塞进来）
    "ffprobe": "",          # 留空则按 ffmpeg 同目录推
    "encoder": "libx264",   # h264_nvenc / libx264
    "fps": 24,
}


def configure(ffmpeg=None, ffprobe=None, encoder=None, fps=None):
    """注入运行环境。技能侧一般在 import 后立刻调一次。"""
    if ffmpeg:
        CFG["ffmpeg"] = str(ffmpeg)
    if ffprobe is not None:
        CFG["ffprobe"] = str(ffprobe)
    if encoder:
        CFG["encoder"] = str(encoder)
    if fps:
        CFG["fps"] = int(fps)
    return dict(CFG)


def ffmpeg_bin():
    return CFG["ffmpeg"]


def ffprobe_bin():
    f = CFG["ffmpeg"]
    if CFG["ffprobe"]:
        return CFG["ffprobe"]
    if f not in ("ffmpeg", ""):
        cand = str(pathlib.Path(f).with_name("ffprobe.exe"))
        if pathlib.Path(cand).is_file():
            return cand
    return "ffprobe"


def dur(p):
    """素材时长（秒）。读不到返回 0。"""
    o = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try:
        return float((o.stdout or "0").strip())
    except ValueError:
        return 0.0


def wh(p):
    """素材宽高 `(w, h)`。图片和视频同一个探针，读不到返回 `(0, 0)`。

    用来判「这段素材是竖构图还是横构图」—— 版式自适应用的判据只在这里。
    """
    o = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    line = (o.stdout or "").strip().splitlines()
    try:
        w, h = line[0].split(",")[:2]
        return (int(w), int(h))
    except (IndexError, ValueError):
        return (0, 0)


# ---------- 素材清单归一 ----------
_VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


def is_video(p):
    return str(p or "").lower().endswith(_VIDEO_EXT)


def _abs(p, base_dir):
    """相对路径按规格文件所在目录解析（绝对路径原样返回）。"""
    if not p:
        return p
    p = pathlib.Path(str(p))
    if p.is_absolute() or not base_dir:
        return str(p)
    cand = pathlib.Path(base_dir) / p
    return str(cand if cand.exists() else p)


def _fit_of(v):
    v = str(v or "").lower()
    if v in ("contain", "完整", "不裁切"):
        return "contain"
    if v in ("blur", "虚化", "模糊铺底"):
        return "blur"
    if v in ("height", "满高", "贴边", "上下贴边", "按高"):
        return "height"
    return "cover"


def normalize_items(media, base_dir=None, seg_fit=None):
    """把一段的 `media` 归一成 [{"path", "kind", "fit", "page"?}]。

    media 可以是一张（字符串）或一叠（list）——一叠时合成端会做成段内轮播（幻灯片）。
    元素写法：
      "素材/a.png"                        图片（默认）
      "素材/b.mp4" / {"src": "b.mp4"}      视频片段（直接贴）
      {"src": "长图.png", "scroll": true}  长图上下滚动（走网页槽）
      {"page": {"type": "text", ...}}      网页槽（文字卡 / 长图 / 自定义 HTML）
      {"url": "https://..."}               直接录一个真实网页
      {"src": ..., "fit": "contain"}       单条覆盖段级 fit

    后三种最终都归成 kind="page"：由调用方渲染 HTML → 录制 → 一条视频流垫进槽里。
    """
    raw = list(media) if isinstance(media, (list, tuple)) else ([media] if media else [])
    items = []
    for it in raw:
        page = None
        if isinstance(it, dict):
            src = it.get("src") or it.get("path") or it.get("media")
            fit = it.get("fit") or it.get("media_fit") or seg_fit
            page = it.get("page")
            if page is None and it.get("url"):
                page = {"type": "url", "url": it["url"]}
            elif page is None and src and it.get("scroll"):
                page = {"type": "longimg", "src": src}
                for k in ("caption", "bg", "accent"):
                    if it.get(k):
                        page[k] = it[k]
            elif page is not None and not isinstance(page, dict):
                page = {"type": "text"}
        else:
            src, fit = it, seg_fit
        if not src and not page:
            continue
        src = _abs(src, base_dir) if src else ""
        f = _fit_of(fit)
        if page is not None:
            page = dict(page)
            if page.get("src"):
                page["src"] = _abs(page["src"], base_dir)
            items.append({"path": str(src or ""), "kind": "page", "fit": f, "page": page})
            continue
        items.append({"path": str(src), "kind": "video" if is_video(src) else "image",
                      "fit": f, "zoom": it.get("zoom") if isinstance(it, dict) else None})
    return items

# zoompan 的裁剪窗口是整数像素，直接按目标尺寸出图会「有的帧不动、有的帧跳一整像素」，
# 24fps 下就是肉眼可见的抖动。放大 4 倍进 zoompan、输出 2 倍再 lanczos 降回来，
# 单帧位移落到亚像素级（实测逐帧差异波动从 0.02↔2.5 收到 0.3↔0.8）。
FRAME_SS = 4        # 送进 zoompan 的放大倍数
FRAME_ZOUT = 2      # zoompan 输出倍数（之后 lanczos 降到目标尺寸）
# 推近每帧至少在超采样空间走这么多像素，否则取整误差占比过大（长段推近太慢 = 12Hz 闪烁）。
# 换算：推近总位移 ≈ w*FRAME_SS*zoom，除以 2.2 就是允许的最长帧数（768 宽 / 5% ≈ 70 帧 ≈ 3 秒）。
# 超过就截断 —— 推近在前 3 秒内走完，之后保持静止（静止帧无抖动，比慢慢挪更好看）。
ZOOM_MIN_STEP = 2.2

# 段内多图的转场：用户写的名字 → xfade 的 transition。
# "cut" 用 0.04s 的 xfade 代替（半帧，等同硬切），这样整条轮播只有一条 xfade 链。
# trans 传 list 时按边界序号取（第 k 个元素管「第 k→k+1 张」那一下），不够就循环 ——
# 一条片子里每个转场可以各不相同（PPT 翻页感：推移 / 翻书 / 圆展 / 擦除混着来）。
REEL_TRANS = {
    # 推移类（PPT 的「左右滑动」）
    "push_left": "slideleft", "左移": "slideleft", "左右推移": "slideleft", "推移": "slideleft", "左滑": "slideleft",
    "push_right": "slideright", "右移": "slideright", "右滑": "slideright",
    "push_up": "slideup", "上移": "slideup", "上下切换": "slideup", "上切": "slideup", "上滑": "slideup",
    "push_down": "slidedown", "下移": "slidedown", "下切": "slidedown", "下滑": "slidedown",
    # 柔滑类（smooth*：带加速减速，最接近「翻书 / 翻页」）
    "smooth_left": "smoothleft", "柔滑左移": "smoothleft", "翻书": "smoothleft", "翻页": "smoothleft", "翻片": "smoothleft",
    "smooth_right": "smoothright", "柔滑右移": "smoothright", "右翻": "smoothright",
    "smooth_up": "smoothup", "柔滑上移": "smoothup", "上翻": "smoothup",
    "smooth_down": "smoothdown", "柔滑下移": "smoothdown", "下翻": "smoothdown",
    # 溶解 / 黑场 / 闪白
    "fade": "fade", "淡入": "fade", "叠化": "fade",
    "dissolve": "dissolve", "溶解": "dissolve",
    "fadeblack": "fadeblack", "黑场": "fadeblack", "闪黑": "fadeblack",
    "fadewhite": "fadewhite", "闪白": "fadewhite",
    # 擦除类
    "wipe_left": "wipeleft", "擦除": "wipeleft", "左擦": "wipeleft",
    "wipe_right": "wiperight", "右擦": "wiperight",
    "wipe_up": "wipeup", "上擦": "wipeup",
    "wipe_down": "wipedown", "下擦": "wipedown",
    # 形状展开
    "circle": "circlecrop", "圆形": "circlecrop", "圆切": "circlecrop",
    "circle_open": "circleopen", "圆展": "circleopen", "圆形展开": "circleopen",
    "circle_close": "circleclose", "圆收": "circleclose", "圆形收起": "circleclose",
    "rect": "rectcrop", "矩形": "rectcrop", "矩形切": "rectcrop",
    # 特效类
    "pixelize": "pixelize", "像素化": "pixelize",
    "diag_tl": "diagtl", "对角": "diagtl", "斜切": "diagtl",
    "diag_tr": "diagtr", "diag_bl": "diagbl", "diag_br": "diagbr",
    "slice_h": "hlslice", "横切": "hlslice", "百叶横": "hlslice",
    "hr_slice": "hrslice", "横切2": "hrslice",
    "slice_v": "vuslice", "竖切": "vuslice", "百叶竖": "vuslice",
    "vd_slice": "vdslice", "竖切2": "vdslice",
    "radial": "radial", "雷达": "radial", "雷达扫描": "radial",
    "distance": "distance", "缩放": "distance", "推进": "distance",
    "cut": "cut", "硬切": "cut",
    "mix": "mix", "轮换": "mix",
}


def _common(enc=None, fps=None, audio=False):
    enc = enc or CFG["encoder"]
    if enc == "h264_nvenc":
        v = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "20", "-b:v", "0"]
    else:
        v = ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
    v += ["-pix_fmt", "yuv420p", "-r", str(fps if fps is not None else CFG["fps"])]
    if not audio:
        v += ["-an"]
    return v


def _ffmpeg(build_cmd, desc):
    """跑 ffmpeg；显卡编码失败自动回落 libx264 重试一次。"""
    enc = CFG["encoder"]
    r = subprocess.run(build_cmd(enc), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 and enc != "libx264":
        print("[warn] %s 用 %s 失败，回落 libx264 重试" % (desc, enc), flush=True)
        CFG["encoder"] = "libx264"
        r = subprocess.run(build_cmd("libx264"), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    return r


def _trans_of(name, idx=0):
    """转场名 → (xfade transition, 是否硬切)。list 按序号轮换；mix 轮换三种常用转场。"""
    if isinstance(name, (list, tuple)):
        if not name:
            name = "push_left"
        name = name[idx % len(name)]
    key = str(name or "push_left").strip().lower()
    t = REEL_TRANS.get(key) or REEL_TRANS.get(str(name).strip()) or "slideleft"
    if t == "mix":
        t = ("slideleft", "slideup", "fade")[idx % 3]
    return ("fade", True) if t == "cut" else (t, False)


def cell_chain(idx, w, h, fps, fit="cover", zoom=0.0, nfr=1, bg="0x0B0B0C"):
    """第 idx 路输入 → w×h 的槽画面，返回以 [c<idx>] 结尾的滤镜串。

    fit=cover  ：撑满裁切（默认，可带推近）
    fit=height ：**按高度对齐**（高度恒等于槽高、水平居中、左右溢出对称裁掉）——
                 左中右分栏专用：视频上下百分之百显示、上下零边距，只有左右被裁/补
    fit=contain：完整装下 + 两侧补底色（不推近，推近会把补边一起放大）
    fit=blur   ：完整装下 + 同图虚化铺底（竖版海报/竖屏素材专用：不留黑边、也不裁内容）
    zoom>0     ：超采样推近（见 FRAME_SS 注释）
    """
    i = "[%d:v]" % idx
    out = "[c%d]" % idx
    f = str(fit or "cover").lower()
    if f == "height":
        # 高度锁死为槽高（scale=-2 保持比例、宽度取偶数），宽度超出就居中裁、不足就补底色。
        # 🔴 顺序固定：先 crop 到 min(iw,W) 再 pad，否则 pad 出来的黑边会被一起裁掉。
        return (i + "scale=-2:%d:flags=lanczos,"
                    "crop='min(iw,%d)':%d:'(iw-min(iw,%d))/2':0,"
                    "pad=%d:%d:(ow-iw)/2:0:color=%s,setsar=1,fps=%s%s"
                % (h, w, h, w, w, h, bg, fps, out))
    if f == "blur":
        return (i + "split[bg%d][fg%d];"
                    "[bg%d]scale=%d:%d:force_original_aspect_ratio=increase:flags=lanczos,"
                    "crop=%d:%d,gblur=sigma=%d,eq=brightness=-0.06:saturation=1.22,setsar=1[bgb%d];"
                    "[fg%d]scale=%d:%d:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1[fgc%d];"
                    "[bgb%d][fgc%d]overlay=(W-w)/2:(H-h)/2:shortest=1,fps=%s,setsar=1%s"
                % (idx, idx,
                   idx, w, h, w, h, max(8, w // 28), idx,
                   idx, w, h, idx,
                   idx, idx, fps, out))
    if f == "contain":
        return (i + "scale=%d:%d:force_original_aspect_ratio=decrease:flags=lanczos,"
                    "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s,setsar=1,fps=%s%s"
                % (w, h, w, h, bg, fps, out))
    if float(zoom or 0) > 0:
        W, H = w * FRAME_SS, h * FRAME_SS
        OW, OH = w * FRAME_ZOUT, h * FRAME_ZOUT
        z = float(zoom)
        # 帧数封顶：推近必须在 3 秒左右走完，速度低于阈值就改成「推完保持」
        nz = min(max(1, int(nfr)), max(36, int(w * FRAME_SS * z / ZOOM_MIN_STEP)))
        return (i + "scale=%d:%d:force_original_aspect_ratio=increase:flags=lanczos,"
                    "crop=%d:%d,setsar=1,"
                    "zoompan=z='min(1+%.4f*on/%d,%.4f)':d=1:"
                    "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=%dx%d:fps=%s,"
                    "scale=%d:%d:flags=lanczos,setsar=1,fps=%s%s"
                % (W, H, W, H, z, nz, 1 + z, OW, OH, fps, w, h, fps, out))
    return (i + "scale=%d:%d:force_original_aspect_ratio=increase:flags=lanczos,"
                "crop=%d:%d,fps=%s,setsar=1%s" % (w, h, w, h, fps, out))


def build_reel(items, out, total, w, h, fps=None, trans="push_left",
               trans_dur=0.55, zoom=0.0, bg="0x0B0B0C", durs=None):
    """段内多张素材 → 一条覆盖 total 秒的槽内轮播片（无声，w×h）。

    这是「幻灯片效果」的本体：几张图在槽里按转场逐张换（推移 / 翻书 / 圆展 / 擦除），
    每张自带缓慢推近。出来后当普通视频素材垫进槽位。

    durs  每张的目标时长列表（长度 = 素材张数）；缺省均分。给了就按旁白节奏走 ——
          实际播放时长会按 xfade 重叠量（转场互相吃掉的时间）自动校正，总和仍等于 total。
          转场本身的类型用 trans：标量（全都用一种）或 list（第 k 个管第 k→k+1 张边界）。
    zoom  全局推近量；item 里带 {"zoom": x} 可单张覆盖（per-shot 运镜）。
    """
    ofps = int(fps or CFG["fps"])
    items = [it for it in items if it and it.get("path")]
    if not items:
        raise ValueError("build_reel: 没有可用素材")
    trans_dur = float(trans_dur or 0.0)

    ins, chains = [], []
    if len(items) == 1:
        it = items[0]
        ins += (["-stream_loop", "-1", "-i", it["path"]] if it.get("kind") == "video"
                else ["-loop", "1", "-framerate", str(ofps), "-i", it["path"]])
        nfr = max(1, int(round(total * ofps)))
        # 🔴 链尾必须再补一次 fps：trim/setpts 之后帧率元信息会退成 1/0，
        # 新版 ffmpeg 容忍、7.0.x 直接判 xfade 输入「不是恒定帧率」而失败。
        chains.append(cell_chain(0, w, h, ofps, it.get("fit"), it.get("zoom", zoom), nfr)
                      + ";[c0]trim=duration=%.3f,setpts=PTS-STARTPTS,format=yuv420p,"
                        "fps=%d[v]" % (total, ofps))
        fc = ";".join(chains)
        prev = "[v]"
    else:
        n = len(items)
        pair = [_trans_of(trans, k) for k in range(n - 1)]
        if durs:
            Ls = [max(0.3, float(x)) for x in durs]
            if len(Ls) != n:
                raise ValueError("build_reel: durs 数量（%d）与素材（%d）不符" % (len(Ls), n))
        else:
            Ls = [total / n] * n

        def _td_of(Ls):
            """每条边界的转场时长：硬切压到半帧，其余 trans_dur，且不吃掉相邻两张各自 60% 以上。"""
            return [0.04 if is_cut else max(0.08, min(trans_dur, Ls[k] * 0.6, Ls[k + 1] * 0.6))
                    for k, (_n, is_cut) in enumerate(pair)]

        td = _td_of(Ls)
        # 总长 = sum(Ls) - sum(td)（xfade 的重叠把两张各自啃掉一口），缩放 Ls 校正到 total
        for _ in range(4):
            if abs(sum(Ls) - sum(td) - total) < 0.01:
                break
            sc = (total + sum(td)) / sum(Ls)
            Ls = [x * sc for x in Ls]
            td = _td_of(Ls)
        if sum(Ls) - sum(td) <= 0.12:
            td = [0.04] * (n - 1)
            Ls = [(total + sum(td)) / n] * n
        for i, it in enumerate(items):
            ins += (["-stream_loop", "-1", "-i", it["path"]] if it.get("kind") == "video"
                    else ["-loop", "1", "-framerate", str(ofps), "-i", it["path"]])
            nfr = max(1, int(round(Ls[i] * ofps)))
            # 链尾补 fps=：见上面单张分支的说明（xfade 只吃带明确帧率的输入）
            chains.append(cell_chain(i, w, h, ofps, it.get("fit"),
                                     it.get("zoom", zoom), nfr)
                          + ";[c%d]trim=duration=%.3f,setpts=PTS-STARTPTS,settb=AVTB,"
                            "format=yuv420p,fps=%d[v%d]" % (i, Ls[i], ofps, i))
        prev, off = "[v0]", Ls[0] - td[0]
        for k in range(1, n):
            name, _ = pair[k - 1]
            tag = "[x%d]" % k
            chains.append("%s[v%d]xfade=transition=%s:duration=%.3f:offset=%.3f%s"
                          % (prev, k, name, td[k - 1], max(0.0, off), tag))
            prev = tag
            if k < n - 1:
                off += Ls[k] - td[k]
        fc = ";".join(chains)

    def build(enc):
        return ([ffmpeg_bin(), "-y"] + ins +
                ["-filter_complex", fc, "-map", prev] + _common(enc, ofps) +
                ["-t", "%.3f" % total, str(out)])
    r = _ffmpeg(build, "槽内轮播 %s" % pathlib.Path(str(out)).name)
    if r.returncode != 0:
        import os as _os
        if _os.environ.get("FXAI_FFMPEG_VERBOSE"):
            print((r.stderr or "")[-4000:], flush=True)
        raise SystemExit("槽内轮播失败 %s: %s" % (out, (r.stderr or "")[-500:]))
    return out


def pad_to(src, out, total, fps=None):
    """把一条素材补/裁到刚好 total 秒（CFR、无声）：不够用定格补，超了裁掉。

    网页录屏出来的片段长度由「画面什么时候静止」决定，和配音时长对不上，
    进合成前一律先对齐 —— 后面拼接才不会越拼越偏。
    """
    ofps = int(fps or CFG["fps"])
    d0 = dur(src)
    hold = max(0.0, float(total) - d0)
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", str(src),
           "-vf", "tpad=stop_mode=clone:stop_duration=%.3f,fps=%d,setsar=1" % (hold, ofps)]
    r = subprocess.run(cmd + _common(None, ofps) + ["-t", "%.3f" % total, str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("素材对齐失败 %s: %s" % (src, (r.stderr or "")[-400:]))
    return out


def materialize(items, outdir, tag, w, h, total, fps=None, render_page=None):
    """把清单里的网页槽先录成视频，其余原样返回。

    轮播段里混网页也走这里：网页先变成 clip，再跟图片一起交给 build_reel 串起来。
    render_page(item, name, outdir, w, h, sec) → mp4 路径：**由调用方注入**，
    因为录制端（Chrome + rec_anim.js）属于技能层，不属于素材槽。
    """
    ofps = int(fps or CFG["fps"])
    out, n = [], max(1, len(items))
    for k, it in enumerate(items):
        if it.get("kind") != "page":
            out.append(it)
            continue
        if render_page is None:
            raise ValueError("materialize: 给了网页槽但没注入 render_page")
        # 轮播段每格大约分到 total/n 秒，多录 0.6 秒兜底（build_reel 会按格裁到精确时长）
        seg = (total if n == 1 else total / n) + (0.0 if n == 1 else 0.6)
        clip = render_page(it, "%s_p%d" % (tag, k + 1), outdir, w, h, seg)
        out.append({"path": str(clip), "kind": "video", "fit": it.get("fit") or "cover"})
    return out


def part(slot, audio, out, w, h, box=None, mask=None, fit="cover", zoom=0.0,
         fade=0.0, fade_in=True, fade_out=True, fps=None, bg="0x0B0B0C", total=None,
         bg_slot=None):
    """一段音频 + 一块槽 → 一段成片（画面跟音频同长，带淡入淡出）。

    这是「填槽」的最后一步，三种展示形式共用：
        整页幻灯 / 广告片：box 缺省（槽 = 整幅），画面铺满后直接合音频
        上中下分栏：给 box=(0, sy, sw, sh) 把画面放进中区，再给 mask 叠壳（媒体区留透明）

    slot  一条无声 mp4（轮播片 / 录屏 / 视频素材）或一张静图
    audio 该段配音；给 None 时用 total 定长、只出画面（广告片按时间码切画面的用法）
    box   (x, y, w, h) 槽在画布上的位置与尺寸；缺省为整幅 (0, 0, w, h)
    mask  带 alpha 的壳遮罩 PNG；给了就叠在最上层（壳不动、素材动）
    fit   cover（默认）/ contain / blur —— 与 cell_chain 同义
    zoom  槽内缓慢推近倍数（0 = 不推；contain / blur 自动关掉）
    """
    head = pathlib.Path(str(slot)).suffix.lower()
    if audio:
        d = dur(audio)
    elif total:
        d = float(total)
    else:
        raise ValueError("part: 需要 audio 或 total 二者之一")
    ofps = int(fps or CFG["fps"])
    sx, sy, sw, sh = box or (0, 0, w, h)
    nfr = max(1, int(round(d * ofps)))

    fv = str(fit or "cover").lower()
    if fv in ("contain", "blur", "height"):
        zoom = 0.0                     # 补边 / 虚化铺底 / 满高时推近会裁掉上下或放大补边

    # 段首尾淡入淡出（只淡画面、不淡声音）；段太短就不淡，免得整段发黑
    fo = ""
    if fade and d > fade * 2 + 0.15:
        g = []
        if fade_in:
            g.append("fade=t=in:st=0:d=%.2f" % fade)
        if fade_out:
            g.append("fade=t=out:st=%.3f:d=%.2f" % (d - fade, fade))
        if g:
            fo = "," + ",".join(g)

    is_video = head in (".mp4", ".mov", ".m4v", ".webm", ".mkv")
    ins = (["-stream_loop", "-1", "-i", str(slot)] if is_video
           else ["-loop", "1", "-framerate", str(ofps), "-i", str(slot)])
    cell = cell_chain(0, sw, sh, ofps, fv, zoom, nfr) + ";[c0]null[v]"

    a_idx = None
    if audio:
        ins += ["-i", str(audio)]
        a_idx = 1
    m_idx = None
    if mask:
        ins += ["-loop", "1", "-i", str(mask)]
        m_idx = 2 if a_idx else 1

    if m_idx:
        # 槽画面垫到指定位置，壳遮罩压在最上层：遮罩一帧不动，素材在框里动
        bg_src = "color=c=%s:s=%dx%d:r=%d" % (bg, w, h, ofps)
        if bg_slot:
            # 虚化底：先铺满裁齐，缩到 1/8 模糊再放回 —— 比直接大半径 boxblur 快得多
            _bv = pathlib.Path(str(bg_slot)).suffix.lower() in (
                ".mp4", ".mov", ".m4v", ".webm", ".mkv")
            ins += (["-stream_loop", "-1", "-i", str(bg_slot)] if _bv
                    else ["-loop", "1", "-framerate", str(ofps), "-i", str(bg_slot)])
            _bi = m_idx + 1
            _sw, _sh = max(2, w // 8), max(2, h // 8)
            bg_src = ("[%d:v]scale=%d:%d:force_original_aspect_ratio=increase:flags=lanczos,"
                      "crop=%d:%d,scale=%d:%d,boxblur=6:2,"
                      "eq=brightness=-0.12:saturation=0.92,scale=%d:%d:flags=bicubic,fps=%d"
                      % (_bi, w, h, w, h, _sw, _sh, w, h, ofps))
        fc = (cell + ";" + bg_src + "[bg];[bg][v]overlay=%d:%d[t];"
                     "[%d:v]format=rgba,fps=%d[mk];[t][mk]overlay=0:0%s[m]"
              % (sx, sy, m_idx, ofps, fo))
        prev = "[m]"
    else:
        fc = cell + (";[v]%s[vf]" % fo.lstrip(",") if fo else "")
        prev = "[vf]" if fo else "[v]"

    def build(enc):
        cmd = [ffmpeg_bin(), "-y"] + ins + ["-filter_complex", fc, "-map", prev]
        if a_idx:
            cmd += ["-map", "%d:a" % a_idx]
        return cmd + _common(enc, ofps, audio=bool(a_idx)) + ["-t", "%.3f" % d,
                ("-shortest" if a_idx else "-an") , str(out)]
    r = _ffmpeg(build, "填槽 %s" % pathlib.Path(str(out)).name)
    if r.returncode != 0:
        raise SystemExit("填槽失败 %s: %s" % (out, (r.stderr or "")[-500:]))
    return out


def concat(parts, out, tmp=None):
    """无损拼接：各段必须是同一套编码参数（分辨率 / 帧率 / 像素格式）。

    段都是 `part()` 出来的，参数一致，所以走 stream copy 不重编码。
    """
    lst = pathlib.Path(tmp or pathlib.Path(str(out)).parent) / "_concat.txt"
    lst.parent.mkdir(parents=True, exist_ok=True)
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '%s'\n" % pathlib.Path(str(p)).as_posix())
    r = subprocess.run([ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("拼接失败：%s" % ((r.stderr or "")[-500:]))
    return out
