# -*- coding: utf-8 -*-
# =====================================================================
# fxai-mv-pipeline —— 音乐→图片→MV 一体化生成管线（用户级技能核心模块）
#
# 视频生成在公共技能 fxai-audio-video 中（音频驱动 → 视频，MV 与数字人口播共用）：
#   · fxai-audio-video/audio_video.py —— gen_video / gen_videos / Ref2VA 提示词 / 尺寸封顶
#     本模块 gen_mv / build_ref2va_prompt / _build_multiref_lines / _build_multiline_prompt 全部转调它。
#   · fxai-workflow/comfy_client.py —— 共享 ComfyUI 客户端，本模块只做镜像。
#   · fxai-fourview-gen —— 角色视图（四视图/三视图）。
#   · fxai-scene-gen —— 场景图。
# 本模块保留的 MV 专属逻辑：音乐（Yue2）、临时件清理、主机记录。
# =====================================================================
import json
import os
import sys
import time

# ---------- 路径：公共客户端 + 公共视频件 + 两个独立技能 ----------
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILLS = os.path.dirname(SKILL_DIR)
for _dep in ("fxai-workflow", "fxai-audio-video", "fxai-fourview-gen", "fxai-scene-gen"):
    _p = os.path.join(_SKILLS, _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------- 共享客户端：镜像全部公开符号（保持向后兼容） ----------
import comfy_client as _C
_globals = globals()
for _n in dir(_C):
    if _n.startswith("__"):
        continue
    _globals[_n] = getattr(_C, _n)

# ---------- 四视图 / 场景图：委托给独立技能（单一真相源） ----------
from fourview_gen import gen_char_view, gen_threeview # noqa: E402 (路径已在上方插入)
from scene_gen import gen_scene_image # noqa: E402

# ---------- 视频生成：委托公共件 fxai-audio-video（单一真相源） ----------
# kind 固定 "sing"（演唱）；数字人口播那边传 kind="speak"，走的是同一份实现。
from audio_video import (  # noqa: E402
    build_multiline_prompt as _build_multiline_prompt,
    build_multiref_lines as _build_multiref_lines,
    build_ref2va_prompt,
    gen_video,
    max_edge as _mv_max_edge,
)

# =====================================================================
# 以下为 MV 专属逻辑（客户端在 comfy_client、视频生成在 fxai-audio-video）
# =====================================================================

# ---------- 生成完成后清理中间件/临时件（用户固化 ） ----------
_SONG_KEEP_FILES = {
    "歌曲.md", "场景素材.md", "分镜剧本.md", "运镜提示词.md", "场景提示词.md", "MV提交提示词.json", "ComfyUI任务记录.md",
    "制作.json", "meta.json", "歌词时间轴.json", "音乐.wav", "字幕.ass", "字幕.srt",
}
_SONG_KEEP_PREFIX = ("成片_", "合并_") # 带分辨率后缀的交付视频
_SONG_KEEP_DIRS = {"图片", ".workbuddy"} # 产出图归档 + 项目记忆（清理时整目录保留）
# ⚠️ 例外（agent 自己的记录，重渲需要，保留）：
# - _vocal_spans.json：whisper 原始人声窗。produce_song --phase render --reuse-music 时跳过对齐，
#   依赖此文件给 classify_segments 判定分段人声。
# - _music_ref.json：音乐服务端相对路径 + 时长。produce_song --phase music/render --reuse-music 复用
#   已有音乐时，靠它判定「可复用」，否则会被清理删掉 → bool(_ref) 为 False → 静默重新生成音乐
#   。
# - _assets.json：人物/场景/道具参考图的服务端相对路径缓存（char_rels/scene_rels/prop_rels）。
#   --mv-only 复用已生成图、resolve_prop_rels 写回都依赖它；缺失会让下次重渲找不到参考图、
#   被迫重新生图或反查日志（日志也带 _ 前缀会被一并清掉），故必须保留。
_SONG_KEEP_TEMP = {"_vocal_spans.json", "_music_ref.json", "_assets.json"}


def cleanup_song_tmp(song_dir):
    '生成完成后清理中间件/临时件：删除 _ 开头的临时文件（whisper 转录/人声段/对齐报告/各阶段日志/\n回收脚本/抽帧）、__pycache__ / .cache* / *.bak，仅保留约定交付物。\n\n删除策略只命中「显式临时模式」（`_` 前缀、`.bak` 后缀、缓存目录），未知文件/目录一律不动，安全。\n返回 (removed:list, kept:list) 便于日志。\n'





    removed, kept = [], []
    if not os.path.isdir(song_dir):
        return removed, kept
    for name in os.listdir(song_dir):
        p = os.path.join(song_dir, name)
        if os.path.isdir(p):
            if name in _SONG_KEEP_DIRS:
                kept.append(name + "/")
                continue
            if name == "__pycache__" or name.startswith(".cache"):
                shutil.rmtree(p, ignore_errors=True)
                removed.append(name + "/")
            continue
        if name in _SONG_KEEP_FILES or name.startswith(_SONG_KEEP_PREFIX) or name in _SONG_KEEP_TEMP:
            kept.append(name)
            continue
        if name.startswith("_") or name.endswith(".bak"):
            try:
                os.remove(p)
                removed.append(name)
            except OSError:
                pass
    return removed, kept


# ---------- 1) 音乐（Yue2 / MiniMax Music 3） ----------
def gen_music(style, lyrics, duration=120, mode="full", tag=None, subdir="音乐生成", timeout=1800, model=None):
    """生成音乐。model=None → 按全局默认（yue2）；model="minimax3" → MiniMax Music 3。

    两套工作流的差异（决定了 style/lyrics 怎么写）：
    · yue2（music-yue2.json）：style=YuE2 风格串（一句话式），lyrics 用结构标签
      [主歌]/[副歌]/[桥段]/[尾奏]。mode/max_duration(360s)/输出目录锁死在工作流内，
      故 duration/mode 入参仅保留兼容性。
    · minimax3（music-minimax3-api.json）：style=**三段式结构化 Caption**
      （Global Metadata / Vocal Details / Arrangement），人声性别与音色写在 Vocal Details；
      lyrics 用 [Intro]/[Verse]/[Pre-Chorus]/[Chorus]/[Bridge]/[Instrumental]/[Solo]/[Outro]。
      duration → 占位符 fxai_music_duration（模型最长时长上限，模型可在歌词唱完后提前结束）。
    """
    require_comfyui()
    tag = tag or ("music_%d" % int(time.time()))
    params = {
        "fxai_prompt": style,      # yue2: node53(Style) / minimax3: node54(caption)
        "fxai_lyrics": lyrics,     # yue2: node54(Lyrics) / minimax3: node54(lyrics)
        "fxai_seed": _seed_rand(),
        # minimax3 用；yue2 工作流无此占位符，多传无副作用（时长锁死在工作流内）。
        "fxai_music_duration": int(duration),
    }
    url = submit_workflow(resolve_wf("music", model), params, tag="music:" + tag, kind="music", timeout=timeout)
    dst = os.path.join(OUT_DIR, tag + ".wav")
    _download(url, dst)
    print("[music] DONE ->", dst)
    return dst, url_to_rel(url)

# ---------- 4) MV 视频（MiniMax 三模式 · 转调公共件 fxai-audio-video） ----------
def gen_mv(mode, aud_rel, char_rel=None, scene_ref=None,
           size=SIZE["portrait_9x16"], tag=None, timeout=3600,
           char_desc=None, scene_desc=None, ref2va_prompts=None,
           seg_prompts=None, char_descs=None, subdir=None, prompt_out=None,
           start_index=None, end_index=None, model=None, prop_rel=None):
    """生成 MV 视频（MiniMax 三模式：single 单场景 / multi 多场景 / multiref 多参考）。

    实现见公共件 fxai-audio-video（音频驱动 → 视频，数字人口播共用同一份）。
    本函数是 MV 专用外壳：kind 固定 "sing"（演唱措辞），子目录/标签前缀用 mv_*。

    ★ multiref 尺寸：按传入 size 的方向定方向——横屏 (W>H) → 1280×768，竖屏 (H>W) → 768×1280
      （公共件 multiref 只认这两个标准尺寸，最大边 1280 且整除 32）。MV 成片方向 = 项目尺寸方向。
    """
    return gen_video(mode, aud_rel, char_rel=char_rel, scene_ref=scene_ref, size=size,
                     tag=tag, timeout=timeout, char_desc=char_desc, scene_desc=scene_desc,
                     ref2va_prompts=ref2va_prompts, seg_prompts=seg_prompts, char_descs=char_descs,
                     subdir=subdir or ("mv_%s" % mode), prompt_out=prompt_out,
                     start_index=start_index, end_index=end_index, model=model,
                     kind="sing", prefix="mv",
                     multiref_landscape=(mode == "multiref"
                                         and int(size[0]) > int(size[1])),
                     prop_rel=prop_rel)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("mv_pipeline 已加载；gen_music / gen_char_view(委托 fxai-fourview-gen) / "
          "gen_scene_image(委托 fxai-scene-gen) / gen_mv(委托 fxai-audio-video) 可用。")
