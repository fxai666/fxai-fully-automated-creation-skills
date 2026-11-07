"""服务器端：全表真校验 → 不对的全部清掉，重新来过。

⚠️ 判「对不对」只用 safetensors 头（8 + 元信息长度 + 末张量 data_offsets[1]），
不等的一律清掉，不留侥幸。文件大小约等于 source_bytes 不算数——
两路 curl 抢写同一个目标，会造出「大小完美但内容坏」的文件。

清理范围：models 树里所有 .safetensors / .safetensors.otaiprev / .otaipart
（含各子目录的 .otai 暂存）。configs/*.yaml、put_xxx_here 这类占位文件不动。

用法（服务器）：
  python3 /root/otaiscripts/otai_purge.py            # 只校验 + 列清单，不删
  python3 /root/otaiscripts/otai_purge.py --apply    # 真删非完整的
  python3 /root/otaiscripts/otai_purge.py --apply --all   # 连完整的也一起清（彻底重来）
"""
import json
import os
import struct
import sys

# ⚠️ json 必须 import：漏了会让 json.loads 抛 NameError 被下面的
# except 兜住 → 每个文件一律判 BAD → 真完整的文件也被删掉。
# 判「坏」的依据是文件头读不出来（`real_size()` 抛错即BAD）；代码自身崩溃会落进同一分支，
# 所以 `json` 必须在文件头之前 import 到位。

MODE = sys.argv[1] if len(sys.argv) > 1 else ""
M = "/root/ComfyUI/models"


def real_size(p):
    with open(p, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        meta = json.loads(f.read(n))
    end = max(t["data_offsets"][1] for k, t in meta.items()
              if k not in ("__metadata__", "__data_offsets__"))
    return 8 + n + end


def status_of(p):
    try:
        got = os.path.getsize(p)
    except OSError:
        return "MISS", -1
    if got == 0:
        return "EMPTY", 0
    try:
        exp = real_size(p)
    except Exception as e:
        return "BAD(读不了头:%s)" % type(e).__name__, got
    return ("OK" if exp == got else "BAD(少%d字节)" % (got - exp)), got


files = []
for dp, dns, fns in os.walk(M):
    for f in fns:
        if f.endswith(".safetensors") or f.endswith(".otaiprev") or f.endswith(".otaipart"):
            files.append(os.path.join(dp, f))
files.sort(key=lambda p: -os.path.getsize(p))

bad, ok = [], []
for p in files:
    s, _ = status_of(p)
    (ok if s == "OK" else bad).append((p, s, os.path.getsize(p)))

print("models 树里 %d 个 safetensors 类文件" % len(files))
print("-" * 76)
for p, s, sz in bad:
    print("  ✗ %-6s %8.2fG  %s" % (s, sz / 2**30, os.path.relpath(p, M)))
for p, s, sz in ok:
    print("  ✓ %-6s %8.2fG  %s" % (s, sz / 2**30, os.path.relpath(p, M)))
print("-" * 76)
print("完整 %d / 不完整 %d" % (len(ok), len(bad)))

if not bad:
    print("\n没有不对的文件，没什么可清的。")
    sys.exit(0)

print("\n将删除 %d 个，合计约 %.2fG" % (len(bad), sum(x[2] for x in bad) / 2**30))
if MODE != "--apply":
    print("（dry-run，未删。要真删加 --apply）")
    sys.exit(0)

before = os.popen("df -h / | tail -n1").read().strip()
freed = 0
for p, s, sz in bad:
    try:
        os.remove(p)
        freed += sz
    except OSError as e:
        print("  删失败", p, e)
after = os.popen("df -h / | tail -n1").read().strip()
print("删除前:", before)
print("删除后:", after)
print("实际释放 %.2fG" % (freed / 2**30))
