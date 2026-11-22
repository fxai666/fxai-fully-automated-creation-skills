# -*- coding: utf-8 -*-
# =====================================================================
# fxai-character-styling 的 API 调用客户端（本技能自己提交，不经下游技能）
# ---------------------------------------------------------------------
# 与本技能自带的扁平 API 工作流 charstyling-qwen21-api.json 配套：
#   人物(角色库 avatar/000|001) + 服装参考图 + 姿势参考图 → 场景图。
# 三图各自经共享客户端 upload_image 拿到服务端相对路径，按
#   「图1人物 / 图2服装 / 图3姿势」顺序拼进 fxai_imageinput（逗号分隔），
# 与提示词的图序严格对齐（buildRecipePrompt 口径，见 prompt_builder.py）。
#
# 提交 / 轮询 / 下载全部委托 fxai-workflow/comfy_client.py（唯一真相源）；
# 本模块只负责「备参数 + 调提交」，不重复实现任何 ComfyUI 协议细节。
# =====================================================================
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(_HERE, "..", ".."))
_WF = os.path.join(SKILLS, "fxai-workflow")
if _WF not in sys.path:
    sys.path.insert(0, _WF)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import comfy_client as C
import prompt_builder as PB

# 工作流经公共注册表解析（charstyling→qwen21→charstyling-qwen21-api.json），
# 路径单一真相源在 fxai-workflow/comfy_client.py；技能目录下同名 json 为随包副本，仅供查阅。
WF_API = C.resolve_wf("charstyling")

# 复用共享客户端符号（单一真相源）
upload_image = C.upload_image
submit_workflow = C.submit_workflow
require_comfyui = C.require_comfyui
_seed_rand = C._seed_rand
_download = C._download
url_to_rel = C.url_to_rel
resolve_asset_subdir = C.resolve_asset_subdir


def _as_ref(local_path, subdir, filename=None):
    """本机图片路径 → 服务端相对路径（供 FxAiCharacterImageSelector 读取）。"""
    if "/" in local_path or "\\" in local_path and os.path.isabs(local_path):
        # 已是服务端相对路径（如 avatar/000.png 或 pg_test/xxx.png）时不重复上传
        if not os.path.isfile(local_path):
            return local_path.replace("\\", "/")
    rel = C.upload_image(local_path, subdir=subdir, filename=filename or os.path.basename(local_path))
    return rel if "/" in rel else "%s/%s" % (subdir, rel)


