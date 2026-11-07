"""服务器端逐条验证 12 个模型是否真下完（safetensors 头校验，离线程级）。

输出列：
  路径   models 树里的相对路径（找不到就空）
  实际   实际字节（-1 = 没找到）
  应为   8 + 元信息长度 + 末张量 data_offsets[1]
  结论   OK=真完整 / PARTIAL=半截 / MISS=没有
"""
import json
import os
import struct

M = "/root/ComfyUI/models"
want = json.load(open("/tmp/_otai_verify.json"))

# 1) 建索引：models 树里所有 safetensors / .otaiprev / .otaipart，按「去后缀的基名」归档
idx = {}
for dp, dns, fns in os.walk(M):
    for f in fns:
        if not (f.endswith(".safetensors") or f.endswith(".otaiprev") or f.endswith(".otaipart")):
            continue
        p = os.path.join(dp, f)
        try:
            s = os.path.getsize(p)
        except OSError:
            continue
        base = f
        for suf in (".otaiprev", ".otaipart"):
            if base.endswith(suf):
                base = base[: -len(suf)]
        idx.setdefault(base, []).append((p, s))


def real_size(p):
    """safetensors 自证：头部 8 字节 uint64 = 元信息长度 n，末尾 = max(data_offsets[1])。"""
    with open(p, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        meta = json.loads(f.read(n))
    end = max(t["data_offsets"][1] for k, t in meta.items()
              if k not in ("__metadata__", "__data_offsets__"))
    return 8 + n + end


for rel, cfg in want.items():
    exp = cfg["size"]
    cands = cfg["names"]
    hit = None
    for c in cands:
        for p, s in idx.get(c, []):
            if hit is None or s > hit[1]:
                hit = (p, s)
    if hit is None:
        print("%s\t%d\t%d\tMISS" % (rel, exp, -1))
        continue
    p, s = hit
    try:
        real = real_size(p)
        ok = (real == exp == s)
        concl = "OK" if ok else "PARTIAL"
    except Exception as e:
        real, ok, concl = -1, False, "PARTIAL(头损坏)"
    print("%s\t%d\t%d\t%s\t%s" % (rel, exp, s, concl, os.path.relpath(p, M)))
