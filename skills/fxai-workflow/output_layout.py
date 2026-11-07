# -*- coding: utf-8 -*-
"""目录脚手架 —— 按约定的项目树建/查目录。

目录规范写在 `fxai-weixin-gzh-publisher/SKILL.md` 的「产出物目录规范」一章；
其他技能各自的目录约定写进它们自己的 SKILL.md。本模块只负责按那套树建/查目录。

ComfyUI 可能跑在另一台电脑上，产物一律经接口下载（comfy_client 的
`GET /fxai/down/file?url=<令牌>`），下载后落到本模块定义的目录里：

    <项目根>/<项目名称>/
    ├─ article/ 文章正文（html/md）+ 标题文案 + 封面源图
    ├─ image/   图片（png 母版 + jpg 发布版）
    ├─ music/   音频（wav 母版 + mp3 片段）
    ├─ video/   视频（mp4 母版 + 封面帧）
    ├─ doc/     过程文件（style / lyrics / 提示词 / 脚本 / 时间轴 json）
    └─ _tmp/    临时中间产物（下划线开头，可随时删，不算备份）

用法（读）:
    import output_layout as OL
    L = OL.layout("一个人的队伍")   # -> {root, article, image, music, video, doc, _tmp}
    dst = os.path.join(L["music"], "一个人的队伍.wav")

用法（写）:
    OL.ensure("一个人的队伍")       # 建好目录，返回项目根
    OL.path("一个人的队伍", "music", "一个人的队伍.wav")  # 完整路径，自动建目录

目录根优先级（**在哪个工作区跑，项目就建在哪**）：
    1. 环境变量 FXAI_PROJECTS_ROOT
    2. 工作区级 <工作区>/_fxai_global.json 的 projects_root
    3. 当前工作目录

约定（与 comfy_client 下载契约配套）：
    ① 取产物只走接口下载，不扫 ComfyUI 本机输出目录
    ② 同名文件不覆盖：已存在自动换名（_1 / _2 …）
    ③ 不删任何源（ComfyUI 侧产物、已下载原件都留着）
    ④ 下载/复制后立即校验文件头，坏了当场报错
"""

import json
import os
import sys

# 类别 -> 目录名。新增类别只改这里（顺序即 tree() 的打印顺序）。
KINDS = [
    ("article", "article", "文章正文（html/md）+ 标题文案 + 封面源图"),
    ("image", "image", "图片（png 母版 + jpg 发布版）"),
    ("music", "music", "音频（wav 母版 + mp3 片段）"),
    ("video", "video", "视频（mp4 母版 + 封面帧）"),
    ("doc", "doc", "过程文件（style / lyrics / 提示词 / 脚本 / 时间轴 json）"),
    ("tmp", "_tmp", "临时中间产物（下划线开头，可随时删，不算备份）"),
]


def _workspace_cfg_root():
    """工作区级配置：cwd 往上 3 层里找 _fxai_global.json 的 projects_root。"""
    c = os.path.abspath(os.getcwd())
    d = c
    for _ in range(3):
        f = os.path.join(d, "_fxai_global.json")
        if os.path.isfile(f):
            try:
                with open(f, encoding="utf-8") as fh:
                    v = json.load(fh).get("projects_root", "")
                if v:
                    return os.path.abspath(os.path.expanduser(v))
            except Exception:
                pass
        parent = os.path.dirname(d)
        if not parent or parent == d:
            break
        d = parent
    return ""


def root():
    """项目根：**默认当前工作区**——在哪个工作区跑，项目文件夹就建在哪。

    优先级：
      1. 环境变量 FXAI_PROJECTS_ROOT
      2. 工作区级 <工作区>/_fxai_global.json 的 projects_root
      3. 当前工作目录
    """
    env = os.environ.get("FXAI_PROJECTS_ROOT", "")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    w = _workspace_cfg_root()
    if w:
        return w
    return os.path.abspath(os.getcwd())


def project(name):
    """项目根目录（不建目录）。"""
    if not name or not str(name).strip():
        raise ValueError("项目名为空，请传 --project（如 --project 一个人的队伍）")
    return os.path.join(root(), str(name).strip())


def layout(name, create=True):
    """返回该项目的目录字典 {root, article, image, music, video, doc, _tmp}。"""
    p = project(name)
    if create:
        os.makedirs(p, exist_ok=True)
    L = {"root": p}
    for key, dname, _desc in KINDS:
        d = os.path.join(p, dname)
        if create:
            os.makedirs(d, exist_ok=True)
        L[key] = d
    L["_tmp"] = L["tmp"]  # 别名：文档里写作 _tmp/，别让人按文档取值时 KeyError
    return L


def ensure(name):
    """建好项目全部目录，返回项目根（只建目录，不碰已有文件）。"""
    return project(name) if os.path.isdir(project(name)) else layout(name)["root"]


def _resolve_kind(kind):
    """类别既接受键名（tmp）也接受目录名（_tmp），传错就报错，不静默建目录。"""
    if any(k == kind for k, _d, _s in KINDS):
        return kind
    hit = next((k for k, d, _s in KINDS if d == kind), None)
    if not hit:
        raise ValueError("未知类别 %r，可用: %s（或目录名 %s）"
                         % (kind, ", ".join(k for k, _d, _s in KINDS),
                            ", ".join(d for _k, d, _s in KINDS)))
    return hit


def path(name, kind, filename, create=True):
    """某项目某类别下的完整文件路径，自动建目录。"""
    return os.path.join(layout(name, create=create)[_resolve_kind(kind)], filename)


def uniq(dst):
    """目标已存在就换名（_1 / _2 …），不覆盖已有文件。返回可用路径。"""
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 1
    while True:
        cand = "%s_%d%s" % (base, i, ext)
        if not os.path.exists(cand):
            return cand
        i += 1


def tree(name, show_files=False, max_files=40):
    """打印项目目录树（发布前自检用）。返回列表形式的行。"""
    p = project(name)
    lines = ["%s/ (根: %s)" % (name, root())]
    n = 0
    for key, dname, desc in KINDS:
        d = os.path.join(p, dname)
        files = []
        if os.path.isdir(d):
            files = sorted(os.listdir(d))
        lines.append(" %-8s %-8s %d 个文件 # %s" % (dname, "", len(files), desc))
        if show_files:
            for f in files[:max_files]:
                lines.append("  - %s" % f)
        n += 1
    return lines


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print("用法: python output_layout.py <项目名> [--files]")
        return
    show = "--files" in args
    name = next((a for a in args if not a.startswith("-")), "")
    ensure(name)  # 跑命令即建好目录
    for line in tree(name, show_files=show):
        print(line)


if __name__ == "__main__":
    main()
