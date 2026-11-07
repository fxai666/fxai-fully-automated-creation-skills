#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fxai-auto-update · 作者侧：由整包 zip 生成 skill.json 并摆好发布目录。

  python scripts/make_index.py --zip <整包zip> --version 1.2 --out <发布目录>
      [--path 包在发布目录下的相对路径] [--no-sha]

产出：<发布目录>/<path>（zip 本体）+ <发布目录>/skill.json（UTF-8 无 BOM）。
整个发布目录上传到官网 upgrade/ 下即完成发布。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="生成官网升级索引")
    ap.add_argument("--zip", required=True, help="整包 zip 路径")
    ap.add_argument("--version", required=True, help="版本号，形如 1.2")
    ap.add_argument("--out", required=True, help="发布目录（对应官网 upgrade/）")
    ap.add_argument("--path", default="", help="包在发布目录下的相对路径，缺省用文件名")
    ap.add_argument("--no-sha", action="store_true", help="不写 sha256 字段")
    a = ap.parse_args()

    zp = os.path.normpath(a.zip)
    if not os.path.isfile(zp):
        print("[ERROR] zip 不存在：%s" % zp, flush=True)
        return 2

    rel = (a.path or os.path.basename(zp)).replace("\\", "/").lstrip("/")
    out = os.path.normpath(a.out)
    os.makedirs(out, exist_ok=True)
    dst = os.path.join(out, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
    if os.path.abspath(dst) != os.path.abspath(zp):
        shutil.copyfile(zp, dst)

    idx = {"version": str(a.version).strip(), "path": rel}
    if not a.no_sha:
        idx["sha256"] = sha256_file(zp)
        idx["size"] = os.path.getsize(zp)

    ip = os.path.join(out, "skill.json")
    with open(ip, "w", encoding="utf-8", newline="\n") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
    print("[INDEX] version %s  path %s" % (idx["version"], idx["path"]), flush=True)
    print("[INDEX] %s" % ip, flush=True)
    print("[NEXT] 把 %s 整个目录上传到官网 upgrade/ 下" % out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
