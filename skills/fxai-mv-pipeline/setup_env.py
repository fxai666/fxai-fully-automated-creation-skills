#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""setup_env.py —— 音乐MV自动生成技能包「一键配置环境」。

给拿到技能包的人用：装好「凤希AI伴侣」后跑一次本脚本，它会自动探测
ffmpeg / whisper / ComfyUI / 工作流目录，并写入用户级全局部署配置
~/.workbuddy/fxai_global.json。之后 mv_pipeline.py 与 fxai-lyric-subtitle（字幕脚本）
都自动读取该配置，无需手动改任何代码路径。

该配置是「机器级、所有技能共享」的：只记录两个软件目录根
（software_install_dir / software_data_dir）与 ComfyUI 地址、模型名等设置；
ffmpeg / whisper 环境路径一律由代码按这两个根目录自动推导，不写死、不随技能包分发。

两个目录名是固定的（由凤希AI伴侣安装时定死，安装过程中会告诉你装在哪个盘）：
 软件安装目录 = <盘>:\fxai         —— ffmpeg、model.rnnn 在里面
 数据目录     = <盘>:\fxaitool     —— 某个盘的根目录下，conda 环境、模型、缓存在里面
 二者**不必同一个盘**，所以数据目录要各盘扫一遍，按安装目录猜盘符会猜错。
所以绝大多数机器上什么都不用配，技能按这两个固定目录名自动命中；
只有把伴侣装到非常规目录、或想换数据盘时，才需要跑本脚本复核或手动填。

探测思路（全部按「用户实际安装位置」自动找，不假设任何固定盘符）：
 1) 凤希AI软件安装目录：优先用 --install-dir / --companion 提供；
    否则按固定目录名在常见盘符下找 <盘>:\fxai（含 ffmpeg 的那个）。
 2) 凤希AI数据目录：优先用 --data-dir 提供；否则取软件安装目录同盘根的固定名
    <盘>:\fxaitool（该目录可能由安装器随后创建，存在与否都不影响配置写入）。
 2) ffmpeg.exe：在安装目录下递归找（优先用伴侣自带的带 libass 版本）。
 3) whisper 环境：在安装目录 / 数据目录下找含 faster_whisper 的 Python。
 4) ComfyUI 根目录：在常见盘符下查找含 main.py 的目录。
 5) 工作流目录：随技能包内置在同级 fxai-workflow/，无需单独配置（可用 workflow_dir 覆盖）。
 6) whisper 转写脚本：优先用 WorkBuddy 的 fxai-whisper-transcribe 技能（与 fxai-mv-pipeline 同级）。
 7) whisper 模型目录：在数据目录 / 安装目录下的 whisper 中找模型文件；
    若自动下载尚未完成，先留空，下载完重跑本脚本或手填即可。
 8) 去噪模型 model.rnnn：在软件安装目录下找（通常在 ffmpeg/ 同目录），供 arnndn 神经网络去噪用。

找不到的会交互询问，回车即用自动探测值。
已存在的配置键会被保留（只覆盖本次探测到的项），重跑不会冲掉你的自定义设置。

用法：
    python setup_env.py
    python setup_env.py --install-dir "<软件安装目录>" --data-dir "<软件数据目录>"
    python setup_env.py --companion "<凤希AI伴侣安装目录>"
    python setup_env.py --comfy "<ComfyUI根目录>"
