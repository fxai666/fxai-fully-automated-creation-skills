#!/usr/bin/env python
"""扫描本机的工作流 JSON，推导「跑这些工作流需要哪些模型 / 哪些自定义节点」，并与
assets/models.json + assets/nodes.json 对账，报告漏项。

为什么要有这个：工作流里的模型名是**节点槽位里的字符串**，改版就可能多出新模型。
对账脚本保证「清单」永远跟「工作流」一致，不会装完才发现少个模型。

用法：
  python scan_workflows.py                       # 自动找 fxai-workflow 目录
  python scan_workflows.py <工作流目录> [...]      # 指定目录
  python scan_workflows.py --json                # 机器可读
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")

MODEL_RE = re.compile(
    r"[^\"\'\s]*\.(?:safetensors|ckpt|pt|pth|gguf|bin|onnx|rnnn)", re.IGNORECASE)

# class_type 前缀 → 提供方 key
PREFIX_OWNER = [
    ("FxAi", "fxai_toolkit"),
]
EXACT_OWNER = {
    # 占位节点，ComfyUI 里并不存在，忽略即可
    "TODO_VocalSeparationNode": "（占位·忽略）",
}


def find_workflow_dirs(extra):
    dirs = list(extra)
    if not dirs:
        for pat in (
            os.path.expanduser("~/.workbuddy/skills/fxai-workflow"),
            "<技能包解压目录>/fxai-workflow",
        ):
            if os.path.isdir(pat):
                dirs.append(pat)
    return [d for d in dirs if os.path.isdir(d)]


def scan(dirs):
    models = collections.defaultdict(set)
    nodes = collections.defaultdict(set)
    files = []
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            if os.path.basename(f) in ("nodeconfig.json",):
                continue
            try:
                raw = open(f, encoding="utf-8").read()
                data = json.loads(raw)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            files.append(f)
            for m in MODEL_RE.findall(raw):
                models[os.path.basename(m)].add(os.path.basename(f))
            for nid, node in data.items():
                if isinstance(node, dict) and node.get("class_type"):
                    nodes[node["class_type"]].add(os.path.basename(f))
    return models, nodes, files


def owner_of(class_type):
    if class_type in EXACT_OWNER:
        return EXACT_OWNER[class_type]
    for pre, key in PREFIX_OWNER:
        if class_type.startswith(pre):
            return key
    return "ComfyUI 内置"


def main():
    argv = [x for x in sys.argv[1:] if not x.startswith("--")]
    as_json = "--json" in sys.argv
    dirs = find_workflow_dirs(argv)
    if not dirs:
        print("没找到工作流目录。用法：python scan_workflows.py <工作流目录> [...]", file=sys.stderr)
        return 2

    models, nodes, files = scan(dirs)
    mf = json.load(open(os.path.join(ASSETS, "models.json"), encoding="utf-8"))
    nf = json.load(open(os.path.join(ASSETS, "nodes.json"), encoding="utf-8"))

    # 清单里所有可能的落盘名
    covered = set()
    for m in mf["models"]:
        covered.add(m["target_name"] or os.path.basename(m["path"]))
        for v in (m.get("variants") or {}).values():
            covered.add(os.path.basename(v["path"]))

    node_ids = {n["id"] for n in nf["nodes"]}

    print(f"扫描 {len(files)} 个工作流，来源：")
    for d in dirs:
        print(f"  - {d}")
    print(f"\n发现 {len(models)} 个模型引用 / {len(nodes)} 种节点类型\n")

    print("── 模型对账 " + "─" * 52)
    missing = []
    for name in sorted(models):
        hit = name in covered
        if not hit:
            missing.append(name)
        mark = "✅ 已覆盖" if hit else "❌ 清单里没有"
        print(f"  {mark}  {name}")
        print(f"              ← {', '.join(sorted(models[name]))}")
    if missing:
        print(f"\n  ⚠ {len(missing)} 个模型不在 assets/models.json 里：")
        for name in missing:
            print(f"      {name}")
        print("    处理：确认 HF 仓库与 ComfyUI 目标目录后，补进 assets/models.json（字段见文件头 _comment）。")

    print("\n── 节点归属 " + "─" * 52)
    buckets = collections.defaultdict(list)
    for ct in sorted(nodes):
        buckets[owner_of(ct)].append(ct)
    for owner, lst in sorted(buckets.items()):
        note = ""
        if owner in node_ids:
            note = "（清单里有，装它即可）"
        elif owner != "ComfyUI 内置":
            note = "（⚠ 清单里没有对应节点包！）"
        else:
            note = "（ComfyUI 内置，要求 >= 0.30.0）"
        print(f"\n  [{owner}] {note}")
        for ct in lst:
            print(f"      {ct}  ({len(nodes[ct])} 个工作流)")

    if as_json:
        print(json.dumps({
            "dirs": dirs,
            "files": len(files),
            "models": {k: sorted(v) for k, v in models.items()},
            "nodes": {k: sorted(v) for k, v in nodes.items()},
            "uncovered_models": missing,
        }, ensure_ascii=False, indent=2))

    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
