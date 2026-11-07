#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""素材采集统一入口：查使用层经验档 → 按链接路由 → 下载 → 回写经验档。"""
import sys
sys.dont_write_bytecode = True

import argparse
import importlib
import os
import pkgutil
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import core

_SOURCES_DIR = os.path.join(_HERE, "sources")
_BOOTSTRAPPED = set()


def discover():
    """自动发现 sources/ 下的适配器；下划线开头的模块留给接入模板。"""
    adapters = []
    for _, name, _ in pkgutil.iter_modules([_SOURCES_DIR]):
        if name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"sources.{name}")
        except Exception as e:
            core.log(f"[warn] 适配器 {name} 载入失败: {type(e).__name__}: {e}")
            continue
        if hasattr(mod, "match") and hasattr(mod, "collect"):
            adapters.append(mod)
    return sorted(adapters, key=lambda m: getattr(m, "PRIORITY", 50))


def route(url, adapters):
    for mod in adapters:
        try:
            if mod.match(url):
                return mod
        except Exception:
            continue
    return None


def load_profile(url, opts):
    """先查用户级分册；命中就复用链路，省去重复分析。"""
    opts.profile = None
    opts.profile_hit = False
    if not opts.use_profile or opts.refresh:
        return
    prof = core.profile_load(opts.profile_dir, url)
    if prof and prof.get("status") == "review":
        core.log("[经验] 已有分册标记待复核，本次走完整链路")
        prof = None
    opts.profile = prof
    opts.profile_hit = bool(prof)
    if prof:
        core.log(f"[经验] 命中 {os.path.basename(prof.get('_path', ''))}"
                 f" · 已复用 {prof.get('hits', 0)} 次 · 验证于 {prof.get('verified', '')}")


def prepare(mod, url, opts):
    """按链接自身推导预热入口；命中档时按档内记录省掉多余预热。"""
    host = urllib.parse.urlparse(url).netloc
    key = (mod.NAME, host)
    if key in _BOOTSTRAPPED:
        opts.bootstrap = opts.bootstrap_by_host.get(host, "link+origin")
        return
    _BOOTSTRAPPED.add(key)
    urls = tuple(getattr(mod, "BOOTSTRAP", ())) or (url,)
    headers = opts.headers(core.UA_DESKTOP, core.origin_of(url))
    names, landing = core.bootstrap(urls, opts.proxy, headers)
    prof = opts.profile if opts.profile_hit else None
    need_origin = not (prof and prof.get("bootstrap") == "link")
    if landing and need_origin:
        extra, _ = core.bootstrap((core.origin_of(landing),), opts.proxy, headers)
        names = list(dict.fromkeys(names + extra))
    opts.landing = landing
    opts.bootstrap = "link+origin" if (landing and need_origin) else "link"
    opts.bootstrap_by_host[host] = opts.bootstrap
    core.log(f"会话 cookie: {', '.join(names) or '无'}"
             + ("" if need_origin else "  [经验] 按档跳过二次预热"))


def save_profile(url, opts, res, size, meta):
    """跑通后把有效链路写入主分册；短链等入口只留一份别名指针。"""
    landing = opts.landing or res.get("final_url") or url
    key = core.profile_key(landing)
    old = core.profile_load(opts.profile_dir, url) or {}
    data = {
        "host": urllib.parse.urlparse(landing).netloc.split(":")[0],
        "form": res.get("form") or old.get("form") or ["mobile", "landing"],
        "bootstrap": getattr(opts, "bootstrap", "link+origin"),
        "adapter": res.get("site", "webpage"),
        "verified": core.today(),
        "hits": int(old.get("hits", 0)) + (1 if opts.profile_hit else 0),
        "fails": 0,
        "baseline": f"{size / 1048576:.2f} MB" + (f" / {meta}" if meta else ""),
        "skill": core.skill_tag(),
        "status": "ok",
    }
    path = core.profile_save(opts.profile_dir, landing, data)
    if core.profile_key(url) != key:
        core.profile_alias(opts.profile_dir, url, key)
    core.log(f"[经验] 写入分册 {os.path.basename(path)}"
             + ("  (复用)" if opts.profile_hit else "  (首次分析)"))


def mark_failure(opts):
    """命中档却失败：累计失败次数，连续两次标记待复核，下次自动走完整链路。"""
    if not opts.profile_hit or not opts.profile:
        return
    prof = dict(opts.profile)
    host = prof.get("host")
    if not host:
        return
    fails = int(prof.get("fails", 0)) + 1
    prof.update({"fails": fails, "status": "review" if fails >= 2 else "ok",
                 "verified": core.today()})
    core.profile_save(opts.profile_dir, "https://" + str(host) + "/", prof)
    core.log(f"[经验] 连续失败 {fails} 次"
             + ("，分册已标记待复核" if fails >= 2 else ""))


