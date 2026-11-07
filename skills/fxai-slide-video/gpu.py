# -*- coding: utf-8 -*-
"""fxai-slide-video · 显卡能力全局检测（进程内只测一次，结果缓存）

策略：**优先用显卡**。
  · 浏览器渲染 → ANGLE/D3D11 + GPU 光栅化；
  · 视频编码   → NVENC（h264_nvenc）。
启动时全局探测一次：真能用显卡才用，用不了自动退回软件渲染 / libx264；
运行中若显卡那一路失败，调用方还会再退回一次（见 slides.render / sv_media._ffmpeg）。

探测方式（都是"真跑一遍"，不是只看名字）：
  · 浏览器：启动一次 Chrome，用 CDP `SystemInfo.getInfo` 读真实 GL 渲染器
            （含 SwiftShader/Basic Render Driver 判定为软件渲染）。
  · 编码器：先看 ffmpeg 有没有 h264_nvenc，再真编一帧确认可用。
"""
import os
import json
import glob
import shutil
import subprocess
import pathlib

# ---------- 路径定位（不写死盘符、不扫盘） ----------
def global_cfg():
    """全局配置 ~/.workbuddy/fxai_global.json（可用环境变量 FXAI_GLOBAL_CONFIG 覆盖）。"""
    p = os.environ.get("FXAI_GLOBAL_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".workbuddy", "fxai_global.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

# ---------- 全局品牌信息（视频页面引导条 site_bar 用） ----------
_BRAND_DEFAULT = {"name": "凤希AI", "official_site": "www.fxai.site", "qq_group": "775649071"}

def brand():
    """全局品牌信息（来自 fxai_global.json 的 brand 块，未配则用凤希AI 默认值）。

    字段：
      name           品牌名（如「凤希AI」）
      official_site  官网展示地址（如 www.fxai.site）
      qq_group       QQ群号

    下载渠道（夸克/迅雷/百度三个网盘 + 邀请码）详见
      fxai-share-skill-package/scripts/pack_skill.py 的 --download/--site/--qq 默认值，
    site_bar 默认会同时提示「去网盘获取安装包」，不暴露具体网盘链接。
    """
    b = (global_cfg().get("brand") or {})
    return {
        "name":          (b.get("name")          or _BRAND_DEFAULT["name"]),
        "official_site": (b.get("official_site") or _BRAND_DEFAULT["official_site"]),
        "qq_group":      (b.get("qq_group")      or _BRAND_DEFAULT["qq_group"]),
    }

def official_site_url():
    """官网完整 URL（带 https://），用于需要拼链接的地方（QR码等）。"""
    s = brand().get("official_site") or _BRAND_DEFAULT["official_site"]
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    return s.rstrip("/") + "/"

def _pf_dirs():
    """Program Files 目录按环境取（系统盘符不写死）。"""
    out = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        p = os.environ.get(key)
        if p and p not in out:
            out.append(p)
    return out


def chrome_exe():
    p = global_cfg().get("chrome_path")
    if p and os.path.isfile(p):
        return p
    for nm in ("chrome", "chrome.exe", "msedge", "msedge.exe"):
        w = shutil.which(nm)
        if w:
            return w
    for pf in _pf_dirs():
        for rel in (("Google", "Chrome", "Application", "chrome.exe"),
                    ("Microsoft", "Edge", "Application", "msedge.exe")):
            p = os.path.join(pf, *rel)
            if os.path.isfile(p):
                return p
    raise RuntimeError("找不到 Chrome/Edge：请在全局配置设 chrome_path，或把浏览器加入 PATH")

def node_exe():
    """录制/探测用 Node ≥21（要内置 WebSocket）。不写死版本，取最新。"""
    p = os.environ.get("FXAI_NODE")
    if p and os.path.isfile(p):
        return p
    for nm in ("node", "node.exe"):
        w = shutil.which(nm)
        if w:
            return w
    cands = sorted(glob.glob(os.path.join(
        os.path.expanduser("~"), ".workbuddy-ai", "binaries", "node", "versions", "*", "node.exe")))
    if cands:
        return cands[-1]
    for pf in _pf_dirs():
        p = os.path.join(pf, "nodejs", "node.exe")
        if os.path.isfile(p):
            return p
    return ""

def ffmpeg_bin():
    """带 libass 的 ffmpeg：全局配置 software_install_dir/ffmpeg/ffmpeg.exe > PATH。"""
    cfg = global_cfg()
    inst = cfg.get("software_install_dir") or cfg.get("video_install_dir") or ""
    if inst:
        for nm in ("ffmpeg.exe", "ffmpeg"):
            c = os.path.join(inst, "ffmpeg", nm)
            if os.path.isfile(c):
                return os.path.normpath(c)
    v = cfg.get("video_ffmpeg") or cfg.get("ffmpeg")
    if v and os.path.isfile(v):
        return v
    return shutil.which("ffmpeg") or "ffmpeg"

# ---------- 探测 ----------
GPU_ARGS = ["--use-gl=angle", "--use-angle=d3d11", "--enable-gpu",
            "--ignore-gpu-blocklist", "--enable-gpu-rasterization", "--enable-zero-copy"]
SOFT_ARGS = ["--disable-gpu"]

_CACHE = None

def _probe_chrome():
    """启动一次 Chrome，用 CDP 读真实 GL 渲染器，判断是否真的在用显卡。"""
    node, chrome = node_exe(), chrome_exe()
    if not node or not chrome:
        return {"ok": False, "renderer": "", "software": True, "error": "缺 node/chrome"}
    js = pathlib.Path(__file__).with_name("rec_anim.js")
    try:
        r = subprocess.run([node, str(js), "--probe-gpu", "--chrome", chrome],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=90)
        for line in reversed((r.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
    except Exception as e:
        return {"ok": False, "renderer": "", "software": True, "error": str(e)[:120]}
    return {"ok": False, "renderer": "", "software": True, "error": "探测无输出"}

def _probe_encoder():
    """真编一帧，确认 NVENC 可用（NVENC 有最小尺寸限制，测试帧用常规尺寸）。"""
    ff = ffmpeg_bin()
    try:
        o = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True,
                           text=True, errors="replace", timeout=30)
        if "h264_nvenc" not in (o.stdout or ""):
            return "libx264"
        t = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "color=c=black:s=320x240:d=0.1", "-c:v", "h264_nvenc",
                            "-f", "null", "-"], capture_output=True, text=True,
                           errors="replace", timeout=60)
        return "h264_nvenc" if t.returncode == 0 else "libx264"
    except Exception:
        return "libx264"

def detect(force=False, verbose=False):
    """全局检测一次并缓存：{"chrome_gpu", "renderer", "encoder", "software"}。"""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    ch = _probe_chrome()
    enc = _probe_encoder()
    _CACHE = {
        "chrome_gpu": bool(ch.get("ok")) and not ch.get("software"),
        "renderer": ch.get("renderer") or "",
        "software": bool(ch.get("software")),
        "encoder": enc,
    }
    if verbose:
        print("[gpu] 浏览器渲染 = %s（%s）   视频编码 = %s"
              % ("显卡" if _CACHE["chrome_gpu"] else "软件",
                 (_CACHE["renderer"] or "-")[:72], enc), flush=True)
    return _CACHE

def chrome_gpu_args():
    """能真用显卡就返回显卡参数，否则返回软件渲染参数。"""
    return GPU_ARGS if detect()["chrome_gpu"] else SOFT_ARGS

def video_encoder():
    """优先显卡编码（h264_nvenc），检测失败回落 libx264。"""
    return detect()["encoder"]

def report():
    """打印一行全局检测结果（供管线开跑时调用一次）。"""
    d = detect(verbose=True)
    return d
