# -*- coding: utf-8 -*-
"""多平台登录巡礼：把要接的站点在一个窗口里依次摆出来，让人逐个登录。

为什么要有这个东西：登录必须人亲手做，但人没法一边守着一个页面一边等程序。
所以一次把 4 个站点按顺序切过去，每个停留固定时间，人坐着按顺序扫码就行；
程序只在中间切页面，不代填任何账号密码，也不读 cookie。

用法：
    python login_tour.py --sites douyin,kuaishou --hold 110
    python login_tour.py --list
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from human import api, browser, clipboard, keyboard, regions, screen  # noqa: E402

DEFAULT_ORDER = ["douyin", "kuaishou", "weixin_channels", "baijiahao",
                 "bilibili", "xiaohongshu"]

LABEL = {
    "douyin": "抖音",
    "kuaishou": "快手",
    "weixin_channels": "视频号",
    "baijiahao": "百家号",
    "toutiao": "今日头条",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "bilibili": "B站",
    "xiaohongshu": "小红书",
}

# 到达判据：标题里出现这些词才算真的跳到该站
ARRIVE = {
    "douyin": "抖音", "kuaishou": "快手", "weixin_channels": "视频号",
    "baijiahao": "百家号", "toutiao": "头条", "youtube": "YouTube",
    "tiktok": "TikTok", "bilibili": "哔哩哔哩", "xiaohongshu": "小红书",
}


def _dismiss_leave(hwnd: int, use_enter: bool = False) -> None:
    """关掉「离开此网站？」模态框。

    只用键盘，不用鼠标：这个框是浏览器自绘的，位置随窗口尺寸变，
    按坐标点很容易落到页面上的链接（实测点出过一次垃圾站），风险太大。
    默认焦点在「取消」上，Tab 一次到「离开」，再回车即可。
    """
    api.activate_window(hwnd)
    time.sleep(0.35)
    api.key_press(keyboard.VK["tab"], hold_ms=40)
    time.sleep(0.25)
    api.key_press(keyboard.VK["enter"], hold_ms=45)
    time.sleep(0.6)


def goto(hwnd: int, url: str, arrive_key: str = "", timeout: float = 20.0) -> bool:
    """在同一标签里跳转：Ctrl+L → 粘贴 → 回车；被 beforeunload 拦住就点「离开」。"""
    api.activate_window(hwnd)
    time.sleep(0.45)
    keyboard.combo("ctrl", "l")
    time.sleep(0.28)
    old = clipboard.get_text()
    if not clipboard.set_text(url):
        raise RuntimeError("写剪贴板失败")
    time.sleep(0.22)
    keyboard.combo("ctrl", "v")
    time.sleep(0.45)
    api.key_press(keyboard.VK["enter"], hold_ms=40)
    if old:
        try:
            clipboard.set_text(old)
        except Exception:
            pass

    if not arrive_key:
        return True
    t0 = time.time()
    tried = 0
    while time.time() - t0 < timeout:
        if arrive_key in api.window_title(hwnd):
            return True
        elapsed = time.time() - t0
        if elapsed > 3.5 + tried * 4.5 and tried < 3:
            _dismiss_leave(hwnd, use_enter=(tried >= 1))
            tried += 1
        time.sleep(0.45)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=",".join(DEFAULT_ORDER))
    ap.add_argument("--hold", type=int, default=110, help="每个站点停留秒数")
    ap.add_argument("--hold-map", default="", help="按站点覆盖停留秒数，如 视频号=170,百家号=140")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--shot-dir", default="")
    args = ap.parse_args()

    hold_map = {}
    if args.hold_map:
        for seg in args.hold_map.split(","):
            k, _, v = seg.partition("=")
            if k and v:
                hold_map[k.strip()] = int(v)

    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    if args.list:
        for s in DEFAULT_ORDER:
            try:
                url = regions.load(s).get("upload_url", "")
            except Exception:
                url = ""
            print("%-18s %-8s %s" % (s, LABEL.get(s, ""), url or "(未配置)"))
        return 0

    api.assert_ready("打开登录页")   # 锁屏时 SendInput 会被拒绝，先报错别硬扛
    hwnd, reused = browser.ensure_window()
    print("窗口 #%s %s" % (hwnd, "复用" if reused else "新开"), flush=True)
    api.activate_window(hwnd)
    time.sleep(0.4)
    got = api.resize_client(hwnd, 1440, 900)
    print("客户区 -> %dx%d（不置顶，人可以直接操作）" % got, flush=True)

    shot_dir = args.shot_dir or os.path.join(regions.ROOT, "shots")
    os.makedirs(shot_dir, exist_ok=True)

    for i, site in enumerate(sites, 1):
        data = regions.load(site)
        url = data.get("upload_url")
        if not url:
            print("[%d/%d] %s 没有配置 upload_url，跳过" % (i, len(sites), site), flush=True)
            continue
        hold = hold_map.get(LABEL.get(site, ""), hold_map.get(site, args.hold))
        arrived = goto(hwnd, url, ARRIVE.get(site, ""))
        time.sleep(2.0)
        print("[%d/%d] %s → %s  %s  停留 %ds"
              % (i, len(sites), LABEL.get(site, site), url,
                 "已到达" if arrived else "未到达（可能被确认框挡住）", hold), flush=True)
        # 中途截一张，留档看它有没有跳到登录页
        try:
            p = os.path.join(shot_dir, "login_%s.png" % site)
            screen.shot_window(hwnd, p)
            print("        截图 %s" % p, flush=True)
        except Exception as e:
            print("        截图失败 %s" % e, flush=True)
        # 分片睡，方便中途看清进度
        left = hold
        while left > 0:
            step = min(20, left)
            time.sleep(step)
            left -= step
        if i < len(sites):
            print("        —— 切下一个", flush=True)

    print("巡礼结束。窗口保持打开，不关不缩。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
