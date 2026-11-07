#!/usr/bin/env python
"""清理服务器上「下坏的半截文件」——**只删严格小于真实大小**的，一个完整的都不碰。

完整性是硬门槛：只按明确判据删。「非空 + 是孤儿」这类近似条件会误删已下完整的文件。

判据（全部满足才删）：
  1. 文件大小 < models.json 里的 source_bytes（真实大小）—— **完整性是硬门槛**
  2. 不是正在被 curl 写的目标
  3. 0 字节的空文件（下了个寂寞，一定删）

用法：
  python clean_partials.py               # dry-run：只列不删
  python clean_partials.py --apply       # 真删
  python clean_partials.py --apply --force-on-empty
"""
from __future__ import annotations

import argparse
import json
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otai_cfg import ssh_cred  # noqa: E402
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otai_ssh import SSH  # noqa: E402
import io  # noqa: E402


# 远端：把 models.json 晒成「相对路径 -> 真实字节」，再逐文件体检。
# 整段塞进一次 exec_command，本地侧零引号拼接。
REMOTE = r'''
python3 - <<'PY'
import json, os, sys
M = "/root/ComfyUI/models"
ROW = "/tmp/_otai_want.json"
text = open(ROW).read()
want = json.loads(text)
rows = []
def walk(base):
    for dp, dns, fns in os.walk(base):
        for fn in fns:
            if fn.endswith(".safetensors") or fn.endswith(".otaiprev") or fn.endswith(".otaipart"):
                yield os.path.join(dp, fn)
procs = os.popen("ps -eo args").read().splitlines()
live = []
for l in procs:
    m = [a[2:] for a in l.split() if a.startswith("-o ")]
    live += m
for p in sorted(walk(M)):
    try:
        st = os.stat(p)
    except OSError:
        continue
    rel = os.path.relpath(p, M)
    stem = rel[:-len(".otaiprev")] if rel.endswith(".otaiprev") else (
        rel[:-len(".otaipart")] if rel.endswith(".otaipart") else rel)
    # 正在被 curl 写的一律跳过
    if any(rel.endswith(l.lstrip("/")) or p == l for l in live):
        status = "LIVE"
    else:
        w = want.get(stem) or want.get(rel)
        if w:
            if st.st_size >= w:
                status = "OK"        # 完整（可能是正式名，也可能是半截的孪生快照）
            else:
                status = "PARTIAL"
        else:
            status = "UNKNOWN"
        if st.st_size == 0:
            status = "EMPTY"
    print("%d\t%d\t%s\t%s\t%d" % (status, st.st_size, st.st_ino, rel, (want.get(stem) or 0)))
PY
'''

WANT_JSON = r'''{
"diffusion_models/qwen_image_2.1_bf16.safetensors":14230280616,
"text_encoders/qwen3vl_8b_int8_convrot.safetensors":9350798360,
"vae/qwen_image_2.1_vae_bf16.safetensors":675509688,
"checkpoints/yue2_3b_bf16.safetensors":7799983228,
"audio_encoders/sheetsage2_bf16.safetensors":1386868122,
"diffusion_models/MelBandRoformer_fp32.safetensors":912885656,
"diffusion_models/minimax_h3_singularity_ref2va_int8.safetensors":34004507622,
"text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors":15687142551,
"vae/minimax_h3_video_vae_fp16.safetensors":5207808496,
"vae/minimax_h3_audio_vae_fp32.safetensors":605254808,
"loras/minimax_h3_ref2v_turbo_8step_768p_bf16.safetensors":1956193000,
"latent_upscale_models/minimax_h3_bf16_conservative.safetensors":690593160
}'''


def gi(n: int) -> str:
    return f"{n/2**30:.2f}G"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真删（默认只列不删）")
    ap.add_argument("--force-on-empty", action="store_true", help="连 0 字节空文件也删")
    ap.add_argument("--host", default="")
    args = ap.parse_args()

    d = otai_cfg.ssh_cred()
    sh = SSH(d["host"], d["port"], d["user"], password=d["password"])
    sh.connect()
    try:
        sh.put_text(WANT_JSON, "/tmp/_otai_want.json")
        sh.run("chmod 644 /tmp/_otai_want.json")
        out = sh.run("bash -lc " + "'" + REMOTE.replace("'", "'\"'\"'") + "'", timeout=180).text()
    finally:
        sh.close()

    parse = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        parse[(parts[3], int(parts[4]))] = (parts[2], int(parts[1]), parts[0])

    # 同一 inode 只代表「同一份数据」，删掉不腾空间，标出来
    ino_map = {}
    for key, (ino, size, st) in parse.items():
        ino_map.setdefault(ino, []).append((key, size, st))

    to_del = []
    print("=" * 74)
    print("半截文件体检（只列「严格小于真实大小」的；完整的一个都不动）")
    print("=" * 74)
    groups = {}
    for ino, members in ino_map.items():
        if len(members) > 1:
            continue  # 多名字 = 同一份数据，删了不腾空间
        (key, size, st), = members
        if st == "PARTIAL":
            groups.setdefault("半截（可删，能腾空间）", []).append((key, size))
        elif st == "EMPTY":
            groups.setdefault("0 字节空文件", []).append((key, size))
        elif st == "LIVE":
            groups.setdefault("正在下载（保留）", []).append((key, size))
        else:
            groups.setdefault("完整 ✅", []).append((key, size))

    for title in ("半截（可删，能腾空间）", "0 字节空文件", "正在下载（保留）", "完整 ✅"):
        if title not in groups:
            continue
        print(f"\n[{title}]")
        tot = 0
        for key, size in sorted(groups[title], key=lambda x: -x[1]):
            print(f"   {gi(size):>8}  {key}")
            tot += size
        print(f"   小计 {gi(tot)}")
        if title in ("半截（可删，能腾空间）",) or (title == "0 字节空文件" and args.force_on_empty):
            if title == "半截（可删，能腾空间）":
                to_del += [k for k, _ in groups[title]]

    print()
    if not to_del:
        print("没有需要清理的半截文件。")
        return 0
    print(f"将删除 {len(to_del)} 个，合计约 {gi(sum(parse[k][1] for k in to_del))}")
    if not args.apply:
        print("\n（dry-run，未删。要真删加 --apply）")
        return 0

    d2 = otai_cfg.ssh_cred()
    sh = SSH(d2["host"], d2["port"], d2["user"], password=d2["password"])
    sh.connect()
    try:
        before = sh.run("df -h / | tail -n1").out.strip()
        for rel in to_del:
            sh.run(f"rm -f /root/ComfyUI/models/{shlex_quote(rel)}")
        after = sh.run("df -h / | tail -n1").out.strip()
    finally:
        sh.close()
    print("删除前:", before)
    print("删除后:", after)
    return 0


def shlex_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    sys.exit(main())
