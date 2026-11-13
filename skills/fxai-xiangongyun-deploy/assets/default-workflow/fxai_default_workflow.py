# -*- coding: utf-8 -*-
"""把指定的自定义工作流固化成 ComfyUI 前端的「内置默认图」（镜像层，不依赖浏览器）。

只做一件事：替换前端 bundle `workflowStore-*.js` 里硬编码的官方基础文生图
`var K={...},_e=JSON.stringify(K)` 中的 K，换成我们指定的那条工作流。

设计口径（2026-11-13 用户定）：
  * 不新建任何文件夹 —— 早期版本建的 `workflows/默认/` 会被清掉
  * 不注入首页跳转 —— 早期版本写进 index.html 的 `?workflows=默认` 会清掉
  * 不产生额外标签 —— 打开就是那一条，标签名就是工作流文件名
  * 指定方式就是「某一条现有工作流」，改哪条只改下面 SRC 一处

`_e = JSON.stringify(K)` 与 `window.comfyAPI.defaultGraph.defaultGraph`
都由 K 派生，所以一处替换，裸地址打开、`Load Default Workflow` 菜单全生效。

历史遗留清理（幂等，每次跑都做）：`workflows/默认/` 软链目录、
`.index.json` 里 `workflows/默认/` 的收藏、index.html 里的 FXAI_DF 注入。

用法（云端）：
    /root/miniconda3/envs/comfyui/bin/python /root/xgyscripts/fxai_default_workflow.py
    --check   只检查不写
    --revert  还原官方默认图（同时清理历史遗留）
"""
import argparse
import base64
import glob
import hashlib
import json
import os
import re
import shutil

WFROOT = "/root/ComfyUI/user/default/workflows"

# ← 要作为「打开即默认」的那一条，改这里就行（相对 workflows 根的路径）
SRC_REL = "千问2.1图片处理.json"
SRC = os.path.join(WFROOT, SRC_REL)

# 早期版本留下的东西，这里统一清掉
LEGACY_FOLDER = os.path.join(WFROOT, "默认")
LEGACY_FAV_PREFIX = "workflows/默认/"
IDXJSON = os.path.join(WFROOT, ".index.json")

MARK_RE = re.compile(r"/\*FXAI_DEFAULT_GRAPH_V1:([0-9a-f]{12})\*/")
ORIG_RE = re.compile(r"/\*FXAI_ORIG_K:([A-Za-z0-9+/=]+)\*/")
MARK_HTML_START = "<!--FXAI_DF_START-->"
MARK_HTML_END = "<!--FXAI_DF_END-->"

K_START = "var K={"
K_START_PATCHED = "var K="
K_END = ",_e=JSON.stringify(K)"


def static_dirs():
    return sorted(glob.glob(
        "/root/miniconda3/envs/*/lib/python*/site-packages/"
        "comfyui_frontend_package/static"))


def load_graph_json():
    """读工作流，压成纯 ASCII 单行 JSON（中文转 unicode 转义，任意编码都安全）。"""
    d = json.load(open(SRC, encoding="utf-8"), strict=False)
    return json.dumps(d, ensure_ascii=True, separators=(",", ":"))


def patch_bundle(path, graph, digest, check=False, revert=False):
    t = open(path, encoding="utf-8", errors="replace").read()
    mark = MARK_RE.search(t)

    if revert:
        if not mark:
            return "OK：未打过补丁"
        m = ORIG_RE.search(t)
        if not m:
            return "FAIL：没有原始 K 备份，无法还原"
        orig = base64.b64decode(m.group(1)).decode("utf-8")
        a = t.find(K_START_PATCHED, mark.end())
        b = t.find(K_END, a) if a >= 0 else -1
        if a < 0 or b < 0:
            return "FAIL：定位不到自定义 K 段"
        t2 = t[:a] + orig + t[b:]
        t2 = MARK_RE.sub("", t2)
        t2 = ORIG_RE.sub("", t2)
        if not check:
            open(path, "w", encoding="utf-8").write(t2)
        return "OK：已还原官方默认图"

    if mark and mark.group(1) == digest:
        return "OK：已是自定义默认图（哈希一致，无需重打）"

    if mark:
        a = t.find(K_START_PATCHED, mark.end())
        b = t.find(K_END, a) if a >= 0 else -1
        if a < 0 or b < 0:
            return "FAIL：找不到已打补丁的 K 段"
        t2 = (t[:mark.start()] + "/*FXAI_DEFAULT_GRAPH_V1:%s*/" % digest
              + t[mark.end():a] + "var K=" + graph + t[b:])
        if not check:
            open(path, "w", encoding="utf-8").write(t2)
        return "OK：已更新自定义默认图（工作流有改动）"

    a = t.find(K_START)
    b = t.find(K_END, a) if a >= 0 else -1
    if a < 0 or b < 0:
        return "SKIP：找不到 var K{ / _e 锚点（前端版本可能变了）"

    orig = t[a:b]                       # 'var K={...}' 整段
    b64 = base64.b64encode(orig.encode("utf-8")).decode("ascii")
    t2 = ("/*FXAI_DEFAULT_GRAPH_V1:%s*/" % digest
          + "/*FXAI_ORIG_K:" + b64 + "*/"
          + t[:a] + "var K=" + graph + t[b:])
    if not check:
        open(path, "w", encoding="utf-8").write(t2)
    return "OK：已替换内置默认图（%d → %d 字节）" % (len(orig), len(graph) + 6)


