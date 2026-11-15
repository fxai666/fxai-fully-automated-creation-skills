# -*- coding: utf-8 -*-
"""从「开发版」工作流目录生成「发布版」目录（幂等）。

做两件事：
  1) 把每条工作流的 banner 图片名从占位图换成真实品牌图（复用 set_banner）；
  2) 可选（--with-images）：把真实品牌图 + 占位图都拷进发布版目录旁的 input/，
     使发布版目录自成一体（缺图时给出告警）。

用法：
    # 生成发布版目录（只切图片名）
    python make_release.py --dev <开发版目录> --release <发布版目录>

    # 同时把品牌图/占位图拷进发布版目录的 input/
    python make_release.py --dev <开发版目录> --release <发布版目录> --with-images

说明：
  - 发布版目录通常是 ComfyUI 工作区里的 user/default/workflows/发布版。
  - 生成后，「发布版」目录里的 banner 是真实品牌图（对外发布用）；
    本地开发/录制仍用开发版目录（占位图，不显示群号）。
  - 上传到线上（仙宫云等）由 fxai-xiangongyun-deploy 的 sync_workflows.py 完成。
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import set_banner  # noqa: E402

try:
    import global_cfg  # 全局配置（~/.workbuddy/fxai_global.json 的 workflows 块）
except Exception:
    global_cfg = None

REAL = set_banner.REAL
PLACEHOLDER = set_banner.PLACEHOLDER


def comfy_root_of(path: str) -> str:
    """把 .../user/default/workflows/xxx 这样的路径规整为 ComfyUI 根目录。"""
    r = os.path.abspath(path)
    for _ in range(4):
        r = os.path.dirname(r)
    return r


def default_dev() -> str:
    """开发版目录：命令行未给时从全局配置取。"""
    if global_cfg is None:
        return ""
    try:
        d = global_cfg.workflows_cfg().get("dev_dir", "")
        return d if os.path.isdir(d) else ""
    except Exception:
        return ""


def default_release() -> str:
    """发布版目录：命令行未给时从全局配置取（环境变量优先）。"""
    if global_cfg is not None:
        rd = global_cfg.release_dir()
        if rd:
            return rd
    return ""


def input_dir_of(release_dir: str) -> str:
    """发布版目录旁的 input/（优先读全局 comfy_root，其次按路径反推）。"""
    if global_cfg is not None:
        try:
            base = global_cfg.workflows_cfg().get("comfy_root", "")
            if base:
                return os.path.join(base, "input")
        except Exception:
            pass
    return os.path.join(comfy_root_of(release_dir), "input")


def main():
    ap = argparse.ArgumentParser(description="从开发版目录生成发布版工作流目录")
    ap.add_argument("--dev", default="", help="开发版工作流目录（banner=占位图）；不给则读全局配置 workflows.dev_dir")
    ap.add_argument("--release", default="", help="发布版工作流目录（将生成/更新）；不给则读全局配置 workflows.release_dir")
    ap.add_argument("--with-images", action="store_true",
                    help="把真实品牌图 + 占位图拷进发布版目录旁的 input/")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    dev = a.dev or default_dev()
    release = a.release or default_release()
    if not dev:
        raise SystemExit("开发版目录：请给 --dev，或在全局配置 workflows.dev_dir 配置（或设 FXAI_COMFY_ROOT）")
    if not release:
        raise SystemExit("发布版目录：请给 --release，或在全局配置 workflows.release_dir 配置（或设 FXAI_WORKFLOW_RELEASE_DIR）")

    if not os.path.isdir(dev):
        raise SystemExit("开发版目录不存在: %s" % dev)
    dev_files = sorted(glob.glob(os.path.join(dev, "*.json")))
    if not dev_files:
        raise SystemExit("开发版目录里没有 json: %s" % dev)

    os.makedirs(release, exist_ok=True)
    print("生成发布版：%s → %s  (%d 个 json)" % (dev, release, len(dev_files)))
    n = 0
    for f in dev_files:
        target = os.path.join(release, os.path.basename(f))
        if a.dry_run:
            print("  + %s (dry)" % os.path.basename(f))
        else:
            raw = open(f, "rb").read()
            open(target, "wb").write(set_banner.swap_banner_bytes(raw, PLACEHOLDER, REAL))
            print("  + %s" % os.path.basename(f))
        n += 1

    if a.with_images:
        in_dir = input_dir_of(release)
        os.makedirs(in_dir, exist_ok=True)
        for img in (REAL, PLACEHOLDER):
            src_img = os.path.join(in_dir, img)
            if os.path.isfile(src_img):
                if not a.dry_run:
                    shutil.copy2(src_img, os.path.join(in_dir, img))
                print("  · 保留 %s" % img)
            else:
                print("  ! 缺图 %s（请确认 ComfyUI/input 里有该图）" % img)

    print("完成：%d 个发布版文件%s" % (n, "（dry-run，未写盘）" if a.dry_run else ""))


if __name__ == "__main__":
    main()
