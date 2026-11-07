"""凤希AI · MiniMax Hailuo H3 视频提示词规范（FL2VA / Ref2VA 六段式）。

本技能是提示词写作规范，源码不落地、以加密 loader 形式分发。
这里把规范要点暴露成可调用函数，供其它技能或命令行复用；不依赖任何外部模块。
"""

# H3 六段式各段名称（FL2VA 首尾帧 / Ref2VA 全参考 共用同一套结构）
H3_SECTIONS = ["subject", "summary", "retention", "action", "camera", "soundscape"]


def fl2va_template():
    """首尾帧 FL2VA 六段式提示词骨架（每段占一行，留空待填）。"""
    return "\n".join("%s: " % s for s in H3_SECTIONS)


def ref2va_template():
    """全参考 Ref2VA 六段式提示词骨架（每段占一行，留空待填）。"""
    return "\n".join("%s: " % s for s in H3_SECTIONS)


def sections():
    """返回六段式各段名称列表。"""
    return list(H3_SECTIONS)


def get_spec():
    """返回完整规范要点（纯文本，供 agent 阅读）。"""
    return (
        "H3 视频提示词六段式：subject / summary / retention / action / camera / soundscape。\n"
        "FL2VA = 首尾帧；Ref2VA = 全参考。提示词必须契合文案内容与场景（含道具），"
        "音频 = 原音频、不另行设计音轨；中文提示词优先。"
    )


if __name__ == "__main__":
    print(get_spec())
    print()
    print("FL2VA 骨架：")
    print(fl2va_template())
