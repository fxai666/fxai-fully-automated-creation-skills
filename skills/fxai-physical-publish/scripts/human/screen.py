# -*- coding: utf-8 -*-
"""截屏与 PNG 输出（zlib 直写，无第三方库），以及画面稳定性判定。"""
from __future__ import annotations

import hashlib
import os
import struct
import time
import zlib

from . import api


# ---------------------------------------------------------------- PNG
def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def bgra_to_rgba(data: bytes) -> bytes:
    """BGRA -> RGBA（只交换 R/B，并把 alpha 置满），走 C 级切片，不做逐像素循环。"""
    b = bytearray(data)
    b[0::4], b[2::4] = bytes(b[2::4]), bytes(b[0::4])
    b[3::4] = b"\xff" * (len(b) // 4)
    return bytes(b)


def save_png(path: str, w: int, h: int, rgba: bytes, rows_hint: int = 0) -> str:
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)  # filter: none
        raw += rgba[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 6)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", comp) + _chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(out)
    return path


def shot(x: int, y: int, w: int, h: int, path: str) -> str:
    gw, gh, data = api.grab(x, y, w, h)
    return save_png(path, gw, gh, bgra_to_rgba(data))


def shot_window(hwnd: int, path: str) -> tuple[str, tuple[int, int, int, int]]:
    """截窗口客户区。优先 PrintWindow（窗口自身渲染，不受遮挡），失败才回退 BitBlt。"""
    got = api.print_window_bgra(hwnd)
    if got:
        x, y, w, h, data = got
        return save_png(path, w, h, bgra_to_rgba(data)), (x, y, w, h)
    r = api.client_rect(hwnd)
    p = shot(r[0], r[1], r[2], r[3], path)
    return p, r


def signature_window(hwnd: int, rect: tuple[int, int, int, int] | None = None, grid: int = 24) -> str | None:
    """窗口自身渲染的抽样指纹（不受遮挡）。rect 为客户区相对坐标，缺省取整个客户区。"""
    got = api.print_window_bgra(hwnd)
    if not got:
        return None
    _x, _y, cw, ch, data = got
    rx, ry, rw, rh = rect or (0, 0, cw, ch)
    rw, rh = min(rw, cw - rx), min(rh, ch - ry)
    if rw <= 0 or rh <= 0:
        return None
    xs = [rx + int((i + 0.5) * rw / grid) for i in range(grid)]
    ys = [ry + int((j + 0.5) * rh / grid) for j in range(grid)]
    hsh = hashlib.md5()
    for yy in ys:
        for xx in xs:
            i = (yy * cw + xx) * 4
            hsh.update(bytes((data[i], data[i + 1], data[i + 2])))
    return hsh.hexdigest()


# ---------------------------------------------------------------- 画面特征
def signature(x: int, y: int, w: int, h: int, grid: int = 24) -> str:
    """抽样指纹：按 grid x grid 取点，用于判断区域是否发生变化。"""
    gw, gh, data = api.grab(x, y, w, h)
    xs = [int((i + 0.5) * gw / grid) for i in range(grid)]
    ys = [int((j + 0.5) * gh / grid) for j in range(grid)]
    hsh = hashlib.md5()
    for yy in ys:
        for xx in xs:
            i = (yy * gw + xx) * 4
            hsh.update(bytes((data[i], data[i + 1], data[i + 2])))
    return hsh.hexdigest()


def signature_rect(rect: tuple[int, int, int, int], grid: int = 24) -> str:
    return signature(rect[0], rect[1], rect[2], rect[3], grid)


def diff_pixels(x: int, y: int, w: int, h: int, baseline: bytes, step: int = 6, tol: int = 24) -> float:
    """与基线 BGRA 比较，返回「差异明显的采样点占比」。"""
    gw, gh, data = api.grab(x, y, w, h)
    if len(baseline) != len(data):
        return 1.0
    total = 0
    bad = 0
    for yy in range(0, gh, step):
        base = yy * gw * 4
        for xx in range(0, gw, step):
            i = base + xx * 4
            if (
                abs(data[i] - baseline[i]) > tol
                or abs(data[i + 1] - baseline[i + 1]) > tol
                or abs(data[i + 2] - baseline[i + 2]) > tol
            ):
                bad += 1
            total += 1
    return (bad / total) if total else 1.0


def wait_stable(rect: tuple[int, int, int, int], stable_ms: int = 800, timeout_ms: int = 20000, poll_ms: int = 220) -> bool:
    """等画面静止：连续 stable_ms 内指纹不变即返回 True。"""
    t0 = time.time()
    last = signature_rect(rect)
    last_change = time.time()
    while (time.time() - t0) * 1000 < timeout_ms:
        time.sleep(poll_ms / 1000.0)
        cur = signature_rect(rect)
        if cur != last:
            last = cur
            last_change = time.time()
        elif (time.time() - last_change) * 1000 >= stable_ms:
            return True
    return False


def wait_stable_win(hwnd: int, rect: tuple[int, int, int, int] | None = None,
                    stable_ms: int = 800, timeout_ms: int = 20000, poll_ms: int = 220) -> bool:
    """同上，但用窗口自身渲染判定——窗口被别的程序盖住也不会误判成「画面没变」。"""
    t0 = time.time()
    last = signature_window(hwnd, rect)
    if last is None:
        return wait_stable(rect or (0, 0, 1, 1), stable_ms, timeout_ms, poll_ms)
    last_change = time.time()
    while (time.time() - t0) * 1000 < timeout_ms:
        time.sleep(poll_ms / 1000.0)
        cur = signature_window(hwnd, rect)
        if cur is None:
            return False
        if cur != last:
            last = cur
            last_change = time.time()
        elif (time.time() - last_change) * 1000 >= stable_ms:
            return True
    return False


def wait_change(
    rect: tuple[int, int, int, int],
    baseline: str,
    timeout_ms: int = 20000,
    poll_ms: int = 250,
    stable_ms: int = 500,
) -> bool:
    """等画面相对基线发生变化并稳定下来。"""
    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout_ms:
        if signature_rect(rect) != baseline:
            return wait_stable(rect, stable_ms=stable_ms, timeout_ms=max(2000, timeout_ms // 3), poll_ms=poll_ms)
        time.sleep(poll_ms / 1000.0)
    return False
