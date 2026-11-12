# -*- coding: utf-8 -*-
"""真人操作发布通道 · 命令行入口。

设计原则（不可退让）：
  1. 不用无头浏览器、不接管 CDP、不注入脚本、不读写 cookie 与登录态；
  2. 所有动作都是系统级 SendInput 的鼠标键盘，和真人用手操作等价；
  3. 坐标来自首次录制，之后只按区域点；鼠标轨迹每次随机；
  4. 需要人工时必须停下来等人（F9 继续 / 长按 ESC 中止），不代填、不绕过。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from human import api, browser, clipboard, flow, keyboard, mouse, probe, regions, screen, ui  # noqa: E402

ROOT = regions.ROOT  # 实例目录（站点台账 / 运行记录 / 截图都在这里）
api.enable_dpi_awareness()


def _hr(t: str = "") -> None:
    print(("-" * 8 + " " + t) if t else "-" * 60)


# ------------------------------------------------------------------ env
def cmd_env(args) -> int:
    _hr("系统")
    vw, vh = api.user32.GetSystemMetrics(78), api.user32.GetSystemMetrics(79)
    print("屏幕         : %d x %d（虚拟桌面 %d x %d）" % (api.user32.GetSystemMetrics(0), api.user32.GetSystemMetrics(1), vw, vh))
    print("DPI          : %d（本进程已置 DPI 感知）" % api.gdi32.GetDeviceCaps(api.user32.GetDC(0), 88))
    print("当前光标     : %s" % (api.cursor_pos(),))
    print("Chrome       : %s" % (browser.chrome_path() or "未找到"))
    _hr("通道")
    print("输入方式     : SendInput（系统级键盘鼠标事件）")
    print("浏览器控制   : 无 CDP / 无自动化开关 / 无 JS 注入")
    print("登录态处理   : 不读写 cookie，不保存登录状态")
    print("中止方式     : 长按 ESC")
    _hr("浏览器窗口")
    wins = browser.chrome_windows()
    if not wins:
        print("(没有打开的浏览器窗口)")
    for w in wins:
        print(browser.describe(w))
    return 0


# ------------------------------------------------------------------ windows
def cmd_windows(args) -> int:
    wins = browser.chrome_windows() if args.chrome else api.list_windows()
    if not wins:
        print("没有窗口")
        return 1
    for w in wins:
        print(
            "#%-10s %-14s %4dx%-4d fg=%-5s %s"
            % (w["hwnd"], os.path.basename(w["exe"])[:14], w["client_size"][0], w["client_size"][1],
               w["foreground"], w["title"][:60])
        )
    return 0


# ------------------------------------------------------------------ launch
def _goto_in_tab(hwnd: int, url: str) -> None:
    """在**当前标签**里换地址：Ctrl+L → 粘贴 → 回车。

    不 Ctrl+T 新开标签——每做一件事多一个标签，窗口很快就堆成一片，
    反而比自动化更显眼。真人干活也是在同一个标签里来回来去地跳。
    """
    api.activate_window(hwnd)
    time.sleep(0.25)
    keyboard.combo("ctrl", "l")
    time.sleep(0.18)
    old = clipboard.get_text()
    if not clipboard.set_text(url):
        raise RuntimeError("写剪贴板失败，无法粘贴地址")
    time.sleep(0.15)
    keyboard.combo("ctrl", "v")
    time.sleep(0.3)
    api.key_press(keyboard.VK["enter"], hold_ms=40)
    if old:
        try:
            clipboard.set_text(old)
        except Exception:
            pass


def cmd_launch(args) -> int:
    url = args.url or "about:blank"
    hwnd, reused = browser.ensure_window(url)
    time.sleep(0.5)
    print("窗口  : #%s（%s）" % (hwnd, "复用已有窗口" if reused else "新开窗口"))
    if args.fit:
        cw, ch = (int(x) for x in args.fit.lower().split("x"))
        api.activate_window(hwnd)
        got = browser.fit(hwnd, cw, ch)
        print("客户区: %dx%d" % (got[0], got[1]))
    if reused and url and url != "about:blank":
        _goto_in_tab(hwnd, url)
        print("跳转  : 同标签 → %s" % url)
    browser.remember(hwnd)
    print("说明  : 窗口一直沿用，不关、不新开；下次执行直接复用同一个")
    return 0


# ------------------------------------------------------------------ snap
def cmd_snap(args) -> int:
    w = browser.pick_window(args.title or "", args.hwnd)
    api.activate_window(w["hwnd"])
    time.sleep(0.35)
    out = args.out or os.path.join(ROOT, "shots", time.strftime("%Y%m%d-%H%M%S") + ".png")
    p, rect = screen.shot_window(w["hwnd"], out)
    print("窗口  : #%s %s" % (w["hwnd"], w["title"][:60]))
    print("客户区: %s 尺寸 %dx%d" % (rect[:2], rect[2], rect[3]))
    print("截图  : %s" % p)
    return 0


# ------------------------------------------------------------------ mark
def _wait_key(vk: int, timeout_s: float = 300.0) -> bool:
    t0 = time.time()
    # 先等它松开，防止上一次按键被误判
    while api.key_state(vk) and time.time() - t0 < 5:
        time.sleep(0.05)
    while time.time() - t0 < timeout_s:
        if api.key_state(vk):
            while api.key_state(vk):
                time.sleep(0.03)
            return True
        if api.key_state(0x1B):
            return False
        time.sleep(0.04)
    return False


def cmd_mark(args) -> int:
    w = browser.pick_window(args.title or "", args.hwnd)
    api.activate_window(w["hwnd"])
    time.sleep(0.4)
    cx, cy, cw, ch = api.client_rect(w["hwnd"])
    print("目标窗口: #%s 客户区 %dx%d @ (%d,%d)" % (w["hwnd"], cw, ch, cx, cy))

    if args.rect:
        x, y, rw, rh = (int(v) for v in args.rect.split(","))
        rel = [x, y, rw, rh]
    else:
        print("\n把鼠标移到【目标区域左上角】，按 F7 记录（按 ESC 取消）…", flush=True)
        if not _wait_key(0x76):
            print("已取消")
            return 1
        ax, ay = api.cursor_pos()
        print("  左上角: (%d, %d)" % (ax, ay), flush=True)
        time.sleep(0.5)
        print("再把鼠标移到【目标区域右下角】，按 F8 记录…", flush=True)
        if not _wait_key(0x77):
            print("已取消")
            return 1
        bx, by = api.cursor_pos()
        print("  右下角: (%d, %d)" % (bx, by), flush=True)
        x0, x1 = sorted((ax, bx))
        y0, y1 = sorted((ay, by))
        rel = [x0 - cx, y0 - cy, x1 - x0, y1 - y0]

    cfg = None
    if args.color:
        cfg = {"color": args.color, "text": args.text or "", "tolerance": args.tolerance, "min_ratio": args.min_ratio}
    p = regions.put_region(args.site, args.name, rel, args.note or "", [cw, ch],
                           anchor=args.anchor, probe=cfg)
    print("\n已记录 → %s" % p)
    print("  区域 %-14s rect=%s  尺寸=%dx%d  锚定=%s" % (args.name, rel, rel[2], rel[3], args.anchor))
    if args.anchor == "bottom":
        print("  距视口底边 bottom_offset=%d（上方增删行不影响）" % (ch - rel[1] - rel[3]))
    sp = os.path.join(ROOT, "shots", "%s_%s.png" % (args.site, args.name))
    if args.shot or args.color:
        screen.shot(cx + rel[0] - 8, cy + rel[1] - 8, rel[2] + 16, rel[3] + 16, sp)
        print("  局部截图: %s" % sp)
    if args.color:
        info = probe.learn(args.site, args.name, (cx + rel[0], cy + rel[1], rel[2], rel[3]),
                           args.color, args.text or "", 12, sp)
        print("  已学模板: %s（主色 %s 占比 %.3f，期望文字 %r）"
              % (os.path.basename(info["png"]), args.color, info["color_ratio"], args.text or ""))
    return 0


# ------------------------------------------------------------------ regions
def cmd_regions(args) -> int:
    if args.site:
        d = regions.load(args.site)
        win = d.get("window", {})
        print("站点      : %s" % args.site)
        print("窗口定位  : exe=%s 标题含=%r" % (win.get("exe", "chrome.exe"), win.get("title_contains", "")))
        print("录制客户区: %s" % (d.get("client"),))
        print("更新时间  : %s" % d.get("updated_at", "-"))
        _hr("区域")
        if not d.get("regions"):
            print("(空，用 mark 录制)")
        for name, r in (d.get("regions") or {}).items():
            print(
                "%-18s rect=%-22s %dx%d  命中%s/失败%s  %s"
                % (name, r.get("rect"), r["rect"][2], r["rect"][3],
                   r.get("hits", 0), r.get("fails", 0), r.get("note", ""))
            )
    else:
        sites = regions.list_sites()
        print("已录制站点 %d 个：%s" % (len(sites), ", ".join(sites) or "无"))
    return 0


# ------------------------------------------------------------------ record
def cmd_record(args) -> int:
    """按清单一次录完一个站点的所有区域：逐个提示「左上角 F7 / 右下角 F8」。"""
    w = browser.pick_window(args.title or "", args.hwnd)
    api.activate_window(w["hwnd"])
    time.sleep(0.4)
    cx, cy, cw, ch = api.client_rect(w["hwnd"])

    if args.plan:
        with open(args.plan, "r", encoding="utf-8") as f:
            plan = json.load(f)
        items = plan.get("regions", plan if isinstance(plan, list) else [])
    elif args.names:
        items = [{"name": n} for n in args.names.split(",")]
    else:
        raise SystemExit("需要 --plan 文件或 --names 逗号列表")

    print("站点 %s｜窗口 #%s｜客户区 %dx%d" % (args.site, w["hwnd"], cw, ch))
    print("共 %d 个区域。每项：鼠标移到【左上角】按 F7 → 移到【右下角】按 F8；ESC 跳过该项。\n" % len(items))

    done, skipped = 0, 0
    for i, it in enumerate(items, 1):
        name = it["name"]
        note = it.get("note", "")
        print("[%d/%d] %-16s %s" % (i, len(items), name, note), flush=True)
        print("      左上角按 F7 …", flush=True)
        if not _wait_key(0x76):
            print("      跳过\n")
            skipped += 1
            continue
        ax, ay = api.cursor_pos()
        time.sleep(0.35)
        print("      右下角按 F8 …", flush=True)
        if not _wait_key(0x77):
            print("      跳过\n")
            skipped += 1
            continue
        bx, by = api.cursor_pos()
        x0, x1 = sorted((ax, bx))
        y0, y1 = sorted((ay, by))
        rel = [x0 - cx, y0 - cy, x1 - x0, y1 - y0]
        if rel[2] < 3 or rel[3] < 3:
            print("      区域太小，跳过\n")
            skipped += 1
            continue
        it_cfg = it.get("probe") or ({"color": args.color, "text": it.get("text", "")} if args.color else None)
        regions.put_region(args.site, name, rel, note, [cw, ch],
                           anchor=it.get("anchor", args.anchor), probe=it_cfg)
        # 顺手截局部图，方便事后核对录的是不是那块
        sp = os.path.join(ROOT, "shots", "%s_%s.png" % (args.site, name))
        try:
            screen.shot(cx + rel[0] - 6, cy + rel[1] - 6, rel[2] + 12, rel[3] + 12, sp)
            if it_cfg and it_cfg.get("color"):
                probe.learn(args.site, name, (cx + rel[0], cy + rel[1], rel[2], rel[3]),
                            it_cfg["color"], it_cfg.get("text", ""), 12, sp)
        except Exception:
            sp = ""
        print("      已记录 rect=%s  %dx%d  %s\n" % (rel, rel[2], rel[3], os.path.basename(sp)))
        done += 1

    d = regions.load(args.site)
    d.setdefault("window", {})["exe"] = "chrome.exe"
    regions.save(args.site, d)
    print("完成：记录 %d 个，跳过 %d 个 → %s" % (done, skipped, regions._path(args.site)))
    return 0


# ------------------------------------------------------------------ probe / reanchor / preflight
def _pick_client(args):
    w = browser.pick_window(args.title or "", args.hwnd)
    api.activate_window(w["hwnd"])
    time.sleep(0.3)
    return w, api.client_rect(w["hwnd"])


def cmd_probe(args) -> int:
    w, cr = _pick_client(args)
    site, name = args.site, args.name
    if args.learn:
        rect = regions.resolve(site, name, cr)
        info = probe.learn(site, name, rect,
                           args.color or (regions.get_region(site, name).get("probe") or {}).get("color", ""),
                           args.text or (regions.get_region(site, name).get("probe") or {}).get("text", ""),
                           int(args.pad), args.out or "")
        print(json.dumps({"action": "learned", "rect": list(rect), **{k: v for k, v in info.items() if k != "fp"}},
                         ensure_ascii=False, indent=2))
        return 0
    if args.relocate:
        reg = regions.get_region(site, name)
        color = args.color or (reg.get("probe") or {}).get("color", "")
        rr = probe.relocate(site, name, cr, color, regions.resolve(site, name, cr))
        print(json.dumps(rr, ensure_ascii=False, indent=2))
        return 0 if rr.get("ok") else 2
    rect = regions.resolve(site, name, cr)
    res = probe.check(site, name, rect, args.out or "",
                      tolerance=args.tolerance if args.tolerance is not None else None)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print("局部截图（只看这一块，不是全屏）: %s" % res["shot"])
    return 0 if res.get("ok") else 2


def cmd_reanchor(args) -> int:
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    if not names:
        raise SystemExit("--names 需要逗号分隔的区域名，或 all")
    if names == ["all"]:
        names = list((regions.load(args.site).get("regions") or {}).keys())
    out = regions.reanchor(args.site, names, args.anchor)
    for n, off in out:
        print("  %-24s anchor=%-6s bottom_offset=%s" % (n, args.anchor, off))
    print("已迁移 %d 个区域 → %s" % (len(out), regions._path(args.site)))
    return 0


def cmd_preflight(args) -> int:
    w = browser.pick_window(args.title or "", args.hwnd)
    cr = api.client_rect(w["hwnd"])
    rep = flow.preflight(args.site, cr)
    # 用户 2026-11-11 口径：确认窗就是普通弹窗，点「离开/确定」即可，不当阻塞
    if rep["blockers"] and not args.no_dismiss:
        gone = flow.dismiss_modals(w["hwnd"], log=lambda e, **k: print("  [%s] %s" % (e, k)))
        rep["dismissed"] = gone
        rep = {**rep, "blockers": [b for b in rep["blockers"]
                                   if not any(g["hwnd"] == b["hwnd"] and g.get("closed") for g in gone)]}
    rep["target_window"] = {"hwnd": w["hwnd"], "title": w["title"][:60]}
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if rep["blockers"]:
        print("\n⚠ 仍有未点掉的确认窗：%s" % ", ".join(b["title"][:30] for b in rep["blockers"]))
    if rep["locked"]:
        print("\n⚠ 工作站已锁定，SendInput 与截图都会失败。")
    return 0


# ------------------------------------------------------------------ run
def cmd_run(args) -> int:
    api.assert_ready("执行发布流程")   # 锁屏 / 显示器关闭时直接报错，别假装在跑
    if args.flow:
        d = regions.load(args.site)
        steps = (d.get("flows") or {}).get(args.flow)
        if not steps:
            raise SystemExit("站点 %s 没有名为 %s 的流程（已有：%s）" % (
                args.site, args.flow, ", ".join((d.get("flows") or {}).keys()) or "无"))
    elif args.steps:
        with open(args.steps, "r", encoding="utf-8") as f:
            steps = json.load(f)
        if isinstance(steps, dict):
            steps = steps.get("steps", [])
    else:
        steps = json.loads(args.inline)

    variables = {}
    for kv in args.var or []:
        if "=" not in kv:
            raise SystemExit("--var 需要 k=v 形式：%s" % kv)
        k, v = kv.split("=", 1)
        variables[k] = v

    override = {}
    if args.fast:
        override = {"scale": 0.08}
    elif args.scale:
        override = {"scale": float(args.scale)}

    # 开工前置诊断：锁屏 / 客户区原点 / 吞点击的独立确认窗
    try:
        rep = flow.preflight(args.site)
        print("前置诊断: 锁屏=%s 可见窗口=%d 确认窗=%d 前台=#%s"
              % (rep["locked"], rep["windows"], len(rep["blockers"]), rep["foreground"]))
        for b in rep["blockers"]:
            print("  ⚠ 顶层确认窗「%s」会吞点击，按铁律 19 用 Tab→Enter 先处理" % b["title"][:40])
    except Exception as e:
        print("前置诊断跳过: %s" % e)

    runner = flow.Runner(args.site, variables, human_override=override)
    print("运行目录: %s" % runner.run_dir)
    res = runner.run(steps)
    _hr("结果")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 2


# ------------------------------------------------------------------ reveal
def cmd_reveal(args) -> int:
    """把人要操作的窗口摆出来：**不置顶** + 靠左缩小，右侧留出对话窗口。"""
    w = browser.pick_window(args.title or "", args.hwnd)
    if args.full:
        ui.release(w["hwnd"], restore_client=(args.w, args.h))
        print("已取消置顶并恢复客户区 %dx%d" % (args.w, args.h))
        return 0
    got = ui.reveal(w["hwnd"], args.w, args.h)
    print("已摆放窗口 #%s，客户区 %dx%d，位置 (0,0)，未置顶" % (w["hwnd"], got[0], got[1]))
    print("右侧保留给对话窗口；点对话窗口就能盖住它，不会挡着你。")
    return 0


def cmd_minimize(args) -> int:
    """把浏览器窗口最小化，把屏幕整个让给对话。"""
    w = browser.pick_window(args.title or "", args.hwnd)
    ui.minimize(w["hwnd"])
    print("已最小化窗口 #%s" % w["hwnd"])
    return 0


# ------------------------------------------------------------------ selftest
def cmd_selftest(args) -> int:
    """安全自检：只在自己的浏览器窗口地址栏里动，验证轨迹、点击、输入都是真实生效的。"""
    w = browser.pick_window(args.title or "", args.hwnd)
    api.activate_window(w["hwnd"])
    time.sleep(0.4)
    cx, cy, cw, ch = api.client_rect(w["hwnd"])
    print("自检窗口 #%s 客户区 %dx%d" % (w["hwnd"], cw, ch))

    os.makedirs(os.path.join(ROOT, "shots"), exist_ok=True)
    before = os.path.join(ROOT, "shots", "selftest_1_before.png")
    after = os.path.join(ROOT, "shots", "selftest_2_after.png")
    screen.shot_window(w["hwnd"], before)

    print("1) 真实鼠标轨迹：移到地址栏区域…")
    tx = cx + int(cw * 0.5)
    ty = cy + 52
    mouse.move_to(tx, ty)
    p0 = api.cursor_pos()
    print("   落点 %s（目标 %s）" % (p0, (tx, ty)))

    print("2) 点击地址栏 + 逐字输入中文与英文…")
    mouse.click(tx, ty)
    time.sleep(0.3)
    keyboard.combo("ctrl", "a")
    time.sleep(0.15)
    text = "human input ok 真人操作自检"
    keyboard.type_text(text)
    time.sleep(0.4)

    screen.shot_window(w["hwnd"], after)
    print("3) 清理（ESC 取消输入）")
    api.key_press(keyboard.VK["escape"])
    time.sleep(0.2)
    api.key_press(keyboard.VK["escape"])

    print("\n截图对照：\n  %s\n  %s" % (before, after))
    print("判定：若第二张截图地址栏出现「%s」，说明轨迹/点击/输入全部真实生效。" % text)
    return 0


# ------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="publish_cli", description="真人操作发布通道（系统级鼠标键盘）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("env", help="环境与通道自检").set_defaults(func=cmd_env)

    p = sub.add_parser("windows", help="列出窗口")
    p.add_argument("--chrome", action="store_true", help="只看浏览器窗口")
    p.set_defaults(func=cmd_windows)

    p = sub.add_parser("launch", help="打开真实浏览器窗口")
    p.add_argument("--url", default="about:blank")
    p.add_argument("--fit", default="", help="把客户区调到指定尺寸，如 1500x900")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("snap", help="截取浏览器窗口客户区")
    p.add_argument("--hwnd", type=int)
    p.add_argument("--title", default="")
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_snap)

    p = sub.add_parser("mark", help="录制站点操作区域（相对窗口客户区）")
    p.add_argument("--site", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--hwnd", type=int)
    p.add_argument("--title", default="")
    p.add_argument("--rect", default="", help="手工指定 client 坐标 x,y,w,h（不走 F7/F8）")
    p.add_argument("--note", default="")
    p.add_argument("--shot", action="store_true", help="同时截一张该区域局部图核对")
    p.add_argument("--anchor", choices=["top", "bottom"], default="bottom",
                   help="默认 bottom：记距视口底边的距离（滚到底后的发布按钮、提交按钮都用它）；"
                        "侧栏入口 / 弹窗内按钮这类不随滚动移动的，显式 --anchor top")
    p.add_argument("--color", default="", help="挂局部校验：按钮主色预设，如 ksred/blue/red")
    p.add_argument("--text", default="", help="期望文字，供上层看局部图核对，如「发布」")
    p.add_argument("--tolerance", type=float, default=0.12, help="模板指纹容差")
    p.add_argument("--min-ratio", type=float, default=0.20, help="主色最小占比")
    p.add_argument("--no-capture", action="store_true")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("record", help="按清单批量录制站点区域（F7 左上 / F8 右下）")
    p.add_argument("--site", required=True)
    p.add_argument("--plan", default="", help="区域清单 JSON")
    p.add_argument("--names", default="", help="逗号分隔的区域名（无提示）")
    p.add_argument("--anchor", choices=["top", "bottom"], default="bottom",
                   help="本批区域的锚定方式，默认 bottom（不随滚动移动的控件用 top）")
    p.add_argument("--color", default="", help="本批区域统一挂的主色（挂了就自动学模板）")
    p.add_argument("--hwnd", type=int)
    p.add_argument("--title", default="")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("regions", help="查看已录制区域")
    p.add_argument("--site", default="")
    p.set_defaults(func=cmd_regions)

    p = sub.add_parser("probe", help="局部截图校验：只截已记录坐标那一小块")
    p.add_argument("--site", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--hwnd", type=int)
    p.add_argument("--title", default="")
    p.add_argument("--out", default="", help="局部图输出路径")
    p.add_argument("--learn", action="store_true", help="把当前这块学成模板")
    p.add_argument("--relocate", action="store_true", help="全屏重扫主色并回写台账")
    p.add_argument("--color", default="", help="主色预设（learn/relocate 用）")
    p.add_argument("--text", default="", help="期望文字，写进模板备注")
    p.add_argument("--pad", type=int, default=12, help="ROI 外扩像素")
    p.add_argument("--tolerance", type=float, default=None, help="指纹容差覆盖")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("reanchor", help="把已有区域批量转成底部/顶部锚定")
    p.add_argument("--site", required=True)
    p.add_argument("--names", required=True, help="逗号分隔区域名，或 all")
    p.add_argument("--anchor", choices=["top", "bottom"], default="bottom")
    p.set_defaults(func=cmd_reanchor)

    p = sub.add_parser("preflight", help="开工前置诊断：锁屏/客户区原点/吞点击的确认窗")
    p.add_argument("--site", required=True)
    p.add_argument("--hwnd", type=int)
    p.add_argument("--title", default="")
    p.add_argument("--no-dismiss", action="store_true",
                   help="只诊断、不自动点掉确认窗（默认会自动点「离开/确定」）")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("run", help="执行流程步骤")
    p.add_argument("--site", required=True)
    p.add_argument("--flow", default="", help="用站点台账里定义的流程名，如 video")
    p.add_argument("--steps", default="", help="步骤 JSON 文件")
    p.add_argument("--inline", default="", help="步骤 JSON 字符串")
    p.add_argument("--var", action="append", help="变量 k=v，模板里用 {k}")
    p.add_argument("--fast", action="store_true", help="调试用：把真人停顿整体压到 8%")
    p.add_argument("--scale", type=float, default=0.0, help="真人停顿倍率（1.0 为真实节奏）")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("selftest", help="安全自检：验证轨迹/点击/输入真实生效")
    p.add_argument("--hwnd", type=int)
    p.add_argument("--title", default="")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("reveal", help="摆放浏览器窗口供人手操作（不置顶，可被对话窗口盖住）")
    p.add_argument("--hwnd", type=int)
    p.add_argument("--title", default="")
    p.add_argument("--w", type=int, default=1180, help="客户区宽度")
    p.add_argument("--h", type=int, default=860, help="客户区高度")
    p.add_argument("--full", action="store_true", help="取消置顶并复位到区域录制时尺寸")
    p.set_defaults(func=cmd_reveal)

    p = sub.add_parser("minimize", help="最小化浏览器窗口，把屏幕整个让给对话")
    p.add_argument("--hwnd", type=int)
    p.add_argument("--title", default="")
    p.set_defaults(func=cmd_minimize)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
