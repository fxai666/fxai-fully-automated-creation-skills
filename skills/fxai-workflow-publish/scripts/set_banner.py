# -*- coding: utf-8 -*-
"""工作流「品牌条」切换：开发版（占位图）⇄ 发布版（真实品牌图），不动节点结构。

概念（用户 2026-11-14 定）：
  - 开发版 / 录制版：本机 ComfyUI 里用来开发、录教程的工作流。
    banner 节点指向占位图 fxai_workflow_banner_placeholder.png
    （中性：品牌色 + 工作流标题，**不带群号 / logo / 网址**，
    录屏时顶部可见、平台检测不到品牌信息）。
  - 发布版：一切对外发布的版本（仙宫云只是其中一个发布渠道，不是唯一）。
    banner 节点换成真实品牌图 fxai_workflow_banner.png（含品牌 + logo + 群号）。

做法：banner 节点用 properties.fxai_banner == true 标记，是 LoadImage 类型，
仅 widgets_values[0] 是图片文件名。脚本只替换这个文件名，绝不增删/移动节点，
因此对出图链路零影响，且天然幂等。

用法：
    # 把某个目录的工作流设为「开发版」（占位图）
    python set_banner.py --src <目录> --to placeholder

    # 把某个目录的工作流设为「发布版」（真实品牌图）
    python set_banner.py --src <目录> --to real

    # 从开发版目录生成发布版目录（复制并切换图片名，幂等）
    python set_banner.py --src <开发版目录> --to real --dst <发布版目录>

    # 仅预览
    python set_banner.py --src <目录> --dry-run
"""
from __future__ import annotations

import argparse
import glob
import os

PLACEHOLDER = "fxai_workflow_banner_placeholder.png"
REAL = "fxai_workflow_banner.png"


def swap_banner_bytes(text: bytes, src: str, dst: str) -> bytes:
    """把工作流 JSON 文本里的 banner 图片名 src 换成 dst。无匹配则原样返回。"""
    return text.replace(src.encode("utf-8"), dst.encode("utf-8"))


def process_file(path: str, src: str, dst: str, dry: bool):
    raw = open(path, "rb").read()
    new = swap_banner_bytes(raw, src, dst)
    name = os.path.basename(path)
    if new == raw:
        has_dst = dst.encode() in raw
        if has_dst:
            print("  = %-44s 已是 %s" % (name, dst))
        else:
            print("  . %-44s 无 banner 节点，跳过" % name)
        return False
    if dry:
        print("  ~ %-44s %s -> %s (dry)" % (name, src, dst))
    else:
        open(path, "wb").write(new)
        print("  ~ %-44s %s -> %s" % (name, src, dst))
    return True


def main():
    ap = argparse.ArgumentParser(description="切换工作流 banner：开发版(占位图)/发布版(品牌图)")
    ap.add_argument("--src", required=True, help="源工作流目录（或单文件）")
    ap.add_argument("--to", choices=["placeholder", "real"], default="placeholder",
                    help="目标：placeholder=开发版(占位图,默认)；real=发布版(品牌图)")
    ap.add_argument("--dst", default="",
                    help="可选：把 src 复制到此目录并切换为发布版（生成发布版目录，幂等）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.to == "placeholder":
        src_name, dst_name = REAL, PLACEHOLDER
    else:
        src_name, dst_name = PLACEHOLDER, REAL

    if os.path.isfile(a.src):
        files = [a.src]
    else:
        files = sorted(glob.glob(os.path.join(a.src, "*.json")))
    if not files:
        raise SystemExit("没有可处理的工作流 json: %s" % a.src)

    dst_dir = a.dst
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)

    print("处理: %s  (%d 个 json)  %s -> %s%s"
          % (a.src, len(files), src_name, dst_name,
             "  → 复制到 %s" % dst_dir if dst_dir else ""))
    n = 0
    for f in files:
        if dst_dir:
            target = os.path.join(dst_dir, os.path.basename(f))
            if not a.dry_run:
                raw = open(f, "rb").read()
                open(target, "wb").write(swap_banner_bytes(raw, src_name, dst_name))
                print("  + %s" % os.path.basename(f))
                n += 1
            else:
                print("  + %s (dry)" % os.path.basename(f))
                n += 1
        else:
            if process_file(f, src_name, dst_name, a.dry_run):
                n += 1
    print("完成：%d 个文件%s" % (n, "（dry-run，未写盘）" if a.dry_run else ""))


if __name__ == "__main__":
    main()
