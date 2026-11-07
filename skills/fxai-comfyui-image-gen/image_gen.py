# -*- coding: utf-8 -*-
# =====================================================================
# 本地 ComfyUI 生图核心模块（用户级技能 fxai-comfyui-image-gen 的唯一真相源）
# ---------------------------------------------------------------------
# 本技能委托公共客户端 fxai-workflow/comfy_client.py 完成 ComfyUI 提交/轮询/下载
#（占位符工作流提交 → /prompt → /fxai/tasks/result 轮询 → 经 HTTP 接口下载产物）。
#
# 模型维度：千问2.1 图片处理在注册表里登记为 slug "qwen21"，任务 "scene"。
# generate_image(..., model=None) → 用该任务默认模型（qwen21）；
# generate_image(..., model="xxx") → 切到指定模型（未来加工作流即可用）。
# 工作流 JSON 随包内置在 fxai-workflow/（命名 img-qwen21-process.json）。
# =====================================================================

import os
import sys
import time

# 共享 ComfyUI 客户端（与 fxai-* 技能同级的公共目录 fxai-workflow/）
_WF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fxai-workflow")
if _WF not in sys.path:
    sys.path.insert(0, _WF)
import comfy_client as C

# 复用客户端常量与函数（单一真相源）
WF_IMG = C.WF_IMG
REF_WOMAN = C.REF_WOMAN
REF_MAN = C.REF_MAN
OUT_DIR = C.OUT_DIR
COMFY = C.COMFY  # 传参换服务器走 C.comfy_url(url)，不传就是配置里那份
resolve_wf = C.resolve_wf
submit_workflow = C.submit_workflow
require_comfyui = C.require_comfyui
prefer_local = C.prefer_local
_role_ref = C._role_ref
check_outfit = C.check_outfit  # 穿搭/发型硬校验：唯一真相源在 comfy_client，勿在本模块另抄词表
check_image_refs = C.check_image_refs  # 多参考图引用标签硬校验：传 N 张图提示词就必须有 N 个 <imageN>
_seed_rand = C._seed_rand
_download = C._download

# 技能默认本地落点：跟随调用者工作目录（成品经 HTTP 下载到这里）。
# 建议从「项目目录」运行，落点即项目目录；技能目录只放代码，生成产物与打包内容都留在项目目录里。
DEFAULT_SAVE_DIR = os.getcwd()


# =====================================================================
# 1) 人物参考图约定：一律用自有角色，禁用他人肖像
# ref_for 保留为兼容导出（本项目 gen_ref.py 已有独立副本，这里仅作别名）。
# =====================================================================
def ref_for(role=None):
    """角色参考图相对路径（喂给工作流节点，由 ComfyUI 服务端解析，不外漏本机目录）。
role: None/""         -> 纯场景，无人物参考（返回空串）
     "woman"/"女"/省略 -> 女性 000（默认）
     "man"/"男"        -> 男性 001
     "both"/"双人"/... -> 001,000（双人同框）
"""
    return _role_ref(role)


# =====================================================================
# 2) 产物完整性校验
# =====================================================================
def png_ok(path):
    """文件是否为「写完整的」PNG：末尾含 IEND 结束块。"""
    try:
        if os.path.getsize(path) < 100:
            return False
        with open(path, "rb") as f:
            f.seek(-16, 2)
            tail = f.read()
        return b"IEND" in tail
    except Exception:
        return False


# =====================================================================
# 3) 核心生图函数（唯一入口）
# =====================================================================
def _clamp_size(v, name="width"):
    """把画布边长收敛到工作流允许范围：512–4096 且 32 的整数倍。

直接把 900x383 这种尺寸塞进占位符，ComfyUI 会以
`value_smaller_than_min: Value 383 smaller than min of 512` 拒掉整张图
（HTTP 400，且报错信息藏在 node_errors 里，不直观）。宁可悄悄修正，
也别让用户为一张封面图白跑一趟。
"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError("%s 必须是整数像素，收到 %r" % (name, v))
    before = n
    n = max(512, min(4096, n))
    n = (n // 32) * 32
    if before != n:
        print("[gen] %s %d → %d（工作流要求 512~4096 且 32 的整数倍）" % (name, before, n))
    return n


# =====================================================================
# 2.5) 穿搭/发型硬校验：唯一真相源是 comfy_client.check_outfit（顶部已做别名引用）
#     词表与校验逻辑一律走公共件，本模块只保留引用，词表以公共件为准。
# 自有角色参考图 000/001 是「裸肩头像原始图」，只固定五官/角色一致性；衣服/发型全靠提示词补。
# 因此规则**只针对「直接引用角色底图」这一种情况**（role=woman/man/both → 挂 000/001）：
#   ①【穿搭/服装】词（缺 → 报错）；②【头型/发型】词（缺 → 报错）；③ 发饰/配饰（缺 → 告警建议）。
# 其他情况一律免检：
#   · 纯场景图 role=None（画面里没人）；
#   · 二次生成 / 场景融合——输入本身就是「已经穿好衣服的角色图」，不必再描述穿搭；
#     这种也走 role=None（或显式传 allow_no_outfit=True）。
# =====================================================================
# （词表 _OUTFIT_WORDS / _HAIR_WORDS / _HAIR_ACC_WORDS 与校验函数已下沉到公共件
#  comfy_client，本模块经顶部别名 check_outfit = C.check_outfit 引用，只留别名。）


def generate_image(prompt, role="woman", width=1280, height=720,
                   tag=None, comfy_subdir="skill_gen", save_dir=None,
                   timeout=1800, model=None, allow_no_outfit=False):
    """提交一张中文提示词图到 ComfyUI（经共享客户端 comfy_client），返回本地 PNG 绝对路径。

