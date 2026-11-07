#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用网页适配器：从任意落地页提取媒体地址与图集。"""
import sys
sys.dont_write_bytecode = True

import hashlib
import html as html_mod
import json
import re
import urllib.parse

import core

NAME = "webpage"
PRIORITY = 100
HOSTS = ()
BOOTSTRAP = ()

_VIDEO_HINT = (".mp4", ".m3u8", ".mpd", "/play", "play?", "media", "download", "stream")
_IMAGE_HINT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", "/image/", "/photo/")
_SKIP_HINT = (".css", ".js", ".ico", ".svg", ".woff", "/sdk/", "analytics", "favicon")
_TITLE_KEYS = ("desc", "title", "caption")

# 请求形态：(UA 类型, Referer 来源)。运行时代入任意链接，不含站点信息。
FORMS = (
    ("mobile", "link"),
    ("desktop", "landing"),
    ("mobile", "landing"),
)


def match(url):
    return bool(url) and url.startswith(("http://", "https://"))


def json_blocks(page):
    """扫描页面内嵌的各形态 JSON 数据块。"""
    blocks = []
    for m in re.finditer(r"window\.\w+\s*=\s*(\{.*?\})\s*;?\s*</script>", page, re.S):
        try:
            blocks.append(json.loads(html_mod.unescape(m.group(1))))
        except Exception:
            pass
    for m in re.finditer(r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', page, re.S):
        try:
            raw = html_mod.unescape(m.group(1).strip())
            blocks.append(json.loads(urllib.parse.unquote(raw) if raw.startswith("%7B") else raw))
        except Exception:
            pass
    for m in re.finditer(r'<script[^>]+id="\w*DATA\w*"[^>]*>(.*?)</script>', page, re.S):
        try:
            blocks.append(json.loads(urllib.parse.unquote(html_mod.unescape(m.group(1).strip()))))
        except Exception:
            pass
    for m in re.finditer(r"self\.__next_f\.push\(\[1,\s*\"((?:[^\"\\]|\\.)*)\"\]\)", page):
        try:
            raw = json.loads('"' + m.group(1) + '"')
            start = raw.find("{")
            if start >= 0:
                blocks.append(json.loads(raw[start:]))
        except Exception:
            pass
    for m in re.finditer(r"window\.\w+\s*=\s*", page):
        tail = html_mod.unescape(page[m.end():]).lstrip()
        try:
            obj, _ = json.JSONDecoder().raw_decode(tail)
            blocks.append(obj)
        except Exception:
            pass
    return blocks


def classify(url, found):
    low = url.lower().split("?")[0]
    if any(h in low for h in _SKIP_HINT):
        return
    if any(h in low for h in _IMAGE_HINT):
        found["images"].add(url)
    elif any(h in low for h in _VIDEO_HINT) or ".mp4" in url.lower():
        found["urls"].add(url)


def collect_json(obj, found, seen=None):
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return
    seen.add(id(obj))
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in _TITLE_KEYS and isinstance(val, str) and not found["title"]:
                text = val.strip()
                if 2 < len(text) < 300:
                    found["title"] = text
            collect_json(val, found, seen)
    elif isinstance(obj, list):
        for it in obj:
            collect_json(it, found, seen)
    elif isinstance(obj, str) and obj.startswith("http"):
        classify(obj.replace("\\u002F", "/"), found)


def html_links(page, found):
    for u in re.findall(r'(?:src|href|content)\s*=\s*["\'](https?://[^"\']+)["\']', page, re.I):
        classify(html_mod.unescape(u), found)
    for u in re.findall(r'https?:\\?/\\?/[^"\'<>\s]{20,}', page):
        u = u.replace("\\/", "/").replace("\\u002F", "/")
        if u.startswith("http"):
            classify(u, found)


def rank(url):
    """按可用性降序排候选地址：直连优先、水印标记靠后。"""
    low = url.lower()
    score = 0
    if ".mp4" in low:
        score -= 30
    if ".m3u8" in low or ".mpd" in low:
        score -= 12
    if "/play" in low or "play?" in low:
        score -= 10
    if "1080" in low:
        score += 3
    if "720" in low or "480" in low:
        score += 2
    if "wm" in low:
        score += 20
    if "logo" in low:
        score += 6
    if len(url) > 400:
        score += 5
    return score


def expand(urls):
    """由水印标记与清晰度参数派生候选地址。"""
    out = []
    for u in urls:
        clean = re.sub(r"([?&])logo_name=[^&]*&?", r"\1", u)
        clean = re.sub(r"wm(?=[/?&]|$)", "", clean)
        clean = re.sub(r"&+", "&", clean).rstrip("?&")
        for base in (clean, u):
            for item in (base, re.sub(r"ratio=\d+p", "ratio=1080p", base)):
                if item and item not in out:
                    out.append(item)
    return out


def build_candidates(found):
    return sorted(expand(found["urls"]), key=rank)[:12]


def id_of(url):
    m = re.search(r"/(\d{8,25})(?:[/?#]|$)", url)
    if m:
        return m.group(1)
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def title_of(page, found):
    if found["title"]:
        return found["title"]
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    if m:
        return html_mod.unescape(m.group(1)).strip()
    m = re.search(r'<meta[^>]+(?:property|name)="og:title"[^>]+content="([^"]+)"', page, re.I)
    return html_mod.unescape(m.group(1)).strip() if m else ""


def collect(url, raw, opts):
    """按多种请求形态重取页面；命中经验档时把档内形态提到首位。"""
    landing = getattr(opts, "landing", None) or url
    forms = list(FORMS)
    prof = getattr(opts, "profile", None)
    if prof and not getattr(opts, "refresh", False):
        want = tuple(prof.get("form") or ())
        if want in forms:
            forms.remove(want)
            forms.insert(0, want)
            core.log(f"  [经验] 复用档内请求形态 {want[0]}/{want[1]}，首轮直取")
    found = {"urls": set(), "images": set(), "title": ""}
    page = ""
    final = landing
    blocks = []
    winner = None
    for form in forms:
        ua_kind, ref_kind = form
        ua = core.UA_MOBILE if ua_kind == "mobile" else core.UA_DESKTOP
        referer = core.origin_of(url if ref_kind == "link" else (landing or url))
        try:
            data, got_url, _ = core.http_get(
                url, headers=opts.headers(ua, referer), proxy=opts.proxy)
        except Exception as e:
            core.log(f"  [warn] 取数失败: {type(e).__name__} {str(e)[:80]}")
            continue
        final = got_url or final
        page = core.decode(data)
        blocks = json_blocks(page)
        for b in blocks:
            collect_json(b, found)
        html_links(page, found)
        if found["urls"]:
            winner = form
            break
    if not page:
        return {"site": NAME, "error": "页面取数失败：各形态均不可达"}
    core.log(f"  [ok] 落地页 {len(page):,} 字符 / 数据块 {len(blocks)}")
    return {
        "site": NAME,
        "id": id_of(final),
        "title": title_of(page, found),
        "urls": build_candidates(found),
        "images": sorted(found["images"])[:30],
        "source_url": url,
        "final_url": final,
        "form": list(winner) if winner else None,
    }


def selftest_adapter():
    """用本地页面结构校验通用解析，不联网、不依赖任何站点。"""
    fake = """<title>通用页标题</title><script>window.page_data = {
      "item": {"desc": "素材标题", "media": {"url_list": [
        "https://media.invalid/clip_720.mp4",
        "https://media.invalid/clip_wm.mp4",
        "https://media.invalid/cover.png",
        "https://static.invalid/sdk.js"]}}};</script>"""
    found = {"urls": set(), "images": set(), "title": ""}
    blocks = json_blocks(fake)
    assert blocks, blocks
    for b in blocks:
        collect_json(b, found)
    html_links(fake, found)
    assert found["title"] == "素材标题", found
    assert any(".png" in u for u in found["images"]), found
    assert not any(".js" in u for u in found["urls"]), found
    candidates = build_candidates(found)
    assert candidates and candidates[0].endswith("clip_720.mp4"), candidates
    assert match("https://any.invalid/page") and not match("file:///tmp/a")
    assert id_of("https://any.invalid/video/7123456789012345678") == "7123456789012345678"
    assert len(id_of("https://any.invalid/")) == 12
    return True
