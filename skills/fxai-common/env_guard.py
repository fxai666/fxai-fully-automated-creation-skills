# -*- coding: utf-8 -*-
"""环境守卫：缺「凤希AI伴侣」给下载指引，缺 pip 小包自动补。

给拿到技能包的机器用，两条职责分得很清楚：

- **大件不自动下载**：ffmpeg / whisper 环境+模型 / ComfyUI 及工作流模型，
  体积大、安装繁琐，统一由「凤希AI伴侣」一次装好。这里只探测，
  没装就打印下载指引（官网 + 三个网盘 + 视频），不做任何自动下载。
- **pip 小包自动补**：Pillow / numpy / opencc 这类体积小，探测到缺就 pip 装，
  装不上才提示手工。

用法：
    from env_guard import first_run_check
    first_run_check(pip_pkgs=["PIL", "numpy"])      # 开工前调一次即可

或分开调：
    require_companion()        # 缺伴侣 -> 打指引 + 抛 RuntimeError
    ensure_pip(["PIL"])        # 缺就装，返回成功装的列表

提示文案写在本文件里，随源码一起被 fxai-crypt 加密打包；
品牌信息优先取 ~/.workbuddy/fxai_global.json 的 brand 段，取不到用下面的兜底值。
"""
import importlib.util
import json
import os
import subprocess
import sys

# ---------- 品牌与下载渠道（兜底值；配置里有就优先用配置） ----------
SITE = "www.fxai.site"
QQ_GROUP = "775649071"
WECHAT = "fxai168"
VIDEO = "https://www.bilibili.com/video/BV1EfHL6eEPQ/"   # 安装配置视频

# 伴侣下载渠道：官网为准，三个网盘为备（提取码写在标签里）
PAN_LINKS = (
    ("官网", "https://www.fxai.site"),
    ("夸克网盘", "https://pan.quark.cn/s/a1213641f8c2#/list/share/5e75665b0a764d4d9a9adbd2ec4b71f7"),
    ("迅雷网盘（提取码 qnc3）", "https://pan.xunlei.com/s/VOm0BR-N1g-SuFr_RceCmVKzA1?pwd=qnc3"),
    ("百度网盘（提取码 7r73）", "https://pan.baidu.com/s/1xt_93bsji6g76pvWZt4GZw?pwd=7r73"),
)

# 伴侣自带的大件：缺伴侣时告诉用户「装它就能一次补齐」，别让用户自己一个个找
BUNDLED = (
    ("ffmpeg（带 libass，烧字幕用）", r"软件安装目录 <盘>:\fxai\ffmpeg"),
    ("whisper 语音识别环境 + 模型", r"数据目录 <盘>:\fxaitool\conda\env\whisper"),
    ("ComfyUI 及工作流所需模型", "伴侣安装时自带，或按官网网盘补齐"),
)

# 固定目录名：伴侣安装时定死，代码只认这两个名字，不猜盘符以外的路径
INSTALL_DIR_NAME = "fxai"
DATA_DIR_NAME = "fxaitool"
DRIVES = [c + ":\\" for c in "CDEFGHIJK"]

# import 名 -> pip 名（不同的才需要列）
PIP_ALIAS = {
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "yaml": "PyYAML",
}

# pip 安装源：先官方，失败再换国内镜像
MIRRORS = (
    "",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
)


def _pad(s, width):
    """按显示宽度补空格：中文字符占两列，普通 %-Ns 会把中文行挤歪。"""
    return s + " " * max(1, width - sum(2 if ord(c) > 0x2E80 else 1 for c in s))


def _brand():
    """品牌信息：优先全局配置的 brand 段，取不到用模块内兜底值。"""
    try:
        p = os.environ.get(
            "FXAI_GLOBAL_CONFIG",
            os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))
        with open(p, encoding="utf-8") as f:
            b = json.load(f).get("brand") or {}
    except Exception:
        b = {}
    return {
        "site": b.get("official_site") or SITE,
        "qq": b.get("qq_group") or QQ_GROUP,
        "wechat": b.get("wechat") or WECHAT,
        "video": b.get("video_url") or VIDEO,   # 安装配置视频页
    }


def _find(root, filename, max_depth=4):
    """在 root 下找 filename，返回首个命中路径。"""
    if not root or not os.path.isdir(root):
        return ""
    root = os.path.abspath(root)
    for cur, dirs, files in os.walk(root):
        if cur[len(root):].count(os.sep) > max_depth:
            dirs[:] = []
            continue
        if filename.lower() in (f.lower() for f in files):
            return os.path.join(cur, filename)
    return ""


def companion_install_dir():
    """凤希AI软件安装目录：固定目录名 <盘>:\\fxai，且里面要有 ffmpeg.exe。"""
    for d in DRIVES:
        if not os.path.isdir(d):
            continue
        for cand in (os.path.join(d, INSTALL_DIR_NAME),
                     os.path.join(d, INSTALL_DIR_NAME, INSTALL_DIR_NAME)):
            if os.path.isdir(cand) and _find(cand, "ffmpeg.exe", 4):
                return cand
    return ""