参数：
  prompt      : 中文提示词（千问2.1 原生中文理解，无需翻译成英文）
  role        : "woman"(默认)/"man"/"both"/None(纯场景)。人物图务必指定，固定自有角色五官
  width/height: 像素。公众号横幅建议 1280x720
  tag         : 输出文件名（不含扩展名）；None 则自动用时间戳
  comfy_subdir: ComfyUI 输出根下的子目录名（节点117「目录」）
  save_dir    : 本地成品落点；None 则用当前工作目录（建议从项目目录运行）
  timeout     : 等待生成完成的秒数（默认 1800；本机单卡，任务排在长渲染后面时要留够）
  model       : 指定模型 slug（如未来 "kling"）；None=该任务默认模型（qwen21）
  allow_no_outfit: True 才允许 role≠None 却无穿搭描述（默认 False：缺穿搭直接报错）
  引用标签铁律：传 N 张参考图（如 role="both"=2 张），提示词必须逐个包含 <image1>..<imageN>，
  把每张图显式绑到具体人物/元素上（编号=参考图列表顺序，both 时 <image1>=男主、<image2>=女主）；
  缺标签会被 check_image_refs 硬拦（多图强制，单图告警）。
返回：本地 PNG 路径字符串；失败抛 RuntimeError
"""
    # 硬校验：人物图必须写穿搭（参考图是裸肩头像，衣服靠提示词补）
    check_outfit(prompt, role, allow_no_outfit)
    # 硬校验：传了 N 张参考图，提示词就必须有 N 个引用标签 <image1>..<imageN>（缺一个都不行）
    check_image_refs(prompt, role)
    # 本机优先：本机 ComfyUI 探得通就不碰云（不开机、不计费）；探不通才由 require_comfyui 走云自动开机
    prefer_local()
    # 云上实例关着的时候，require_comfyui 内部会先让 xgy_guard 探活 + 自动开机，再校验在线
    require_comfyui()
    if tag is None:
        tag = "img_%d" % int(time.time())
    save_dir = save_dir or DEFAULT_SAVE_DIR
    width = _clamp_size(width, "width")
    height = _clamp_size(height, "height")

    wf_path = resolve_wf("scene", model)  # model=None → 任务默认模型（qwen21 → img-qwen21-process.json）
    params = {
        "fxai_prompt": prompt,
        "fxai_imageinput": _role_ref(role),
        "fxai_width": width,
        "fxai_height": height,
        "fxai_outputdir": comfy_subdir,
        # 服务端固定文件序号（配合「启用文件名覆盖」实现同名覆盖）；
        # 本地文件名由 tag 控制，服务端序号只需唯一，用随机值即可让并发任务各自落到不同名。
        "fxai_filename_index": _seed_rand(),
        "fxai_seed": _seed_rand(),
    }
    url = submit_workflow(wf_path, params, tag="img:" + tag, kind="scene", timeout=timeout)
    dst = os.path.join(save_dir, tag + ".png")
    _download(url, dst)
    if not png_ok(dst):
        raise RuntimeError("下载完成但 PNG 不完整(无 IEND)，疑似半张图：%s" % dst)
    print("[%s] DONE -> %s (%d bytes)" % (tag, dst, os.path.getsize(dst)), flush=True)
    return dst


# =====================================================================
# 4) CLI 入口：python image_gen.py "提示词" --role woman --w 1280 --h 720 --tag xxx
# =====================================================================
def main():
    import argparse
    ap = argparse.ArgumentParser(description="本地 ComfyUI 中文提示词生图（经 fxai-workflow/comfy_client）")
    ap.add_argument("prompt", help="中文提示词")
    ap.add_argument("--role", default="woman", help="woman/man/both/空(纯场景)")
    ap.add_argument("--w", type=int, default=1280)
    ap.add_argument("--h", type=int, default=720)
    ap.add_argument("--tag", default=None, help="输出文件名(无扩展名)")
    ap.add_argument("--subdir", default="skill_gen", help="ComfyUI 输出子目录")
    ap.add_argument("--model", default=None, help="模型 slug（默认 qwen21）")
    ap.add_argument("--no-outfit", action="store_true",
                    help="允许人物图无穿搭描述（默认缺穿搭会报错）")
    a = ap.parse_args()
    role = a.role if a.role else None
    out = generate_image(a.prompt, role=role, width=a.w, height=a.h,
                         tag=a.tag, comfy_subdir=a.subdir, model=a.model,
                         allow_no_outfit=a.no_outfit)
    print("OUTPUT:", out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