def name_for(res, opts, suffix, index=None):
    rid = str(res.get("id") or "item")
    if index is not None:
        suffix = f"_{index:02d}{suffix}"
    if opts.ascii_name:
        return f"{rid}{suffix}"
    return core.safe_name(res.get("title", ""), rid, suffix)


def dump_info(res, adapters):
    core.log("[解析结果]")
    for u in res["urls"][:12]:
        core.log("  " + u[:160])
    if res["images"]:
        core.log(f"  图集 {len(res['images'])} 张")
    if not res["urls"] and not res["images"]:
        core.log("  未取到地址")
    return bool(res["urls"] or res["images"])


def failure_hint(mod):
    core.log("[fail] 页面返回校验壳页或未吐出素材地址。")
    core.log("       1) --browser chrome            本机浏览器会话")
    core.log("       2) --cookies cookies.txt       登录态 cookie 文件")
    core.log("       3) --proxy <节点>              换出口")
    core.log("       4) --refresh                   忽略旧经验，重新分析链路")
    core.log("       5) --engine ytdlp              外部下载器通道")


def download_video(res, opts, dest):
    referer = core.origin_of(res.get("final_url") or res["source_url"])
    for i, u in enumerate(res["urls"][:10], 1):
        core.log(f"[下载] 尝试 {i}/{min(len(res['urls']), 10)}: {u[:110]}")
        try:
            size, msg = core.download(u, dest, opts, headers={"Referer": referer})
        except Exception as e:
            core.log(f"    [warn] {type(e).__name__}: {str(e)[:140]}")
            continue
        if size:
            return size
        core.log(f"    [warn] {msg}")
    return 0


def fetch_one(raw, opts, adapters):
    state = {"url": None, "ok": False, "res": None, "size": 0, "meta": "", "skip": False}
    try:
        core.log("")
        core.log("=" * 68)
        url = core.pick_url(raw)
        if not url:
            core.log(f"[skip] 未找到链接: {raw[:80]}")
            return False
        state["url"] = url
        core.log(f"[链接] {url}")
        load_profile(url, opts)
        mod = route(url, adapters)
        if not mod:
            core.log("[fail] 链接协议不受支持。当前解析器：" + ", ".join(m.NAME for m in adapters))
            return False
        core.log(f"[模式] {getattr(mod, 'LABEL', '通用网页解析')}")
        prepare(mod, url, opts)

        if opts.engine == "ytdlp":
            state["skip"] = True
            if not core.ytdlp_cmd():
                core.log("[fail] 外部下载器未安装")
                return False
            ok, msg = core.run_ytdlp(url, opts)
            if ok:
                core.log("[完成] 外部下载器通道")
                return True
            core.log(f"  [warn] {msg[:300]}")
            return False

        res = mod.collect(url, raw, opts)
        if res.get("error"):
            core.log(f"[fail] {res['error']}")
            return False
        core.log(f"[信息] 视频地址 {len(res['urls'])} 个 / 图 {len(res['images'])} 张")
        if res.get("title"):
            core.log(f"[标题] {res['title'][:70]}")

        if opts.info:
            return dump_info(res, adapters)

        if not res["urls"] and not res["images"]:
            if opts.engine == "auto" and core.ytdlp_cmd():
                core.log("  [info] 解析通道无结果，转外部下载器兜底")
                ok, msg = core.run_ytdlp(url, opts)
                if ok:
                    core.log("[完成] 外部下载器兜底")
                    return True
                core.log(f"  [warn] {msg[:200]}")
            failure_hint(mod)
            return False

        os.makedirs(opts.out, exist_ok=True)

        if res["urls"]:
            dest = os.path.join(opts.out, name_for(res, opts, ".mp4"))
            size = download_video(res, opts, dest)
            if not size:
                if opts.engine == "auto" and core.ytdlp_cmd():
                    ok, msg = core.run_ytdlp(url, opts)
                    if ok:
                        core.log("[完成] 外部下载器兜底")
                        return True
                    core.log(f"  [warn] {msg[:200]}")
                core.log("[fail] 所有播放地址不可用")
                return False
            core.log(f"[完成] {dest}  ({size / 1048576:.2f} MB)")
            meta = core.describe_probe(core.probe_media(dest))
            if meta:
                core.log(f"[媒体] {meta}")
            state.update(ok=True, res=res, size=size, meta=meta)
            return True

        core.log("[图集] 逐张保存")
        ok = 0
        for i, u in enumerate(res["images"], 1):
            dest = os.path.join(opts.out, name_for(res, opts, ".jpg", index=i))
            try:
                size, msg = core.download(u, dest, opts)
                ok += bool(size)
                core.log(f"  {os.path.basename(dest)} ({size / 1048576:.2f} MB)" if size else f"  [warn] {msg}")
            except Exception as e:
                core.log(f"  [warn] {e}")
        state.update(ok=ok > 0, res=res, meta=f"图集 {ok} 张")
        return ok > 0
    finally:
        # finally 里的 return 会覆盖 try 的返回值，故改用条件包裹。
        if state["url"] and opts.use_profile and not state["skip"]:
            if state["ok"] and state["res"] is not None:
                save_profile(state["url"], opts, state["res"], state["size"], state["meta"])
            elif not opts.info:
                mark_failure(opts)