def companion_data_dir():
    """凤希AI数据目录：固定目录名 <盘>:\\fxaitool，与软件目录不必同盘。"""
    for d in DRIVES:
        cand = os.path.join(d, DATA_DIR_NAME)
        if os.path.isdir(cand):
            return cand
    return ""


def has_companion():
    """本机装没装凤希AI伴侣：以「安装目录里有 ffmpeg」为准。"""
    return bool(companion_install_dir())


def guide_text(reason=""):
    """缺伴侣时的完整指引文案（直接打印给人看）。"""
    b = _brand()
    line = "=" * 62
    out = ["", line, "  这台机器上还没装「凤希AI伴侣」", line, ""]
    if reason:
        out.append("  原因：%s" % reason)
        out.append("")
    out.append("  本技能需要的下面这些大件，伴侣一次装好，不用你逐个找：")
    out.append("")
    for name, where in BUNDLED:
        out.append("    · %s %s" % (_pad(name, 30), where))
    out.append("")
    out.append("  三步搞定（照着视频走，几分钟）：")
    out.append("    1. 打开官网 %s 下载「凤希AI伴侣」" % b["site"])
    out.append("    2. 打开安装配置视频，跟着做一遍：%s" % b["video"])
    out.append("    3. 安装时记住装在哪个盘（软件目录 <盘>:\\%s，数据目录 <盘>:\\%s）"
               % (INSTALL_DIR_NAME, DATA_DIR_NAME))
    out.append("")
    out.append("  下载渠道（官网为主，网盘为备）：")
    for label, url in PAN_LINKS:
        out.append("    · %s %s" % (_pad(label, 24), url))
    out.append("")
    out.append("  装完回来重新运行本技能，会自动识别，不用改任何配置。")
    out.append("  需要帮助：QQ 群 %s / 微信 %s" % (b["qq"], b["wechat"]))
    out.append(line)
    out.append("")
    return "\n".join(out)


def require_companion(reason=""):
    """有伴侣就返回安装目录；没有就打印指引并抛 RuntimeError。"""
    d = companion_install_dir()
    if d:
        return d
    print(guide_text(reason), flush=True)
    raise RuntimeError("未检测到凤希AI伴侣，请先到 %s 下载安装" % _brand()["site"])


def _importable(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def ensure_pip(pkgs, quiet=False, timeout=300):
    """缺的 pip 包自动装。pkgs 可以是 import 名（自动映射 pip 名）。

    逐个试官方源 -> 清华 -> 阿里；全失败才返回失败清单（不抛异常）。
    返回 (已装上的, 装不上的)。
    """
    if not pkgs:
        return [], []
    need = [p for p in pkgs if not _importable(p)]
    if not need:
        return [], []

    done, failed = [], []
    for mod in need:
        pip_name = PIP_ALIAS.get(mod, mod)
        last = ""
        for mirror in MIRRORS:
            cmd = [sys.executable, "-m", "pip", "install", pip_name]
            if mirror:
                cmd += ["-i", mirror]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            except Exception as e:
                last = str(e)
                continue
            if r.returncode == 0:
                if not quiet:
                    print("[env] ✓ 已补装 %s%s" % (pip_name, "（国内源）" if mirror else ""), flush=True)
                done.append(mod)
                last = ""
                break
            last = (r.stdout or "")[-200:] or (r.stderr or "")[-200:]
        if last:
            failed.append((mod, pip_name))
            print("[env] ✗ 补装失败 %s：%s" % (pip_name, last.strip()[-160:]), flush=True)
    return done, failed


def first_run_check(pip_pkgs=(), need_companion=True, quiet=False):
    """开工前调一次：查伴侣 + 补 pip 包。

    need_companion=True 时缺伴侣会打指引并抛 RuntimeError；
    pip 包缺了自动装，装不上只警告不阻断。
    """
    info = {"companion": "", "data_dir": "", "pip_installed": [], "pip_failed": []}
    if need_companion:
        info["companion"] = require_companion()
        if not quiet:
            print("[env] ✓ 凤希AI伴侣：%s" % info["companion"], flush=True)
    else:
        info["companion"] = companion_install_dir()
    info["data_dir"] = companion_data_dir()
    done, failed = ensure_pip(list(pip_pkgs), quiet=quiet)
    info["pip_installed"], info["pip_failed"] = done, failed
    if failed and not quiet:
        print("[env] 以下依赖未能自动装上，请手工 pip install：%s"
              % ", ".join(p for _, p in failed), flush=True)
    return info


if __name__ == "__main__":
    print("伴侣已装 :", has_companion())
    print("安装目录 :", companion_install_dir() or "（未找到）")
    print("数据目录 :", companion_data_dir() or "（未找到）")
    print()
    if not has_companion():
        print(guide_text("本机未探测到 <盘>:\\%s 下的 ffmpeg.exe" % INSTALL_DIR_NAME))
    else:
        print("指引文案（伴侣缺失时才会给用户看）：")
        print(guide_text("（预览）"))