def generate_styling(persona_ref, costume_path, pose_path, prompt,
                     costume_name="", pose_name="", angle="", style="写实",
                     w=960, h=1280, tag=None, subdir=None, save_dir=None,
                     timeout=3600, force=False):
    """提交一次「角色造型合成」到本机/云端 ComfyUI，返回 (本地PNG路径, 服务端相对路径)。

    参数：
      persona_ref : 人物参考（角色库相对路径 avatar/000.png、avatar/001.png，或本机图片路径）
      costume_path: 服装参考图（本机路径 或 服务端相对路径）
      pose_path   : 姿势参考图（本机路径 或 服务端相对路径）
      prompt      : 完整提示词；建议直接用 prompt_builder.build_recipe_prompt() 生成，
                    杜绝手写穿搭文字（服装名称只在括号作辅助说明）。
      w/h         : 画布尺寸。本机默认走 960 档（见用户硬规则），可被显式传参覆盖。
      subdir      : 服务端输出子目录；None 时由 resolve_asset_subdir 解析（项目目录内自动归位）。
      save_dir    : 本地成品落点；None 时用当前工作目录（建议从项目目录运行）。
      force       : True 才重画已存在的同名图。
    """
    require_comfyui()
    subdir = resolve_asset_subdir(subdir)
    tag = tag or ("styling_%d" % int(time.time()))
    save_dir = save_dir or os.getcwd()
    os.makedirs(save_dir, exist_ok=True)

    # 三图 → 服务端相对路径，顺序固定：图1人物 / 图2服装 / 图3姿势
    person = persona_ref if ("/" in persona_ref or "\\" in persona_ref) and not os.path.isfile(persona_ref) \
        else (_as_ref(persona_ref, subdir) if persona_ref else "")
    costume = _as_ref(costume_path, subdir) if costume_path else ""
    pose = _as_ref(pose_path, subdir) if pose_path else ""
    refs = [x for x in (person, costume, pose) if x]
    if not refs:
        raise ValueError("三张参考图至少给一张（人物/服装/姿势）")

    cached = os.path.join(save_dir, tag + ".png")
    if not force and os.path.isfile(cached) and os.path.getsize(cached) > 1024:
        print("[styling] 复用已有成图：%s" % cached, flush=True)
        return cached, "%s/%03d.png" % (subdir, 0)

    params = {
        "fxai_prompt": prompt,
        "fxai_imageinput": ", ".join(refs),
        "fxai_width": int(w),
        "fxai_height": int(h),
        "fxai_outputdir": subdir,
        "fxai_filename_index": _seed_rand(),  # 随机序号 → 并发任务各落各名
        "fxai_seed": _seed_rand(),
    }
    # 本技能自带工作流；提示词由 buildRecipePrompt 生成（服装靠图2，零穿搭文字），
    # 符合官方规范，提交时无需本地穿搭闸门。
    url = submit_workflow(WF_API, params, tag="styling:" + tag, kind="scene", timeout=timeout)
    dst = os.path.join(save_dir, tag + ".png")
    _download(url, dst)
    print("[styling] DONE -> %s (服务端:%s)" % (dst, url_to_rel(url)), flush=True)
    return dst, url_to_rel(url)


def main():
    """CLI：给三图路径 + 风格，走 buildRecipePrompt 构建提示词并提交。"""
    import argparse
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="角色造型合成（本技能 API 客户端）")
    ap.add_argument("--人物", dest="persona", required=True, help="人物参考：avatar/000.png 或本机图路径")
    ap.add_argument("--服装", dest="costume", default="", help="服装参考图路径")
    ap.add_argument("--服装名", dest="costume_name", default="", help="服装名称（仅括号辅助，不写进穿搭描述）")
    ap.add_argument("--姿势", dest="pose", default="", help="姿势参考图路径")
    ap.add_argument("--姿势名", dest="pose_name", default="")
    ap.add_argument("--角度", dest="angle", default="")
    ap.add_argument("--风格", dest="style", default="写实", help="风格 key（见 styles.json）")
    ap.add_argument("--场景", dest="scene", default="", help="卧姿承托场景行（仅环境，可选）")
    ap.add_argument("--宽", dest="w", type=int, default=960)
    ap.add_argument("--高", dest="h", type=int, default=1280)
    ap.add_argument("--标签", dest="tag", default=None)
    ap.add_argument("--子目录", dest="subdir", default=None)
    ap.add_argument("--落点", dest="save_dir", default=None)
    ap.add_argument("--dry-run", dest="dry", action="store_true", help="只构建提示词与参数，不提交")
    a = ap.parse_args()

    portrait = {"source": "upload"}
    costume = {"source": "library", "服装名称": a.costume_name} if a.costume else None
    pose = {"source": "library", "姿势名称": a.pose_name, "角度": a.angle} if a.pose else None
    prompt = PB.build_recipe_prompt(portrait=portrait, costume=costume, pose=pose,
                                    style=a.style, extra_scene=a.scene)
    print("===== 提示词 =====\n" + prompt)
    if a.dry:
        print("\n[dry-run] 不提交。工作流：%s" % WF_API)
        return
    generate_styling(a.persona, a.costume, a.pose, prompt, w=a.w, h=a.h,
                     tag=a.tag, subdir=a.subdir, save_dir=a.save_dir)


if __name__ == "__main__":
    main()
