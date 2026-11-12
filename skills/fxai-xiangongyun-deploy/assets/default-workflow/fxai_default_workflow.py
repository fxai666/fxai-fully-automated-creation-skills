# -*- coding: utf-8 -*-
"""把自定义工作流固化成 ComfyUI 前端的「内置默认图」（镜像层，不依赖浏览器）。

在服务端/镜像层做三件事，全部幂等，可反复执行（前端包升级后重跑即可）：

1. 替换前端 bundle `workflowStore-*.js` 里硬编码的官方基础文生图
   `var K={...},_e=JSON.stringify(K)` 中的 K，换成我们自己的工作流 JSON。
   → 任何浏览器、任何 localStorage 状态、裸地址打开，都不会再出现官方默认图。
   → `Load Default Workflow` 菜单加载的也是自定义工作流。
   → `_e = JSON.stringify(K)` 与 `window.comfyAPI.defaultGraph.defaultGraph`
      都由 K 派生，一处替换全链路生效。

2. 给 `index.html` 注入首访重定向（无 URL 参数、无历史标签时
   → `/?workflows=<文件夹>`），让打开的是「持久化工作流」而不是临时默认图，
   标签名、保存、侧栏书签都正常。

3. 保证 `workflows/默认/` 里只有一条指向源文件的软链，`.index.json` favorites 就位。

两份前端包（python3.1 / python3.12 的 site-packages）都打，避免开机脚本
`head -1` 挑错目录（ComfyUI 实际跑在 python3.12）。

用法（云端）：
    /root/miniconda3/envs/comfyui/bin/python /root/xgyscripts/fxai_default_workflow.py
    --check   只检查不写
    --revert  还原官方默认图与首页注入
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
SRC = WFROOT + "/千问2.1图片处理.json"
FOLDER = "默认"
DST = WFROOT + "/" + FOLDER + "/" + os.path.basename(SRC)
IDXJSON = WFROOT + "/.index.json"

MARK_RE = re.compile(r"/\*FXAI_DEFAULT_GRAPH_V1:([0-9a-f]{12})\*/")
ORIG_RE = re.compile(r"/\*FXAI_ORIG_K:([A-Za-z0-9+/=]+)\*/")
MARK_HTML_START = "<!--FXAI_DF_START-->"
MARK_HTML_END = "<!--FXAI_DF_END-->"

SNIPPET = (
    MARK_HTML_START
    + "<script>/*凤希AI：首访默认工作流*/try{(function(){"
    + "if(location.search)return;"
    + "if(sessionStorage.getItem('fxai_df_wf'))return;"
    + "if(localStorage.getItem('Comfy.PreviousWorkflow')||"
    + "localStorage.getItem('Comfy.OpenWorkflowsPaths'))return;"
    + "sessionStorage.setItem('fxai_df_wf','1');"
    + "location.replace('/?workflows='+encodeURIComponent('%s'));" % FOLDER
    + "})()}catch(e){}</script>"
    + MARK_HTML_END
)

K_START = "var K={"
K_START_PATCHED = "var K="
K_END = ",_e=JSON.stringify(K)"


def static_dirs():
    return sorted(glob.glob(
        "/root/miniconda3/envs/*/lib/python*/site-packages/"
        "comfyui_frontend_package/static"))


def load_graph_json():
    """读自定义工作流，压缩成纯 ASCII 单行 JSON（中文转成 unicode 转义，任意编码都安全）。"""
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
        # 工作流更新了：只换 K 段，保留原始 K 备份，更新哈希标记
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


def patch_index(path, check=False, revert=False):
    t = open(path, encoding="utf-8", errors="replace").read()
    clean = re.sub(re.escape(MARK_HTML_START) + r".*?" + re.escape(MARK_HTML_END),
                   "", t, flags=re.S)
    if revert:
        if clean == t:
            return "OK：无注入"
        if not check:
            open(path, "w", encoding="utf-8").write(clean)
        return "OK：已移除首访重定向"
    if SNIPPET in t and t.count(MARK_HTML_START) == 1:
        return "OK：首访重定向已是最新版"
    t2 = clean.replace("<head>", "<head>" + SNIPPET, 1) if "<head>" in clean else SNIPPET + clean
    if not check:
        open(path, "w", encoding="utf-8").write(t2)
    return "OK：已注入/更新首访重定向 -> /?workflows=%s" % FOLDER


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(SRC):
        raise SystemExit("找不到自定义工作流：%s" % SRC)
    graph = "" if a.revert else load_graph_json()
    digest = hashlib.sha1(graph.encode("ascii")).hexdigest()[:12]
    print("自定义工作流：%s (%d 字节 ASCII JSON, sha1=%s)"
          % (os.path.basename(SRC), len(graph), digest))

    if not a.check and not a.revert:
        os.makedirs(os.path.dirname(DST), exist_ok=True)
        for n in os.listdir(os.path.dirname(DST)):
            if n != os.path.basename(SRC):
                p = os.path.join(os.path.dirname(DST), n)
                (shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p)
                 else os.remove(p))
        if os.path.islink(DST) or os.path.isfile(DST):
            os.remove(DST)
        os.symlink(os.path.join("..", os.path.basename(SRC)), DST)
        fav = []
        try:
            fav = json.load(open(IDXJSON, encoding="utf-8")).get("favorites", [])
        except Exception:
            pass
        item = "workflows/%s/%s" % (FOLDER, os.path.basename(SRC))
        if item not in fav:
            fav.append(item)
        open(IDXJSON, "w", encoding="utf-8").write(
            json.dumps({"favorites": fav}, ensure_ascii=False))
    print("软链 %s -> %s" % (DST, os.path.realpath(DST)))

    dirs = static_dirs()
    print("找到 %d 个前端 static 目录" % len(dirs))
    for d in dirs:
        print("\n--- %s" % d)
        idx = os.path.join(d, "index.html")
        if os.path.isfile(idx):
            print("  index.html :", patch_index(idx, a.check, a.revert))
        for w in sorted(glob.glob(os.path.join(d, "assets", "workflowStore-*.js"))):
            print("  %s : %s" % (os.path.basename(w),
                                 patch_bundle(w, graph, digest, a.check, a.revert)))
    print("\n[完成] %s" % ("（只检查，未写入）" if a.check else ""))


if __name__ == "__main__":
    main()
