# -*- coding: utf-8 -*-
"""交付前自检：把跨技能的环境约束做成可执行检查，一次跑完全部。

用法（按交付物类型选入口，可组合）：
  python deliver_check.py --project <项目目录>          # 目录体检：素材名 / 分段目录 / 页面 / 标识 / 码率
  python deliver_check.py --video <成片> --src <源片>   # 单件体检：时长 / 命名 / AIGC 标识 / 码率上界
  python deliver_check.py --html <页面.html>            # 页面体检：图片绝对路径 / 正文保留名
  python deliver_check.py --image <图1> <图2>           # 素材名体检：服务端 image/ 只用 ASCII 名
  python deliver_check.py --segdir <分段目录>           # 分段目录体检：只放纯数字命名的分段
  python deliver_check.py --ffmpeg "<完整命令行>"       # 命令体检：-loop 1 配 -shortest、fade 在 scale 前、overlay 居中
  python deliver_check.py ... --json                    # 机器读

退出码：全 PASS 为 0，有 FAIL 为 1。SKIP 表示该入口不适用此项。
"""
import argparse
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

DUR_RATIO = 0.95          # 成片时长下限：短于源片这个比例判为不完整
SEG_NAME_RE = re.compile(r"^\d+$")
KEEP_NAME = "正文.html"   # 服务端对该保留名锁读，上传正文统一改名 news.html
SERVED_IMG_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
IMG_SRC_RE = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.I)


class Result(object):
    def __init__(self, item, ok, detail, fix=""):
        self.item = item
        self.ok = ok
        self.detail = detail
        self.fix = fix

    def as_dict(self):
        return {"item": self.item, "status": "PASS" if self.ok else "FAIL",
                "detail": self.detail, "fix": self.fix}


def _res(item, ok, detail, fix=""):
    return Result(item, ok, detail, fix)


def _skip(item, why):
    r = Result(item, True, why)
    r.skipped = True
    return r


def _ffprobe(path, field):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
            stderr=subprocess.STDOUT, timeout=60)
        return float(out.decode("utf-8", "replace").strip() or 0)
    except Exception:
        pass
    try:
        from video_tone import probe
        st = probe(path) or {}
        if field == "duration":
            return float(st.get("duration") or 0)
    except Exception:
        return 0.0
    return 0.0


def _rel(p):
    try:
        return os.path.relpath(p)
    except Exception:
        return p


# ---------- 素材与服务端命名 ----------

def check_image_names(paths):
    """服务端 image/ 的图片只用 ASCII 名；封面先写到别处再 cp 进来。"""
    bad = []
    for p in paths:
        name = os.path.basename(str(p))
        if not SERVED_IMG_NAME_RE.match(os.path.splitext(name)[0]):
            bad.append(name)
    if bad:
        return _res("素材名 ASCII", False,
                    "非 ASCII 图片名 %d 个：%s" % (len(bad), ", ".join(bad[:5])),
                    "改成 ASCII 名（cover.png / fig1.png）再上传；封面先写到本地临时目录，再 cp 进 image/")
    return _res("素材名 ASCII", True, "%d 个图片名全部合规" % len(paths))


def check_segdir(segdir):
    """分段目录只放纯数字命名的分段，源片放 <子目录>_src/。"""
    if not os.path.isdir(segdir):
        return _res("分段目录布局", False, "目录不存在：%s" % segdir, "核对路径")
    bad = []
    for fn in sorted(os.listdir(segdir)):
        full = os.path.join(segdir, fn)
        if not os.path.isfile(full):
            continue
        if os.path.splitext(fn)[1].lower() not in (".mp4", ".mov", ".mkv", ".webm"):
            continue
        if not SEG_NAME_RE.match(os.path.splitext(fn)[0]):
            bad.append(fn)
    if bad:
        return _res("分段目录布局", False,
                    "分段目录里有非分段命名的视频 %d 个：%s" % (len(bad), ", ".join(bad[:5])),
                    "源片移到同级 <子目录>_src/，分段目录只留纯数字命名的分段")
    return _res("分段目录布局", True, "只含纯数字命名的分段")


