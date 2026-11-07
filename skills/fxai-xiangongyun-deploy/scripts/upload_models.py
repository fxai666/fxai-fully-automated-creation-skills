#!/usr/bin/env python
"""把本机已经有的模型直接传到仙宫云实例（远端下载搞不定时走这条路）。

什么时候用：
  - 某个模型在魔搭 / HF 上都探测不到（比如社区的冷门仓库、上架又下架的权重）
  - 远端下载反复失败、或者本机那个 curl 的网比实例还快
  - 本机 ComfyUI 的 models 目录里本来就有同一份权重，没必要再从网上重下一次

用法：
  python upload_models.py --missing              # 只传远端缺的/不到标的
  python upload_models.py --only h3_latent_upscaler,yue2_sheetsage
  python upload_models.py --group mv --parallel 3
  python upload_models.py --all --parallel 3     # 全部（远端已有的自动跳过）
  python upload_models.py --local-root MODELS_DIR

断点续传：远端已经有一部分的话，从那个偏移接着写，不重头传。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# ComfyUI 的 models / input 目录由全局配置的 comfy_root 决定，不写死本机盘符
MODELS_DIR = r""
INPUT_DIR = r""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote import get_ssh  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "assets")

_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


def load_models():
    with open(os.path.join(ASSETS, "models.json"), encoding="utf-8") as f:
        return json.load(f)


def find_local(root, name):
    """在本机模型根目录下按文件名找，返回绝对路径。"""
    if not root or not os.path.isdir(root):
        return None
    for dp, _dn, fn in os.walk(root):
        if name in fn:
            return os.path.join(dp, name)
    return None


def local_is_complete(path: str) -> bool:
    """本机这份权重本身是不是完整的？

    不看 models.json 里的 min_bytes —— 那个数字是当年写清单时拍的，
    和现在手里的文件不一定严丝合缝 —— 判坏一律读文件头自描述。
    safetensors 是自描述的：头 8 字节给 JSON 头长度，头里每个张量都写了
    data_offsets，最后一个张量的结束位置 + 8 + 头长 == 文件应有的总字节数。
    对得上就是完整的一份，可以直接传。
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(8)
            n = int.from_bytes(raw, "little")
            if n <= 0 or n > 100_000_000:
                return False
            hdr = json.loads(f.read(n))
        offs = [v["data_offsets"][1] for k, v in hdr.items() if k != "__metadata__"]
        return bool(offs) and 8 + n + max(offs) == os.path.getsize(path)
    except Exception:
        return False


def plan(models, root, group, only, do_all):
    """算出「要传哪些」：本地有、远端没有或不够大。"""
    out = []
    for m in models:
        if only and m["id"] not in only:
            continue
        if not only and group != ["all"] and m["group"] not in group:
            continue
        name = m.get("target_name") or os.path.basename(m["path"])
        src = find_local(root, name)
        # variant 会改 path/tname
        if m.get("variants"):
            v = (m["variants"] or {}).get("full") or {}
            if v.get("path"):
                alt = os.path.basename(v["path"])
                if alt != name:
                    src = src or find_local(root, alt)
        dst = f"/root/ComfyUI/models/{m['target_dir']}/{name}"
        out.append({
            "id": m["id"], "name": name, "src": src, "dst": dst,
            "expect": int(m.get("source_bytes") or m["min_bytes"]),
            "min": int(m["min_bytes"]),
        })
    return out


def remote_sizes(sh, items):
    """一次 SSH 问清所有目标文件的现有大小（省得多趟跑）。"""
    if not items:
        return {}
    cmd = "for f in " + " ".join(f'"{i["dst"]}"' for i in items) + \
          "; do if [ -f \"$f\" ]; then stat -c%s \"$f\"; else echo -1; fi; done"
    r = sh.run(cmd, timeout=120)
    sizes = []
    for line in r.out.splitlines():
        line = line.strip()
        sizes.append(int(line) if line.lstrip("-").isdigit() else -1)
    return {it["dst"]: sizes[k] for k, it in enumerate(items) if k < len(sizes)}


