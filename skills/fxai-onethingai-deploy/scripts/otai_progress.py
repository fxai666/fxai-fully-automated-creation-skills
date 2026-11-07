#!/usr/bin/env python
"""照着 assets/manifest.json 出「下载进度表」——一次远端往返查全表。

**这份工具的清单来自配置文件，不是 models.json**。你往 manifest.json 加一行
模型（url / size / save / status），`python otai_progress.py` 就会把它一起查出来。
这是配置文件作为唯一真相源的那一步。

为什么必须把 `.otai` 一并查：
gen_script 的落盘逻辑是「先下到 <save>/.otai/<名>，下完才 mv 到 save/<名>」，
下载中的正式路径查起来就是 MISS；而 `.otaiprev` 又是下载前的硬链接快照。
只看一个名字必然把「正在下」误判成「没开始」。

用法：
  python otai_progress.py                # markdown 表（可直接贴聊天框）
  python otai_progress.py --text         # 纯文本表
  python otai_progress.py --json         # 机器可读
  python otai_progress.py --sync         # 出表后把 status/got 写回 manifest.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otai_cfg import ssh_cred  # noqa: E402
import sys

# ComfyUI 的 models / input 目录由全局配置的 comfy_root 决定，不写死本机盘符
MODELS_DIR = r""
INPUT_DIR = r""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otai_ssh import SSH  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
MANIFEST_JSON = os.path.join(ASSETS, "manifest.json")

# 远端一次性体检：正式路径 + .otai 暂存 + 是否有 curl 在写。
# 判「有没有在下载」不看日志（日志是 UTC，还常乱码），直接看 ps 里 -o 的目标。
# ⚠️ .otai 里存的是**下载时的源文件名**（URL 末尾那截），不是落盘名 ——
# gen_script 是先下原名、下完才 mv 成 target_name。只按落盘名找必然一个都匹配不上。
REMOTE = r'''
python3 - <<'PY'
import json, os, glob
M = "/root/ComfyUI/models"
want = json.load(open("/tmp/_otai_want.json"))
live = []
for line in os.popen("ps -eo args").read().splitlines():
    t = line.split()
    for i, x in enumerate(t):
        if x == "-o" and i + 1 < len(t):
            live.append(t[i + 1])
def sz(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return -1
for rel, cfg in want.items():
    want_sz, bases = cfg[0], cfg[1]
    full = os.path.join(M, rel)
    fsz = sz(full)
    xs = []
    liveit = []
    for b in bases:
        for d in glob.glob(os.path.join(M, "*", ".otai", b)):
            xs.append(sz(d))
            if os.path.join(M, d) in live:
                liveit.append(d)
    is_live = full in live or bool(liveit)
    cur = max([n for n in [fsz] + xs if n >= 0] or [-1])
    print("%s\t%d\t%d\t%d" % (rel, want_sz, cur, 1 if is_live else 0))
PY
'''


def gi(n: int) -> str:
    return f"{n / 2**30:.2f}G"


def pct(cur: int, tot: int) -> str:
    return "-" if cur < 0 else f"{cur * 100.0 / tot:5.1f}%"


def load_manifest():
    """清单以 manifest.json 为准；兼容只有文件名没有 save 目录的写法。"""
    m = json.load(io.open(MANIFEST_JSON, encoding="utf-8"))
    items = [(k, v) for k, v in m.items() if k != "_说明" and isinstance(v, dict)]
    return m, items


def probe_local(items):
    """本机模式：直接扫本机模型目录（<你的ComfyUI目录>/models），不连服务器。

    下载任务跑在云端容器里，落盘的却是本机这张盘，两个视角分开走：
    --local 看「文件到哪了」，不带参数看「服务器上还在不在下」。
    """
    M = MODELS_DIR
    rows = []
    for name, v in items:
        save = (v.get("save") or "").strip("/")
        rel = (save + "/" + name) if save else name
        full = os.path.join(M, *rel.split("/"))
        tot = int(v.get("size") or 0)
        cur = -1
        if os.path.exists(full):
            cur = os.path.getsize(full)
        else:
            # 落盘名对不上时（站点原名就当存名用）兜底找一下
            for root, _dn, fns in os.walk(M):
                if name in fns:
                    cur = os.path.getsize(os.path.join(root, name))
                    break
        rows.append({"name": name, "save": v.get("save", ""), "total": tot,
                     "cur": cur, "live": False, "status": "完整" if cur >= tot else ("半截" if cur > 0 else "未开始")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sync", action="store_true", help="把 status/got 写回 manifest.json")
    ap.add_argument("--local", action="store_true", help="扫本机模型目录 <你的ComfyUI目录>\\models（不连服务器）")
    args = ap.parse_args()

    m, items = load_manifest()
    if args.local:
        rows = probe_local(items)
        finish(rows, m, args)
        return 0

    want = {}
    for name, v in items:
        save = (v.get("save") or "").strip("/")
        rel = (save + "/" + name) if save else name
        # 候选名：落盘名 + 下载时的源文件名（URL 末尾），两个都要，.otai 里存的是后者
        src = v.get("url", "").rstrip("/").rsplit("/", 1)[-1] or name
        want[rel] = [int(v.get("size") or 0), sorted({name, src})]

    d = otai_cfg.ssh_cred()
    sh = SSH(d["host"], d["port"], d["user"], password=d["password"])
    try:
        sh.put_text(json.dumps(want, ensure_ascii=False), "/tmp/_otai_want.json")
        sh.run("chmod 644 /tmp/_otai_want.json")
        raw = sh.run("bash -lc " + "'" + REMOTE.replace("'", "'\"'\"'") + "'", timeout=180).text()
    finally:
        sh.close()

    stat = {}
    for line in raw.splitlines():
        p = line.split("\t")
        if len(p) == 4 and p[1].lstrip("-").isdigit():
            stat[p[0]] = (int(p[1]), int(p[2]), int(p[3]))

    rows = []
    for name, v in items:
        rel = ((v.get("save") or "").strip("/") + "/" + name) if (v.get("save") or "").strip("/") else name
        tot = int(v.get("size") or 0)
        w, cur, live = stat.get(rel, (tot, -1, 0))
        st = "下载中" if live else ("完整" if cur >= tot else ("半截" if cur > 0 else "未开始"))
        rows.append({"name": name, "save": v.get("save", ""), "total": tot,
                     "cur": cur, "live": bool(live), "status": st})

    finish(rows, m, args)
    return 0


def finish(rows, m, args):
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    if args.text:
        print(f"{'文件名':<50}{'进度':>8}{'已下':>11}{'目标':>11}  状态")
        print("-" * 86)
        for r in rows:
            print(f"{r['name']:<50}{pct(r['cur'], r['total']):>8}"
                  f"{gi(max(r['cur'], 0)):>11}{gi(r['total']):>11}  {r['status']}")
    else:
        print("| # | 模型文件 | 进度 | 已下 | 目标 | 状态 |")
        print("|---|---|---|---|---|---|")
        for i, r in enumerate(rows, 1):
            tag = {"完整": "✅ 完整", "下载中": "🔵 下载中", "半截": "🟡 半截"}.get(r["status"], "⚪ 未开始")
            print(f"| {i} | {r['name']} | {pct(r['cur'], r['total'])} | "
                  f"{gi(max(r['cur'], 0))} | {gi(r['total'])} | {tag} |")
    done = sum(1 for r in rows if r["status"] == "完整")
    print("")
    print(f"合计 {done}/{len(rows)} 个已完整")

    if args.sync:
        n = 0
        for r in rows:
            v = m.get(r["name"])
            if not v:
                continue
            want_s = {"完整": "done", "下载中": "downloading",
                      "半截": "partial", "未开始": ""}[r["status"]]
            if v.get("status") != want_s or int(v.get("got") or 0) != max(r["cur"], 0):
                v["status"] = want_s
                v["got"] = max(r["cur"], 0)
                n += 1
        json.dump(m, io.open(MANIFEST_JSON, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"（已把 {n} 条 status/got 写回 assets/manifest.json）")


if __name__ == "__main__":
    sys.exit(main())
