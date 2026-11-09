# -*- coding: utf-8 -*-
"""真人化键盘：逐字敲击、随机节奏、中文走 Unicode 直送、组合键与短语停顿。"""
from __future__ import annotations

import random
import time

from . import api

VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "win": 0x5B, "lwin": 0x5B,
    "num0": 0x60, "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}

# 需要 Shift 的符号（美式键盘）
SHIFT_SYMBOLS = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
    "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}

_SIMPLE_VK = {}
for c in "abcdefghijklmnopqrstuvwxyz":
    _SIMPLE_VK[c] = ord(c.upper())
for c in "0123456789":
    _SIMPLE_VK[c] = ord(c)
for c, low in zip("!@#$%^&*()_+{}|:\"<>?~", "1234567890-=[]\\;',./`"):
    _SIMPLE_VK[c] = None  # 走 shift 分支
_SIMPLE_VK[" "] = 0x20
_SIMPLE_VK["\t"] = 0x09
_SIMPLE_VK["\n"] = 0x0D


def _shift_char(ch: str) -> None:
    base = SHIFT_SYMBOLS.get(ch) or (ch.lower() if ch.isalpha() else ch)
    vk = ord(base.upper()) if base.isalpha() else ord(base)
    api.send_inputs([
        api._key_input(0x10, 0, 0),
        api._key_input(vk, 0, 0),
        api._key_input(vk, 0, api.KEYEVENTF_KEYUP),
        api._key_input(0x10, 0, api.KEYEVENTF_KEYUP),
    ])


def tap(ch: str, rnd: random.Random | None = None, real_keys: bool = False) -> None:
    """敲一个可见字符。

    默认走 KEYEVENTF_UNICODE：这是操作系统级的键盘事件（等同键盘发出的字符），
    但不经过输入法合成——中文输入法处于激活态时，走真实键位会被 IME 吞掉并合成成
    别的汉字（实测过）。真人也一样：他会先切到英文输入法再打字。
    real_keys=True 时才走真实键位，仅在确认输入法已关闭时使用。
    """
    r = rnd or random
    if ch in ("\n", "\r"):
        api.key_press(VK["enter"])
        return
    if ch == "\t":
        api.key_press(VK["tab"])
        return
    if real_keys and ch.isascii() and ch.isprintable():
        vk = _SIMPLE_VK.get(ch)
        if vk is None:
            _shift_char(ch)
        elif ch.isupper() or ch in SHIFT_SYMBOLS:
            _shift_char(ch)
        else:
            api.key_press(vk, hold_ms=r.randint(18, 46))
        return
    api.unicode_char(ch)


def type_text(text: str, rnd: random.Random | None = None, wpm: tuple[int, int] = (28, 62),
              typo_rate: float = 0.0, real_keys: bool = False) -> None:
    """逐字输入，节奏随机；词间停顿略长，模拟真人。"""
    r = rnd or random
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        tap(ch, r, real_keys=real_keys)
        i += 1
        # 基础间隔：wpm 越高间隔越短
        base = 60000.0 / r.uniform(*wpm) / 5.0  # 半字符尺度
        delay = r.uniform(base * 0.55, base * 1.9)
        # 词/句边界多停一下
        if i < n and (text[i - 1] in " ，。、,.!?；;：:" or ch == "\n"):
            delay += r.uniform(45, 190)
        # 偶尔手停（思考、看屏幕）
        if r.random() < 0.02:
            delay += r.uniform(220, 620)
        # 可选：打错再退格（默认关闭，正式发布不开）
        if typo_rate and r.random() < typo_rate and ch.isalnum():
            wrong = r.choice("abcdefghijklmnopqrstuvwxyz")
            time.sleep(delay / 1000.0)
            tap(wrong, r)
            time.sleep(r.uniform(0.08, 0.3))
            api.key_press(VK["backspace"], hold_ms=r.randint(18, 40))
            delay = r.uniform(base * 0.6, base * 1.4)
        time.sleep(delay / 1000.0)


def combo(*keys: str, hold_ms: int | None = None) -> None:
    """组合键，如 combo('ctrl', 'v')。"""
    vks = [VK[k.lower()] if k.lower() in VK else ord(k.upper()) for k in keys]
    for vk in vks:
        api.send_inputs([api._key_input(vk, 0, 0)])
        time.sleep(random.uniform(0.02, 0.06))
    if hold_ms:
        time.sleep(hold_ms / 1000.0)
    else:
        time.sleep(random.uniform(0.04, 0.1))
    for vk in reversed(vks):
        api.send_inputs([api._key_input(vk, 0, api.KEYEVENTF_KEYUP)])
        time.sleep(random.uniform(0.015, 0.05))


def press(key: str, times: int = 1) -> None:
    vk = VK[key.lower()] if key.lower() in VK else ord(key.upper())
    for _ in range(times):
        api.key_press(vk, hold_ms=random.randint(20, 55))
        time.sleep(random.uniform(0.06, 0.16))


def select_all_then_replace(text: str, rnd: random.Random | None = None) -> None:
    """清空输入框再重打：Ctrl+A、退格、再逐字输入（不是设置 value）。"""
    r = rnd or random
    combo("ctrl", "a")
    time.sleep(r.uniform(0.1, 0.22))
    api.key_press(VK["backspace"], hold_ms=r.randint(20, 45))
    time.sleep(r.uniform(0.12, 0.26))
    type_text(text, r)
