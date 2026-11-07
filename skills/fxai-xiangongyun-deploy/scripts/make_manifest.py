#!/usr/bin/env python
"""从 assets/models.json 重建 assets/manifest.json（纯本地，不连服务器）。

manifest.json 就是那份**扁平配置**：key = 落盘文件名，一条一个模型。
用户以后加模型，有两种方式，等价：
  ① 往 assets/models.json 加一条，跑 `python make_manifest.py` 重建；
  ② 直接往 assets/manifest.json 里加一行（推荐，改完立刻生效，不用重建）。
字段：
  url    下载直链
  size   真实字节数（完整性判据用这个；min_bytes 是清单下限，会误判「已存在」）
  save   相对 ComfyUI/models 的目录
  status ""=没下 / partial=半截 / downloading=正在下 / done=已下完
  got    当前已下字节（只由 xgy_progress.py --json 回填，手工别乱写）
  _id    内部 id（跟 models.json 对齐用）
"""
from __future__ import annotations

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
MODELS_JSON = os.path.join(ASSETS, "models.json")
MANIFEST_JSON = os.path.join(ASSETS, "manifest.json")

HEAD = (
    "扁平模型清单：key = **相对 models 的实际存储路径**（如 diffusion_models/xxx.safetensors）。"
    "加模型直接往这个文件里加一行即可（key + url + size + save 四样就够）。"
    "为什么 key 用完整路径：2026-10-03 拿本机 ComfyUI 的 models 目录结构一对照，"
    "「一看实际路径就清晰了」—— 只有文件名看不出落哪个目录，还得配 save 才补全。\n"
    "字段 url=下载地址 | size=真实字节 | save=相对 models 的目录 | "
    "status=空/未下 · partial/半截 · downloading/正在下 · done/已下完 | "
    "got=当前已下字节（由 xgy_progress.py 回填） | _id=内部编号。"
)


def main():
    d = json.load(io.open(MODELS_JSON, encoding="utf-8"))
    ms = d["models"] if isinstance(d, dict) and "models" in d else d
    if isinstance(ms, dict):
        ms = list(ms.values())

    out = {"_说明": HEAD}
    for m in ms:
        rel = m["path"]
        if "/" not in rel:
            # path 漏了目录前缀（MelBandRoformer / Minimax-h3_Singularity 两条）
            td = m.get("target_dir") or ""
            if td:
                rel = td + "/" + rel
        name = m.get("target_name") or rel.rsplit("/", 1)[-1]
        if "/" in name:
            name = name.rsplit("/", 1)[-1]
        out[name] = {
            "url": m.get("source_url") or "",
            "size": int(m.get("source_bytes") or 0),
            "save": m.get("target_dir") or (rel.rsplit("/", 1)[0] if "/" in rel else ""),
            "status": m.get("status") or "",
            "got": 0,
            "_id": m.get("id", ""),
        }

    json.dump(out, io.open(MANIFEST_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # 顺手把 models.json 里漏掉的目录前缀补齐，免得下次重建又丢
    fixed = 0
    for m in ms:
        if "/" not in m["path"]:
            continue
    for m in ms:
        if m["path"] and "/" in m["path"]:
            continue
    for m in ms:
        if "/" in m["path"]:
            continue
        td = m.get("target_dir")
        if td:
            m["path"] = td + "/" + m["path"]
            fixed += 1
    if fixed:
        json.dump(d, io.open(MODELS_JSON, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    print(f"已重建 {len(out) - 1} 条 → assets/manifest.json"
          + (f"（补齐 models.json 里 {fixed} 条漏写的目录前缀）" if fixed else ""))


if __name__ == "__main__":
    sys.exit(main())
