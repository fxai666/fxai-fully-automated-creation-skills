# -*- coding: utf-8 -*-
"""批量核查各站点登录态：逐站导航 → 强制激活 → 截图 → 拼成一张联系表。

为什么需要：窗口被人挡一下，GDI 截到的就是遮挡物，单看一张图会误判成"没登录"。
这里每张截图前都重新激活并等页面稳定，最后拼成 3x3 的一张表，一眼看全。

用法：
    python check_login.py                    # 全部站点
    python check_login.py --sites douyin,toutiao
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from human import api, browser, clipboard, keyboard, regions, screen  # noqa: E402

ORDER = ["douyin", "kuaishou", "weixin_channels", "baijiahao", "toutiao",
         "youtube", "tiktok", "bilibili", "xiaohongshu"]

LABEL = {
    "douyin": "抖音", "kuaishou": "快手", "weixin_channels": "视频号",
    "baijiahao": "百家号", "toutiao": "今日头条", "youtube": "YouTube",
    "tiktok": "TikTok", "bilibili": "B站", "xiaohongshu": "小红书",
}

SUSPECT = ["登录", "登陆", "log in", "login", "sign in", "扫码", "验证码",
           "Log in", "Sign up"]

# 导航到达的判据：标题里出现这些词才算真的到了该站。
# 只按固定秒数等会误判——上一个站点有未保存内容时，Chrome 会弹「离开此网站？」
# 把跳转卡住，标题一直不变，截出来的图全是上一站。
ARRIVE = {
    "douyin": "抖音", "kuaishou": "快手", "weixin_channels": "视频号",
    "baijiahao": "百家号", "toutiao": "头条", "youtube": "YouTube",
    "tiktok": "TikTok", "bilibili": "哔哩哔哩", "xiaohongshu": "小红书",
}


def leave_confirm(hwnd: int, cw: int, ch: int, use_enter: bool = False) -> None:
    """点掉 Chrome 的「离开此网站？」模态框（「离开」按钮在右侧）。

    必须先把窗口切到前台：SendInput 的鼠标事件按屏幕坐标投递，窗口不在前台时
    会被别的窗口吃掉，看起来像"点了没反应"。
    """
    from human import mouse
    import random
    api.activate_window(hwnd)
    time.sleep(0.35)
    if use_enter:
        api.key_press(keyboard.VK["enter"], hold_ms=45)
    else:
        mouse.click(int(cw * 0.57), int(ch * 0.213), rnd=random.Random())
    time.sleep(0.6)


def wait_arrive(hwnd: int, site: str, timeout: float = 20.0) -> bool:
    """等页面真的跳到目标站点，中途遇到 beforeunload 确认框就点「离开」。"""
    key = ARRIVE.get(site, "")
    if not key:
        return True
    t0 = time.time()
    tried = 0
    while time.time() - t0 < timeout:
        if key in api.window_title(hwnd):
            return True
        elapsed = time.time() - t0
        # 卡住超过 4 秒，多半是被确认框挡住：第 1 次点「离开」，第 2 次改按回车
        if elapsed > 4 + tried * 5 and tried < 3:
            _x, _y, cw, ch = api.client_rect(hwnd)
            leave_confirm(hwnd, cw, ch, use_enter=(tried >= 1))
            tried += 1
        time.sleep(0.5)
    return False


def goto(hwnd: int, url: str) -> None:
    api.activate_window(hwnd)
    time.sleep(0.4)
    keyboard.combo("ctrl", "l")
    time.sleep(0.26)
    old = clipboard.get_text()
    clipboard.set_text(url)
    time.sleep(0.2)
    keyboard.combo("ctrl", "v")
    time.sleep(0.4)
    api.key_press(keyboard.VK["enter"], hold_ms=40)
    if old:
        try:
            clipboard.set_text(old)
        except Exception:
            pass


def shrink(data: bytes, w: int, h: int, k: int) -> tuple[int, int, bytes]:
    """最近邻降采样（BGRA 进，RGBA 出）。"""
    ow, oh = w // k, h // k
    out = bytearray(ow * oh * 4)
    for y in range(oh):
        sy = y * k * w * 4
        row = sy
        dst = y * ow * 4
        for x in range(ow):
            i = row + x * k * 4
            o = dst + x * 4
            out[o] = data[i + 2]
            out[o + 1] = data[i + 1]
            out[o + 2] = data[i]
            out[o + 3] = 255
    return ow, oh, bytes(out)


def contact_sheet(tiles: list, path: str, cols: int = 3, gap: int = 6) -> str:
    n = len(tiles)
    rows = (n + cols - 1) // cols
    tw, th = tiles[0][0], tiles[0][1]
    W = cols * tw + (cols + 1) * gap
    H = rows * th + (rows + 1) * gap
    canvas = bytearray(b"\x2b\x2b\x2b\xff" * (W * H))
    for idx, (_w, _h, rgba) in enumerate(tiles):
        r, c = idx // cols, idx % cols
        ox = gap + c * (tw + gap)
        oy = gap + r * (th + gap)
        for y in range(th):
            src = y * tw * 4
            dst = ((oy + y) * W + ox) * 4
            canvas[dst:dst + tw * 4] = rgba[src:src + tw * 4]
    screen.save_png(path, W, H, bytes(canvas))
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=",".join(ORDER))
    ap.add_argument("--wait", type=float, default=7.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--scale", type=int, default=4)
    args = ap.parse_args()

    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    api.assert_ready("核查登录态")   # 锁屏时别空转，直接报错
    hwnd, reused = browser.ensure_window()
    api.activate_window(hwnd)
    time.sleep(0.4)
    api.resize_client(hwnd, 1440, 900)
    print("窗口 #%s %s" % (hwnd, "复用" if reused else "新开"), flush=True)

    shot_dir = os.path.join(regions.ROOT, "shots", "check")
    os.makedirs(shot_dir, exist_ok=True)
    tiles = []
    report = []
    for i, site in enumerate(sites, 1):
        try:
            url = regions.load(site).get("upload_url", "")
        except Exception:
            url = ""
        if not url:
            print("[%d/%d] %-16s 未配置地址" % (i, len(sites), site), flush=True)
            continue
        goto(hwnd, url)
        arrived = wait_arrive(hwnd, site)
        if arrived:
            time.sleep(args.wait)          # 到了再多等它把内容渲染完
        # 关键：截图前再激活一次，避免截到抢了前台的对话窗口
        api.activate_window(hwnd)
        time.sleep(0.6)
        title = api.window_title(hwnd)
        cx, cy, cw, ch = api.client_rect(hwnd)
        w, h, data = api.grab(cx, cy, cw, ch)
        p = os.path.join(shot_dir, "%02d_%s.png" % (i, site))
        screen.save_png(p, w, h, screen.bgra_to_rgba(data))
        sw, sh, sd = shrink(data, w, h, args.scale)
        tiles.append((sw, sh, sd))
        hit = [k for k in SUSPECT if k in title]
        flag = "？疑似登录页" if hit else ""
        if not arrived:
            flag = "×未到达目标页"
        report.append((site, title, p, flag))
        print("[%d/%d] %-16s 标题=%s %s" % (i, len(sites), site, title[:46], flag), flush=True)

    if tiles:
        out = args.out or os.path.join(shot_dir, "contact.png")
        contact_sheet(tiles, out)
        print("联系表: %s" % out, flush=True)
    print("--- 汇总 ---", flush=True)
    for site, title, p, flag in report:
        print("%-16s %-34s %s %s" % (LABEL.get(site, site), title[:34], flag, os.path.basename(p)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
