#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chrome 运行时统一探测点：就绪即返回可执行文件，缺失时按官方源补装。

取值顺序：全局配置 chrome_path > PATH > 平台默认位置 > 自动化库自带浏览器。
--check-only 全程零下载、零安装、零系统改动。
--json 返回 ok / path / source / action / reason / version。
退出码：0 就绪、1 未就绪或补装失败、2 参数或平台不受支持。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True

GLOBAL_CONFIG = Path.home() / ".workbuddy" / "fxai_global.json"
CONFIG_FIELD = "chrome_path"
HOSTS = frozenset({"dl.google.com"})
MAX_BYTES = 300 * 1024 * 1024
NAMES = ("chrome", "chrome.exe", "google-chrome", "google-chrome-stable",
         "chromium", "chromium-browser")
PACKAGES = {
    "Windows": ("https://dl.google.com/chrome/install/latest/chrome_installer.exe", ".exe"),
    "Darwin": ("https://dl.google.com/chrome/mac/stable/GGRO/googlechrome.dmg", ".dmg"),
    "Linux": ("https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb", ".deb"),
}
RPM_URL = "https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm"


def config_value() -> str | None:
    """只取全局配置的 chrome_path 字段，其余字段零读取、零输出。"""
    if not GLOBAL_CONFIG.is_file():
        return None
    try:
        data = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        val = data.get(CONFIG_FIELD)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def default_locations() -> list[Path]:
    """平台默认位置由系统环境变量或系统目录拼接，零盘符字面量。"""
    name = platform.system()
    out: list[Path] = []
    if name == "Windows":
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(key)
            if base:
                out.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif name == "Darwin":
        out.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    else:
        out.append(Path("/opt/google/chrome/chrome"))
        out.append(Path("/usr/bin/google-chrome-stable"))
    return out


