# -*- coding: utf-8 -*-
"""一次性摸清：每个模型在服务器上的可下载性（只看存在性与真实大小，Range 能力查 `models.json` 的 `resume` 段）。

输出三张清单：
  A 服务器上能下、且和我们本地那一份字节完全一致  -> 直接在服务器下，最快
  B 服务器上能下、但和本地不是同一份（大小不同）    -> 让你决定用哪份
  C 服务器上找不到                                  -> 只能从本机传
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
from remote import get_ssh  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
mf = json.load(io.open(os.path.join(HERE, "..", "assets", "models.json"), encoding="utf-8"))
MS = mf["mirror2"]
HF = mf["fallback"]
LOCAL_ROOT = os.environ.get("OTAI_LOCAL_MODELS") or r"<你的ComfyUI目录>\models"

PROBE = r'''
for row in "${ROWS[@]}"; do
  IFS='~' read -r mid sname url <<< "$row"
  code=$(curl -sL -m 25 -r 0-0 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
  sz=$(curl -sL -m 25 -r 0-0 -D - -o /dev/null "$url" 2>/dev/null \
       | tr -d '\r' | grep -i '^content-range:' | tail -n1 | sed 's#.*/##' | tr -dc '0-9')
  echo "ROW|$mid|$sname|$code|${sz:-0}"
done
'''


def is_complete_size(path, expect):
    """本机这份是否完整：safetensors 自描述校验，或大小对得上。"""
    try:
        import struct
        with open(path, "rb") as f:
            n = int.from_bytes(f.read(8), "little")
            if 0 < n < 100_000_000:
                hdr = json.loads(f.read(n))
                offs = [v["data_offsets"][1] for k, v in hdr.items() if k != "__metadata__"]
                if offs and 8 + n + max(offs) == os.path.getsize(path):
                    return True
    except Exception:
        pass
    return os.path.getsize(path) >= expect


def find_local(name):
    for dp, _dn, fn in os.walk(LOCAL_ROOT):
        if name in fn:
            return os.path.join(dp, name)
    return None


def main():
    rows = []
    meta = {}
    for m in mf["models"]:
        name = m.get("target_name") or os.path.basename(m["path"])
        expect = int(m.get("source_bytes") or m["min_bytes"])
        meta[m["id"]] = dict(group=m["group"], name=name, tdir=m["target_dir"], expect=expect)
        mrepo = m.get("ms_repo") or m["repo"]
        rows.append((m["id"], "ms", f"{MS}/{mrepo}/resolve/master/{m['path']}"))
        rows.append((m["id"], "hf", f"{HF}/{m['repo']}/resolve/main/{m['path']}"))

    body = ["#!/bin/bash", "ROWS=("]
    body += ['  "%s~%s~%s"' % r for r in rows]
    body += [")", PROBE, 'echo "PROBE_DONE"']
    script = "\n".join(body)

    ssh = get_ssh("default")
    try:
        ssh.put_text(script, "/tmp/otai_avail.sh")
        ssh.run("chmod +x /tmp/otai_avail.sh")
        raw = ssh.run("bash /tmp/otai_avail.sh", timeout=600).out
    finally:
        ssh.close()

    got = {}
    for line in raw.splitlines():
        if not line.startswith("ROW|"):
            continue
        _, mid, sname, code, sz = line.split("|")
        if code in ("200", "206") and int(sz) > 0:
            got.setdefault(mid, {})[sname] = int(sz)

    gb = lambda n: "%.2f GB" % (n / 1073741824) if n >= 1073741824 else "%.0f MB" % (n / 1048576)

    print("=" * 92)
    print("清单 A｜服务器能下，且和本机那一份字节完全一致  -> 直接在服务器下载")
    print("=" * 92)
    print("=" * 92)
    print("清单 B｜服务器能下，但和本机不是同一份（体积不同）  -> 需要你定夺用哪份")
    print("=" * 92)
    print("=" * 92)
    print("清单 C｜服务器上根本没有  -> 只能从本机传")
    print("=" * 92)

    bucket = {"A": [], "B": [], "C": []}
    for m in mf["models"]:
        mid = m["id"]
        info = meta[mid]
        lp = find_local(info["name"])
        local_ok = bool(lp) and is_complete_size(lp, info["expect"])
        srcs = got.get(mid, {})
        if not srcs:
            key = "C" if local_ok else "C(missing-local)"
        else:
            same = any(abs(v - info["expect"]) <= max(4096, info["expect"] * 0.001) for v in srcs.values())
            key = "A" if same else "B"
        bucket.setdefault(key, []).append((mid, info, srcs, local_ok))

    for key in ("A", "B", "C", "C(missing-local)"):
        if not bucket.get(key):
            continue
        title = {"A": "A 服务器可下且与本机一致", "B": "B 服务器可下但版本不同",
                 "C": "C 只能本机传", "C(missing-local)": "C' 服务器没有、本机也没有"}[key]
        print(f"\n【{title}】")
        print("  %-20s %-9s %12s %-16s %-10s %s" % ("id", "分组", "本机/期望", "服务器上大小", "目标目录", "备注"))
        for mid, info, srcs, local_ok in bucket[key]:
            ms = srcs.get("ms")
            hf = srcs.get("hf")
            avail = ("ms=%s" % gb(ms)) if ms else ""
            if hf:
                avail += (" hf=%s" % gb(hf)) if avail else ("hf=%s" % gb(hf))
            print("  %-20s %-9s %12s %-16s %-10s" % (mid, info["group"], gb(info["expect"]),
                                                     avail or "-", info["tdir"]))

    print("\n说明：本机模型根目录 %s，本机有货的条目按同一个文件名匹配。" % LOCAL_ROOT)


if __name__ == "__main__":
    main()
