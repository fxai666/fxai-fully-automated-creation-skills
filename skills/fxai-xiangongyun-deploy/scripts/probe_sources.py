#!/usr/bin/env python
"""下载之前先把每个模型在**各个源**上的真实住址摸清楚。

流程（用户 2026-10-03 定调）：
    先探测 → 拿到「哪个模型在哪个源存在 / 真实 URL / 多大 / 支不支持续传」
    → 再按这张表下载。

'\u63a2\u6d4b\u4ec0\u4e48'：
  - HTTP 状态码（200 是常态；不存在也不一定 404，见下）
  - Content-Length —— 文件真实大小
  - Range 支持（206 = 可断点续传）
  - 文件头魔数 —— 排除「HTTP 200 但其实是 JSON 错误页」的坑
    （魔搭 / 部分镜像对不存在的仓库也返回 200 + 一小段 JSON）

\u7528\u6cd5\uff1a
  python probe_sources.py                      # 用默认凭据，探测全部模型
  python probe_sources.py --group img,music    # 只探测指定分组
    --write 会往 assets/models.json 写两类东西：
        source / source_url / source_bytes   单个最佳源（向后兼容）
        sources[]                           全部候选源（每个都带 range 标记）

    sources[] 才是下载脚本真正用的。为什么要带 range 标记：
    `curl -C -` 撞上不支持 Range 的源（返回 200 全量）会**静默把目标文件从 0 截断重写**，
    日志不报错、只是「进度倒退」，已下载的大文件会被打回几十 MB。
    所以「哪些源能续传」在这里问清楚并固化，下载时只按sources[] 里验过的源下单。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote import get_ssh  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "assets")

# 远端 probe 脚本：对每个 URL，用最小代价拿到 (http码, 大小, range支持, 头4字节)
PROBE_BODY = r'''
probe_one() {
  local url="$1" out
  # -r 0-0 只取 1 字节：既能拿 content-range 里的真实总大小，又能同时验证 Range 支持。
  out=$(curl -sL -m 30 -r 0-0 -D - -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
  local code="${out##*$'\n'}"
  local hdr="${out%$'\n'*}"
  local size range_sup
  size=$(printf '%s' "$hdr" | tr -d '\r' | grep -i '^content-range:' | tail -n1 \
         | sed 's#.*/##' | tr -dc '0-9')
  [ -n "$size" ] || size=$(printf '%s' "$hdr" | tr -d '\r' | grep -i '^content-length:' \
         | tail -n1 | tr -dc '0-9')
  [ -n "$size" ] || size=0
  case "$code" in 206) range_sup=1;; *) range_sup=0;; esac
  echo "$code|$size|$range_sup"
}
# 取远端文件前 4 字节的十六进制 + 是否像 error 页
magic_one() {
  local url="$1" m
  m=$(curl -sL -m 30 -r 0-3 "$url" 2>/dev/null | od -An -tx1 -N4 2>/dev/null | tr -d ' \n')
  case "$m" in
    7b*|3c*|5b*) echo "$m|ERRPAGE";;
    "")          echo "|EMPTY";;
    *)           echo "$m|OK";;
  esac
}
'''

ROW_TMPL = r'''
for row in "${ROWS[@]}"; do
  IFS='~' read -r mid sname url <<<"$row"
  r=$(probe_one "$url")
  code="${r%%|*}"; rest="${r#*|}"; size="${rest%%|*}"; rg="${rest##*|}"
  mg=""; verdict=""
  case "$code" in
    200|206)
      m=$(magic_one "$url"); hex="${m%%|*}"; kind="${m##*|}"
      case "$kind" in
        ERRPAGE) verdict="NOT_FOUND(errpage)";;
        EMPTY)   verdict="EMPTY";;
        *)       verdict="OK";;
      esac
      mg="$hex"
      ;;
    401) verdict="NOT_FOUND(401-\u955c\u50cf\u672a\u6536\u5f55)";;
    403) verdict="NOT_FOUND(403)";;
    404) verdict="NOT_FOUND(404)";;
    *)   verdict="HTTP_$code";;
  esac
  echo "ROW|$mid|$sname|$code|$size|$rg|$mg|$verdict"
done
'''


def load_models(group=None, only=None):
    mf = json.load(open(os.path.join(ASSETS, "models.json"), encoding="utf-8"))
    mirror = mf["mirror"].rstrip("/")
    fallback = mf["fallback"].rstrip("/")
    mirror2 = (mf.get("mirror2") or "").rstrip("/")
    groups = [g.strip() for g in (group or "all").split(",") if g.strip()]
    only_set = {o.strip() for o in (only or "").split(",") if o.strip()}
    srcs = [("mirror", mirror), ("ms", mirror2), ("hf", fallback)]
    rows = []
    for m in mf["models"]:
        if m.get("optional") and not include_optional:
            continue
        if only_set and m["id"] not in only_set:
            continue
        if not only_set and groups != ["all"] and m["group"] not in groups:
            continue
        variant = None
        for sname, base in srcs:
            if not base:
                continue
            repo = m["repo"]
            if sname == "ms":
                repo = m.get("ms_repo") or m["repo"]
                if not repo:
                    continue
                url = f"{base}/{repo}/resolve/master/{m['path']}"
            else:
                url = f"{base}/{repo}/resolve/main/{m['path']}"
            rows.append((m["id"], sname, url, int(m["min_bytes"])))
    return mf, sorted(rows, key=lambda r: (r[0], r[1]))


include_optional = False


def build_script(rows):
    lines = ["#!/bin/bash", PROBE_BODY, "ROWS=("]
    for mid, sname, url, _ in rows:
        lines.append(f'  "{mid}~{sname}~{url}"')
    lines.append(")")
    lines.append(ROW_TMPL)
    lines.append('echo "PROBE_DONE"')
    return "\n".join(lines)


def human(n):
    n = int(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n}{unit}"
        n = n / 1024.0
    return f"{n:.1f}TB"


def url_of(mf, m, sname):
    """按源名拼出这个模型的下载 URL。"""
    base = {"mirror": mf["mirror"].rstrip("/"),
            "ms": (mf.get("mirror2") or "").rstrip("/"),
            "hf": mf["fallback"].rstrip("/")}[sname]
    repo = m["repo"]
    if sname == "ms":
        repo = m.get("ms_repo") or m["repo"]
        return f"{base}/{repo}/resolve/master/{m['path']}"
    return f"{base}/{repo}/resolve/main/{m['path']}"


def candidates(results, mf, mid):
    """这个模型全部可用的源，按「能续传的优先」排好序。

    range=0 的源仍可用，只是**只从 0 整量重下、不参与续传**：其余源都没有这个文件时
    （非它不可的地步），下载脚本才退到它。
    """
    minb = {m["id"]: int(m["min_bytes"]) for m in mf["models"]}
    order = {"ms": 0, "mirror": 1, "hf": 2}
    out = []
    for r in results.get(mid, []):
        if r["verdict"] != "OK":
            continue
        if r["size"] and minb.get(mid, 0) and r["size"] < minb[mid] * 0.9:
            continue
        out.append({"src": r["src"], "range": bool(r["range"]), "size": int(r["size"])})
    for c in out:
        for m in mf["models"]:
            if m["id"] == mid:
                c["url"] = url_of(mf, m, c["src"])
    out.sort(key=lambda c: (not c["range"], order.get(c["src"], 9)))
    return out


def main():
    global include_optional
    ap = argparse.ArgumentParser(description="探测模型在各下载源上的真实住址")
    ap.add_argument("--group", default="all")
    ap.add_argument("--only", default="")
    ap.add_argument("--include-optional", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="把每个模型的最佳源写回 assets/models.json 的 source 字段")
    ap.add_argument("--profile", default="default")
    a = ap.parse_args()
    include_optional = a.include_optional

    mf, rows = load_models(a.group, a.only)
    n_models = len({r[0] for r in rows})
    print(f"探测 {n_models} 个模型 × {len(rows)} 个 (模型,源) 组合 ...\n")

    ssh = get_ssh(a.profile)
    try:
        ssh.put_text(build_script(rows), "/tmp/xgy_probe.sh")
        ssh.run("chmod +x /tmp/xgy_probe.sh")
        r = ssh.run("bash /tmp/xgy_probe.sh", timeout=900)
        raw = r.out
    finally:
        ssh.close()

    results = {}
    for line in raw.splitlines():
        if not line.startswith("ROW|"):
            continue
        _, mid, sname, code, size, rg, mg, verdict = line.split("|") + [""] * (8 - len(line.split("|")))
        results.setdefault(mid, []).append({
            "src": sname, "code": code, "size": int(size or 0),
            "range": rg == "1", "magic": mg, "verdict": verdict,
        })

    minb = {m["id"]: int(m["min_bytes"]) for m in mf["models"]}
    best = {}
    order = {"ms": 0, "mirror": 1, "hf": 2}
    print("=" * 96)
    print(f"{'模型':<22}{'源':<8}{'HTTP':<6}{'大小':<11}{'续传':<7}{'文件头':<10}{'判定'}")
    print("=" * 96)
    for mid in sorted(results):
        for r in sorted(results[mid], key=lambda x: order.get(x["src"], 9)):
            ok = r["verdict"] == "OK"
            if ok:
                cand = minb.get(mid, 0)
                if r["size"] and r["size"] < cand * 0.9:
                    ok = False
                    r["verdict"] = f"偏小({human(r['size'])}<{human(cand)})"
            print(f"{mid:<22}{r['src']:<8}{r['code']:<6}{human(r['size']):<11}"
                  f"{('206 支持' if r['range'] else '200 不支持'):<7}{r['magic'][:8]:<10}{r['verdict']}")
            if ok and (mid not in best or order.get(r["src"], 9) < order.get(best[mid]["src"], 9)):
                best[mid] = r
        print("-" * 96)

    print("\n>>> 建议的下载地址（每个模型选一个可用源）")
    chosen = {}
    for mid, b in sorted(best.items()):
        base = {"mirror": mf["mirror"].rstrip("/"), "ms": (mf.get("mirror2") or "").rstrip("/"),
                "hf": mf["fallback"].rstrip("/")}[b["src"]]
        for m in mf["models"]:
            if m["id"] != mid:
                continue
            repo = m["repo"]
            if b["src"] == "ms":
                repo = m.get("ms_repo") or m["repo"]
                url = f"{base}/{repo}/resolve/master/{m['path']}"
            else:
                url = f"{base}/{repo}/resolve/main/{m['path']}"
        print(f"  {mid:<22} -> {b['src']:<7} {human(b['size']):<10} range={'是' if b['range'] else '否'}  {url}")
        chosen[mid] = {"src": b["src"], "size": b["size"], "range": b["range"], "url": url}

    missing = [m for m in results if m not in best]
    if missing:
        print(f"\n⚠ 没有任何源可用：{', '.join(missing)}"
              f"\n  401 = 镜像没收录，找仙宫云客服；魔搭上没有的社区仓库只能走 hf。")
    # 每个模型的候选源表：range=1 的排前面，range=0 的排在后面兜底
    cands = {mid: candidates(results, mf, mid) for mid in results}

    print("\n>>> 每个模型的下载源顺序（range=0 的会标 ✗）")
    for mid in sorted(results):
        cs = cands.get(mid) or []
        if not cs:
            print(f"  {mid:<22} ⚠ 无可用源")
            continue
        desc = "  →  ".join(f"{c['src']}{'' if c['range'] else '✗'}({human(c['size'])})" for c in cs)
        print(f"  {mid:<22} {desc}")
    print("  （✗ = 该源不支持 Range 续传，只在其它源都拿不到时才轮到它，届时从 0 重下）")

    if a.write:
        p = os.path.join(ASSETS, "models.json")
        for m in mf["models"]:
            if m["id"] in chosen:
                m["source"] = chosen[m["id"]]["src"]
                m["source_url"] = chosen[m["id"]]["url"]
                m["source_bytes"] = chosen[m["id"]]["size"]
            if cands.get(m["id"]):
                m["sources"] = cands[m["id"]]
        with open(p, "w", encoding="utf-8") as f:
            json.dump(mf, f, ensure_ascii=False, indent=2)
        print(f"\n已写回 {os.path.abspath(p)}")
        print("  source / source_url / source_bytes = 最佳源；sources[] = 全候选（带 range 标记）")
    else:
        print("\n（想把结果固化进 models.json 就加 --write）")


if __name__ == "__main__":
    main()
