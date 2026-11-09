# -*- coding: utf-8 -*-
"""真人操作通道（human-like OS input channel）。

不依赖浏览器自动化框架：所有动作都是系统级 SendInput 鼠标键盘事件。
"""
__all__ = ["api", "screen", "mouse", "keyboard", "clipboard", "regions", "flow", "browser"]
