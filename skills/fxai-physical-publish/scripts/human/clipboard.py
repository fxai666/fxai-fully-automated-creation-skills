# -*- coding: utf-8 -*-
"""剪贴板读写（ctypes）。仅用于「复制粘贴」这种真人也会做的动作，不读写任何站点数据。"""
from __future__ import annotations

import ctypes
import time

from .api import kernel32, user32

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.GetClipboardData.restype = ctypes.c_void_p


def _open(retries: int = 12) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.08)
    return False


def get_text() -> str:
    if not _open():
        return ""
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_text(text: str) -> bool:
    if not _open():
        return False
    try:
        user32.EmptyClipboard()
        size = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        if not p:
            return False
        try:
            ctypes.memmove(p, ctypes.create_unicode_buffer(text), size)
        finally:
            kernel32.GlobalUnlock(h)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(h)))
    finally:
        user32.CloseClipboard()


CF_HDROP = 15


class DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", ctypes.c_uint32),
        ("pt", ctypes.c_int32 * 2),
        ("fNC", ctypes.c_int32),
        ("fWide", ctypes.c_int32),
    ]


def clear() -> bool:
    """彻底清空剪贴板（连文件列表等所有格式一起清）。"""
    if not _open():
        return False
    try:
        return bool(user32.EmptyClipboard())
    finally:
        user32.CloseClipboard()


def set_files(paths) -> bool:
    """按「资源管理器复制文件」的格式放剪贴板（CF_HDROP）。

    文件对话框粘贴这个格式会自动定位到文件所在目录并选中它，
    不会像纯文本那样被解析成 `"文件1" "文件2"` 这种多文件列表。
    """
    paths = [str(p) for p in (paths or []) if p]
    if not paths:
        return False
    if not _open():
        return False
    try:
        if not user32.EmptyClipboard():
            return False
        body = ("".join(p + "\0" for p in paths) + "\0").encode("utf-16-le")
        df = DROPFILES()
        df.pFiles = ctypes.sizeof(DROPFILES)
        df.fNC = 0
        df.fWide = 1
        data = bytes(df) + body
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        if not p:
            return False
        try:
            ctypes.memmove(p, data, len(data))
        finally:
            kernel32.GlobalUnlock(h)
        return bool(user32.SetClipboardData(CF_HDROP, ctypes.c_void_p(h)))
    finally:
        user32.CloseClipboard()