def strip_index(path, check=False):
    """清掉早期版本注入的首访重定向（保留下方所有内容）。"""
    t = open(path, encoding="utf-8", errors="replace").read()
    clean = re.sub(re.escape(MARK_HTML_START) + r".*?" + re.escape(MARK_HTML_END),
                   "", t, flags=re.S)
    if clean == t:
        return "OK：无首页注入"
    if not check:
        open(path, "w", encoding="utf-8").write(clean)
    return "OK：已移除首页跳转注入"


def cleanup_legacy(check=False):
    """删掉「默认」文件夹、「默认」收藏、首页注入，并报告实际做了啥。"""
    msgs = []

    # 1. workflows/默认/ 目录：只动软链与空目录，真文件一律保留
    if os.path.isdir(LEGACY_FOLDER):
        entries = os.listdir(LEGACY_FOLDER)
        real = [e for e in entries if not os.path.islink(os.path.join(LEGACY_FOLDER, e))]
        if real:
            msgs.append("保留 workflows/默认/：里面有真实文件 %s（不是我们建的软链）" % real[:3])
        else:
            if not check:
                for e in entries:
                    os.remove(os.path.join(LEGACY_FOLDER, e))
                shutil.rmtree(LEGACY_FOLDER, ignore_errors=True)
            msgs.append("%s 已删除 workflows/默认/（%d 个软链）"
                        % ("[预演]" if check else "[已删]", len(entries)))
    else:
        msgs.append("OK：没有 workflows/默认/ 目录")

    # 2. .index.json 里的「默认」收藏
    if os.path.isfile(IDXJSON):
        try:
            raw = json.load(open(IDXJSON, encoding="utf-8"))
        except Exception:
            raw = None
        if isinstance(raw, dict):
            fav = raw.get("favorites", [])
            left = [f for f in fav if not str(f).startswith(LEGACY_FAV_PREFIX)]
            if len(left) != len(fav):
                if not check:
                    if left or set(raw) - {"favorites"}:
                        open(IDXJSON, "w", encoding="utf-8").write(
                            json.dumps({**raw, "favorites": left}, ensure_ascii=False))
                    else:
                        os.remove(IDXJSON)
                msgs.append("%s 已从 .index.json 移除「默认」收藏（剩 %d 条%s）"
                            % ("[预演]" if check else "[已改]", len(left),
                               "，文件已删" if not left else ""))
            else:
                msgs.append("OK：.index.json 无「默认」收藏")
        else:
            msgs.append("SKIP：.index.json 格式不认识，未动")
    else:
        msgs.append("OK：没有 .index.json")
    return msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(SRC):
        raise SystemExit("找不到要指定的工作流：%s" % SRC)
    graph = "" if a.revert else load_graph_json()
    digest = hashlib.sha1(graph.encode("ascii")).hexdigest()[:12]
    print("指定工作流：%s (%d 字节 ASCII JSON, sha1=%s)"
          % (SRC_REL, len(graph), digest))

    for m in cleanup_legacy(a.check):
        print("  " + m)

    dirs = static_dirs()
    print("找到 %d 个前端 static 目录" % len(dirs))
    for d in dirs:
        print("\n--- %s" % d)
        idx = os.path.join(d, "index.html")
        if os.path.isfile(idx):
            print("  index.html :", strip_index(idx, a.check))
        for w in sorted(glob.glob(os.path.join(d, "assets", "workflowStore-*.js"))):
            print("  %s : %s" % (os.path.basename(w),
                                 patch_bundle(w, graph, digest, a.check, a.revert)))
    print("\n[完成] %s" % ("（只检查，未写入）" if a.check else ""))


if __name__ == "__main__":
    main()