def run_selftest(adapters):
    assert core.selftest_core()
    core.log("[selftest] 公共底座通过：链接清洗 / 文件名 / URL 解析 / 经验档读写")
    for mod in adapters:
        fn = getattr(mod, "selftest_adapter", None)
        if fn:
            assert fn()
            core.log(f"[selftest] 适配器 {mod.NAME} 通过")
    return True


def main():
    ap = argparse.ArgumentParser(
        description="素材采集：把指定网页素材下载到本地",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("links", nargs="*", help="链接或整段分享文案")
    ap.add_argument("--file", help="每行一条链接的 txt")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "素材采集"),
                    help="素材落点，默认当前工作目录下的 素材采集/")
    ap.add_argument("--info", action="store_true", help="只解析地址，不下载")
    ap.add_argument("--engine", choices=["auto", "parse", "ytdlp"], default="auto")
    ap.add_argument("--cookie", help="cookie 头字符串")
    ap.add_argument("--cookies", help="Netscape cookies.txt")
    ap.add_argument("--browser", help="外部下载器读浏览器会话：chrome / edge / firefox")
    ap.add_argument("--proxy", help="代理地址")
    ap.add_argument("--ascii-name", action="store_true",
                    help="文件名只用 ASCII，供后续 ffmpeg 链路使用")
    ap.add_argument("--no-profile", action="store_true", help="本次不读不写经验档")
    ap.add_argument("--refresh", action="store_true", help="忽略旧经验重新分析，成功后覆盖分册")
    ap.add_argument("--list-profiles", action="store_true", help="列出已有站点分册")
    ap.add_argument("--list-sites", action="store_true", help="列出已注册解析器")
    ap.add_argument("--selftest", action="store_true", help="离线自检")
    args = ap.parse_args()

    adapters = discover()
    if args.list_sites:
        for m in adapters:
            desc = ", ".join(getattr(m, "HOSTS", ())) or "通用网页"
            core.log(f"{m.NAME}: {desc}")
        return 0
    if args.selftest:
        return 0 if run_selftest(adapters) else 1

    opts = core.Options(out=args.out, info=args.info, engine=args.engine,
                        cookie=args.cookie, cookies=args.cookies,
                        browser=args.browser, proxy=args.proxy)
    opts.use_profile = not args.no_profile
    opts.refresh = args.refresh
    opts.ascii_name = args.ascii_name

    if args.list_profiles:
        rows = core.list_profiles(opts.profile_dir)
        core.log(f"经验档目录: {opts.profile_dir}")
        if not rows:
            core.log("  暂无分册（首次跑通后自动生成）")
        for name, data in rows:
            if data.get("alias"):
                core.log(f"  {name}  → 别名，主分册 {data['alias']}.md")
            else:
                core.log(f"  {name} | {data.get('host', '')} | 复用 {data.get('hits', 0)} 次"
                         f" | 验证 {data.get('verified', '')} | {data.get('status', 'ok')}"
                         f" | {data.get('baseline', '')}")
        return 0

    items = list(args.links)
    if args.file:
        with open(args.file, "r", encoding="utf-8", errors="replace") as f:
            items += [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    if not items:
        ap.print_help()
        return 1

    core.log(f"素材落点: {opts.out}")
    core.log(f"经验档: {opts.profile_dir}" + ("  [本次停用]" if args.no_profile else ""))
    ok = 0
    for raw in items:
        try:
            ok += bool(fetch_one(raw, opts, adapters))
        except KeyboardInterrupt:
            core.log("\n已中断")
            break
        except Exception as e:
            core.log(f"[error] {type(e).__name__}: {e}")
    core.log("")
    core.log(f"===== 完成 {ok}/{len(items)} 条 =====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
