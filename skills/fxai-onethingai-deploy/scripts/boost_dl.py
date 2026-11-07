"""按清单顺序补位起下载：开一个 → 验一下 → 正常再开下一个，最多 N 路。

跟 otai_launch.py launch 的区别（别搞混）：
  launch = 整个 gen_script 流水线重跑一遍（重启外壳、重排全部模型）
  boost  = 只往「已经在跑的那几路」旁边补新的 curl，**正在跑的一个都不碰**

为什么必须「起完验一下再起下一个」：2026-10-03 一次拉 12 路，几秒内魔搭下发
acw_tc 反爬 cookie，11 个小文件全部 0 字节。起一个、看它字节真在涨、再起下一个，
慢一步但能活下来。

用法：
  python boost_dl.py                # 补到 4 路（默认）
  python boost_dl.py 3              # 补到 3 路
  python boost_dl.py 4 --probe 40   # 每路起完等 40 秒验快慢
"""
from __future__ import annotations

import argparse
import io
import json
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otai_cfg import ssh_cred  # noqa: E402
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otai_ssh import SSH  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(os.path.dirname(HERE), "assets", "manifest.json")
ROOT = "/root/ComfyUI/models"


def gb(n: int) -> str:
    return f"{max(n, 0) / 2**30:.2f}G"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", type=int, default=4)
    ap.add_argument("--probe", type=int, default=25, help="起完等几秒验快慢")
    args = ap.parse_args()

    d = otai_cfg.ssh_cred()
    sh = SSH(d["host"], d["port"], d["user"], password=d["password"])
    sh.connect()

    m = json.load(io.open(MANIFEST, encoding="utf-8"))
    # 清单顺序 = manifest 里的书写顺序；跳掉已下完的（status=done）和已在跑的
    items = [(k, v) for k, v in m.items() if k != "_说明" and v.get("url") and v.get("size")]

    def running():
        """⚠️ 别用 `ps | grep 'curl -L'`：执行这条命令的 bash 自己命令行里也含
        "curl -L"，会把自个儿算成一路，报出「已经在 6 路」这种假象。
        扫 /proc/<pid>/cmdline 才准。"""
        r = sh.run("python3 /tmp/otai_proc.py list", timeout=120)
        return [l.split("=", 1)[1].strip() for l in r.out.splitlines() if l.strip().startswith("pid=")]

    # 上限取「N」和「待下数量」的较小者：只剩 3 个待下就起 3 路，别凑满 4 路去空转。
    todo = [k for k, v in items if v.get("status") != "done"]
    real_target = min(args.target, max(len(todo), 0))
    print(f"待下 {len(todo)} 个 / 目标 {args.target} 路 → 实际最多 {real_target} 路")

    launched = 0
    for name, v in items:
        cur = running()
        if len(cur) >= real_target:
            print(f"已经在 {len(cur)} 路，达到上限 {real_target}，停")
            break

        if v.get("status") == "done" or any(name in c for c in cur):
            continue  # 下完的跳过；已在跑的算在路数里

        save = (v.get("save") or "").strip("/")
        rel = (save + "/" + name) if save else name
        out = ROOT + "/" + rel
        q = "'"
        # ⚠️ 续传必须落在「没下完」的文件上。目标文件若已是完整 safetensors，
        # `-C -` 会把旧内容和新内容叠在一起，尾部长出一截变成超标文件（2026-10-03 踩过）。
        launcher = (
            f"setsid bash -c {q}sleep {5 + len(cur) * 7}; exec curl -L -C - "
            f"--retry 6 --retry-delay 3 --retry-all-errors --connect-timeout 30 "
            f"--speed-time 60 --speed-limit 1024 -o {q}{out}{q} {q}{v['url']}{q}{q} "
            f"</dev/null >/tmp/otai_dl_{name[:24]}.log 2>&1 & "
        )
        sh.run(launcher + "disown 2>/dev/null || true", timeout=60)
        launched += 1
        print(f"  已起 [{len(cur) + 1}/{real_target}] {rel}  ({gb(int(v['size']))})")

        time.sleep(args.probe)
        g = sh.run(f"stat -c %s {q}{out}{q} 2>/dev/null || echo 0", timeout=60).out.strip()
        print(f"     等 {args.probe}s 后 = {gb(int(g))}")
        if int(g) <= 0:
            print("     ⚠️ 没字节 —— 多半又被反爬挡了，跳过去开下一个")

    time.sleep(15)
    cur = running()
    print(f"\n当前服务器 curl 路数：{len(cur)}")
    for c in cur:
        print("   ", c.replace(ROOT + "/", "").replace(
            "--retry 6 --retry-delay 3 --retry-all-errors --connect-timeout 30 "
            "--speed-time 60 --speed-limit 1024", ""))
    sh.close()


if __name__ == "__main__":
    sys.exit(main())
