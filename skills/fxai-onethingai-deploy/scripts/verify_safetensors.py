#!/usr/bin/env python
"""safetensors 真·完整性校验（本地，离线，不连服务器）。

为什么必须走这一步：2026-10-03 我光看「文件大小 ≈ source_bytes」就当它下完了，
转头把 4 份**其实完整**的文件当半截删了（千问 UNet 13.25G / CLIP 8.70G /
H3 视频VAE 4.85G / H3 32B 编码器 14.60G）。光看大小不够，得让文件自己说它完整。

safetensors 格式自证：文件头 8 字节小端 uint64 = 后面元信息 JSON 的字节数 n，
元信息里每个张量带 data_offsets，最后一个张量的 [1] 就是数据区末尾。
于是  应有总字节 = 8 + n + max(data_offsets[1])
拿它和 os.path.getsize 对，全等 = 真完整，不等 = 坏/半截。

用法：
  python verify_safetensors.py                # 按 manifest.json 查本机模型目录 <你的ComfyUI目录>/models
  python verify_safetensors.py --dir DIR      # 直接扫某个目录
  python verify_safetensors.py --path A B     # 指定文件
  python verify_safetensors.py --json         # 机器可读
退出码：0 全过 / 1 有坏的
"""
from __future__ import annotations

import argparse
import io
import json
import os
import struct
import sys

# ComfyUI 的 models / input 目录由全局配置的 comfy_root 决定，不写死本机盘符
MODELS_DIR = r""
INPUT_DIR = r""

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_JSON = os.path.join(os.path.dirname(HERE), "assets", "manifest.json")
DEFAULT_ROOT = MODELS_DIR


def expected_size(path: str):
    """返回 (实际, 应有)；读不了头就抛。"""
    sz = os.path.getsize(path)
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        meta = json.loads(f.read(n))
    end = max(t["data_offsets"][1] for k, t in meta.items()
              if k not in ("__metadata__", "__data_offsets__"))
    return sz, 8 + n + end


def check(path: str):
    """返回 (实际字节, 应有字节, 差异说明)。"""
    try:
        got, exp = expected_size(path)
    except Exception as e:  # 头都读不出来 = 必坏
        return -1, -1, f"读不了文件头：{e}"
    return got, exp, "" if got == exp else f"少 {got - exp} 字节"


def files_from_args(a):
    if a.path:
        return [(p, p) for p in a.path]
    if a.dir:
        out = []
        for dp, _dn, fns in os.walk(a.dir):
            out += [(os.path.join(dp, fn), os.path.join(dp, fn)) for fn in fns
                    if fn.endswith(".safetensors")]
        return sorted(out)
    m = json.load(io.open(MANIFEST_JSON, encoding="utf-8"))
    rows = []
    for name, v in m.items():
        if name == "_说明":
            continue
        save = (v.get("save") or "").strip("/")
        p = os.path.join(DEFAULT_ROOT, save, name) if save else os.path.join(DEFAULT_ROOT, name)
        rows.append((name, p))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir")
    ap.add_argument("--path", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    a = ap.parse_args()

    rows = files_from_args(a)
    res = []
    for label, p in rows:
        if not os.path.exists(p):
            res.append((label, -1, -1, "文件不存在"))
            continue
        res.append((label,) + check(p))

    bad = [r for r in res if r[1] != r[2]]
    if a.json:
        print(json.dumps([{"file": r[0], "size": r[1], "expect": r[2],
                           "err": r[3]} for r in res], ensure_ascii=False, indent=1))
        return 1 if bad else 0

    def gb(n):
        n = n if isinstance(n, int) else 0
        return f"{max(n, 0) / 2**30:.2f}G"
    print(f"扫描 {len(res)} 个 safetensors（判据：8 + 元信息长度 + 末张量偏移 == 实际字节）")
    print("-" * 78)
    for r in res:
        if r[3] == "文件不存在":
            print(f"  ⚪ 缺   {r[0]}")
        elif not r[3]:
            print(f"  ✅ 完整 {r[0]:<52} {gb(r[1])}")
        else:
            print(f"  ❌ 坏   {r[0]:<52} {gb(r[1])} / 应为 {gb(r[2])}  {r[3]}")
    print("-" * 78)
    print(f"完整 {len(res) - len(bad)}/{len(res)}" +
          (f"，坏 {len(bad)} 个：" + "、".join(r[0] for r in bad) if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
