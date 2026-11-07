"""服务器端下载进程管理：列表 / 全停。在服务器执行，绕开 grep 转义坑。

为什么不用 `ps | grep curl`：多层引号嵌套下 grep 模式经常转义坏，报「已停 0 个」
但实际一个都没杀 —— 2026-10-03 就栽在这，害得重复文件被两路 curl 抢着写。
扫 /proc/<pid>/cmdline 最笨也最准。

用法（服务器）：
  python3 /tmp/otai_proc.py list
  python3 /tmp/otai_proc.py kill
"""
import json
import os
import signal
import sys


def scan(kw):
    out = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            raw = open("/proc/%s/cmdline" % p, "rb").read().decode("utf8", "ignore")
        except OSError:
            continue
        # ⚠️ /proc/<pid>/cmdline 用 \x00 分段，decode 后还是 \x00。
        # 不 replace 成空格的话，"curl -L" 这种带空格的关键词永远匹配不上，
        # 于是「明明有 4 路在下载却一直报 0 路」（2026-10-03 栽在这）。
        a = raw.replace("\x00", " ")
        if kw in a:
            out.append((int(p), a))
    return out


def targets_of(cmdline):
    """从 curl 的 cmdline 里抠出 -o 后面的落盘路径。"""
    t = cmdline.split("\x00")
    for i, x in enumerate(t[:-1]):
        if x == "-o":
            return t[i + 1]
    return ""


mode = sys.argv[1] if len(sys.argv) > 1 else "list"

if mode == "check":
    """验证单个文件是不是真下完：safetensors 自证（8 + 元信息长度 + 末张量偏移），
    光比大小不够 —— 2026-10-03 就是只比大小把 4 份完整文件当半截删过。
    用法: python3 /tmp/otai_proc.py check <相对 models 的路径> <应有字节>
    """
    import struct

    rel, exp = sys.argv[2], int(sys.argv[3])
    p = os.path.join("/root/ComfyUI/models", rel)
    if not os.path.exists(p):
        print("MISS")
        sys.exit(0)
    got = os.path.getsize(p)
    if got != exp:
        print("PARTIAL %d" % got)
        sys.exit(0)
    try:
        with open(p, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            meta = json.loads(f.read(n))
        end = max(t["data_offsets"][1] for k, t in meta.items()
                  if k not in ("__metadata__", "__data_offsets__"))
    except Exception as e:
        print("BAD %s" % e)
        sys.exit(0)
    print("DONE" if 8 + n + end == got else "BAD")
    sys.exit(0)

curls = scan("curl -L")
shells = scan("otai_deploy")

if mode == "list":
    print("CURL %d 路:" % len(curls))
    seen = {}
    for pid, a in curls:
        tgt = targets_of(a) or a.strip()
        seen[tgt] = seen.get(tgt, 0) + 1
        flag = "  ⚠重复" if seen[tgt] > 1 else ""
        print("  pid=%d  %s%s" % (pid, tgt, flag))
    print("外壳 %d 个:" % len(shells))
    for pid, _a in shells:
        print("  pid=%d" % pid)
    dup = sum(1 for v in seen.values() if v > 1)
    if dup:
        print("⚠ 有 %d 个文件被两路以上同时写，会互相覆盖出坏文件" % dup)
    sys.exit(1 if dup else 0)

# kill：先杀外壳（否则它马上又补位），再杀 curl
killed = 0
for pid, _a in shells + curls:
    try:
        os.kill(pid, signal.SIGKILL)
        killed += 1
    except Exception:
        pass
print("已杀 %d 个进程" % killed)
import time

time.sleep(3)
left_c = len(scan("curl -L"))
left_s = len(scan("otai_deploy"))
print("复查：curl 剩 %d，外壳 剩 %d" % (left_c, left_s))
sys.exit(0 if left_c == 0 and left_s == 0 else 1)
