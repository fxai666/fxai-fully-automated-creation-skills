#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""素材采集公共底座：HTTP、cookie、下载、文件名、外部兜底。"""
import sys
sys.dont_write_bytecode = True

import datetime
import http.cookiejar
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request

UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
)

_JAR = http.cookiejar.CookieJar()
_URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff\"'<>，。；、）】]+")

# 使用层全局配置：技能包外、固定用户级、跨项目复用。
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_AUTO_BEGIN = "<!-- auto:begin -->"
_AUTO_END = "<!-- auto:end -->"


class Options:
    """一次采集任务的运行参数。"""

    def __init__(self, out, info=False, engine="auto", cookie=None,
                 cookies=None, browser=None, proxy=None):
        self.out = os.path.abspath(out)
        self.info = info
        self.engine = engine
        self.browser = browser
        self.proxy = proxy
        self.cookies_file = cookies
        parts = []
        if cookie:
            parts.append(cookie.strip())
        if cookies:
            parts.append(load_cookie_file(cookies))
        self.cookie = "; ".join(p for p in parts if p)
        self.landing = None
        self.profile_dir = profile_dir()
        self.use_profile = True
        self.refresh = False
        self.profile = None
        self.profile_hit = False
        self.bootstrap_by_host = {}

    def headers(self, ua=UA_DESKTOP, referer=None):
        h = {"User-Agent": ua, "Accept-Encoding": "identity"}
        if referer:
            h["Referer"] = referer
        if self.cookie:
            h["Cookie"] = self.cookie
        return h


def log(msg):
    print(msg, flush=True)


