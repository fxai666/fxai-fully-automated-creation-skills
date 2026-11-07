# -*- coding: utf-8 -*-
"""工作区级配置加载器（fxai-mv-pipeline 技能）。\n\n配置来源：{workspace}/config.json —— 这是「用户态 / 资源管理」配置，\n由用户（或后续 UI）维护：人物参考图、产出尺寸约定等。\n\n与 fxai_global.json（用户级全局部署配置）的关系（两种配置职责不同，不混）：\n- config.json（工作区根）    ← 资源/规则配置（人物、尺寸约定），UI 管、随工作区走。\n- fxai_global.json（~/.workbuddy/） ← 部署/环境配置（ComfyUI 地址、whisper 路径、ffmpeg 等），\n  机器级、所有技能共享，由 setup_env.py 探测生成，不随技能包分发。\n\n搜索顺序：环境变量 MV_CONFIG → {workspace}/config.json。\n缺失或字段不全时回退到内置 DEFAULTS，保证不崩。\n"""
import os
import json

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
# 工作区根：优先读环境变量 MUSIC_WORKSPACE（跨机器/换盘通用化），缺省回退默认
DEFAULT_WORKSPACE = os.environ.get("MUSIC_WORKSPACE") or os.getcwd()

# 全局默认值（用户未配置时使用）；这些是「尺寸约定」，一般不动。
DEFAULTS = {
    "landscape_width": 1920,   # 横屏场景图宽
    "landscape_height": 1080,  # 横屏场景图高
    "mv_width": 1280,          # 最终 MV 宽
    "mv_height": 768,          # 最终 MV 高
    "video_max_edge": 1300,     # 生视频最大边长封顶
}


def cn(n):
    """图片序号统一用阿拉伯数字（人物1~N、场景1~N），便于排序、且跨数量一致。\n\n    ⚠️ 约定（2026-09-29 用户拍板）：无论数量多少，一律数字编号，\n    中文数字（一~十）与零填充（11/12）混用会在 >10 张时不一致。\n    """
    return str(n)


def _find_config(workspace):
    candidates = []
    env = os.environ.get("MV_CONFIG")
    if env:
        candidates.append(env)
    if workspace:
        candidates.append(os.path.join(workspace, "config.json"))
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def load_config(workspace=DEFAULT_WORKSPACE):
    """加载工作区配置，合并默认值。返回 dict；并写入 _config_path 标记实际来源（无则 None）。"""
    path = _find_config(workspace)
    cfg = {}
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["_config_path"] = path
        except Exception as e:
            print("[mv_config] 读取配置失败 %s: %s" % (path, e))
    # 合并默认值（用户配置优先）
    merged = dict(DEFAULTS)
    merged.update(cfg.get("defaults", {}))
    cfg["defaults"] = merged
    cfg.setdefault("characters", {})
    return cfg


def get_character(cfg, name):
    return cfg["characters"].get(name)


def resolve_ref(ref_image):
    """参考图路径：绝对路径直接用；相对路径（如 'avatar/000.png'）即 workflow 节点传参规范\n    （节点图片列表相对 fxai/image/），由 ComfyUI 服务器端 fxai-toolkit 解析，客户端无需本机 fxai_root。\n    因此相对路径原样返回即可。"""


    if not ref_image:
        return ref_image
    return ref_image
