# -*- coding: utf-8 -*-
# =====================================================================
# fxai-fourview-gen —— 角色视图（四视图/三视图）生成（独立技能，跨项目通用）
# 用法：其它技能/脚本把自己的技能目录加入 sys.path 后
#       from fourview_gen import gen_char_view, gen_threeview
# 依赖共享客户端：fxai-workflow/comfy_client.py（提交/下载/校验全流程）。
# 规则：有参考人物图（自有角色 000/001）→ 四视图；无参考人物 → 三视图；默认四视图。
# =====================================================================
import os
import sys
import time

# 共享 ComfyUI 客户端（与 fxai-* 技能同级的公共目录 fxai-workflow/）
_WF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fxai-workflow")
if _WF not in sys.path:
    sys.path.insert(0, _WF)
import comfy_client as C

# 复用客户端常量与函数
WF_FOURVIEW = C.WF_FOURVIEW
WF_THREEVIEW = C.WF_THREEVIEW
REF_WOMAN = C.REF_WOMAN
REF_MAN = C.REF_MAN
OUT_DIR = C.OUT_DIR
submit_workflow = C.submit_workflow
require_comfyui = C.require_comfyui
resolve_wf = C.resolve_wf
_seed_rand = C._seed_rand
check_outfit = C.check_outfit
_download = C._download
url_to_rel = C.url_to_rel


def gen_char_view(prompt, ref=REF_WOMAN, views=None, tag=None, subdir="charview", timeout=600,
                  num=0, model=None, allow_no_outfit=False):
    """生成角色视图（默认四视图）。
- 一律出四视图：views=None → 4。有 ref → 把参考头像注进人脸特征节点锚脸；无 ref → 纯按提示词生成。
- 尺寸由工作流内置（输出长边随宽高比浮动，实读为准），本函数不覆盖宽高。
- prompt 直接写入主生成节点的「正向提示词」，不做追加/改写。

⚠️ `ref` 参数格式：`ref` 是【服务端相对路径】`<子目录>/<文件名>`（如 `avatar/000.png`），直接喂给节点147「图片列表」由服务端按 rel 读取；格式与取值判据见 `fxai-standard/references/10-资源引用路径规范.md`。自有形象直接写 `avatar/000.png` / `avatar/001.png`（即 `C.REF_WOMAN`/`C.REF_MAN`）。

⚠️ 提示词只管「内容」，技术性构图由节点内置（分栏/白底/8k 等）：
  · 三/四视图只需写【发型 + 妆容 + 服装】这类穿搭内容；
  · 「完整全身人物/头身比例/站姿/镜头/白底/8k」等由自定义节点默认写好，提示词里省掉；
  · 仅当本次生图确实需要强调（如要半身、要特定镜头）时，才在提示词里额外补。
  · 工作流里的提示词与节点输出（170 提示词 / 147 图片列表 / 205、210 生成器）保持原样。

返回 (本地png路径, ComfyUI相对路径)。
输出目录默认 `charview`（与场景图 `mv_*` 分开，两个目录各取各的图）。
"""
    require_comfyui()
    # ref 非空＝直接引用角色底图（000/001 裸肩头像），必写穿搭+发型；三视图 ref="" 时自动免检
    check_outfit(prompt, ref, allow_no_outfit)
    # 四视图：有 ref → 参考头像喂 210 人脸特征节点锚脸；无 ref → 传空图列表，纯按提示词出。两种都是四视图。
    views = 4 if views is None else views
    if views != 4:
        raise ValueError("一律出四视图：gen_char_view(prompt, ref=..., views=4) 或 gen_char_view(prompt, views=4)")
    tag = tag or ("charview_%d" % int(time.time()))
    _task = "charview4" if views == 4 else "charview3"
    wf_path = resolve_wf(_task, model)  # model=None → 该任务默认模型（qwen21）
    # 有参考图 → 注入节点147「图片列表」喂人脸特征节点；没参考图 → 传空图列表，走纯提示词支路。
    image_input = ref if ref else ""
    params = {
        "fxai_prompt": prompt,  # 170 正向提示词（三/四视图主生成节点）
        "fxai_imageinput": image_input,  # 147 图片列表：四视图=参考图，三视图=空
        "fxai_outputdir": subdir,  # 节点117 目录
        "fxai_filename_index": int(num),  # 节点117 文件名序号（配合已固化的「启用文件名覆盖=True」=同名覆盖）
        "fxai_seed": _seed_rand(),
    }
    url = submit_workflow(wf_path, params, tag="charview%d:%s" % (views, tag),
                          kind=("charview4" if views == 4 else "charview3"), timeout=timeout)
    dst = os.path.join(OUT_DIR, tag + ".png")
    _download(url, dst)
    print("[charview%d] DONE ->" % views, dst)
    return dst, url_to_rel(url)


def gen_threeview(prompt, w=1080, h=1920, tag=None, ref="", subdir="views", timeout=600):
    """显式生成三视图（默认不提供参考图 → 纯设定生成）。尺寸由工作流固定，w/h 忽略。
    若确实需要以某张图作基底，可显式传 ref='avatar/xxx.png'。"""
    return gen_char_view(prompt, ref=ref, views=3, tag=tag or ("threeview_%d" % int(time.time())),
                         subdir=subdir, timeout=timeout)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("fxai-fourview-gen 已加载；gen_char_view / gen_threeview 可用。")