# ---------- 页面与正文 ----------

def check_html(path):
    out = []
    try:
        html = open(path, encoding="utf-8", errors="replace").read()
    except Exception as exc:
        return [_res("页面可读", False, str(exc), "核对路径与编码")]

    srcs = IMG_SRC_RE.findall(html)
    rel = [s for s in srcs if s and not re.match(r"^(?:[a-zA-Z]+:|//|/)", s)]
    if rel:
        out.append(_res("图片绝对路径", False,
                        "相对路径 %d 处：%s" % (len(rel), ", ".join(rel[:5])),
                        "页面里图片 src 一律写绝对路径（脚本按 cwd 拼接）"))
    else:
        out.append(_res("图片绝对路径", True, "%d 处图片 src 均为绝对路径" % len(srcs)))

    name = os.path.basename(path)
    if name == KEEP_NAME:
        out.append(_res("正文保留名", False, "上传正文用了服务端锁读的保留名 %s" % KEEP_NAME,
                        "改名为 news.html 再上传"))
    else:
        out.append(_res("正文保留名", True, "文件名 %s 未占用保留名" % name))
    return out


# ---------- ffmpeg 命令 ----------

def check_ffmpeg(cmd):
    out = []
    low = cmd.lower()

    if re.search(r"-loop\s+1\b", low):
        if "-shortest" in low:
            out.append(_res("静态图收尾", True, "-loop 1 已配 -shortest"))
        else:
            out.append(_res("静态图收尾", False, "-loop 1 是无限流，缺 -shortest",
                            "在输出参数里补 -shortest，否则编码不结束"))

    vf = ""
    m = re.search(r"-vf\s+(?:\"([^\"]*)\"|'([^']*)'|(\S+))", cmd)
    if m:
        vf = next(g for g in m.groups() if g)
    chain = [s.strip() for s in vf.split(",") if s.strip()] if vf else []
    if chain:
        fi = [i for i, s in enumerate(chain) if s.startswith("fade")]
        si = [i for i, s in enumerate(chain) if s.startswith("scale")]
        if fi and si and min(fi) > min(si):
            out.append(_res("滤镜链顺序", False,
                            "fade 排在 scale 之后：%s" % " → ".join(chain[:6]),
                            "顺序固定 format → fade → scale → overlay"))
        else:
            out.append(_res("滤镜链顺序", True, "fade 与 scale 顺序正确"))

        body = ",".join(chain)
        if "overlay" in body:
            if re.search(r"x\s*=\s*[^:]*if\(|y\s*=\s*[^:]*if\(", body):
                out.append(_res("overlay 居中", False, "overlay 定位用了嵌套 if()",
                                "缩放放进 scale，overlay 用 overlay_w/overlay_h 居中"))
            else:
                out.append(_res("overlay 居中", True, "overlay 定位未用嵌套表达式"))

    if not out:
        out.append(_skip("命令参数", "命令行未涉及静态图 / 滤镜链，跳过"))
    return out


# ---------- 交付视频 ----------

def check_video(path, src=None):
    out = []
    name = os.path.basename(path)
    if not name.startswith("成片_最终"):
        out.append(_res("交付命名", False, "交付视频名为 %s" % name,
                        "交付物只认 成片_最终_{W}x{H}.mp4；中间片跑完 finalize.py 再交"))
    else:
        out.append(_res("交付命名", True, name))

    dur = _ffprobe(path, "duration")
    if src:
        sdur = _ffprobe(src, "duration")
        if dur and sdur:
            ratio = dur / sdur if sdur else 0
            if ratio < DUR_RATIO:
                out.append(_res("成片时长", False,
                                "成片 %.2fs 仅为源片 %.2fs 的 %.1f%%" % (dur, sdur, ratio * 100),
                                "整片范围参数用 --end-index=-1（0 只跑第一段），重跑后再验"))
            else:
                out.append(_res("成片时长", True, "%.1f%% ≥ 95%%" % (ratio * 100)))
        else:
            out.append(_res("成片时长", False, "时长读取失败（成片 %.2fs / 源片 %.2fs）" % (dur, sdur),
                            "确认两段路径与 ffprobe 可用"))
    else:
        out.append(_skip("成片时长", "未给 --src，跳过时长比对"))

    try:
        import aigc_mark
        aigc_mark.verify(path)
        out.append(_res("AIGC 隐式标识", True, "Label=1 且字段齐全"))
    except Exception as exc:
        out.append(_res("AIGC 隐式标识", False, str(exc),
                        "交付前走 aigc_mark.embed(path, project)；显式角标与隐式标识两者都要有"))

    try:
        import video_encode
        video_encode.assert_delivery_bitrate(path)
        out.append(_res("交付码率", True, "视频码率在 2000 kbps 上界内"))
    except Exception as exc:
        out.append(_res("交付码率", False, str(exc)[:200],
                        "用 video_encode.encode_delivery_2pass() 重编，别各自写 -b:v / -crf"))
    return out