def bundled_browser() -> str | None:
    """自动化库自带浏览器由调用方显式允许后才兜底。"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            return pw.chromium.executable_path
    except Exception:
        return None


def nt_version(path: Path) -> str | None:
    """Windows 读 PE 版本资源，零窗口、零启动。"""
    from ctypes import wintypes

    class FixedInfo(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wintypes.DWORD),
            ("dwStrucVersion", wintypes.DWORD),
            ("dwFileVersionMS", wintypes.DWORD),
            ("dwFileVersionLS", wintypes.DWORD),
            ("dwProductVersionMS", wintypes.DWORD),
            ("dwProductVersionLS", wintypes.DWORD),
            ("dwFileFlagsMask", wintypes.DWORD),
            ("dwFileFlags", wintypes.DWORD),
            ("dwFileOS", wintypes.DWORD),
            ("dwFileType", wintypes.DWORD),
            ("dwFileSubtype", wintypes.DWORD),
            ("dwFileDateMS", wintypes.DWORD),
            ("dwFileDateLS", wintypes.DWORD),
        ]

    dll = ctypes.WinDLL("version.dll", use_last_error=True)
    dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    dll.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                        wintypes.DWORD, ctypes.c_void_p]
    dll.GetFileVersionInfoW.restype = wintypes.BOOL
    dll.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(wintypes.UINT)]
    dll.VerQueryValueW.restype = wintypes.BOOL

    holder = wintypes.DWORD()
    size = dll.GetFileVersionInfoSizeW(str(path), ctypes.byref(holder))
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not dll.GetFileVersionInfoW(str(path), 0, size, buf):
        return None
    ptr = ctypes.c_void_p()
    length = wintypes.UINT()
    if not dll.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        return None
    info = ctypes.cast(ptr, ctypes.POINTER(FixedInfo)).contents
    ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
    return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def probe(raw: str) -> str | None:
    """命中判据：文件存在且版本可读，残留旧版本与失效快捷方式不计命中。"""
    path = Path(raw).expanduser()
    if not path.is_file():
        return None
    if os.name == "nt":
        return nt_version(path)
    try:
        run = subprocess.run([str(path), "--version"], capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (run.stdout.strip() or "unknown") if run.returncode == 0 else None


def resolve(allow_bundled: bool = False) -> dict:
    """按取值顺序返回第一个命中项；全部落空返回未就绪。"""
    chain: list[tuple[str, str]] = []
    cfg = config_value()
    if cfg:
        chain.append(("global_config", cfg))
    for name in NAMES:
        hit = shutil.which(name)
        if hit:
            chain.append(("PATH", hit))
            break
    for loc in default_locations():
        chain.append(("platform_default", str(loc)))
    if allow_bundled:
        bundled = bundled_browser()
        if bundled:
            chain.append(("bundled", bundled))
    for source, raw in chain:
        version = probe(raw)
        if version:
            return {"ok": True, "path": str(Path(raw).expanduser()), "source": source,
                    "action": "none", "reason": "", "version": version}
    return {"ok": False, "path": "", "source": "", "action": "none",
            "reason": "", "version": ""}


def package_url() -> tuple[str | None, str]:
    """取包地址只认官方域名白名单，协议限定 HTTPS。"""
    name = platform.system()
    if name == "Linux" and shutil.which("rpm") and not shutil.which("dpkg"):
        return RPM_URL, ".rpm"
    url, suffix = PACKAGES.get(name, (None, ""))
    if url:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS:
            return None, ""
    return url, suffix


def fetch(url: str, suffix: str, workdir: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS:
        raise RuntimeError("下载地址未过白名单")
    dest = Path(workdir) / ("chrome_package" + suffix)
    with urllib.request.urlopen(url, timeout=60) as resp, dest.open("wb") as fout:
        total = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise RuntimeError("安装包超出体积上限")
            fout.write(chunk)
    return dest


def install(pkg: Path, suffix: str) -> tuple[bool, str]:
    """安装属系统级修改；权限不足返回原因与手动指引，不做提权重试。"""
    name = platform.system()
    try:
        if name == "Windows":
            run = subprocess.run([str(pkg), "/silent", "/install"],
                                 capture_output=True, text=True, timeout=300)
        elif name == "Darwin":
            mount = subprocess.run(["hdiutil", "attach", "-nobrowse", "-quiet", str(pkg)],
                                   capture_output=True, text=True, timeout=120)
            if mount.returncode != 0:
                return False, "磁盘映像挂载失败，按官方安装说明手动安装"
            volume = "/Volumes/Google Chrome"
            app = Path(volume) / "Google Chrome.app"
            if app.is_dir():
                shutil.copytree(app, Path("/Applications/Google Chrome.app"), dirs_exist_ok=True)
            subprocess.run(["hdiutil", "detach", volume, "-quiet"],
                           capture_output=True, timeout=60)
            run = subprocess.run(["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                                  "--version"], capture_output=True, text=True, timeout=30)
        else:
            cmd = ["rpm", "-i", str(pkg)] if suffix == ".rpm" else ["dpkg", "-i", str(pkg)]
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "安装进程失败（%s），按官方安装说明手动安装" % type(exc).__name__
    if run.returncode != 0:
        return False, "包管理器返回非零，按官方安装说明手动安装"
    return True, ""


def emit(res: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if res["ok"]:
        print("浏览器就绪  : %s" % res["path"])
        print("来源        : %s" % res["source"])
        print("版本        : %s" % res["version"])
        print("动作        : %s" % res["action"])
    else:
        print("浏览器未就绪：%s" % (res["reason"] or "按取值顺序全部落空"), file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Chrome 运行时检测与缺失补装")
    ap.add_argument("--check-only", action="store_true", help="只检测，零下载零安装")
    ap.add_argument("--allow-bundled", action="store_true", help="允许自动化库自带浏览器兜底")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    res = resolve(args.allow_bundled)
    if res["ok"]:
        emit(res, args.json)
        return 0

    if args.check_only:
        res["reason"] = "未检测到可用 Chrome（只读模式，零下载零安装）"
        emit(res, args.json)
        return 1

    url, suffix = package_url()
    if not url:
        res["action"] = "failed"
        res["reason"] = "平台不受支持或取包地址未过白名单，按官方安装说明手动安装"
        emit(res, args.json)
        return 2

    if not args.json:
        print("浏览器未就绪，将按官方源补装：%s" % urllib.parse.urlparse(url).hostname,
              file=sys.stderr)
    ok, reason = False, ""
    try:
        with tempfile.TemporaryDirectory(prefix="fxai-chrome-") as workdir:
            pkg = fetch(url, suffix, workdir)
            ok, reason = install(pkg, suffix)
    except Exception as exc:
        reason = "取包失败（%s）" % type(exc).__name__

    after = resolve(args.allow_bundled)
    if after["ok"]:
        after["action"] = "installed" if ok else "none"
        emit(after, args.json)
        return 0
    res["action"] = "failed"
    res["reason"] = reason or "补装后仍未检测到可用 Chrome"
    emit(res, args.json)
    return 1


if __name__ == "__main__":
    sys.exit(main())
