# -*- coding: utf-8 -*-
# =====================================================================
# fxai-scene-gen —— 场景图生成（独立技能，跨项目通用）
# 用法：其它技能/脚本把自己的技能目录加入 sys.path 后
# from scene_gen import gen_scene_image
# 依赖共享客户端：fxai-workflow/comfy_client.py。
# 两种模式（抽象出来，供各项目复用）：
# · 空场景（role=None）：只画环境，不含任何人物 —— MV 多参考(multiref)模式用；
# · 含人物（role="woman"/"man"）：把人物直接放进场景 —— single/multi MV、短剧用。
# =====================================================================
import os
import sys
import time

# 共享 ComfyUI 客户端（与 fxai-* 技能同级的公共目录 fxai-workflow/）
_WF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fxai-workflow")
if _WF not in sys.path:
 sys.path.insert(0, _WF)
import comfy_client as C

WF_IMG = C.WF_IMG
REF_WOMAN = C.REF_WOMAN
REF_MAN = C.REF_MAN
OUT_DIR = C.OUT_DIR
submit_workflow = C.submit_workflow
require_comfyui = C.require_comfyui
resolve_wf = C.resolve_wf
_role_ref = C._role_ref
check_outfit = C.check_outfit
_seed_rand = C._seed_rand
_download = C._download
url_to_rel = C.url_to_rel


def gen_scene_image(prompt, role="woman", w=1080, h=1920, tag=None, subdir="mv_scene",
 timeout=3600, num=None, model=None, force=False, allow_no_outfit=False):
 """生成场景图（可把人物直接放进场景）。

 模式（抽象出来，供各项目复用）：
 · 空场景 → role=None：只画环境，不含任何人物（MV 多参考 multiref 用，人物由
 多参考参考图在视频生成阶段注入，场景图只留环境）。
 · 含人物 → role="woman"/"man"（或自有参考图相对路径）：把人物直接画进场景
 （single/multi MV、短剧用）。

 - num：写入服务端的固定文件序号（配合「启用文件名覆盖」实现同名覆盖）。
 逐张生成时传该张的序号（0 起）；None 表示沿用工作流默认（递增命名）。
 - prompt 直接写入「正向提示词」，完全按传入内容生成，**不追加任何后缀**。
 - 内容怎么描述看需要：场景图比视图更需要「完整描述」——场景、氛围、光线、
 人物在做什么与穿搭等；但「是否全身入画」等技术性构图不必每次都写，除非本次确实需要。
 - role 控制角色参考图（woman→000 / man→001 / 自定义相对路径 / None→纯场景不带人物参考）。
 - w/h 为规范尺寸（竖屏 1080×1920 / 1440×1920，横屏宽高交换）；缩放/对齐/裁剪由工作流内部完成。
 - timeout 默认 3600：ComfyUI 队列拥堵时（同一台机还有别人的任务）一张图可能要等十几分钟，
 600s 会误判超时。force=True 才重画已有同名图。
 """
 require_comfyui()
 check_outfit(prompt, role, allow_no_outfit)  # 含人物(role非空)必写穿搭+发型；role=None 纯场景自动免检
 tag = tag or ("scene_%d" % int(time.time()))
 cached = os.path.join(OUT_DIR, tag + ".png")
 if not force and os.path.isfile(cached) and os.path.getsize(cached) > 1024:
  print("[scene] 复用已有场景图：%s" % cached, flush=True)
  return cached, _rel_of(cached, subdir, num)
 wf_path = resolve_wf("scene", model) # model=None → 该任务默认模型（qwen21）
 params = {
  "fxai_prompt": prompt,
  "fxai_imageinput": _role_ref(role),
  "fxai_width": int(w),
  "fxai_height": int(h),
  "fxai_outputdir": subdir,
  "fxai_filename_index": int(num) if num is not None else 0,
  "fxai_seed": _seed_rand(),
 }
 url = submit_workflow(wf_path, params, tag="scene:" + tag, kind="scene", timeout=timeout)
 dst = os.path.join(OUT_DIR, tag + ".png")
 _download(url, dst)
 print("[scene] DONE ->", dst)
 return dst, url_to_rel(url)


def _rel_of(dst, subdir, num):
 """复用缓存时的服务端相对路径（节点按 fxai_filename_index 固定命名，复用它才不会错绑）。"""
 n = ("%03d" % int(num)) if num is not None else "000"
 return "%s/%s.png" % (subdir, n)


if __name__ == "__main__":
 sys.stdout.reconfigure(encoding="utf-8", errors="replace")
 print("fxai-scene-gen 已加载；gen_scene_image 可用（role=None=空场景，role='woman'/'man'=含人物）。")