def opener(proxy=None):
    handlers = [urllib.request.HTTPCookieProcessor(_JAR)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def http_get(url, headers=None, timeout=25, proxy=None):
    """返回 (bytes, final_url, headers dict)，跟随重定向。"""
    base = {
        "User-Agent": UA_DESKTOP,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    if headers:
        base.update(headers)
    req = urllib.request.Request(url, headers=base)
    with opener(proxy).open(req, timeout=timeout) as resp:
        return resp.read(), resp.geturl(), dict(resp.headers)


def decode(data):
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def bootstrap(urls, proxy=None, headers=None):
    """按给定入口换取会话 cookie，返回 (cookie 名, 落地页地址)。"""
    landing = None
    for url in urls:
        try:
            _, final, _ = http_get(url, headers=headers, proxy=proxy, timeout=15)
            landing = final
        except Exception:
            pass
    return [c.name for c in _JAR], landing


def pick_url(text):
    """从整段分享文案里取第一个链接，去掉尾部标点与干扰串。"""
    m = _URL_RE.search(text)
    return m.group(0).rstrip("/.,;:!?") if m else None


def load_cookie_file(path):
    """读 Netscape cookies.txt 或纯 cookie 字符串，统一成 cookie 头。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read().strip()
    if re.search(r"^\S+\t\S+\t\S+", raw, re.M):
        parts = []
        for line in raw.splitlines():
            if not line or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) >= 7:
                parts.append(f"{cols[5]}={cols[6]}")
        return "; ".join(parts)
    return raw.replace("\n", "; ")


def origin_of(url):
    """从目标链接推导站点根地址。"""
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def cookie_domain(url):
    """从目标链接推导 cookie 作用域。"""
    host = urllib.parse.urlparse(url).netloc.split(":")[0]
    return "." + host.removeprefix("www.") if host else "."


def write_cookie_string(text, domain=None, name="fxai_cookie_string.txt"):
    """把 cookie 头写成 Netscape 文件，供外部下载器读取。"""
    path = os.path.join(tempfile.gettempdir(), name)
    dom = domain or "."
    lines = ["# Netscape HTTP Cookie File", ""]
    for pair in text.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        lines.append(f"{dom}\tTRUE\t/\tTRUE\t0\t{k.strip()}\t{v.strip()}")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")
    return path


def export_cookie_file(name="fxai_anon_cookies.txt"):
    """把会话 cookie 写成 Netscape 文件，供外部下载器读取。"""
    path = os.path.join(tempfile.gettempdir(), name)
    jar = http.cookiejar.MozillaCookieJar(path)
    for c in _JAR:
        try:
            jar.set_cookie(c)
        except Exception:
            pass
    try:
        jar.save(ignore_discard=True, ignore_expires=True)
        return path
    except Exception:
        return None


def safe_name(text, fallback, suffix=".mp4"):
    text = re.sub(r'[\\/:*?"<>|\r\n\t\x00-\x1f]', "_", (text or "").strip())
    text = re.sub(r"\s+", " ", text)[:40].strip(" ._")
    if not text or text == fallback:
        return f"{fallback}{suffix}"
    return f"{fallback}_{text}{suffix}"


def download(url, dest, opts, headers=None):
    """流式下载并校验，返回 (字节数, 状态串)。"""
    base = {
        "User-Agent": UA_MOBILE,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if headers:
        base.update(headers)
    if opts.cookie and "Cookie" not in base:
        base["Cookie"] = opts.cookie
    req = urllib.request.Request(url, headers=base)
    with opener(opts.proxy).open(req, timeout=60) as resp:
        ctype = resp.headers.get("Content-Type", "")
        total = int(resp.headers.get("Content-Length") or 0)
        if "text/html" in ctype:
            return 0, "返回 HTML"
        tmp = dest + ".part"
        got = 0
        last = -1
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    pct = int(got * 100 / total)
                    if pct // 10 != last // 10:
                        last = pct
                        log(f"    {pct}%  ({got / 1048576:.1f} MB)")
        if got < 100 * 1024:
            os.remove(tmp)
            return 0, f"文件过小 ({got} B)"
        os.replace(tmp, dest)
        return got, "ok"


def ytdlp_cmd():
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    exe = shutil.which("yt-dlp")
    return [exe] if exe else None


def run_ytdlp(url, opts):
    """外部下载器兜底，返回 (是否成功, 说明)。"""
    base = ytdlp_cmd()
    if not base:
        return False, "外部下载器未安装"
    tmpl = os.path.join(opts.out, "%(id)s_%(title).40B.%(ext)s")
    cmd = base + [
        "--no-playlist", "--no-warnings", "-f", "best[ext=mp4]/best",
        "--merge-output-format", "mp4", "-o", tmpl,
    ]
    if opts.cookies_file:
        cmd += ["--cookies", opts.cookies_file]
    elif opts.browser:
        cmd += ["--cookies-from-browser", opts.browser]
    elif opts.cookie:
        f = write_cookie_string(opts.cookie, domain=cookie_domain(url))
        if f:
            cmd += ["--cookies", f]
    elif len(_JAR):
        f = export_cookie_file()
        if f:
            cmd += ["--cookies", f]
    if opts.proxy:
        cmd += ["--proxy", opts.proxy]
    if opts.info:
        cmd += ["--skip-download", "--print", "%(id)s | %(title)s | %(duration)ss"]
    cmd += [url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        if p.stdout.strip():
            lines = [x.strip() for x in p.stdout.strip().splitlines()
                     if x.strip() and not x.lstrip().startswith("[")]
            if lines:
                log("  " + " | ".join(lines[-5:]))
        if p.returncode == 0:
            return True, "ok"
        detail = re.sub(r"\[[^\]]+\]\s*", "", (p.stderr or "").strip())[-600:]
        return False, detail or f"exit {p.returncode}"
    except Exception as e:
        return False, str(e)


def probe_media(path):
    """用 ffprobe 读时长与尺寸；未安装时返回空 dict。"""
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        p = subprocess.run(
            [exe, "-v", "error", "-show_entries",
             "format=duration:stream=codec_name,width,height",
             "-of", "json", path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
        return json.loads(p.stdout) if p.returncode == 0 else {}
    except Exception:
        return {}


def describe_probe(info):
    """把 ffprobe 结果压成一行可读文本。"""
    if not info:
        return ""
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("width")), {})
    bits = []
    if video.get("width"):
        bits.append(f"{video['width']}x{video['height']}")
    codecs = "/".join(s.get("codec_name", "") for s in streams if s.get("codec_name"))
    if codecs:
        bits.append(codecs)
    dur = info.get("format", {}).get("duration")
    if dur:
        bits.append(f"{float(dur):.2f}s")
    return " / ".join(bits)


# ---------------------------------------------------------------- 使用层全局配置
# 技能包只提供这套机制；站点分册由运行时生成，落在技能包外的用户级空间。

_SKILL_TAG = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def skill_tag():
    return _SKILL_TAG


def today():
    return datetime.date.today().isoformat()


def default_profile_root():
    """固定的用户级全局配置根，跨项目复用。"""
    return os.path.join(os.path.expanduser("~"), ".workbuddy", _SKILL_TAG)


def profile_dir(root=None):
    """分册目录；root 仅供离线自检指定临时目录。"""
    return os.path.join(os.path.abspath(root or default_profile_root()), "sites")


def parse_front_matter(text):
    """读分册头部的机器参数块；零依赖，只认标量与短列表。"""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    data = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip(), val.strip().strip("\"'")
        if val.startswith("[") and val.endswith("]"):
            data[key] = [x.strip().strip("\"'") for x in val[1:-1].split(",") if x.strip()]
        elif val.lower() in ("true", "false"):
            data[key] = val.lower() == "true"
        elif re.fullmatch(r"-?\d+", val):
            data[key] = int(val)
        else:
            data[key] = val
    return data, text[m.end():]


def dump_front_matter(data):
    lines = ["---"]
    for key, val in data.items():
        if key.startswith("_"):
            continue
        if isinstance(val, (list, tuple)):
            val = "[" + ", ".join(str(x) for x in val) + "]"
        lines.append(f"{key}: {val}")
    return "\n".join(lines + ["---", ""]) + "\n"


def profile_key(url):
    host = urllib.parse.urlparse(url).netloc.split(":")[0].lower()
    host = host.removeprefix("www.")
    return re.sub(r"[^a-z0-9.-]", "_", host) or "unknown"


def profile_path(directory, url):
    return os.path.join(directory, profile_key(url) + ".md")


def profile_load(directory, url):
    """按链接查分册；遇入口别名自动跳到主分册。"""
    seen = set()
    path = profile_path(directory, url)
    for _ in range(3):
        if path in seen or not os.path.exists(path):
            return None
        seen.add(path)
        with open(path, "r", encoding="utf-8") as f:
            data, body = parse_front_matter(f.read())
        alias = data.get("alias")
        if alias:
            path = os.path.join(directory, str(alias) + ".md")
            continue
        data["_path"] = path
        data["_body"] = body
        return data
    return None


def render_manual(data):
    form = data.get("form") or []
    ua = "移动 UA" if form and form[0] == "mobile" else "桌面 UA"
    ref = "落地页根" if len(form) > 1 and form[1] == "landing" else "目标链接"
    boot = ("目标链接 + 落地页根（两次）" if data.get("bootstrap") == "link+origin"
            else "目标链接（一次）")
    return "\n".join([
        f"# 站点采集手册 · {data.get('host', '')}",
        "",
        "> 本分册由运行时自动生成，属**使用层全局配置**，不在技能包内。",
        "> 通用能力与步骤见技能包 `references/02-扩展解析.md`，本册不复写。",
        "",
        _AUTO_BEGIN,
        "",
        "## 有效链路",
        "",
        "| 项 | 值 |",
        "|----|----|",
        f"| 请求形态 | {ua} / Referer 取{ref} |",
        f"| 会话预热 | {boot} |",
        f"| 解析器 | {data.get('adapter', 'webpage')} |",
        "| 候选处理 | 去水印标记派生 + 高清变体，无标记直连优先 |",
        "",
        "## 产物基线",
        "",
        f"- {data.get('baseline') or '待记录'}",
        f"- 最近验证：{data.get('verified', '')}",
        "",
        "## 复用记录",
        "",
        f"- 复用 {data.get('hits', 0)} 次 / 连续失败 {data.get('fails', 0)} 次"
        f" / 状态 {data.get('status', 'ok')}",
        f"- 技能版本：{data.get('skill', '')}",
        "",
        _AUTO_END,
        "",
    ]) + "\n"


def profile_save(directory, url, data):
    """写/更新分册：参数块与自动区块重写，人工笔记原样保留。"""
    path = profile_path(directory, url)
    os.makedirs(directory, exist_ok=True)
    manual = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            old = f.read()
        if _AUTO_END in old:
            manual = old.split(_AUTO_END, 1)[1].lstrip("\n")
    if not manual.strip():
        manual = ("## 已知坑（人工补充）\n\n"
                  "<!-- 这一节不会被自动回写覆盖，踩到坑就记在这里 -->\n")
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    text = dump_front_matter(payload) + render_manual(payload) + "\n" + manual
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def profile_alias(directory, url, target_key):
    """给短链等入口写一份只含指针的别名册，保证同一站点正文只有一份。"""
    path = profile_path(directory, url)
    if os.path.exists(path):
        return
    os.makedirs(directory, exist_ok=True)
    text = dump_front_matter({"alias": target_key, "verified": today()})
    text += f"# 入口别名\n\n主分册见 `{target_key}.md`，参数与链路以主分册为准。\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def list_profiles(directory):
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(directory, name), "r", encoding="utf-8") as f:
            data, _ = parse_front_matter(f.read())
        out.append((name, data))
    return out


def selftest_core():
    assert pick_url("文案 https://link.invalid/abc/ 尾部").startswith("https://link.invalid/abc")
    assert safe_name("标题 / 带斜杠", "123").endswith(".mp4")
    assert safe_name("标题 / 带斜杠", "123").find("/") == -1
    assert origin_of("https://page.invalid/video/1") == "https://page.invalid/"
    assert cookie_domain("https://www.page.invalid/video/1") == ".page.invalid"
    with tempfile.TemporaryDirectory() as root:
        d = profile_dir(root)
        assert profile_load(d, "https://any.invalid/x") is None
        profile_save(d, "https://any.invalid/x",
                     {"host": "any.invalid", "form": ["mobile", "landing"],
                      "bootstrap": "link+origin", "hits": 2})
        got = profile_load(d, "https://www.any.invalid/y")
        assert got and got["host"] == "any.invalid" and got["hits"] == 2, got
        assert got["form"] == ["mobile", "landing"], got
        profile_alias(d, "https://short.invalid/a", profile_key("https://any.invalid/x"))
        assert profile_load(d, "https://short.invalid/a")["host"] == "any.invalid"
        assert len(list_profiles(d)) == 2
    return True
