# -*- coding: utf-8 -*-
# =====================================================================
# fxai-vocal-separate —— 人声分离（服务端 ComfyUI 模型工作流）
# 调用 comfy_client 提交「人声分离」工作流，经 HTTP 接口下载产物。
# 输入音频走服务端相对路径（与 MV 同一份 音乐.wav），产物全部经
# GET /fxai/down/file 下载到本地；产物一律经 HTTP 下载，本机 ComfyUI 目录只由服务端使用。
# =====================================================================
import os
import re
import sys
import json
import subprocess

# comfy_client 位于同级公共目录 fxai-workflow/
_HERE = os.path.dirname(os.path.abspath(__file__))
_WF_DIR = os.path.join(os.path.dirname(_HERE), "fxai-workflow")
if _WF_DIR not in sys.path:
    sys.path.insert(0, _WF_DIR)

import comfy_client as cc  # noqa: E402


def _global_cfg():
    """用户级全局部署配置（fxai_global.json）。"""
    p = os.environ.get("FXAI_GLOBAL_CONFIG",
                       os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ffmpeg_path(g=None):
    """带 arnndn 的 ffmpeg（用户软件目录下的那一套，不是 conda 自带的）。"""
    g = g or _global_cfg()
    c = g.get("ffmpeg") or ""
    if c and os.path.isfile(c):
        return c
    si = g.get("software_install_dir") or ""
    if si:
        p = os.path.join(si, "ffmpeg", "ffmpeg.exe")
        if os.path.isfile(p):
            return p
    return "ffmpeg"


def denoise_model(g=None):
    """RNNoise 神经网络模型文件（model.rnnn）：全局配置 rnnn_model > ffmpeg 同目录。"""
    g = g or _global_cfg()
    m = g.get("rnnn_model") or ""
    if m and os.path.isfile(m):
        return m
    p = os.path.join(os.path.dirname(ffmpeg_path(g)), "model.rnnn")
    return p if os.path.isfile(p) else None


def denoise_audio(src, dst=None, mix=1.0, timeout=1800):
    """用 ffmpeg 的 arnndn（RNNoise 循环神经网络）对人声做二次去噪。

    src: 输入音频（通常是分离出来的 人声.wav）。
    dst: 输出路径，默认与 src 同目录、文件名加「_去噪」后缀。
    mix: 0~1，去噪输出与原音的混合比例（1=全用去噪结果，0=保留原音）。
    返回: 输出文件路径。
    """
    src = os.path.abspath(src)
    if not os.path.isfile(src):
        raise RuntimeError("待去噪音频不存在: %s" % src)
    model = denoise_model()
    if not model:
        raise RuntimeError("未找到 RNNoise 模型 model.rnnn（应放在 ffmpeg 同目录，"
                           "或在 fxai_global.json 用 rnnn_model 指定绝对路径）")
    root, ext = os.path.splitext(src)
    dst = os.path.abspath(dst or (root + "_去噪" + (ext or ".wav")))
    # ⚠️ filtergraph 里冒号是选项分隔符，绝对路径的盘符（<盘符>:/...）必须转义；
    # 这里改为 cwd 切到模型目录、滤镜只写文件名，彻底绕开转义坑（也更通用：用户装在哪都行）。
    af = "aresample=48000,arnndn=m=%s:mix=%s" % (os.path.basename(model), mix)
    cmd = [ffmpeg_path(), "-y", "-i", src, "-af", af, dst]
    print("[denoise] %s" % " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=os.path.dirname(model), timeout=timeout,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0 or not os.path.isfile(dst):
        raise RuntimeError("去噪失败: %s" % (r.stderr or b"").decode("utf-8", "replace")[-800:])
    print("[denoise] 完成：%s（%.1f MB）" % (dst, os.path.getsize(dst) / 1048576.0))
    return dst


def _out_subdir(audio_rel):
    '由输入音频路径派生服务端输出子目录（每歌一个独立子目录，编号按歌递增、不跨歌累加）。\n例：audio/<歌名>/音乐.wav -> "<歌名>_vocalsep"。'
    parts = str(audio_rel or "").replace("\\", "/").split("/")
    stem = parts[-2] if len(parts) >= 2 else "vocalsep"
    stem = re.sub(r'[\\/*?:"<>|]', "", stem).strip() or "vocalsep"
    return stem + "_vocalsep"


def resolve_audio_input(audio, upload_subdir="vocal_input"):
    '输入音频归一化为「服务端相对路径」。\n- 本地存在的文件 → 先经 POST /fxai/audio/upload 传到服务端，返回服务端相对路径；\n- 已是服务端相对路径（如 音乐生成/051_audio.wav）→ 原样返回。\n这样本技能既能嵌在管线里用服务端音频，也能独立对任意本地音频跑。'

    if audio and os.path.isfile(audio):
        rel = cc.upload_audio(audio, subdir=upload_subdir)
        print("[vocalsep] 已上传本地音频 → %s" % rel, flush=True)
        return rel
    return audio


def separate_vocals(audio_rel, out_dir, model=None, vocal_name="人声.wav",
                    accomp_name=None, out_sub=None, timeout=1800, tag="vocalsep",
                    denoise=True, denoise_name="人声_去噪.wav", mix=1.0):
    """用服务端模型工作流分离人声。

    audio_rel: 服务端音频相对路径，如 'audio/<歌名>/音乐.wav'（与 MV 同一份）。
    out_dir:   本地落地目录（通常是歌曲项目目录）。
    model:     工作流模型 slug（默认 comfy_client.DEFAULT_MODEL['vocalsep']，当前 'melband'）。
    vocal_name: 人声落地文件名（服务端实际文件名为 <序号>_audio.wav，这里下载后改名）。
    accomp_name: 伴奏落地文件名；None = 不下载（当前工作流只保存人声一路输出）。
    out_sub:   服务端输出子目录（默认由 audio_rel 派生，如 '<歌名>_vocalsep'）。
    denoise:   分离后是否再用 ffmpeg arnndn（RNNoise 神经网络）对**人声**做一次去噪，默认开。
    denoise_name / mix: 去噪产物落地名；mix = 去噪输出与原音的混合比（1=全用去噪结果）。
    返回: {'vocal': 本地路径或None, 'denoised': 去噪后路径或None, 'accompaniment': ...,
           'token': 结果令牌, 'out_sub': 子目录}
    """
    os.makedirs(out_dir, exist_ok=True)
    # ★ 本地已有人声分离结果 → 跳过上传与服务端工作流，直接走本地去噪。
    _vocal_hit = os.path.join(out_dir, vocal_name)
    if os.path.isfile(_vocal_hit):
        print("[vocalsep] 本地已有人声，跳过分离：%s" % _vocal_hit)
        res = {"vocal": _vocal_hit, "denoised": None, "accompaniment": None,
               "token": None, "out_sub": out_sub or ""}
        if denoise:
            res["denoised"] = denoise_audio(_vocal_hit, os.path.join(out_dir, denoise_name), mix=mix)
        return res
    cc.require_comfyui()
    # 输入既可以是本地音频文件，也可以是服务端相对路径；本地文件先上传再分离。
    audio_rel = resolve_audio_input(audio_rel)
    wf = cc.resolve_wf("vocalsep", model)
    out_sub = re.sub(r'[\\/*?:"<>|]', "", str(out_sub or _out_subdir(audio_rel)).strip()) or "vocalsep"
    # 提交：输入音频 + 输出目录占位符（占位符名与全局一致：fxai_audioinput / fxai_outputdir）
    token = cc.submit_workflow(
        wf,
        {"fxai_audioinput": audio_rel, "fxai_outputdir": out_sub},
        kind="vocalsep", timeout=timeout, tag=tag,
    )
    os.makedirs(out_dir, exist_ok=True)
    res = {"vocal": None, "denoised": None, "accompaniment": None,
           "token": token, "out_sub": out_sub}
    # 主产物（人声）：/fxai/tasks/result 返回的 url 令牌下载（服务端文件名 <序号>_audio.wav，与落地名无关）
    if not token:  # 兜底：结果令牌为空时按输出目录列举最新一个音频
        files = cc._list_files(out_sub, cat="audio")
        token = ("audio|%s|%s" % (out_sub, files[-1])) if files else None
    if token:
        vpath = os.path.join(out_dir, vocal_name)
        cc._download(cc._rel_to_token(token), vpath, timeout=900)
        res["vocal"] = vpath
        print("[vocalsep] 人声已下载：%s" % vpath)
    # 二次去噪：只对「人声」做（伴奏不需要）——ffmpeg arnndn / RNNoise 神经网络
    if denoise and res["vocal"]:
        try:
            res["denoised"] = denoise_audio(res["vocal"], os.path.join(out_dir, denoise_name), mix=mix)
        except Exception as e:
            print("[vocalsep] 去噪跳过（不影响人声本身）：%s" % e)
    # 伴奏：当前工作流未保存 instruments 路，默认不下载；需要时先在服务端补一个保存节点再传 accomp_name
    if accomp_name:
        try:
            files = cc._list_files(out_sub, cat="audio")
            cand = [f for f in files if not token or f != token.split("|")[-1]]
            if not cand:
                print("[vocalsep] 伴奏跳过：服务端目录 %s 只有 1 个音频（工作流未保存 instruments）" % out_sub)
            else:
                apath = os.path.join(out_dir, accomp_name)
                cc._download("audio|%s|%s" % (out_sub, cand[-1]), apath, timeout=900)
                if os.path.getsize(apath) > 100:
                    res["accompaniment"] = apath
                    print("[vocalsep] 伴奏已下载：%s" % apath)
                else:
                    os.remove(apath)
        except Exception as e:
            print("[vocalsep] 伴奏下载跳过：%s" % e)
    return res


if __name__ == "__main__":
    # 音频既可以是本地文件路径，也可以是服务端相对路径（本地的会自动先上传）。
    # 分离+去噪: python separate_vocals.py <音频> <输出目录> [伴奏落地名]
    # 只去噪  : python separate_vocals.py --denoise <音频> [输出路径] [mix]
    if len(sys.argv) >= 2 and sys.argv[1] == "--denoise":
        _src = sys.argv[2] if len(sys.argv) >= 3 else ""
        _dst = sys.argv[3] if len(sys.argv) >= 4 else None
        _mix = float(sys.argv[4]) if len(sys.argv) >= 5 else 1.0
        print(denoise_audio(_src, _dst, mix=_mix))
    elif len(sys.argv) >= 3:
        _r = separate_vocals(sys.argv[1], sys.argv[2],
                             accomp_name=(sys.argv[3] if len(sys.argv) >= 4 else None))
        print(json.dumps(_r, ensure_ascii=False, indent=2))
    else:
        print("用法: python separate_vocals.py <音频(本地文件或服务端路径)> <输出目录> [伴奏落地名]")
        print("      python separate_vocals.py --denoise <音频> [输出路径] [mix]")