def upload_one(item, offset, expect, profile, idx, total):
    """单文件上传，带进度输出。用独立 SSH 连接，方便并行。"""
    sh = get_ssh(profile)
    sh.connect()          # get_ssh() 只造壳不连，SFTP 前必须自己 connect
    try:
        if sh._client is None:
            raise RuntimeError("SSH 走的不是 paramiko 后端，开不了 SFTP。"
                               "检查本机是否装了 paramiko（有 paramiko 时才用 SFTP 传文件）")
        sh.run(f"mkdir -p $(dirname {item['dst']})")
        sftp = sh._client.open_sftp()
        t0 = time.time()
        done = 0
        last = 0
        try:
            f = sftp.open(item["dst"], "wb+" if not offset else "r+b")
            try:
                if offset:
                    f.seek(offset)
                while True:
                    with open(item["src"], "rb") as g:
                        g.seek(offset + done)
                        chunk = g.read(8 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last >= 10:
                        last = now
                        spd = done / (now - t0) / 1048576
                        pct = 100.0 * (offset + done) / expect if expect else 0
                        log(f"  [{idx}/{total}] {item['name'][:40]:<42}"
                            f" {pct:5.1f}%  {spd:6.1f} MB/s")
            finally:
                f.close()
        finally:
            sftp.close()
        spd = done / max(time.time() - t0, 1) / 1048576
        log(f"  [{idx}/{total}] {item['name'][:40]:<42} 传完 {human(done)} "
            f"({spd:.1f} MB/s)")
        return True
    except Exception as e:
        log(f"  [{idx}/{total}] {item['name'][:40]:<42} 失败：{e}")
        return False
    finally:
        sh.close()


def main():
    ap = argparse.ArgumentParser(description="从本机直接传模型到仙宫云实例")
    ap.add_argument("--local-root", default=MODELS_DIR)
    ap.add_argument("--group", default="all")
    ap.add_argument("--only", default="")
    ap.add_argument("--all", dest="do_all", action="store_true")
    ap.add_argument("--missing", action="store_true", help="只传远端缺失/不足的")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--profile", default="default")
    a = ap.parse_args()

    only = {o.strip() for o in a.only.split(",") if o.strip()}
    mf = load_models()
    para = max(1, min(a.parallel, 8))

    items = plan(mf["models"], a.local_root,
                 [g.strip() for g in a.group.split(",") if g.strip()], only, a.do_all)

    sh = get_ssh(a.profile)
    try:
        sizes = remote_sizes(sh, items)
    finally:
        sh.close()

    sh = get_ssh(a.profile)
    try:
        print(sh.run("df -h / | tail -1", timeout=60).out.strip())
    finally:
        sh.close()

    todo = []
    for i, it in enumerate(items):
        if not it["src"]:
            log(f"  跳过 {it['id']}：本机没找到 {it['name']}")
            continue
        have = sizes.get(it["dst"], -1)
        local_size = os.path.getsize(it["src"])
        # safetensors 能自证完整（头 + 张量偏移量），优先信它；
        # 不是 safetensors（gguf / bin）就退回按体积下限判断。
        if not local_is_complete(it["src"]) and local_size < it["min"]:
            log(f"  跳过 {it['id']}：本机这份不完整（{human(local_size)} < {human(it['min'])}）")
            continue
        if have >= local_size and have >= it["min"]:
            log(f"  跳过 {it['name'][:44]:<46} 远端已有 {human(have)}")
            continue
        it["offset"] = max(have, 0)
        it["src_size"] = local_size
        todo.append(it)

    if not todo:
        print("\n没有需要上传的文件。")
        return
    total_bytes = sum(i["src_size"] - i["offset"] for i in todo)
    print(f"\n要传 {len(todo)} 个文件，共 {human(total_bytes)}（并行 {para}）")
    for it in todo:
        print(f"  {it['name'][:44]:<46} {human(it['src_size']):>10}"
              f"{'  从 ' + human(it['offset']) + ' 续' if it['offset'] else ''}")
    print()

    t0 = time.time()
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=para) as ex:
        futs = {ex.submit(upload_one, it, it["offset"], it["src_size"],
                          a.profile, k + 1, len(todo)): it
                for k, it in enumerate(todo)}
        for fu in as_completed(futs):
            if fu.result():
                ok += 1
            else:
                fail += 1
    n = len(todo)
    print(f"\n结果：成功 {ok} / {n}，失败 {fail}，耗时 {time.time() - t0:.0f} 秒")

    sh = get_ssh(a.profile)
    try:
        print(sh.run("df -h / | tail -1; echo; du -sh /root/ComfyUI/models/* | sort -h | tail -8",
                     timeout=120).out)
    finally:
        sh.close()


if __name__ == "__main__":
    main()