# ---------- 目录综合 ----------

def check_project(root):
    out = []
    imgs, vids, pages = [], [], []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in ("__pycache__", "_backup", ".git", "_tmp")]
        for fn in fns:
            full = os.path.join(dp, fn)
            ext = os.path.splitext(fn)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".webp"):
                imgs.append(full)
            elif ext in (".mp4", ".mov"):
                vids.append(full)
            elif ext == ".html":
                pages.append(full)

    if imgs:
        out.append(check_image_names(imgs))
    if pages:
        for p in pages:
            out.extend(check_html(p))

    finals = [v for v in vids if os.path.basename(v).startswith("成片_最终")]
    if finals:
        for v in finals:
            out.append(_res("交付命名", True, os.path.basename(v)))
        cand = finals[0]
        try:
            import aigc_mark
            aigc_mark.verify(cand)
            out.append(_res("AIGC 隐式标识", True, os.path.basename(cand)))
        except Exception as exc:
            out.append(_res("AIGC 隐式标识", False, "%s：%s" % (os.path.basename(cand), exc),
                            "交付前走 aigc_mark.embed(path, project)"))
    elif vids:
        out.append(_res("交付命名", False, "目录里有 %d 个视频，无 成片_最终_*.mp4" % len(vids),
                        "数字人交付物须跑 finalize.py 生成 成片_最终_{W}x{H}.mp4"))
    return out


def _render(results, as_json):
    if as_json:
        print(json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2))
        return
    fails = [r for r in results if not r.ok]
    for r in results:
        tag = "SKIP" if getattr(r, "skipped", False) else ("PASS" if r.ok else "FAIL")
        print("[%s] %s：%s" % (tag, r.item, r.detail))
        if not r.ok and r.fix:
            print("       处置：%s" % r.fix)
    print("\n合计 %d 项：PASS %d / FAIL %d" % (len(results), len(results) - len(fails), len(fails)))
    if fails:
        print("交付前把这些 FAIL 处理完再发。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="交付前自检：环境约束与交付判据一次跑完")
    ap.add_argument("--project", help="项目目录：素材名 / 页面 / 交付命名 / 标识")
    ap.add_argument("--video", help="交付成片路径")
    ap.add_argument("--src", help="源片路径（用于时长比对）")
    ap.add_argument("--html", help="页面路径")
    ap.add_argument("--image", nargs="*", help="图片路径（服务端素材名检查）")
    ap.add_argument("--segdir", help="分段目录（只允许纯数字命名的分段）")
    ap.add_argument("--ffmpeg", help="ffmpeg 完整命令行（静态检查滤镜链与收尾参数）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args(argv)

    results = []
    if a.project:
        results.extend(check_project(a.project))
    if a.video:
        results.extend(check_video(a.video, a.src))
    if a.html:
        results.extend(check_html(a.html))
    if a.image:
        results.append(check_image_names(a.image))
    if a.segdir:
        results.append(check_segdir(a.segdir))
    if a.ffmpeg:
        results.extend(check_ffmpeg(a.ffmpeg))

    if not results:
        ap.print_help()
        return 2

    _render(results, a.json)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