"""
import os
import json
import argparse
import subprocess

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

# 只扫常见盘符（全盘遍历太慢）；含 I/J/K（ComfyUI、数据目录常在此）
DRIVES = [f"{c}:\\" for c in "CDEFGHIJK"]
# 固定的两个根目录名（凤希AI伴侣安装时定死，代码只认这两个名字）
INSTALL_DIR_NAME = "fxai"
DATA_DIR_NAME = "fxaitool"
# 非默认的其它旧命名，仅作兜底探测（不含 fxaitool：数据目录按定义不含 ffmpeg，
# 让它参与匹配容易把数据目录误判成软件安装目录）
COMPANION_HINTS = ["RssEasyAiTooL", "FxAi", INSTALL_DIR_NAME, "凤希AI"]
COMFY_HINTS = ["ComfyUI"]


def search_hint_files(filename, max_depth=4):
    """在命中『提示名』的目录里找文件（更准、更快）。"""
    hits = []
    for d in DRIVES:
        if not os.path.isdir(d):
            continue
        for name in COMFY_HINTS + COMPANION_HINTS:
            for cand in (os.path.join(d, name), os.path.join(d, name, name)):
                if os.path.isdir(cand):
                    hits += iter_find(cand, filename, max_depth)
    return hits


def iter_find(root, filename, max_depth=3):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return []
    hits = []
    for cur, dirs, files in os.walk(root):
        depth = cur[len(root):].count(os.sep)
        if depth > max_depth:
            dirs[:] = []
            continue
        if filename.lower() in (f.lower() for f in files):
            hits.append(os.path.join(cur, filename))
    return hits


def search_drives(filename, max_depth=2):
    """在各盘符直接子目录里找某个文件名（快）。"""
    hits = []
    for d in DRIVES:
        if not os.path.isdir(d):
            continue
        hits += iter_find(d, filename, max_depth)
    return hits


def search_hint_dirs(filename, max_depth=4):
    """在命中『提示名』的目录里找文件（更准）。"""
    hits = []
    for d in DRIVES:
        if not os.path.isdir(d):
            continue
        for name in COMFY_HINTS + COMPANION_HINTS:
            for cand in (os.path.join(d, name), os.path.join(d, name, name)):
                if os.path.isdir(cand):
                    hits += iter_find(cand, filename, max_depth)
    return hits


def find_ffmpeg(base):
    r = iter_find(base, "ffmpeg.exe", max_depth=4)
    return r[0] if r else ""


def find_rnnn(base, max_depth=4):
    """人声去噪用的 RNNoise 模型（model.rnnn），通常与 ffmpeg 同目录。"""
    r = iter_find(base, "model.rnnn", max_depth)
    return r[0] if r else ""


def find_whisper_venv(base, max_depth=5):
    """【仅安装期体检】在 base 下找一个能 import faster_whisper 的 python 解释器。

    只用于装机时判断「本地兜底环境是否就位」；运行期 whisper 一律走 HTTP 接口，不起本地进程。
    """
    pys = iter_find(base, "python.exe", max_depth)
    for py in pys:
        try:
            r = subprocess.run([py, "-c", "import faster_whisper"],
                               capture_output=True, timeout=30)
            if r.returncode == 0:
                return py
        except Exception:
            continue
    return ""


def find_comfy(base):
    if os.path.isfile(os.path.join(base, "main.py")):
        return base
    r = iter_find(base, "main.py", max_depth=2)
    return r[0][:r[0].rfind(os.sep)] if r else ""


def detect_install_dir():
    """凤希AI软件安装目录：固定目录名 <盘>:\fxai，且里面要有 ffmpeg.exe。"""
    for d in DRIVES:
        if not os.path.isdir(d):
            continue
        for cand in (os.path.join(d, INSTALL_DIR_NAME),
                     os.path.join(d, INSTALL_DIR_NAME, INSTALL_DIR_NAME)):
            if os.path.isdir(cand) and find_ffmpeg(cand):
                return cand
    return detect_companion()


def detect_data_dir(install_dir=""):
    """凤希AI数据目录：固定目录名 <盘>:\fxaitool，且与软件目录**不必同盘**——各盘都要扫。

    数据目录可能还没被安装器创建，此时照样按固定名写好（用到前代码都会判 isdir），
    这样换台机器只要盘符对得上就零配置。
    """
    for d in DRIVES:
        if not os.path.isdir(d):
            continue
        cand = os.path.join(d, DATA_DIR_NAME)
        if os.path.isdir(cand):
            return cand
    # 没找到已存在的 fxaitool：取软件安装目录同盘的固定名作为约定默认值
    base = os.path.splitdrive(install_dir)[0] if install_dir else ""
    return os.path.join(base, DATA_DIR_NAME) if base else ""


def detect_companion():
    """兜底：含 ffmpeg.exe 的『提示名』目录；其次：全盘 ffmpeg.exe。"""
    cands = []
    for d in DRIVES:
        if not os.path.isdir(d):
            continue
        for name in COMPANION_HINTS:
            for cand in (os.path.join(d, name), os.path.join(d, name, name)):
                if os.path.isdir(cand) and find_ffmpeg(cand):
                    cands.append(cand)
    if cands:
        return cands[0]
    # 回退：全盘找 ffmpeg.exe，取所在目录
    ff = (search_drives("ffmpeg.exe", 3) + search_hint_dirs("ffmpeg.exe", 4))
    if ff:
        p = ff[0]
        # 向上取到含 ffmpeg 的『主目录』
        return os.path.dirname(p)
    return ""


def detect_comfy():
    for d in DRIVES:
        if not os.path.isdir(d):
            continue
        for name in COMFY_HINTS:
            for cand in (os.path.join(d, name), os.path.join(d, name, name)):
                if os.path.isdir(cand):
                    c = find_comfy(cand)
                    if c:
                        return c
    r = search_hint_files("main.py", 2)
    if r:
        return r[0][:r[0].rfind(os.sep)]
    r = search_drives("main.py", 2)
    return r[0][:r[0].rfind(os.sep)] if r else ""


def detect_whisper():
    """【仅安装期体检】在提示名目录（含数据目录）下找含 faster_whisper 的 python。

    运行期 whisper 一律走 HTTP 接口，不依赖本地解释器。
    """
    cands = search_hint_files("python.exe", 5)
    for py in cands:
        try:
            r = subprocess.run([py, "-c", "import faster_whisper"],
                               capture_output=True, timeout=30)
            if r.returncode == 0:
                return py
        except Exception:
            continue
    return ""


def detect_whisper_dir(bases=()):
    """找 whisper 程序目录（含 whisper_server.py，如 <盘>:/whisper）。

    模型在程序自己的 model/ 子目录里，**用哪个模型由程序定**；
    全局配置只记「程序在哪」（whisper_dir），模型名称 / 模型目录不进全局配置。
    """
    cands = [os.path.join(b, "whisper") for b in bases if b]
    # 兜底：扫各盘根下的 whisper 目录（不写死盘符）
    for d in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        cands.append(os.path.join(d + ":\\", "whisper"))
    for c in cands:
        if os.path.isfile(os.path.join(c, "whisper_server.py")):
            return os.path.normpath(c)
    return ""


def ask(prompt, default):
    try:
        val = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        val = ""
    return val or default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--companion", default="", help="凤希AI伴侣主目录（含 ffmpeg，等同 --install-dir）")
    ap.add_argument("--install-dir", default="", help="凤希AI软件安装目录（含 ffmpeg 的伴侣目录）")
    ap.add_argument("--data-dir", default="", help="凤希AI软件数据目录（模型/缓存所在）")
    ap.add_argument("--comfy", default="", help="ComfyUI 根目录")
    args = ap.parse_args()

    print("=== 音乐MV自动生成 · 环境配置（写入 ~/.workbuddy/fxai_global.json） ===\n")

    # 0) 软件安装目录 / 数据目录（默认按固定目录名自动命中；装到非常规位置时才需要传参）
    install_dir = args.install_dir or detect_install_dir()
    install_dir = ask("凤希AI软件安装目录（固定目录名 <盘>:/fxai）", install_dir)
    data_dir = args.data_dir or detect_data_dir(install_dir)
    data_dir = ask("凤希AI软件数据目录（固定目录名 <盘>:/fxaitool）", data_dir or "未自动找到，请手动填写")

    # 1) 伴侣目录（= 安装目录，向下兼容 --companion）
    companion = args.companion or install_dir
    companion = ask("凤希AI伴侣主目录（含 ffmpeg）", companion or "未自动找到，请手动填写")

    # 2) ffmpeg / whisper 环境：仅做存在性校验（打印确认），不写入配置
    # —— 这些路径一律由「软件安装目录 / 数据目录」在代码里自动推导，
    # 全局配置只存两个根目录，换盘/换目录时无需逐处改绝对路径。
    ffmpeg_found = find_ffmpeg(companion) if os.path.isdir(companion) else ""
    whisper_found = ""
    if os.path.isdir(companion):
        whisper_found = find_whisper_venv(companion) or find_whisper_venv(os.path.dirname(companion))
    if not whisper_found and os.path.isdir(data_dir):
        whisper_found = find_whisper_venv(data_dir)
    if not whisper_found:
        whisper_found = detect_whisper()
    if ffmpeg_found:
        print(f" ✓ 已探测到 ffmpeg：{ffmpeg_found}")
    else:
        print(" [提示] 未探测到 ffmpeg（需带 libass），请确认软件安装目录正确。")
    if whisper_found:
        print(f" ✓ 已探测到 whisper 环境：{whisper_found}")
    else:
        print(" [提示] 未探测到 whisper 环境，请确认软件数据目录下 conda/env/whisper 存在。")

    # 3) ComfyUI
    comfy = args.comfy or detect_comfy()
    comfy = ask("ComfyUI 根目录", comfy or "未自动找到，请手动填写")

    # whisper 转写脚本默认用 WorkBuddy 自带技能（相对路径解析，不写死用户名）；
    # 注意：ffmpeg / whisper 环境路径一律不写入全局配置，由代码按两个根目录自动推导。

    # 去噪模型：人声分离后由 ffmpeg arnndn（RNNoise）做二次去噪，模型通常在 ffmpeg 同目录
    rnnn = ""
    if os.path.isdir(install_dir):
        rnnn = find_rnnn(install_dir)
    if rnnn:
        print(f" ✓ 已探测到去噪模型：{rnnn}")
    else:
        print(" [提示] 未探测到 model.rnnn（人声去噪用）。它在软件安装目录的 ffmpeg 下；"
              " 缺失时人声分离仍可用，只是跳过去噪。")

    # whisper 程序目录（模型在其 model/ 子目录；用哪个模型由程序自己定，配置不记模型）
    whisper_dir = detect_whisper_dir([data_dir, companion, os.path.dirname(companion)])

    out = os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json")
    # 只覆盖本次探测到的项，保留用户已有的自定义键（workflow_models / vocal_separation 等）
    cfg = {}
    if os.path.isfile(out):
        try:
            with open(out, encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict):
                cfg = old
        except Exception:
            cfg = {}
    cfg.update({
        "software_install_dir": install_dir,
        "software_data_dir": data_dir,
        "comfy_url": cfg.get("comfy_url") or "http://127.0.0.1:8188",
        "whisper_device": cfg.get("whisper_device") or "cuda",
        "whisper_dir": whisper_dir or cfg.get("whisper_dir") or "",
        "ollama_url": cfg.get("ollama_url") or "http://127.0.0.1:11434",
    })
    # 旧键清理：模型选择在程序内部，全局配置只记程序位置
    for k in ("whisper_model", "whisper_model_dir", "whisper_model_path"):
        cfg.pop(k, None)
    if rnnn:
        cfg["rnnn_model"] = rnnn

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 已写入全局部署配置：{out}")
    print("现在 fxai-mv-pipeline / fxai-lyric-subtitle / fxai-vocal-separate 均自动读取该配置，无需再改代码路径。")
    if not whisper_dir:
        print("[提示] 未探测到 whisper 程序目录（应含 whisper_server.py 与 model\\）。")
        print(" 装好后重跑本脚本，或把 whisper_dir 填进上面的配置文件即可。")


if __name__ == "__main__":
    main()
