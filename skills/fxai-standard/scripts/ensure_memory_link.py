#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作区记忆空间对齐：真身恒在 <工作区>/.workbuddy/memory，别名目录建成指向它的目录联接。

用法:
    python ensure_memory_link.py [<工作区根>] [--alias .workbuddy-ai] [--apply] [--json]

    <工作区根>      缺省取当前目录
    --alias NAME    别名目录名（相对工作区根），可重复；缺省 .workbuddy-ai
    --apply         落地变更；缺省只体检
    --json          机器可读输出
    --keep-backup   迁移时保留备份副本（缺省迁移成功后清掉）

退出码:
    0  已对齐 / 已执行完成
    3  待对齐（未加 --apply）
    1  出错

口径与判据见 fxai-standard `references/09-会话记忆.md`「记忆空间目录」。
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time

sys.dont_write_bytecode = True

REAL_REL = os.path.join(".workbuddy", "memory")
DEFAULT_ALIAS = ".workbuddy-ai"
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

# ---------- 文件系统判定 ----------


def link_state(path):
    """返回 (状态, 解析后的目标)。状态取 junction / symlink / dir / file / missing。"""
    if not os.path.lexists(path):
        return "missing", None
    st = os.lstat(path)
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        tag = getattr(st, "st_reparse_tag", 0)
        kind = "junction" if tag == IO_REPARSE_TAG_MOUNT_POINT else "symlink"
        return kind, os.path.realpath(path)
    return ("dir" if os.path.isdir(path) else "file"), None


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_junction(link, target):
    """建 NTFS 目录联接。不需管理员权限，不需开发者模式。"""
    if os.path.lexists(link):
        kind, _ = link_state(link)
        if kind != "dir" or os.listdir(link):
            raise RuntimeError("目标位置被占用，拒绝建联接: %s" % link)
        os.rmdir(link)
    if os.name != "nt":
        os.makedirs(os.path.dirname(link) or ".", exist_ok=True)
        os.symlink(os.path.abspath(target), link, target_is_directory=True)
        return
    os.makedirs(os.path.dirname(link) or ".", exist_ok=True)
    os.mkdir(link)
    import ctypes
    import struct
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FSCTL_SET_REPARSE_POINT = 0x000900A4
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    h = k32.CreateFileW(
        str(link), GENERIC_WRITE, 0, None, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if h == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateFileW 失败: %s" % link)
    try:
        # 布局口径：替换名 \\??\<绝对路径>；打印名留空（长度 0）；路径缓冲末尾补 NUL。
        # 三者缺一即被系统判为 ERROR_INVALID_REPARSE_DATA（4392）。
        subst = "\\??\\" + os.path.abspath(target)
        subst_b = subst.encode("utf-16-le")
        path_buffer = subst_b + b"\x00\x00" + b"\x00\x00"
        data = struct.pack(
            "<IHHHHHH", IO_REPARSE_TAG_MOUNT_POINT, 8 + len(path_buffer), 0,
            0, len(subst_b), len(subst_b) + 2, 0,
        ) + path_buffer
        buf = ctypes.create_string_buffer(data, len(data))
        ret = wintypes.DWORD(0)
        k32.DeviceIoControl.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD, wintypes.LPVOID,
        ]
        ok = k32.DeviceIoControl(
            h, FSCTL_SET_REPARSE_POINT, buf, len(data), None, 0,
            ctypes.byref(ret), None,
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "DeviceIoControl 失败: %s" % link)
    finally:
        k32.CloseHandle(h)


def drop_link(path):
    """只删联接本身，不碰目标内容。"""
    kind, _ = link_state(path)
    if kind not in ("junction", "symlink"):
        raise RuntimeError("不是联接，拒绝按联接删除: %s" % path)
    os.rmdir(path)


# ---------- 迁移 ----------


def iter_files(root):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            yield os.path.relpath(full, root)


def migrate(src, dst):
    """把 src 的正文并入 dst。同名同内容跳过；同名不同内容保留 dst 并记为冲突。"""
    copied, same, conflicts = [], [], []
    for rel in iter_files(src):
        s, d = os.path.join(src, rel), os.path.join(dst, rel)
        os.makedirs(os.path.dirname(d) or dst, exist_ok=True)
        if os.path.exists(d):
            if md5(s) == md5(d):
                same.append(rel)
            else:
                conflicts.append(rel)
            continue
        shutil.copy2(s, d)
        copied.append(rel)
    return {"copied": copied, "same": same, "conflicts": conflicts}


def verify_mirror(src, dst):
    """src 里每个文件在 dst 中同内容才算通过。"""
    bad = []
    for rel in iter_files(src):
        d = os.path.join(dst, rel)
        if not os.path.isfile(d) or md5(os.path.join(src, rel)) != md5(d):
            bad.append(rel)
    return bad


# ---------- 主流程 ----------


