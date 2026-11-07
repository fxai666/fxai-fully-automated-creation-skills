#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fxai-auto-update · 合并结果落盘。

  apply --file <相对路径> --merged <合并后文件>   备份本地 → 落盘 → 刷新基线与状态
  keep  --file <相对路径>                         保留本地，把本地当前记为新基线

两个命令都会把该条从待合并队列里摘掉；队列清空时写入上游版本号。
"""
import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update import (backup_root, cfg_skills_dir, load_pending,  # noqa: E402
                    load_state, newest_backup, pending_path, safe_rel,
                    sha256_file, save_state, write_baseline)


def _backup_one(rel, bdir):
    skills = cfg_skills_dir()
    dst = os.path.join(skills, rel.replace("/", os.sep))
    if os.path.isfile(dst):
        bak = os.path.join(bdir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        shutil.copyfile(dst, bak)
    return dst


def _drop_pending(rel):
    d = load_pending()
    if not d:
        return None
    d["tasks"] = [t for t in d.get("tasks", []) if t.get("path") != rel]
    with open(pending_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d


def _finish(d, version):
    """队列清空 → 写入上游版本号。"""
    if d is not None and not d.get("tasks") and version:
        st = load_state()
        st["version"] = version
        st["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(st)
        print("[APPLY] 待合并已清空，本机版本 %s" % version, flush=True)


def cmd_apply(a):
    rel = a.file
    if not safe_rel(rel):
        print("[ERROR] 非法相对路径：%s" % rel, flush=True)
        return 2
    if not os.path.isfile(a.merged):
        print("[ERROR] 合并后文件不存在：%s" % a.merged, flush=True)
        return 2
    bdir = newest_backup() or os.path.join(backup_root(), time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(bdir, exist_ok=True)
    dst = _backup_one(rel, bdir)
    shutil.copyfile(a.merged, dst)
    write_baseline(rel, dst)
    st = load_state()
    st["files"][rel] = {"sha256": sha256_file(dst), "size": os.path.getsize(dst)}
    save_state(st)
    d = load_pending()
    version = (d or {}).get("version", "")
    d = _drop_pending(rel)
    print("[APPLY] 已落盘 %s" % rel, flush=True)
    _finish(d, version)
    return 0


def cmd_keep(a):
    rel = a.file
    if not safe_rel(rel):
        print("[ERROR] 非法相对路径：%s" % rel, flush=True)
        return 2
    skills = cfg_skills_dir()
    dst = os.path.join(skills, rel.replace("/", os.sep))
    if not os.path.isfile(dst):
        print("[ERROR] 本地文件不存在：%s" % dst, flush=True)
        return 2
    write_baseline(rel, dst)
    st = load_state()
    st["files"][rel] = {"sha256": sha256_file(dst), "size": os.path.getsize(dst)}
    save_state(st)
    d = load_pending()
    version = (d or {}).get("version", "")
    d = _drop_pending(rel)
    print("[KEEP] 保留本地 %s，已记为新基线" % rel, flush=True)
    _finish(d, version)
    return 0


def main():
    ap = argparse.ArgumentParser(description="合并结果落盘")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("apply", help="落盘合并后的文件")
    p.add_argument("--file", required=True, help="相对路径（清单里的写法）")
    p.add_argument("--merged", required=True, help="合并后的文件路径")
    p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("keep", help="保留本地，刷新基线")
    p.add_argument("--file", required=True, help="相对路径（清单里的写法）")
    p.set_defaults(fn=cmd_keep)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