def align_real(root, apply_):
    real = os.path.join(root, REAL_REL)
    kind, _ = link_state(real)
    if kind == "dir":
        return {"path": real, "action": "ok", "note": "真身已存在"}
    if kind == "missing":
        if apply_:
            os.makedirs(real, exist_ok=True)
            return {"path": real, "action": "created", "note": "真身目录已建"}
        return {"path": real, "action": "create", "note": "真身目录缺失"}
    return {"path": real, "action": "error",
            "note": "真身路径不是目录（%s），需人工处理" % kind}


def align_alias(root, alias_name, real, apply_):
    alias = os.path.join(root, alias_name, "memory")
    kind, target = link_state(alias)
    real_abs = os.path.realpath(real)
    want = os.path.normcase(real_abs)

    if kind == "missing":
        return {"path": alias, "action": "skip", "note": "工作区无此别名目录"}

    if kind in ("junction", "symlink"):
        if target and os.path.normcase(target) == want:
            return {"path": alias, "action": "ok", "note": "已指向真身（%s）" % kind}
        if not apply_:
            return {"path": alias, "action": "repoint",
                    "note": "指向了别处: %s" % target}
        drop_link(alias)
        make_junction(alias, real)
        return {"path": alias, "action": "repointed", "note": "已改指真身"}

    if kind == "file":
        return {"path": alias, "action": "error", "note": "同名文件占位，需人工处理"}

    # kind == dir：正文在别名里，需迁回真身再改联接
    body = list(iter_files(alias))
    if not body:
        if not apply_:
            return {"path": alias, "action": "link", "note": "空目录，待改为联接"}
        os.rmdir(alias)
        make_junction(alias, real)
        return {"path": alias, "action": "linked", "note": "空目录已改为联接"}

    if not apply_:
        return {"path": alias, "action": "migrate",
                "note": "正文在别名内（%d 个文件），待迁回真身后改联接" % len(body)}

    os.makedirs(real, exist_ok=True)
    backup = tempfile.mkdtemp(prefix="mem-migrate-")
    shutil.copytree(alias, os.path.join(backup, "memory"))
    res = migrate(alias, real)
    if res["conflicts"]:
        # 同名不同内容一律不自动裁决：真身已收下非冲突文件，别名原样保留，等人工合并后重跑
        return {"path": alias, "action": "error",
                "note": "同名文件内容不同，停下等人工合并：%s；别名目录保持原样；备份 %s"
                        % (", ".join(res["conflicts"][:5]), backup),
                "conflicts": res["conflicts"], "backup": backup}
    bad = verify_mirror(alias, real)
    if bad:
        return {"path": alias, "action": "error",
                "note": "迁移校验未通过，别名目录保持原样；备份 %s；不一致 %s"
                        % (backup, ", ".join(bad[:5]))}
    shutil.rmtree(alias)
    try:
        make_junction(alias, real)
    except OSError as exc:
        kind2, _ = link_state(alias)
        if kind2 in ("junction", "symlink"):
            drop_link(alias)
        elif os.path.isdir(alias):
            shutil.rmtree(alias)
        shutil.copytree(os.path.join(backup, "memory"), alias)
        return {"path": alias, "action": "error",
                "note": "建联接失败（%s），别名目录已回填；备份 %s" % (exc, backup)}
    if not keep_backup:
        shutil.rmtree(backup, ignore_errors=True)
        backup = None
    return {"path": alias, "action": "migrated",
            "note": "正文已迁入真身并改为联接（新增 %d / 同内容 %d / 冲突 %d）"
                    % (len(res["copied"]), len(res["same"]), len(res["conflicts"])),
            "conflicts": res["conflicts"], "backup": backup}


def main(argv=None):
    global keep_backup
    ap = argparse.ArgumentParser(description="工作区记忆空间对齐")
    ap.add_argument("root", nargs="?", default=os.getcwd(), help="工作区根目录")
    ap.add_argument("--alias", action="append", default=None,
                    help="别名目录名，可重复；缺省 %s" % DEFAULT_ALIAS)
    ap.add_argument("--apply", action="store_true", help="落地变更")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--keep-backup", action="store_true", help="迁移后保留备份")
    args = ap.parse_args(argv)
    keep_backup = args.keep_backup

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("工作区不存在: %s" % root)
        return 1
    aliases = args.alias or [DEFAULT_ALIAS]

    real_res = align_real(root, args.apply)
    rows = [real_res]
    if real_res["action"] != "error":
        real = os.path.join(root, REAL_REL)
        for name in aliases:
            rows.append(align_alias(root, name, real, args.apply))

    pending = [r for r in rows if r["action"] in ("create", "link", "repoint", "migrate")]
    errors = [r for r in rows if r["action"] == "error"]

    if args.json:
        print(json.dumps({"root": root, "rows": rows,
                          "pending": len(pending), "errors": len(errors)},
                         ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print("[%s] %s  %s" % (r["action"], r["path"], r["note"]))
        if pending:
            print("待对齐 %d 项；加 --apply 落地" % len(pending))
        elif not errors:
            print("记忆空间已对齐")

    if errors:
        return 1
    if pending:
        return 3
    return 0


keep_backup = False

if __name__ == "__main__":
    sys.exit(main())
