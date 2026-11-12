# -*- coding: utf-8 -*-
# =====================================================================
# fxai-workflow —— 共享 ComfyUI 客户端（所有 fxai-* 生图/生乐/生视频技能共用）
# 位置：与 fxai-* 技能同级的公共目录 fxai-workflow/（非技能，无 SKILL.md）。
# 职责：封装「占位符版工作流提交 → /prompt → /fxai/tasks/result 轮询 →
# 经 HTTP 接口下载产物」的全流程，供 fxai-fourview-gen / fxai-scene-gen /
# fxai-mv-pipeline / fxai-comfyui-image-gen 等技能 import 复用。
# 调用模式（产物「全部走 HTTP 接口」，不读 ComfyUI 本地目录，本地/远端通用）：
# 1) 把 API 格式工作流 POST 到 /prompt
# 2) 用 /fxai/tasks/result 轮询结果 URL 令牌（"type/dir|file"）
# 3) 把令牌交给统一的 GET /fxai/down/file?url=<令牌> 端点经 HTTP 下载到本地
# 工作流 JSON 随包内置在 fxai-workflow/（命名：<任务>-<模型>-<变体>.json，如 mv-minimax-single.json / img-qwen21-process.json），
# 内部 fxai_ 占位符固定不变。
# =====================================================================
import difflib
import json
import os
import re
import random
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

# ---------- 本机代理绕过（127.0.0.1 直连，不经代理） ----------
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fxai-common"))
import fxai_common as fxc  # noqa: E402

fxc.allow_local()
_OPENER = fxc.opener()
norm_url = fxc.norm_url

# ---------- 环境配置（可被用户级全局部署配置 fxai_global.json 覆盖） ----------
# 部署/环境项（ComfyUI 地址、ffmpeg、whisper 等）一律放进
# 用户级全局配置 ~/.workbuddy/fxai_global.json（由 setup_env.py 探测生成），
# 不随技能包分发、不写死进代码。可用环境变量 FXAI_GLOBAL_CONFIG 覆盖其路径。
# 无该文件时回退到下方默认值。
# 本模块位于 fxai-workflow/，故「工作流目录」即自身所在目录。
FXAI_WORKFLOW_DIR = os.path.dirname(os.path.abspath(__file__))

_GLOBAL_CFG_PATH = os.environ.get(
    "FXAI_GLOBAL_CONFIG",
    os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))


def _load_cfg():
    """读取用户级全局部署配置（fxai_global.json）。"""
    try:
        with open(_GLOBAL_CFG_PATH, encoding="utf-8") as _f:
            return json.load(_f)
    except Exception:
        return {}


CFG = _load_cfg()


def _cfg(key, default):
    return CFG.get(key, default)


COMFY = _cfg("comfy_url", "http://127.0.0.1:8188")


def comfy_url(override=""):
    """ComfyUI地址，按优先级取：**传进来的 > 全局配置 > 本机默认**。

    同一条任务里可能要用到不同的ComfyUI服务器，传地址进来即可，不必改配置文件；
    不传就退回配置里的那份。
    所有对外暴露的地址都经过 `norm_url` 规范化，`//` 与反斜杠统一后再进请求。

    这里**不读环境变量**：临时换服务器走参数。
    """
    return norm_url(override) or norm_url(COMFY)


# ---------- 本机优先（默认策略）----------
# 能在本机跑的一律走本机，本机没启动 / 忙到探不通才用云。
LOCAL_URL = "http://127.0.0.1:8188"


def prefer_local(timeout=2.0, quiet=False):
    """探本机 ComfyUI：探得通就把全局地址切成本机（本进程内之后的调用都走本机）。

    返回 True = 已切本机；返回 False = 本机没起/没就绪，调用方照旧走配置里的云地址
    （require_comfyui 会按云地址自动开机）。任何生图/生成类任务开工前先调它。
    """
    if not check_comfyui(LOCAL_URL, timeout=timeout):
        if not quiet:
            print("[comfy] 本机 ComfyUI 未就绪，按配置走云端", flush=True)
        return False
    global COMFY
    COMFY = LOCAL_URL
    if not quiet:
        print("[comfy] ✓ 本机 ComfyUI 已就绪，本次走本机（不烧云端 GPU）", flush=True)
    return True


def prefer_cloud(quiet=False):
    """把全局 ComfyUI 地址切到**云端容器**（配置项 comfy_cloud_url），强制走线上。

    与 prefer_local 相反：不管本机 8188 是否在跑，都指向云端实例，让 require_comfyui
    按云地址自动开机 + 等就绪。`--no-local`（不碰本机盘、强制线上）走这里。
    """
    cloud = _cfg("comfy_cloud_url", "")
    if not cloud:
        raise RuntimeError(
            "⚠️ 未配置 comfy_cloud_url：fxai_global.json 里加一行"
            ' "comfy_cloud_url": "https://<实例ID>-8188.container.x-gpu.com" 即可强制走线上。')
    global COMFY
    COMFY = norm_url(cloud)
    if not quiet:
        print("[comfy] ✓ 已切到云端 ComfyUI：%s（不碰本机）" % COMFY, flush=True)
    return COMFY


def down_base(url=None):
    """产物下载基址 `/fxai/down/file` 所在主机（同样按 comfy_url() 的优先级解析）。"""
    return comfy_url(url).rstrip("/") + "/fxai/down/file"


# ---------- 换人 / 重绘的分辨率档位（按显存给默认值）----------
# 口径（用户定，2026-11-11）：**本机 16G 及以下卡长边固定 960；>16G（24G 卡）用 1280；
# 线上实例（仙宫云等）按 1280。** 显式 --max-side 永远优先，这里只决定「没指定时」的默认值。
# 判断依据是 nvidia-smi 报的单卡总显存，不按显卡型号猜：
#   16G 卡实测 16311 MiB、24G 卡实测 24564 MiB，取 20G 作分界即可稳定区分。
# 注意：工作流按 64 的倍数对齐，960 档在 720×1386 源上落 480×960，1280 档落 640×1280。
SIDE_TIER_16G = 960
SIDE_TIER_24G = 1280
VRAM_TIER_MIB = 20 * 1024


def gpu_vram_mib():
    """本机单卡最大显存（MiB）。取不到返回 None —— 不猜、不按型号编。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    best = None
    for line in out.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 2 and parts[1].isdigit():
            mib = int(parts[1])
            if best is None or mib > best:
                best = mib
    return best


def default_max_side(url="", quiet=False):
    """未显式指定长边时的默认档位：本机按显存，线上按 1280。

    ≤16G（含读不到显存）→ 960；≥20G（24G 卡）→ 1280；非本机（仙宫云等）→ 1280。
    读不到显存时**取保守档 960**：宁可少一点分辨率，也不让 16G 卡去跑 1280 炸片。
    """
    base = comfy_url(url)
    host = (urllib.parse.urlparse(base).hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1", ""):
        if not quiet:
            print("[side] 线上实例（%s）→ 默认长边 %d" % (host, SIDE_TIER_24G), flush=True)
        return SIDE_TIER_24G
    mib = gpu_vram_mib()
    if mib is None:
        if not quiet:
            print("[side] 读不到本机显存，按 16G 及以下档取 %d（要更高请显式 --max-side）"
                  % SIDE_TIER_16G, flush=True)
        return SIDE_TIER_16G
    if mib >= VRAM_TIER_MIB:
        if not quiet:
            print("[side] 本机显存 %d MiB（≥20G，24G 档）→ 默认长边 %d"
                  % (mib, SIDE_TIER_24G), flush=True)
        return SIDE_TIER_24G
    if not quiet:
        print("[side] 本机显存 %d MiB（16G 档）→ 默认长边 %d" % (mib, SIDE_TIER_16G), flush=True)
    return SIDE_TIER_16G


# 仙宫云看门狗：见 xgy_guard.py。云上的实例通常关机着（释放 GPU 后停计费），
# 开工前先探活、探不通就自动 boot（开工 = 本模块 require_comfyui）；
# 整套流程跑完（所有任务都做完那一步）才调 finish() 问用户是否关实例，中间每跑完一个子任务都跳过。
# 本机 / 其它地址两头都不管：不自动开机、也不问关机。
import xgy_guard as XGY  # noqa: E402
# 产物下载「全部走 HTTP 接口」：用 comfy_url 作基址，把 /fxai/tasks/result 返回的
# url 令牌交给统一的 GET /fxai/down/file?url=<令牌> 端点下载（本地/远端通用）。
# 工作流 JSON 随包内置在 fxai-workflow/（公共目录，默认从相对路径加载）；
# 如需使用包外自定义工作流，可在 fxai_global.json 用 workflow_dir 覆盖。
DL = _cfg("workflow_dir", FXAI_WORKFLOW_DIR)
# 产物/查询缓存一律落在「技能目录之外」的用户级缓存。
# 真正下载落点由各调用方显式传入 dst（通常是项目目录），这里只作查询结果的临时缓存。
OUT_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_cache")
os.makedirs(OUT_DIR, exist_ok=True)

# 工作流文件：去前缀命名 + 显式模型 token（<任务>-<模型>-<变体>.json）。
# 内部 fxai_xxx 占位符固定不变。
# 模型维度集中登记在下方 WORKFLOWS 注册表；resolve_wf(task, model=None) 负责解析路径。
# · mv-minimax-single.json = MiniMax Hailuo H3 单场景
# · img-qwen21-process.json = 千问2.1 场景图
# · music-yue2.json = Yue2 音乐生成
# · vc-melband-vocalsep-api.json = Mel-Band RoFormer 人声分离

# ---------- 模型注册表：任务 -> {模型slug: 工作流文件名} ----------
# slug 约定：minimax = MiniMax Hailuo H3（视频）、qwen21 = 千问2.1（图片）、yue2 = Yue2（音乐）。
WORKFLOWS = {
    # 音乐生成：yue2（本地 YuE2，结构标签，无男女声标签）
    #           minimax3（MiniMax Music 3，支持男女对唱——人声性别/音色写在 Caption 的 Vocal Details）
    "music": {"yue2": "music-yue2.json", "minimax3": "music-minimax3-api.json"},
    "charview4": {"qwen21": "img-qwen21-fourview.json"},
    "charview3": {"qwen21": "img-qwen21-threeview.json"},
    "scene": {"qwen21": "img-qwen21-process.json"},
    "single": {"minimax": "mv-minimax-single.json"},
    "multi": {"minimax": "mv-minimax-multi.json"},
    "multiref": {"minimax": "mv-minimax-multiref.json"},
    "shortdrama": {"minimax": "shortdrama-minimax-filmdrama-api.json"},
    # 人声分离（服务端模型工作流，纯 HTTP 调用；本地不装 demucs/torch）
    # melband = Mel-Band RoFormer（MelBandRoformer_fp32.safetensors，当前服务端实际模型，默认）
    "vocalsep": {
        "melband": "vc-melband-vocalsep-api.json",
        "uvr": "vc-uvr-vocalsep-api.json",
    },
    # 人脸精修（本机 ComfyUI · 分段→FxAiFaceRestoreLiteV2 全脸轻量链→合并，视频整片逐段精修）
    # lite = 自研超分轻量链（超分倍数 4），调用入口见 fxai-face-edit/scripts/face_restore.py
    "facerestore": {"lite": "face-restore-lite.json"},
    # 角色替换（本机 ComfyUI · 分段→MiniMax H3 图生视频换人→合并，整片换人/换服装）
    # h3 = MiniMax H3 singularity 底座 + turbo8step / character_swap / Motion_Repair 三条 LoRA
    # 调用入口见 fxai-face-edit/scripts/char_swap.py
    "charswap": {"h3": "h3-char-swap-api.json"},
    # 视频超分（本机 ComfyUI · 分段→FxAiVideoUpscale 自研超分→FxAiFaceRestoreLiteV2 修脸/换脸→合并）
    # fvsr = 自研超分权重（models/FlashVSR/），任意倍数 1.0~4.0，精修恒在链路里
    # 调用入口见 fxai-video-upscale/scripts/video_upscale.py
    "upscale": {"fvsr": "upscale-fvsr.json"},
    # 图片指令编辑（本机 ComfyUI · MOSS-Image 底座 + 编辑 LoRA，喂一张已有的图 + 一句改法，出改后的图）
    # moss = moss_image_sft_bf16 + moss_image_edit_lora_rank_64，CLIP qwen3.5_9b、VAE flux2-vae
    # 与 scene（千问2.1 按提示词重画）的分工：imgedit 是「在原图上改」（保留构图/主体），scene 是「照描述画新的」
    # 输入 fxai_imageinput 必须是服务端相对路径（上一步产物的 rel，如 mv_scene/scene1.png），本机绝对路径服务端读不到
    # fxai_prompt 写「改什么、改成什么」（如「把天空改成黄昏，其余不动」），不是整幅画面描述
    # 无独立技能入口：调用方直接 submit_workflow(resolve_wf("imgedit", "moss"), params, kind="imgedit")
    "imgedit": {"moss": "img-moss-process-api.json"},
}
# 每任务默认模型（代码默认 = 注册表第一项）。
DEFAULT_MODEL = {t: next(iter(m)) for t, m in WORKFLOWS.items()}
# 用户可在用户级全局部署配置 fxai_global.json 用 `workflow_models` 覆盖每任务默认模型：
# {"workflow_models": {"single": "minimax", "music": "yue2", ...}}
_MODEL_OVERRIDE = _cfg("workflow_models", {})


def resolve_wf(task, model=None):
    '按 任务+模型 解析工作流路径。\nmodel=None → 用默认（CFG 覆盖 > 代码默认）。\n找不到任务/模型时抛清晰错误，列出该任务可用模型。'


    models = WORKFLOWS.get(task)
    if not models:
        raise ValueError("未知任务 %r，已登记任务: %s" % (task, ", ".join(sorted(WORKFLOWS))))
    m = model or _MODEL_OVERRIDE.get(task) or DEFAULT_MODEL.get(task)
    if m not in models:
        raise ValueError("任务 %s 无模型 %r，可用模型: %s" % (task, m, ", ".join(sorted(models))))
    path = os.path.join(DL, models[m])
    # 占位模板防呆：vc-uvr-vocalsep-api.json 之类只登记了 TODO 节点的工作流一旦被选中，
    # 提交会得到一句看不懂的 400（unknown class_type）。这里提前拦下并给出可执行的改法。
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        txt = ""
    if "TODO" in txt and "_TEMPLATE_" in txt:
        raise ValueError(
            "工作流 %s 只是「占位模板」，里面的节点是 TODO_* 占位，服务端没有这个节点，提交必失败。\n"
            "  改成可跑的两条路，任选其一：\n"
            "  1) 用现成的人声分离：%s（Mel-Band RoFormer，兼容 UVR 的分轨需求）\n"
            "  2) 真要换分离模型：复制 %s 改 class_type/模型文件为服务端实际有的节点（"
            "先用 object_info() 查），再在 WORKFLOWS 里登记。"
            % (models[m], "vc-melband-vocalsep-api.json", models[m]))
    return path


# 向后兼容别名：WF_* 指向各任务「默认模型」工作流（produce_song.py 等仍可直用）。
WF_IMG = resolve_wf("scene")
WF_THREEVIEW = resolve_wf("charview3")
WF_FOURVIEW = resolve_wf("charview4")
WF_MUSIC = resolve_wf("music")
WF_MV_MULTI = resolve_wf("multi")
WF_MV_MULTIREF= resolve_wf("multiref")
WF_MV_SINGLE = _cfg("wf_mv_single", resolve_wf("single")) # MiniMax单场景（可经 fxai_global.json 的 wf_mv_single 覆盖）
WF_VOCALSEP = resolve_wf("vocalsep")  # 人声分离（服务端模型工作流）

# ---------- 占位符传值机制（镜像 C# ComfyUIAdapter.submitWorkflowTask） ----------
# 工作流 JSON 内预埋 fxai_xxx 占位符；提交前按「非字符串 / JSON值 / 普通字符串」三类规则做文本替换，
# 缺必填参数直接拦截；nodeconfig.json 提供类型规范做提交前格式校验。
_NODECFG_PATH = os.path.join(FXAI_WORKFLOW_DIR, "nodeconfig.json")


def _load_nodeconfig():
    try:
        with open(_NODECFG_PATH, encoding="utf-8") as _f:
            return json.load(_f)
    except Exception:
        return {"placeholders": {}, "non_string_placeholders": []}


_NODECFG = _load_nodeconfig()
_NON_STRING = set(_NODECFG.get("non_string_placeholders", []))
_PLACEHOLDERS = _NODECFG.get("placeholders", {})
_PH_RE = re.compile(r"fxai_\w+")


def _seed_rand():
    return random.randint(1, 2 ** 31 - 1)


def _is_json(text):
    if not isinstance(text, str) or not text.strip():
        return False
    t = text.strip()
    if (t.startswith("[") and t.endswith("]")) or (t.startswith("{") and t.endswith("}")):
        try:
            json.loads(t); return True
        except Exception:
            return False
    return False


def _to_replacement(key, val):
    '把参数值转成替换文本（与 C# 三类规则一致）。\n\n★ 分类依据是「Python 值的真实类型」，而非「字符串内容是否像 JSON」——\n否则 multiref 的 fxai_prompt（一个 JSON 格式字符串）会被误判成原始 JSON，\n裸插成 list，触发 ComfyUI 的 bad_linked_input\n（lines_data / 关键帧JSON 都是 STRING 输入，必须插成带引号的字符串字面量）。\n- _NON_STRING 占位符（数值/布尔）：直接裸插（无引号）。\n- 其余一律作为字符串：用 json.dumps 包成带引号+转义的字符串字面量；\n  若调用方传的是 list/dict（真实 JSON 对象），则先序列化再包成字符串（双层 dumps）。\n'









    if key in _NON_STRING:
        if isinstance(val, bool):
            return "true" if val else "false"
        return str(val)
    if isinstance(val, (list, dict)):
        return json.dumps(json.dumps(val, ensure_ascii=False), ensure_ascii=False)
    return json.dumps(str(val), ensure_ascii=False)


def _validate_placeholder_params(pdict, tag="run"):
    """提交前格式校验：按 nodeconfig 占位符类型检查（integer/boolean/string）。"""
    for key, val in pdict.items():
        spec = _PLACEHOLDERS.get(key)
        if not spec:
            continue
        t = spec.get("type")
        if t == "integer":
            try:
                int(val)
            except (TypeError, ValueError):
                raise ValueError("[%s] 参数 %s 应为整数，实际: %r" % (tag, key, val))
        elif t == "boolean":
            if isinstance(val, bool):
                continue
            if str(val).lower() not in ("true", "false"):
                raise ValueError("[%s] 参数 %s 应为布尔，实际: %r" % (tag, key, val))
        # string: 任意
    return True


# ---------- 服务器模型列表 → 工作流模型名自动纠正 ----------
# 场景：服务器升级后模型文件名变了（如 xxx_int8 → xxx_v2_int8），工作流 JSON 里写死的旧名字
# 会被 ComfyUI 以 value_not_in_list 拒收。这里在提交前、以及被拒后，把名字改成服务器上实际有的那个。
# 「出错才改」——服务器名字没变时本机制静默零动作，不写文件、不提示。
#
# 只认模型文件名类输入，不碰采样器/调度器这类改了就变生成行为的枚举：
# 字段名在 MODEL_NAME_FIELDS 里，或值以模型文件后缀结尾。
MODEL_NAME_FIELDS = {
    "ckpt_name", "unet_name", "lora_name", "vae_name", "clip_name",
    "control_net_name", "style_model_name", "upscale_model_name", "gligen_name",
    "model_name", "diffusion_model", "clip_vision_name", "embedding_name",
}
MODEL_FILE_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".sft")

_OBJINFO_FULL = {}   # url -> 全量 /object_info
_OBJINFO_NODE = {}   # (url, class_type) -> 单节点 object_info
_MODEL_OPTS = {}     # (url, class_type, field) -> [可选值...]
# 版本号与精度标记：用来判断「是不是同一个模型的升级版」
_VER_RE = re.compile(r"[ _\-.]v\d+(?=$|[ _\-.])")
_PREC_RE = re.compile(
    r"(?:^|[_\-.])(int8|int4|fp8|fp16|fp32|bf16|fp4|e4m3fn|e5m2|awq|nvfp4|gguf)(?=[_\-.]|$)")


def object_info(node=None, url=None, refresh=False):
    '''GET /object_info（或 /object_info/<node>），进程内缓存。失败返回 {} —— 绝不因它挡住出片。'''
    url = comfy_url(url)
    try:
        if node is None:
            if refresh or url not in _OBJINFO_FULL:
                with _http("GET", url + "/object_info", timeout=120) as r:
                    _OBJINFO_FULL[url] = json.loads(r.read().decode("utf-8", "replace"))
            return _OBJINFO_FULL[url]
        key = (url, node)
        if refresh or key not in _OBJINFO_NODE:
            with _http("GET", url + "/object_info/" + urllib.parse.quote(node), timeout=60) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            if not isinstance(d, dict) or not d:
                return {}
            _OBJINFO_NODE[key] = d.get(node) or next(iter(d.values()))
        return _OBJINFO_NODE[key]
    except Exception:
        return {}


def model_options(class_type, field, url=None):
    '''某节点某输入在服务器上的可选值列表；不是下拉枚举（自由输入）时返回 []。'''
    key = (comfy_url(url), class_type, field)
    if key in _MODEL_OPTS:
        return _MODEL_OPTS[key]
    info = object_info(class_type, url) or {}
    inp = info.get("input") if isinstance(info, dict) else None
    opts = []
    for sect in ("required", "optional"):
        spec = ((inp or {}).get(sect) or {}).get(field)
        if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], (list, tuple)):
            opts = list(spec[0])
            break
    _MODEL_OPTS[key] = opts
    return opts


def _is_model_field(field, val):
    '''判定「这条输入是模型文件名」：字段名在白名单，或值本身是模型文件后缀。'''
    if not isinstance(val, str) or not val:
        return False
    return field in MODEL_NAME_FIELDS or val.lower().endswith(MODEL_FILE_EXTS)


def _norm_model(name):
    '''归一化：去目录、去扩展名、小写、抹平 -_ . 差异（大小写/分隔符变化不算改名）。'''
    s = str(name).replace("\\", "/").split("/")[-1].lower()
    for ext in MODEL_FILE_EXTS:
        if s.endswith(ext):
            s = s[: -len(ext)]
            break
    return re.sub(r"[\-_.\s]+", "", s)


def _model_family(name):
    '''族名：归一化后再抹掉版本号（v1/v2…），用于识别「同一模型的升级版」。'''
    s = str(name).replace("\\", "/").split("/")[-1].lower()
    for ext in MODEL_FILE_EXTS:
        if s.endswith(ext):
            s = s[: -len(ext)]
            break
    s = _VER_RE.sub("", s)
    return re.sub(r"[\-_.\s]+", "", s)


def _model_prec(name):
    '''精度/量化标记（int8/fp16/bf16…），无则空串。'''
    m = _PREC_RE.search(str(name).lower())
    return m.group(1) if m else ""


def _pick_model(want, cands):
    '''在服务器候选里找 want 的对应项。返回 (新名字 or None, 相关候选)。

    四档，逐档放宽；**只有能唯一确定时才返回新名字**（会自动替换）：
      1. 名字完全一致            → 直接用
      2. 归一化后一致且唯一      → 大小写/分隔符差异，安全替换
      3. 同族 + 同精度且唯一     → 版本升级改名（xxx_int8 → xxx_v2_int8），安全替换
      4. 同精度 + 归一化后互为前缀且只差 ≤8 字符 → 加了/去了 _old、_v2、_convrot 这类尾缀的改名
    第 4 档要求「精度一致 + 一个完整包含另一个」，pruned/turbo 这类不同变体不会被误认成新版。
    精度不同（int8 ↔ fp8）永远不自动换：换错会 OOM 或掉速，只把候选摆出来让人定。
    '''
    if not cands:
        return None, []
    if want in cands:
        return want, [want]
    wn = _norm_model(want)
    hit = [c for c in cands if _norm_model(c) == wn]
    if len(hit) == 1:
        return hit[0], hit
    if len(hit) > 1:
        return None, hit
    fam = [c for c in cands
           if _model_family(c) == _model_family(want) and _model_prec(c) == _model_prec(want)]
    if len(fam) == 1:
        return fam[0], fam
    if fam:
        return None, fam
    # 同精度里找「归一化后互为前缀、只差几个字符」的（加了/去了 _old、_v2、_convrot 这类尾缀）。
    # 判据比相似度更严：必须一个完整包含另一个，pruned/turbo 这类不同变体不算新版。
    wp = _model_prec(want)
    close = []
    for c in cands:
        if _model_prec(c) != wp:
            continue
        cn = _norm_model(c)
        a, b = (wn, cn) if len(wn) >= len(cn) else (cn, wn)
        if a != b and a.startswith(b) and len(a) - len(b) <= 8:
            close.append(str(c))
    if len(close) == 1:
        return close[0], close
    if close:
        return None, close
    scored = sorted(((difflib.SequenceMatcher(None, wn, _norm_model(c)).ratio(), str(c))
                     for c in cands if _model_prec(c) == _model_prec(want)), reverse=True)
    top = [c for _r, c in scored]
    return None, top[:6] or difflib.get_close_matches(str(want), [str(c) for c in cands], n=6, cutoff=0.4)


def _rebuild_instance_hint(url=None, pre="  "):
    '''V1.9 起模型与工作流整套换版：服务端缺模型时，强制提示「必须用新镜像重新创建实例」。

    模型与节点装在云端实例的系统盘里，技能包升级改不到旧实例；重下模型、改工作流里的
    模型名、重启 ComfyUI 都补不齐。这里只提示与给出创建入口，绝不自动创建 ——
    deploy-image 会实时扣费，必须使用者点头才真建（见 fxai-xiangongyun-deploy 铁律 2）。
    '''
    u = str(url or "")
    if not u:
        try:
            u = str(comfy_url() or "")
        except Exception:
            u = ""
    cloud = ".x-gpu.com" in u
    tail = "（仙宫云同样必须重新创建实例）" if cloud else ""
    return "\n".join([
        "",
        "%s🚨 [V1.9 必读] 提交用到的模型在服务端找不到对应项 —— 若这台是 V1.9 之前用旧镜像创建的实例，" % pre,
        "%s   那就是旧实例缺新模型：重下模型 / 改工作流模型名 / 重启 ComfyUI 都补不齐。" % pre,
        "%s   ✅ 必须用 V1.9 对应的新镜像重新创建一个新的实例%s，" % (pre, tail),
        "%s      建好把新实例 ID 写进全局配置（xgy_guard.py use <新实例ID>），comfy_url 指向新实例再出片。" % pre,
        "%s      入口：fxai-xiangongyun-deploy → xgy_api.py deploy-image <新镜像ID> --yes" % pre,
        "%s      ⚠️ 建实例会实时扣费，须用户确认后才真建；本提示不会自动创建。" % pre,
        "%s      本机部署不受此约束：按 fxai-xiangongyun-deploy/assets/models.json 补齐模型即可。" % pre,
    ])


def _fmt_model_fix(changes, unresolved, tag="", url=None):
    '''把改动/未决项格式化成给人看的日志行。

    unresolved（服务器上找不到对应模型）时追加「旧镜像必须重建实例」的强制提示，
    让「提交返回模型没有了」这一刻就能看到该做什么，不靠人去翻规范。
    '''
    pre = ("  [%s] " % tag) if tag else "  "
    out = []
    for nid, cls, field, old, new in changes:
        out.append("%s[模型适配] %s#%s %s：%s → %s" % (pre, cls, nid, field, old, new))
    for nid, cls, field, old, cands in unresolved:
        out.append("%s[模型适配] ⚠️ %s#%s %s 在服务器上找不到对应项：%s\n"
                   "%s           服务器候选：%s"
                   % (pre, cls, nid, field, old, pre, ", ".join(cands[:8]) or "(无)"))
    if unresolved:
        out.append(_rebuild_instance_hint(url, pre))
    return "\n".join(out)


def repair_models(wf, url=None, quiet=False, tag=""):
    '''扫 wf 里所有模型文件名输入，值在服务器上不存在就换成对应的那个（就地改 wf）。

    返回 (changes, unresolved)：changes=[(node_id, class_type, field, old, new)]，
    unresolved=[(node_id, class_type, field, old, [候选...])]（拿不准的不动，只报候选）。
    服务器/节点信息取不到（离线、自定义节点没装）时静默跳过，不影响正常提交。
    '''
    changes, unresolved = [], []
    for nid, node in (wf or {}).items():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type") or ""
        inputs = node.get("inputs")
        if not cls or not isinstance(inputs, dict):
            continue
        for field, val in list(inputs.items()):
            if not _is_model_field(field, val):
                continue
            opts = model_options(cls, field, url)
            if not opts or val in opts:
                continue
            new, cands = _pick_model(val, opts)
            if new and new != val:
                inputs[field] = new
                changes.append((nid, cls, field, val, new))
            else:
                unresolved.append((nid, cls, field, val, cands))
    if (changes or unresolved) and not quiet:
        print(_fmt_model_fix(changes, unresolved, tag, url=url), flush=True)
    return changes, unresolved


def repair_from_node_errors(wf, resp, url=None, quiet=False, tag=""):
    '''解析 /prompt 被拒响应里的 node_errors（value_not_in_list）并就地修 wf。

    比 repair_models 更准：ComfyUI 直接点名了节点、输入字段和它收到的值，
    连「自定义节点动态加载的模型字段」也能覆盖（这类节点可能不在 object_info 全量表里）。
    '''
    changes, unresolved = [], []
    for nid, ne in ((resp or {}).get("node_errors") or {}).items():
        cls = (ne or {}).get("class_type") or ""
        node = (wf or {}).get(nid)
        for err in ((ne or {}).get("errors") or []):
            if (err or {}).get("type") != "value_not_in_list":
                continue
            ex = err.get("extra_info") or {}
            field = ex.get("input_name") or ""
            old = ex.get("received_value")
            if not isinstance(node, dict) or not field:
                continue
            if old is None:
                old = (node.get("inputs") or {}).get(field)
            if not isinstance(old, str):
                continue
            if not cls:
                cls = node.get("class_type") or ""
            opts = model_options(cls, field, url)
            if not opts and not _is_model_field(field, old):
                continue   # 不是模型字段（采样器名之类）→ 不擅自改
            new, cands = _pick_model(old, opts)
            if new and new != old:
                node.setdefault("inputs", {})[field] = new
                changes.append((nid, cls, field, old, new))
            else:
                unresolved.append((nid, cls, field, old, cands))
    if (changes or unresolved) and not quiet:
        print(_fmt_model_fix(changes, unresolved, tag, url=url), flush=True)
    return changes, unresolved


def persist_model_fix(wf_path, changes):
    '''把改过的模型名写回工作流文件（fxai_ 占位符原样保留），改前先存 .bak。

    只做「模型名字面值 → 新名」的文本替换，不动占位符和其它内容。
    写失败只提示，不影响本次提交（内存里已经是新名字了）。
    '''
    if not changes or not wf_path or not os.path.isfile(wf_path):
        return False
    try:
        with open(wf_path, encoding="utf-8") as f:
            orig = f.read()
    except Exception:
        return False
    new_text = orig
    for _nid, _cls, _field, old, new in changes:
        new_text = new_text.replace('"%s"' % old, '"%s"' % new)
    if new_text == orig:
        return False
    try:
        shutil.copy2(wf_path, wf_path + ".bak")
        with open(wf_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        print("  [模型适配] 已回写 %s（改前的存为 .bak）" % os.path.basename(wf_path), flush=True)
        return True
    except Exception as e:
        print("  [模型适配] 回写工作流失败（本次仍按新名字提交）：%r" % e, flush=True)
        return False


def submit_workflow(wf_path, params, optional_keys=None, validate=True, tag="run",
                    kind=None, timeout=1800, poll=6):
    '占位符版提交：读工作流文本 → 正则扫占位符 → 校验 → 替换 → 提交 /prompt。\n参数：\n  wf_path: 占位符版工作流 JSON 路径\n  params:  {fxai_xxx: 值}（int/bool/str/list/dict 均可；字符串 JSON 视为 JSON 值）\n  optional_keys: 允许留空的占位符（缺省时替换为空串，不报缺失）\n返回：fxai 结果 url（同 run_workflow）。\n'






    with open(wf_path, encoding="utf-8") as _f:
        text = _f.read()
    pdict = {}
    for _k, _v in (params or {}).items():
        if _v is None:
            continue
        pdict[str(_k)] = _v
    # seed<1 → 随机（镜像 C#）
    if "fxai_seed" in pdict:
        try:
            if int(pdict["fxai_seed"]) < 1:
                pdict["fxai_seed"] = _seed_rand()
        except (TypeError, ValueError):
            pdict["fxai_seed"] = _seed_rand()
    # 扫描工作流内所有占位符，缺填/校验
    _found = set(_PH_RE.findall(text))
    _missing = []
    _opt = set(optional_keys or [])
    for _key in _found:
        if _key in pdict:
            continue
        if _key in _opt:
            pdict[_key] = ""
            continue
        # 兜底：沿用「工作流默认」（nodeconfig 的 default）。
        _spec = _PLACEHOLDERS.get(_key)
        if _spec and "default" in _spec:
            pdict[_key] = _spec["default"]
        else:
            _missing.append(_key)
    if _missing:
        raise ValueError("工作流 %s 缺少必填参数且无默认值: %s"
                         % (os.path.basename(wf_path), ", ".join(sorted(_missing))))
    if validate:
        _validate_placeholder_params(pdict, tag=tag)
    # 三类替换（镜像 C#）。⚠️ 字符串值必须用 json.dumps 转义（换行/引号等控制字符）。
    for _key in list(pdict.keys()):
        _rep = _to_replacement(_key, pdict[_key])
        text = text.replace('"%s"' % _key, _rep)
    _wf = json.loads(text)
    # ★ multiref 适配：当前 ComfyUI 的 FxAiMiniMaxVideoSaveV2 节点必填「保存目录」输入，
    # 但导出的工作流 JSON 常漏填（节点版本不匹配），提交会被拒 required_input_missing。
    # 这里按 class_type 自动注入 fxai_outputdir 值，不改工作流文件本身，对换工作流也自动适配。
    if kind == "multiref":
        _save_dir = str(pdict.get("fxai_outputdir", "mv_multiref"))
        for _nid, _node in _wf.items():
            if isinstance(_node, dict) and _node.get("class_type") == "FxAiMiniMaxVideoSaveV2":
                _node.setdefault("inputs", {})["保存目录"] = _save_dir
    # ★ facerestore 适配：FxAiReleaseResources 的必填输入「输入」（type *）在导出的工作流里常缺，
    # 服务端直接拒 required_input_missing。这里补成「接循环结束节点第 2 个输出」——与它
    # 「卸载所有模型」的接法一致，保证释放动作排在循环之后。不改工作流文件本身。
    if kind == "facerestore":
        _loop = next((_nid for _nid, _n in _wf.items()
                      if isinstance(_n, dict) and _n.get("class_type") == "FxAiForLoopEnd"), None)
        for _nid, _node in _wf.items():
            if isinstance(_node, dict) and _node.get("class_type") == "FxAiReleaseResources":
                _inp = _node.setdefault("inputs", {})
                if "输入" not in _inp and _loop:
                    _inp["输入"] = [_loop, 1]
    # ★ 模型名自动适配：服务器升级后文件名变了 → 换成服务器上实际有的那个，并写回工作流文件。
    # 服务器名字没变时这一步零动作（不打印、不写文件）。
    _ch, _un = repair_models(_wf, tag=tag)
    if _ch:
        persist_model_fix(wf_path, _ch)
    return run_workflow(_wf, timeout=timeout, poll=poll, tag=tag, kind=kind,
                        models_checked=True)


# 角色参考图（自有角色，肖像来源限定本技能自带的 000/001）。注意：这两个是【服务端相对路径】（fxai/image/ 下的 rel，
# 不是本机路径）。路径格式与取值判据见 fxai-standard/references/10-资源引用路径规范.md。
REF_WOMAN = "avatar/000.png"
REF_MAN = "avatar/001.png"

MAX_EDGE = 1300 # 生视频「最大边长」封顶固定值（1300，不可改）；生图保持 1080×1920 不动。

# 视频目标分辨率（1080×1920 竖屏全高清）：
SIZE = {
    "portrait_9x16": (1080, 1920),
    "portrait_3x4": (1440, 1920),
    "landscape_3x4": (1920, 1440),
}
IMG_SIZE = SIZE # 图片与视频共用同一套目标尺寸（1080×1920 等）

VIEW_REF_LONG_EDGE = {4: 1447, 3: 1280} # 仅兜底参考值（视图工作流输出尺寸随宽高比浮动）
ALIGN_BASE = 32 # 读图算最大边长时用的向下对齐基数（≠ 工作流里那个固定的「长宽对齐基数」）

AUTO_SIZE_GUARD = True


def _img_long_edge(img_rel_or_path):
    '取图片（相对 fxai/image/ 的令牌、或本地绝对路径）的长边像素；失败返回 None。\n相对令牌经 HTTP 下载到本地缓存后只读文件头（PNG/JPEG），不依赖 PIL。'

    if not img_rel_or_path:
        return None
    try:
        p = _ensure_local(img_rel_or_path)
    except Exception:
        return None
    if not p or not os.path.exists(p):
        return None
    if os.path.isdir(p):
        for fn in sorted(os.listdir(p)):
            if os.path.splitext(fn)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                p = os.path.join(p, fn)
                break
        else:
            return None
    try:
        import struct
        with open(p, "rb") as f:
            head = f.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return max(w, h)
        if head[:2] == b"\xff\xd8":  # JPEG：扫 SOF 段
            with open(p, "rb") as f:
                data = f.read()
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                mk = data[i + 1]
                if mk in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return max(w, h)
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + seg
    except Exception:
        return None
    return None


def safe_max_edge(img_rel_or_path, want=MAX_EDGE):
    '视频「最大边长」安全值：取 MAX_EDGE 与输入图长边的较小者（超过长边会让节点「放大」并报错）。\n返回按 ALIGN_BASE 下取整的对齐值。找不到图时退回按视图固定尺寸推断的最小安全值。'

    le = _img_long_edge(img_rel_or_path)
    if not le:
        le = min(VIEW_REF_LONG_EDGE.values())
    v = min(int(want), int(le))
    v = (v // ALIGN_BASE) * ALIGN_BASE
    return max(v, ALIGN_BASE)


def _mv_limit(mode, char_rel, scene_rel, req_w=0, req_h=0):
    '三种 MV 模式统一的「视频最大边长」上限（生视频封顶 MAX_EDGE=1300）。\n规则：取 min(MAX_EDGE=1300, 源图真实长边, 请求分辨率长边)，再按 ALIGN_BASE 下取整对齐。'

    if mode == "single":
        le = _img_long_edge(scene_rel or char_rel)
    elif mode == "multi":
        le = _img_long_edge(_first_in_dir(scene_rel))
    else:  # multiref：人物图与场景图取较小长边
        les = [x for x in (_img_long_edge(char_rel), _img_long_edge(scene_rel)) if x]
        le = min(les) if les else None
    if not le:
        le = min(VIEW_REF_LONG_EDGE.values())
    req_le = max(int(req_w or 0), int(req_h or 0)) or le
    v = min(MAX_EDGE, int(le), int(req_le))
    v = (v // ALIGN_BASE) * ALIGN_BASE
    return max(v, ALIGN_BASE)


def _cap_dims(w, h, cap=MAX_EDGE):
    """把 (w, h) 按最长边封顶 cap（保持比例），并向下对齐 ALIGN_BASE。用于多参考 925 节点。"""
    w, h = int(w), int(h)
    long = max(w, h)
    if long > cap:
        s = cap / long
        w, h = int(round(w * s)), int(round(h * s))
    w = max(ALIGN_BASE, (w // ALIGN_BASE) * ALIGN_BASE)
    h = max(ALIGN_BASE, (h // ALIGN_BASE) * ALIGN_BASE)
    return w, h


def _first_in_dir(dir_rel):
    '取 fxai/image/<dir_rel> 下第一个图片文件的相对令牌（多场景用来估算尺寸上限）。\n经 HTTP 列目录（/fxai/files/list）。'

    if not dir_rel:
        return None
    try:
        url = comfy_url().rstrip("/") + "/fxai/files/list?dir=fxai&subdir=" + urllib.parse.quote("image/" + str(dir_rel))
        with _http("GET", url, timeout=20) as r:
            data = json.loads(r.read().decode())
        imgs = (data.get("images") or [])
        if imgs:
            name = imgs[0].get("name") or ""
            if name:
                return str(dir_rel).rstrip("/") + "/" + name
    except Exception:
        pass
    return dir_rel # 兜底：返回目录本身（_img_long_edge 会走 VIEW_REF 兜底）


def _list_files(sub, cat="image"):
    "经 HTTP 列 fxai/<cat>/<sub> 下的文件（相对路径列表，如 ['sub/a.png']）。\ncat 取 image/audio/video；全部经接口、不读本机目录。用于重跑脚本发现已生成素材。"

    try:
        url = comfy_url().rstrip("/") + "/fxai/files/list?dir=fxai&subdir=" + urllib.parse.quote(cat + "/" + str(sub))
        with _http("GET", url, timeout=20) as r:
            data = json.loads(r.read().decode())
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # 服务端按类别分键返回：image→images、audio→audio、video→video
            key = {"image": "images", "audio": "audio", "video": "video"}.get(cat)
            items = data.get(key) or data.get("files") or []
        else:
            items = []
        out = []
        for x in items:
            name = x.get("name") if isinstance(x, dict) else str(x)
            if name:
                out.append(str(sub).rstrip("/") + "/" + name)
        return out
    except Exception:
        return []


def _fit_to_ratio(w, h, max_edge):
    """把 (w,h) 等比缩放到长边 ≤ max_edge，并都按 ALIGN_BASE 对齐；保底 32。"""
    m = max(w, h, 1)
    sc = min(1.0, float(max_edge) / m)
    ww = max(ALIGN_BASE, int(w * sc) // ALIGN_BASE * ALIGN_BASE)
    hh = max(ALIGN_BASE, int(h * sc) // ALIGN_BASE * ALIGN_BASE)
    return ww, hh


# ---------- HTTP ----------
def _http(method, url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    return _OPENER.open(req, timeout=timeout)


# ---------- ComfyUI 在线检测（干活前必须先确认） ----------
def check_comfyui(url=None, timeout=5):
    """检测ComfyUI是否可访问。url 不传就按 comfy_url() 解析。返回 True/False。"""
    url = comfy_url(url)
    try:
        req = urllib.request.Request(f"{url}/system_stats")
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def wait_for_comfyui(max_wait=1800, interval=15, url=None):
    """轮询等待ComfyUI启动（用户去启动后自动继续）。最多等 max_wait 秒。返回是否就绪。"""
    url = comfy_url(url)
    print(f"[等待] ComfyUI 未检测到，正在等待启动（{url}）……", flush=True)
    waited = 0
    while waited < max_wait:
        if check_comfyui(url):
            print("[等待] ComfyUI 已就绪，开始干活。", flush=True)
            return True
        time.sleep(interval)
        waited += interval
        if waited % 60 == 0:
            print(f"[等待] 已等 {waited}s，仍未检测到 ComfyUI，请确认是否已启动。", flush=True)
    return False


def require_comfyui(url=None):
    """干活前调用：云实例关机就自动开机，再确认ComfyUI 在线；本机没起才抛错提醒。

    url 不传就按 comfy_url() 解析（传 url 可临时换到另一台服务器）。
    """
    url = comfy_url(url)
    # 本机地址但探不通 → 自动回退云端（本机没起就走线上）。
    if not XGY.is_xgpu(url) and not check_comfyui(url, timeout=2.0):
        try:
            prefer_cloud()
            url = COMFY
        except RuntimeError as e:
            print("[comfy] 本机不可达且无线上配置：%s" % e, flush=True)
    # 顺序固定为「先保活、再判定」：云实例关机时 check_comfyui 必然 False，先让看门狗把它拉起来
    try:
        ok, msg = XGY.ensure_service(url, lambda u: check_comfyui(u, timeout=8),
                                     wait=1800, name="ComfyUI")
        if not ok:
            print("[xgy] " + str(msg), flush=True)
    except Exception as e:
        print("[xgy] 自动开机检查跳过：" + str(e), flush=True)
    if not check_comfyui(url):
        raise RuntimeError(
            "⚠️ ComfyUI 不可达（地址 %s）。\n"
            "  · 仙宫云地址：应已自动开机，若实例刚开机需等 1~2 分钟加载完镜像、端口才通，稍后重试即可。\n"
            "  · 容器地址的端口段必须是 ComfyUI 服务端口 8188（<实例ID>-8188.container.x-gpu.com），"
            "管理端口 -80 不是服务端口。\n"
            "  · 本机地址：请用 ComfyUI 启动器启动后重试。\n"
            "  · 缺仙宫云令牌无法自动开机：见 fxai-xiangongyun-deploy/SKILL.md。" % url)


def finish(url=None):
    """整套流程最后一个步骤调它：只在用的地址是仙宫云时，打印「问用户是否关实例」的两档文案。

    调用时机：所有任务都做完、收工整备交付之前调一次（或由调用方直接问用户）；单个子任务跑完时跳过。
    url 不传就按 comfy_url() 解析。
    """
    return XGY.ask_hint(comfy_url(url))


# ---------- UI 图格式 → API 格式（按 widgets 顺序回填未连线输入） ----------
def ui_to_api(ui):
    api = {}
    for n in ui.get("nodes", []):
        nid = str(n["id"])
        inputs = {}
        widgets = n.get("widgets_values") or []
        wptr = 0
        for inp in n.get("inputs", []):
            name = inp["name"]
            if inp.get("link") is not None:
                inputs[name] = [inp["link"]]
            else:
                inputs[name] = widgets[wptr] if wptr < len(widgets) else None
                wptr += 1
        api[nid] = {"class_type": n["type"], "inputs": inputs,
                    "_meta": {"title": n.get("title", "")}}
    return api


def _load_api(path):
    d = json.load(open(path, encoding="utf-8"))
    if "nodes" in d:  # 是 UI 格式，转一下
        return ui_to_api(d)
    return d


# ---------- 结果 URL 解析 ----------
def url_to_rel(url):
    "'image/mv_scene|scene1.png' -> 'mv_scene/scene1.png' （MV 节点要的相对路径）"
    left, _, fname = url.partition("|")
    _, _, sub = left.partition("/")
    return (sub + "/" + fname).replace("\\", "/")


# ---------- 产物下载（全部走 HTTP 接口） ----------
def _rel_to_token(rel):
    '相对路径 -> /fxai/tasks/result 风格的 url 令牌。\n- 已是令牌（含 |）原样返回；\n- image/sub/file.png -> image|sub/file.png；\n- avatar/000.png（无类别）-> image|avatar/000.png。'



    if not rel:
        return rel
    if "|" in rel:
        return rel
    parts = rel.split("/")
    if parts[0] in ("image", "audio", "video"):
        return parts[0] + "|" + "/".join(parts[1:])
    return "image|" + rel


def _file_url(token, url=None):
    # comfy_url() 走同一套优先级，故换服务器下载时产物也一定从那台服务器取
    base = down_base(url)
    return base + "?url=" + urllib.parse.quote(token, safe="")


_DL_CACHE = {}  # token -> 本地缓存路径，同一 token 只下载一次


def _download(token, dst, timeout=600, url=None):
    """按 url 令牌经 HTTP 接口下载文件到 dst（GET /fxai/down/file）。全部经接口，不读本机目录。

    url 不传就按 comfy_url() 解析；传了就从那台服务器取（跟提交任务那台保持一致）。
    """
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    last_err = None
    for attempt in range(3):
        try:
            with _http("GET", _file_url(token, url), timeout=timeout) as r:
                with open(dst, "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
            return True
        except urllib.error.HTTPError as e:
            if 400 <= getattr(e, "code", 0) < 500:  # 4xx 永久性错误，不重试
                try:
                    last_err = "HTTP %s: %s" % (e.code, e.read(400).decode("utf-8", "replace"))
                except Exception:
                    last_err = "HTTP %s" % e.code
                break
            last_err = e
        except Exception as e:
            last_err = e
        time.sleep(2)
    raise RuntimeError("产物 HTTP 下载失败（令牌=%s）：%s" % (token, last_err))


def _ensure_local(rel_or_token):
    """返回可本地读像素的路径：绝对路径直接用；相对令牌则经 HTTP 下载到缓存并返回缓存路径。"""
    p = rel_or_token or ""
    if os.path.isabs(p) and os.path.isfile(p):
        return p
    token = _rel_to_token(p)
    if not token:
        return p
    if token in _DL_CACHE and os.path.isfile(_DL_CACHE[token]):
        return _DL_CACHE[token]
    fname = token.split("|")[-1].split("/")[-1] or "img.png"
    cache = os.path.join(OUT_DIR, ".cache", fname)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    _download(token, cache, timeout=120)
    _DL_CACHE[token] = cache
    return cache


# ---------- 文件上传（HTTP · 覆盖模式，对应 fxai-toolkit /fxai/image/v2/upload） ----------
def upload_image(local_path, subdir="", filename=None, overwrite=True, timeout=120):
    """经 HTTP 把本地图片上传到 ComfyUI 服务端 fxai/image/<subdir>/（覆盖模式）。

    - subdir 为单层目录名（服务端接口会剥离斜杠，勿传嵌套路径；短剧统一用 'shortdrama'）。
    - overwrite=True 时传 overwrite=true + filename=<指定名>，服务端直接覆盖该文件名（不递增）。
      覆盖参数的生效需 ComfyUI 重启加载新接口；重启前旧接口忽略该参数、对空目录仍按递增给 000..N（首传等价）。
    - 返回服务端实际文件名（NNN.png）。
    依赖 fxai-toolkit 的 /fxai/image/v2/upload（支持 overwrite + filename 参数）。
    """
    subdir = (subdir or "").replace("\\", "/")
    subdir = re.sub(r'[\\/*?:"<>|]', "", subdir)  # 接口只认单层目录
    fname = filename or os.path.basename(local_path)
    if not os.path.isfile(local_path):
        raise RuntimeError("待上传文件不存在: " + local_path)
    boundary = "----fxaiup" + os.urandom(6).hex()
    with open(local_path, "rb") as f:
        data = f.read()
    body = b""
    body += ("--%s\r\n" % boundary).encode()
    body += b"Content-Disposition: form-data; name=\"subdir\"\r\n\r\n"
    body += (subdir + "\r\n").encode()
    body += ("--%s\r\n" % boundary).encode()
    body += b"Content-Disposition: form-data; name=\"overwrite\"\r\n\r\n"
    body += (("true" if overwrite else "false") + "\r\n").encode()
    body += ("--%s\r\n" % boundary).encode()
    body += b"Content-Disposition: form-data; name=\"filename\"\r\n\r\n"
    body += (fname + "\r\n").encode()
    body += ("--%s\r\n" % boundary).encode()
    body += ('Content-Disposition: form-data; name="image"; filename="%s"\r\n' % fname).encode()
    body += b"Content-Type: image/png\r\n\r\n"
    body += data
    body += b"\r\n"
    body += ("--%s--\r\n" % boundary).encode()
    url = comfy_url().rstrip("/") + "/fxai/image/v2/upload"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
    with _OPENER.open(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("success"):
        raise RuntimeError("上传失败: %s" % resp.get("error"))
    return resp.get("name") or fname


# ---------- 音频上传（HTTP · 对应 fxai-toolkit POST /fxai/audio/upload） ----------
def upload_audio(local_path, subdir="", filename=None, timeout=300):
    """经 HTTP 把本地音频上传到服务端 fxai/audio/<subdir>/（人声分离等要先有服务端音频时用）。

    - 服务端会用 ffmpeg 转成 wav 并按「目录内已有数量」编号（<NNN>_<原名>.wav）。
    - 返回服务端相对路径 '<subdir>/<name>'（可直接喂给 FxAiAudioSelector 的「音频文件」）。
    """
    subdir = re.sub(r'[\\/*?:"<>|]', "", (subdir or "").replace("\\", "/"))
    fname = filename or os.path.basename(local_path)
    if not os.path.isfile(local_path):
        raise RuntimeError("待上传音频不存在: " + local_path)
    boundary = "----fxaiaudio" + os.urandom(6).hex()
    with open(local_path, "rb") as f:
        data = f.read()
    body = b""
    body += ("--%s\r\n" % boundary).encode()
    body += b'Content-Disposition: form-data; name="subdir"\r\n\r\n'
    body += (subdir + "\r\n").encode()
    body += ("--%s\r\n" % boundary).encode()
    body += ('Content-Disposition: form-data; name="audio"; filename="%s"\r\n' % fname).encode()
    body += b"Content-Type: audio/wav\r\n\r\n"
    body += data + b"\r\n"
    body += ("--%s--\r\n" % boundary).encode()
    url = comfy_url().rstrip("/") + "/fxai/audio/upload"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
    with _OPENER.open(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("success"):
        raise RuntimeError("音频上传失败: %s" % resp.get("error"))
    name = resp.get("name") or fname
    return (subdir + "/" + name) if subdir else name


# ---------- 视频上传（HTTP · 对应 fxai-toolkit POST /fxai/video/upload） ----------
_VIDEO_EXT = ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.ts', '.mts')


def upload_video(local_path, subdir="", filename=None, overwrite=False, timeout=900):
    """经 HTTP 把本地视频上传到服务端 fxai/video/<subdir>/。

    - 默认按「目录内已有数量」编号（<NNN>_<原名>）；overwrite=True + filename=<名> 直接覆盖该名、不递增。
    - 返回服务端相对路径 '<subdir>/<name>'，rel 末段一律以返回值为准，不按原文件名推。
    - 覆盖模式校验返回名与期望名一致；不一致即服务端未加载覆盖接口，抛错。
    - 整文件读入内存后上传；超大素材由调用方先切段再传。
    """
    subdir = re.sub(r'[\\/*?:"<>|]', "", (subdir or "").replace("\\", "/"))
    fname = filename or os.path.basename(local_path)
    if not os.path.isfile(local_path):
        raise RuntimeError("待上传视频不存在: " + local_path)
    boundary = "----fxaivideo" + os.urandom(6).hex()
    with open(local_path, "rb") as f:
        data = f.read()
    body = b""
    body += ("--%s\r\n" % boundary).encode()
    body += b'Content-Disposition: form-data; name="subdir"\r\n\r\n'
    body += (subdir + "\r\n").encode()
    if overwrite:
        body += ("--%s\r\n" % boundary).encode()
        body += b'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
        body += b"true\r\n"
        body += ("--%s\r\n" % boundary).encode()
        body += b'Content-Disposition: form-data; name="filename"\r\n\r\n'
        body += (fname + "\r\n").encode()
    body += ("--%s\r\n" % boundary).encode()
    body += ('Content-Disposition: form-data; name="video"; filename="%s"\r\n'
             % os.path.basename(local_path)).encode()
    body += b"Content-Type: video/mp4\r\n\r\n"
    body += data + b"\r\n"
    body += ("--%s--\r\n" % boundary).encode()
    url = comfy_url().rstrip("/") + "/fxai/video/upload"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
    with _OPENER.open(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("success"):
        raise RuntimeError("视频上传失败: %s" % resp.get("error"))
    name = resp.get("name") or fname
    if overwrite:
        ext = os.path.splitext(local_path)[1].lower()
        if ext not in _VIDEO_EXT:
            ext = ".mp4"
        want = re.sub(r'[\\/*?:"<>|]', "", os.path.splitext(fname)[0]) + ext
        if name != want:
            raise RuntimeError(
                "覆盖未生效：返回 %s，期望 %s —— 服务端 fxai-toolkit 未加载覆盖接口，重启后重试" % (name, want))
    return (subdir + "/" + name) if subdir else name


# ---------- 文件完整性 ----------
def _complete(path, ext):
    try:
        if os.path.getsize(path) < 100:
            return False
    except OSError:
        return False
    if ext == ".png":
        with open(path, "rb") as f:
            f.seek(-16, 2)
            return b"IEND" in f.read()
    return True


# ---------- 工作流提交前格式自检（防 JSON / 传值低级错误） ----------
def _type_ok(val, spec_type):
    if isinstance(spec_type, tuple):
        return any(_type_ok(val, t) for t in spec_type)
    if spec_type is list:
        return isinstance(val, list)
    if spec_type is dict:
        return isinstance(val, dict)
    return isinstance(val, spec_type)


def _check_multiref_lines(ld):
    'lines_data 必须是合法 JSON 字符串，或解析后为非空 list，每行 [int, str(提示词), str(图片列表)]。\n占位符注入后 lines_data 已是 list，故同时接受 str/list。'

    if isinstance(ld, str):
        try:
            rows = json.loads(ld)
        except Exception as e:
            raise ValueError("lines_data 不是合法 JSON 字符串：%s" % e)
    elif isinstance(ld, list):
        rows = ld
    else:
        raise ValueError("lines_data 必须是 str 或 list，实际 %r" % type(ld))
    if not isinstance(rows, list) or not rows:
        raise ValueError("lines_data 解析后必须是非空 list")
    for i, r in enumerate(rows):
        if not (isinstance(r, list) and len(r) >= 3):
            raise ValueError("lines_data 第 %d 行必须是 [int,str,str]，实际 %r" % (i, r))
        if not isinstance(r[0], int):
            raise ValueError("lines_data 第 %d 行第0列应为 int(序号)，实际 %r" % (i, r[0]))
        if not isinstance(r[1], str) or not r[1].strip():
            raise ValueError("lines_data 第 %d 行第1列应为非空提示词，实际 %r" % (i, r[1]))
        if not isinstance(r[2], str) or not r[2].strip():
            raise ValueError("lines_data 第 %d 行第2列应为非空图片列表，实际 %r" % (i, r[2]))
    return True


# 各工作流传值规范（节点 id + 字段 + 期望类型）。kind 与 gen_* 调用时传入的值对应。
WF_SPECS = {
    "music": {
        "desc": "音乐生成（两套工作流任选其一：Yue2 / MiniMax Music 3）",
        # ★ any_of：同一 kind 下有多套模型工作流时，任一变体校验通过即放行。
        #   新增音乐模型只需在此追加一个分支，不动其它任务。
        "any_of": [
            {
                "desc": "Yue2（music-yue2.json）",
                "required": [
                    ("42", "style", str), ("42", "lyrics", str), ("42", "mode", str),
                    ("45", "style", str), ("45", "lyrics", str), ("45", "mode", str),
                    ("45", "max_duration", (int, float)),
                    ("50", "seconds", list),  # 保持对 node45 的链接（list 形式），写死静态值会断开该链接
                    ("55", "目录", str),
                ],
            },
            {
                "desc": "MiniMax Music 3（music-minimax3-api.json，支持男女对唱）",
                "required": [
                    ("54", "caption", str), ("54", "lyrics", str),
                    ("54", "max_duration", (int, float)),
                    ("56", "目录", str),
                ],
            },
        ],
    },
    "charview4": {
        "desc": "千问2.1 四视图（有参考图→四视图，1280×1080 内置）",
        "required": [
            ("147", "图片列表", str), ("205", "正向提示词", str),
            ("117", "目录", str), ("2", "seed", int),
        ],
    },
    "charview3": {
        "desc": "千问2.1 三视图（无参考人物）",
        "required": [
            ("147", "图片列表", str), ("203", "正向提示词", str),
            ("117", "目录", str), ("2", "seed", int),
        ],
    },
    "scene": {
        "desc": "千问2.1 图片处理（场景图，可带人物参考）",
        "required": [
            ("152", "正向提示词", str), ("152", "宽度", int), ("152", "高度", int),
            ("141", "图片列表", str), ("117", "目录", str), ("2", "seed", int),
        ],
    },
    "multiref": {
        "desc": "MiniMax 多参考 V2（Ref2VA，人物图+多场景图）",
        "required": [
            ("989", "音频文件", str),
            ("1038", "保存目录", str),  # 由 submit_workflow 在 multiref 提交前自动注入 fxai_outputdir
            ("1039", "lines_data", str),  # 必须为合法 JSON 字符串（见 _check_multiref_lines）
        ],
    },
    "single": {
        "desc": "MiniMax 单场景（单场景参考图）",
        "required": [("1105", "音频文件", str), ("1005", "图片列表", str)],
    },
    "multi": {
        "desc": "MiniMax 多场景（图片文件夹）",
        "required": [("989", "音频文件", str), ("990", "目录", str)],
    },
    "vocalsep": {
        "desc": "人声分离（Mel-Band RoFormer 模型，服务端工作流）",
        "required": [
            ("815", "音频文件", str),  # FxAiAudioSelector：输入音频（fxai_audioinput）
            ("816", "目录", str),      # FxAiAudioManager：输出子目录（fxai_outputdir）
        ],
    },
    "upscale": {
        "desc": "视频超分（自研超分，可选叠加人脸精修）",
        "required": [
            ("108", "视频文件", str),
            ("111", "结束索引", int),   # 传 -1 跑完全部段，0 只跑第一段
            ("125", "value", str),      # 放大倍数走公式节点，超分与精修共用
        ],
    },
}


def validate_workflow_payload(wf, kind=None, tag="run"):
    """提交 /prompt 前的格式自检。失败抛 RuntimeError（点名节点/字段），成功返回 True。"""
    try:
        json.dumps({"prompt": wf})
    except Exception as e:
        raise RuntimeError("[%s] 工作流整体不可 JSON 序列化（检查是否有非基本类型值，如 Path/对象/bytes）：%s" % (tag, e))
    if not isinstance(wf, dict):
        raise RuntimeError("[%s] 工作流顶层必须是 dict（节点 id -> 节点定义），实际 %s" % (tag, type(wf)))
    for nid, node in wf.items():
        if not isinstance(node, dict) or "class_type" not in node or "inputs" not in node:
            raise RuntimeError("[%s] 节点 %s 结构非法：缺少 class_type/inputs 或不是 dict（%r）" % (tag, nid, node))
        if not isinstance(node["class_type"], str):
            raise RuntimeError("[%s] 节点 %s 的 class_type 必须是 str，实际 %s" % (tag, nid, type(node["class_type"])))
        if not isinstance(node["inputs"], dict):
            raise RuntimeError("[%s] 节点 %s 的 inputs 必须是 dict，实际 %s" % (tag, nid, type(node["inputs"])))
    spec = WF_SPECS.get(kind)
    if spec:
        if "any_of" in spec:
            # 同一 kind 下多套模型工作流：任一分支校验通过即放行。
            errs = []
            for _br in spec["any_of"]:
                try:
                    _check_required_fields(wf, _br["required"], kind, tag)
                    errs = []
                    break
                except RuntimeError as e:
                    errs.append("%s → %s" % (_br.get("desc", "?"), e))
            if errs:
                raise RuntimeError("[%s] 工作流(%s) 不匹配任何已登记变体：\n  - %s"
                                   % (tag, kind, "\n  - ".join(errs)))
        else:
            _check_required_fields(wf, spec["required"], kind, tag)
        if kind == "multiref":
            try:
                _check_multiref_lines(wf["1039"]["inputs"]["lines_data"])
            except ValueError as e:
                raise RuntimeError("[%s] 工作流(multiref) lines_data 结构非法: %s" % (tag, e))
    return True


def _check_required_fields(wf, required, kind, tag):
    """校验一组 (节点id, 字段名, 期望类型) 是否齐全且类型正确；失败抛 RuntimeError。"""
    for (nid, field, ftype) in required:
        if nid not in wf:
            raise RuntimeError("[%s] 工作流(%s) 缺失必填节点 %s —— 工作流版本可能已变，需同步 WF_SPECS" % (tag, kind, nid))
        ins = wf[nid]["inputs"]
        if field not in ins:
            raise RuntimeError("[%s] 工作流(%s) 节点 %s 缺失必填字段「%s」——可能是 _set 静默跳过（节点字段名已变）" % (tag, kind, nid, field))
        val = ins[field]
        if val is None:
            raise RuntimeError("[%s] 工作流(%s) 节点 %s 字段「%s」为 None（此字段必填，须给到值）" % (tag, kind, nid, field))
        if isinstance(val, (list, dict)):
            continue
        if not _type_ok(val, ftype):
            raise RuntimeError("[%s] 工作流(%s) 节点 %s 字段「%s」类型应为 %s，实际 %s（值=%r）" % (tag, kind, nid, field, ftype, type(val), val))
    return True


# ---------- 提交并等待 ----------
def _load_watcher():
    """载入全局广播监听技能（fxai-comfyui-watcher），失败返回 None。"""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "fxai-comfyui-watcher", "scripts", "comfy_watch.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("fxai_comfy_watch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 广播等待默认**安静**：过程事件只在记录里，不往对话/终端刷；需要实时看进度时置 True。
BROADCAST_VERBOSE = False


def _wait_by_broadcast(pid, lis, timeout, tag):
    """用广播监听等任务终态。返回终态字典；连不上/长期无信号返回 None（调用方回落轮询）。"""
    if lis is None:
        return None
    try:
        return lis.wait(pid, timeout=timeout, verbose=BROADCAST_VERBOSE, tag=tag,
                        idle_fallback=90)
    except Exception as e:
        print("  [%s] 广播监听起不来，回落轮询: %r" % (tag, e), flush=True)
        return None


def _is_temp_name(name):
    """是不是 ComfyUI 临时目录里的预览文件名（PreviewImage 写的 ComfyUI_temp_xxx_00001_.png）。"""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base.startswith("comfyui_temp")


def _token_from_term(term):
    """从广播终态里拼出下载令牌。

    `fxai:task_saved` 事件本身带 url（形如 `video/merged|xxx.mp4`），监听器已把它拆成
    term 的 category / subdir / outputs / merged。**这就是任务结果的原始形态**，比回头
    查 `/fxai/tasks/result` 更可靠——收尾节点 output_node=False 的工作流不登记结果表，
    查接口只会拿到空字典。
    """
    term = term or {}
    merged = term.get("merged")
    if merged:
        return "video/merged|%s" % merged
    cat = term.get("category") or ""
    sub = term.get("subdir") or ""
    outs = [f for f in (term.get("outputs") or []) if f]
    # 兜底：老台账里可能混进了 PreviewImage 的临时预览名（ComfyUI_temp_xxx_00001_.png），
    # 那不是成品，拿去做下载令牌一定 404。正式产物优先，只剩临时名就当没有。
    real = [f for f in outs if not _is_temp_name(f)]
    if real:
        outs = real
    else:
        outs = []
    if not cat or not outs:
        return None
    return ("%s/%s|%s" % (cat, sub, outs[-1])) if sub else ("%s|%s" % (cat, outs[-1]))


def _path_to_token(path):
    """服务端绝对路径 → 下载令牌 `类别/子目录|文件名`。

    类别只认 image / audio / video（服务端下载接口的白名单），取路径里对应那一段；
    取不到就按扩展名推。
    """
    parts = str(path).replace("\\", "/").rstrip("/").split("/")
    if len(parts) < 2:
        return None
    fname, subdir = parts[-1], parts[-2]
    cat = parts[-3] if len(parts) >= 3 and parts[-3] in ("image", "audio", "video") else ""
    if not cat:
        cat = "video" if fname.lower().endswith((".mp4", ".mov", ".mkv", ".webm")) else "image"
    return "%s/%s|%s" % (cat, subdir, fname)


def _token_from_history(pid, url):
    """从 /history 的 outputs 里找产物路径。

    有些工作流的收尾节点 output_node=False（只写文件、不登记结果表、不发 task_saved），
    但工作流末端挂了 PreviewAny 之类的输出节点时，产物路径会以文本形式落在 history.outputs。
    这条是这类工作流唯一能自动拿到成品的路。
    """
    try:
        with _http("GET", url + "/history/" + pid, timeout=15) as r:
            hist = json.loads(r.read().decode())
    except Exception:
        return None
    entry = hist.get(pid) or {}
    for node_out in (entry.get("outputs") or {}).values():
        for val in (node_out or {}).values():
            for s in (val if isinstance(val, list) else [val]):
                if not isinstance(s, str):
                    continue
                if s.lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".wav", ".mp3")):
                    tok = _path_to_token(s)
                    if tok:
                        return tok
    return None


def _fxai_result_url(pid, url):
    try:
        with _http("POST", url + "/fxai/tasks/result",
                   data=json.dumps({"prompt_ids": pid}).encode("utf-8"),
                   headers={"Content-Type": "application/json"}, timeout=10) as r:
            data = json.loads(r.read().decode())
        item = data.get(pid)
        return item.get("url") if item else None
    except Exception:
        return None


def _in_queue(pid, url):
    """这个 prompt_id 还在不在 ComfyUI 队列里（超时提示用：区分「排队没轮到」与「真失败」）。"""
    try:
        with _http("GET", url + "/queue", timeout=8) as r:
            q = json.loads(r.read().decode())
    except Exception:
        return False
    for key in ("queue_running", "queue_pending"):
        for item in q.get(key) or []:
            cand = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item
            if isinstance(cand, str) and cand.startswith(pid):
                return True
    return False


def gpu_gate(url="", tag="run"):
    """POST /prompt 前的显存门禁，唯一实现在 fxai-standard/scripts/check_gpu_gate.py。

    门禁未过抛 RuntimeError 停在提交前；此处只调判据，不另写一份判据。
    提交侧一律经本函数，技能脚本各自复制门禁属第二处事实源。
    """
    scripts = os.environ.get(
        "FXAI_STANDARD_SCRIPTS",
        os.path.normpath(os.path.join(FXAI_WORKFLOW_DIR, "..", "fxai-standard", "scripts")))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import check_gpu_gate as G
    except ImportError as exc:
        raise RuntimeError(
            "GPU 提交前门禁缺失：%s 下未找到 check_gpu_gate.py，"
            "按门禁口径停手，卸载验收未完成即提交属于越界" % scripts) from exc
    result = G.gate(base=url, tag=tag)
    print("  [%s] %s" % (tag, result["message"]), flush=True)
    return result


def gpu_self_heal(url="", tag="run", error_text="", http_code=0):
    """运行时显存自愈，唯一实现在 fxai-standard/scripts/check_gpu_gate.py::self_heal()。

    提交遇 5xx / 执行期 OOM 时调：读显存实况与错误日志判因 → 卸载一次 →
    设备级验收，返回 True 时调用方立即重投一轮。判为服务不健康则停手交回给人。
    """
    scripts = os.environ.get(
        "FXAI_STANDARD_SCRIPTS",
        os.path.normpath(os.path.join(FXAI_WORKFLOW_DIR, "..", "fxai-standard", "scripts")))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import check_gpu_gate as G
    except ImportError:
        return False
    try:
        res = G.self_heal(base=comfy_url(url), tag=tag,
                          error_text=error_text, http_code=http_code)
    except Exception as e:
        print("  [%s] 显存自愈探测失败，按原错误处理：%r" % (tag, e), flush=True)
        return False
    print("  [%s] 自愈判定：%s" % (tag, res.get("reason")), flush=True)
    return bool(res.get("healed"))


def _heal_and_retry(wf, timeout, poll, tag, kind, url, models_checked,
                    heal_rounds, error_text, http_code=0):
    """执行期显存错误：自愈一次后整体重投；返回 None 交回原错误处理。"""
    if heal_rounds >= 1:
        return None
    if not gpu_self_heal(url=url, tag=tag, error_text=error_text,
                         http_code=http_code):
        return None
    print("  [%s] 显存已回落，自动重投一轮（自愈额度 1 次）" % tag, flush=True)
    return run_workflow(wf, timeout=timeout, poll=poll, tag=tag, kind=kind,
                        url=url, models_checked=True, heal_rounds=heal_rounds + 1)


def run_workflow(wf, timeout=1800, poll=6, tag="run", kind=None, url=None,
                 models_checked=False, heal_rounds=0):
    """提交工作流并等产物 url。url 不传就按 comfy_url() 解析（可换服务器）。

    等待优先走全局广播监听（fxai-comfyui-watcher）：提交时带上监听自己的 clientId，
    于是定向的报错与 fxai 的「第 N/M 镜」广播同时到达；监听起不来或长期无信号时
    自动回落 /history 轮询。

    模型名自适应：提交前会比对服务器 /object_info，模型文件名对不上就先换掉再提交；
    万一仍被 value_not_in_list 拒收，会按 ComfyUI 点名的节点/字段纠正后立即重发。
    服务器名字没变时这两步都零动作。`models_checked=True` 表示调用方已自检过，跳过预检。
    """
    validate_workflow_payload(wf, kind=kind, tag=tag)  # ← 提交前格式自检
    url = comfy_url(url)
    gpu_gate(url=url, tag=tag)  # ← 提交前显存门禁，唯一实现在 fxai-standard/scripts
    # 模型名自适应：服务器升级后模型文件名变了，这里先换成服务器上实际有的那个再提交，
    # 省掉一次注定被拒的往返。名字没变时零动作；取不到服务器信息（离线）时静默跳过。
    if not models_checked:
        try:
            repair_models(wf, url=url, tag=tag)
        except Exception as e:
            print("  [%s] 模型名自检跳过：%r" % (tag, e), flush=True)
    watcher = _load_watcher()
    lis = None
    if watcher is not None:
        try:
            lis = watcher.Listener(url)          # 先连上，拿到 sid
        except Exception as e:
            print("  [%s] 广播监听连接失败，改用轮询: %r" % (tag, e), flush=True)
            lis = None
    body = {"prompt": wf}
    if lis is not None:
        body["client_id"] = lis.cid
    payload = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    last_err = None
    net_try = 0          # 网络抖动重试计数（模型名纠正不占这个额度）
    fixed_rounds = 0     # 模型名纠正次数上限，防「修了还是不对」时死循环
    heal_used = False    # 5xx 显存自愈额度：整条提交链只用一次
    while net_try < 3:
        try:
            with _http("POST", url + "/prompt", data=payload, headers=headers, timeout=30) as r:
                resp = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            body_txt = ""
            try:
                body_txt = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            # ★ 模型名在服务器上不存在（value_not_in_list）→ 就地纠正后立刻重发。
            # 这是确定性错误，不值得等 5 秒；ComfyUI 的报错已点名节点/字段/收到的值。
            if e.code == 400 and body_txt and fixed_rounds < 3:
                try:
                    bad = json.loads(body_txt)
                except Exception:
                    bad = None
                if bad and bad.get("node_errors"):
                    ch, un = repair_from_node_errors(wf, bad, url=url, tag=tag)
                    if ch:
                        fixed_rounds += 1
                        payload = json.dumps(body).encode("utf-8")
                        print("  [%s] 已按服务器实际模型名纠正，重新提交" % tag, flush=True)
                        continue
                    if un:
                        if lis is not None:
                            lis.close()
                        raise RuntimeError(_fmt_model_fix([], un, tag, url=url)) from None
            # ★ 5xx：按显存实况与错误日志判因，命中显存类就卸载全部模型并立即重投。
            # 自愈成功不占网络抖动重试额度；判为服务不健康时停手，把重启交回给人。
            if e.code >= 500 and not heal_used and heal_rounds < 1:
                heal_used = True
                if gpu_self_heal(url=url, tag=tag,
                                 error_text=body_txt or ("HTTP %d @ /prompt" % e.code),
                                 http_code=e.code):
                    print("  [%s] 显存自愈完成，立即重投一次" % tag, flush=True)
                    continue
            last_err = f"[{tag!s}] 提交被拒 HTTP {e.code!s}: {body_txt[:1000]!s}"
            net_try += 1
            print("  [%s] 提交重试 %d/3: %s" % (tag, net_try, last_err), flush=True)
            time.sleep(5)
    else:
        if lis is not None:
            lis.close()
        raise RuntimeError(last_err or "[%s] 提交失败" % tag)
    if resp.get("error") or resp.get("node_errors"):
        # 200 但带校验错误：能认出是模型名问题就纠正重发一次
        if fixed_rounds < 3 and resp.get("node_errors"):
            ch, un = repair_from_node_errors(wf, resp, url=url, tag=tag)
            if ch and not un:
                if lis is not None:
                    lis.close()
                return run_workflow(wf, timeout=timeout, poll=poll, tag=tag,
                                    kind=kind, url=url, models_checked=True,
                                    heal_rounds=heal_rounds)
        if lis is not None:
            lis.close()
        raise RuntimeError(f"[{tag!s}] 提交被拒: {json.dumps(resp, ensure_ascii=False)[:800]!s}")
    pid = resp["prompt_id"]
    # 提交即落工作流快照：ComfyUI 的 /history 是内存态，服务重启/滚动清理后查不到，
    # 断线续跑（resume）就靠这份本地快照恢复原工作流。写失败不影响主流程。
    try:
        _load_shot_query().save_snapshot(pid, wf, server=url)
    except Exception:
        pass
    print(f"[{tag!s}] queued {pid!s}", flush=True)
    deadline = time.time() + timeout          # 广播等待与轮询兜底共用这一个时间预算
    try:
        term = _wait_by_broadcast(pid, lis, max(1, int(deadline - time.time())), tag)
        if term is not None:
            if term.get("status") == "error":
                err_txt = str(term.get("error") or "")
                retried = _heal_and_retry(wf, timeout, poll, tag, kind, url,
                                          models_checked, heal_rounds, err_txt)
                if retried is not None:
                    return retried
                raise RuntimeError(f"[{tag!s}] 工作流报错: {err_txt or '(无异常信息)'!s}"
                                   f"｜prompt_id={pid!s}（查详情：comfy_watch.py status {pid!s}）")
            # ① 先用广播事件自带的 url 拼令牌（task_saved 已带 category/subdir/outputs）；
            #    收尾节点不登记结果表的工作流只在这条路上拿得到。
            token = _token_from_term(term)
            if token and "ComfyUI_temp_" not in token:
                print(f"[{tag!s}] ✅ 合并成功，已保存：{token!s}（后续取产物/烧字幕/归档可开始）", flush=True)
                return token
            # ① 的令牌是 temp 预览名（含 ComfyUI_temp_，GET /fxai/down/file 必 404）时，
            #    跳过它走 ② 的 fxai 结果接口取真实产物令牌——temp 名只存在于广播，不可下载。
            # ② 广播只说「完成」且没带文件时：先查 fxai 结果接口，再翻 history.outputs
            #   （output_node=False 的收尾节点只写文件不登记，产物路径只能从 history 里读）
            t_end = time.time() + 90
            while time.time() < t_end:
                token = _fxai_result_url(pid, url) or _token_from_history(pid, url)
                if token:
                    print(f"[{tag!s}] ✅ 合并成功，已保存：{token!s}（后续取产物/烧字幕/归档可开始）", flush=True)
                    return token
                time.sleep(4)
            raise RuntimeError("[%s] 广播已报完成，但 90s 内未取到产物（结果接口与 history 都为空）" % tag)
    finally:
        if lis is not None:
            lis.close()
    if time.time() >= deadline:
        queued = "（任务仍在服务端队列里，可稍后再跟）" if _in_queue(pid, url) else ""
        raise RuntimeError("[%s] 等结果超时 %ds%s｜prompt_id=%s" % (tag, timeout, queued, pid))
    terminal_at = None
    while time.time() < deadline:
        time.sleep(poll)
        status_str, errmsg = _history_status(pid)
        if status_str == "error":
            retried = _heal_and_retry(wf, timeout, poll, tag, kind, url,
                                      models_checked, heal_rounds, errmsg or "")
            if retried is not None:
                return retried
            raise RuntimeError(f"[{tag!s}] 工作流报错: {errmsg or '(无异常信息)'!s}"
                               f"｜prompt_id={pid!s}（查详情：comfy_watch.py status {pid!s}）")
        if status_str == "success":
            if terminal_at is None:
                terminal_at = time.time()
            if time.time() - terminal_at > 90:
                raise RuntimeError("[%s] 工作流已成功执行，但 90s 内未取到 fxai 结果 url（可能渲染失败）" % tag)
        else:
            terminal_at = None
        try:
            with _http("POST", url + "/fxai/tasks/result",
                       data=json.dumps({"prompt_ids": pid}).encode("utf-8"),
                       headers={"Content-Type": "application/json"}, timeout=10) as r:
                data = json.loads(r.read().decode())
            item = data.get(pid)
            if item and item.get("url"):
                print(f"[{tag!s}] ✅ 合并成功，已保存：{item['url']!s}（后续取产物/烧字幕/归档可开始）", flush=True)
                return item["url"]
        except Exception as e:
            print(f"  [{tag!s}] fxai 结果查询异常(忽略): {e!r}", flush=True)
        # 结果接口为空时翻 history.outputs（output_node=False 的收尾节点只写文件不登记）
        _tok = _token_from_history(pid, url)
        if _tok:
            print(f"[{tag!s}] ✅ 合并成功，已保存：{_tok!s}（来自 history.outputs）", flush=True)
            return _tok
    queued = "（任务仍在服务端队列里，可稍后再跟）" if _in_queue(pid, url) else ""
    raise RuntimeError("[%s] 等结果超时 %ds%s｜prompt_id=%s" % (tag, timeout, queued, pid))


def _history_status(pid, url=None):
    """返回 (status_str, errmsg)。多端点兜底：先看 /history/{pid}，空了再查 /history 全集。
    url 不传就按 comfy_url() 解析。"""
    base = comfy_url(url)
    def _parse(hist):
        entry = hist.get(pid) if isinstance(hist, dict) else None
        if not entry:
            return None, ""
        st = entry.get("status", {}) or {}
        sstr = st.get("status_str")
        err = ""
        for m in st.get("messages", []):
            if isinstance(m, (list, tuple)) and len(m) >= 2 and isinstance(m[1], dict):
                err += m[1].get("exception_message", "") + " "
        return sstr, err.strip()
    try:
        with _http("GET", base + "/history/" + pid, timeout=10) as r:
            s, e = _parse(json.loads(r.read().decode()))
        if s is not None:
            return s, e
    except Exception as e:
        print(f"  [history] /history/{pid!s} 查询异常(兜底): {e!r}", flush=True)
    try:
        with _http("GET", base + "/history", timeout=15) as r:
            allh = json.loads(r.read().decode())
        if isinstance(allh, dict) and pid in allh:
            return _parse(allh)
    except Exception as e:
        print("  [history] /history 全集查询异常: %r" % (e,), flush=True)
    return None, ""


def _role_ref(role):
    """把角色参考参数解析成 ComfyUI 图像选择器（FxAiCharacterImageSelector「图片列表」）能识别的相对路径。

    路径格式与取值判据见 fxai-standard/references/10-资源引用路径规范.md
    （全技能唯一真相源，各技能文档只留指针）。一句话：返回的「相对路径」
    一律指【服务端相对路径】（fxai/image/ 下的 rel，如 avatar/000.png），不是本机路径。

    解析：
      None / 空 / 假值            → ""  （空场景：不含任何人物参考；MV 多参考 multiref 用）
      "woman"/"女"/"女性"         → REF_WOMAN  (avatar/000.png)
      "man"/"男"/"男性"           → REF_MAN    (avatar/001.png)
      "both"/"couple"/"双人"/"男女" → REF_MAN + "," + REF_WOMAN
      其它任意非空字符串           → 原样返回（自定义参考图：角色三视图 / 四视图相对路径，
                                     或单张五官参考图；直接作为图像选择器的图片列表输入）。
    """







    if not role:
        return ""
    r = str(role).strip()
    rl = r.lower()
    if rl in ("woman", "女", "女性"):
        return REF_WOMAN
    if rl in ("man", "男", "男性"):
        return REF_MAN
    if rl in ("both", "couple", "双人", "男女"):
        return REF_MAN + "," + REF_WOMAN
    return r


# =====================================================================
# 分镜进度查询（生成视频通用：短剧 / MV / 数字人 / 幻灯片视频都走这条）
#
# 多段循环生成中途断了（超时、关机、报错、换机器）时，先查「已经生成到第几个分镜」，
# 下次从断点接着生成，不从头重跑。
# 判定口径：**视频文件只要不在合并目录（merged）里，文件名上的文件号 = 分镜序号**
# （000.mp4 = 第 1 镜，007.mp4 = 第 8 镜）；合并片（成片）不算分镜。
# 完整实现在 fxai-workflow/shot_query.py（也可命令行直接跑）。
# =====================================================================
def _load_shot_query():
    """载入同目录的分镜进度查询模块。"""
    import importlib.util
    path = os.path.join(FXAI_WORKFLOW_DIR, "shot_query.py")
    if not os.path.exists(path):
        raise RuntimeError("缺少 fxai-workflow/shot_query.py（分镜进度查询模块），"
                           "技能包不完整，请重新分发 fxai-workflow/")
    spec = importlib.util.spec_from_file_location("fxai_shot_query", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def shot_progress(pid, url=None, save_dir="", expect_total=None, only_this_task=False):
    """查某个 prompt_id 已生成到第几个分镜。返回结构化字典，关键字段：

      shots        已落盘的分镜（[{index, shot, name, token, size, mtime}]，index 0-based）
      shot_count   已生成几镜
      missing      缺哪几镜（传 expect_total 或台账有总镜数才算）
      next_index   下一个要生成的索引（直接喂工作流的 start_index）
      next_shot    下一个是第几镜（1-based，给人看）
      merged       合并成片名（没有就是还没合并）
      save_dir     该任务保存分镜的目录（video/<save_dir>/）

    断点续跑：r = shot_progress(pid); build_workflow(..., start_index=r["next_index"])
    """
    sq = _load_shot_query()
    return sq.query(pid, url=url or "", save_dir=save_dir,
                    expect_total=expect_total, only_this_task=only_this_task)


def resume_task(pid, url=None, wait=False, timeout=7200, dry_run=False,
                end_index=-1, save_dir="", expect_total=None, only_this_task=None):
    """断了 / 服务器挂了之后**一条命令接着跑**（生成视频通用）。

    做了什么：从 `/history/<pid>` 取回上次提交的完整工作流 → 查已生成到第几镜 →
    把「开始索引」改到下一镜、`结束索引` 置 -1 → 重新 `POST /prompt`。
    lines_data 保留全部镜头，合并节点照旧合并整集（已生成镜头命中节点缓存会直接复用）。

    返回 dict，关键字段：action(resume/done/blocked)、start_index、new_pid、submitted。
    例：r = resume_task(pid); comfy_watch.py wait r["new_pid"]
    """
    sq = _load_shot_query()
    return sq.resume(pid, url=url or "", wait=wait, timeout=timeout, dry_run=dry_run,
                     end_index=end_index, save_dir=save_dir,
                     expect_total=expect_total, only_this_task=only_this_task)


# =====================================================================
# 直接引用自有角色底图出图 → 必须写【穿搭/服装】+【头型/发型】（硬校验，铁律）
# 自有角色参考图 000/001 是「裸肩头像原始图」，只固定五官/角色一致性；衣服/发型全靠提示词补。
# 漏写会直接出「肩膀以上原始裸露」的图，所以这里硬拦，不告警了事。
#
# 规则**只针对「直接引用角色底图」这一种情况**（role=woman/man/both/自定义参考图 → ref 非空）：
#   ①【穿搭/服装】词（缺 → 报错）；②【头型/发型】词（缺 → 报错）；③ 发饰/配饰（缺 → 告警建议）。
# 免检情况：
#   · 纯场景图 role=None（画面里没人）；
#   · 二次生成 / 场景融合——输入本身就是「已经穿好衣服的角色图」，不必再描述穿搭；
#     这种也走 role=None（或显式传 allow_no_outfit=True）。
#
# 🔴 本函数是全技能唯一真相源：fxai-comfyui-image-gen / fxai-scene-gen / fxai-fourview-gen /
#    fxai-audio-video 等所有生图入口一律调用它，各技能统一以此处词表为准。
# =====================================================================
_OUTFIT_WORDS = (
    "西装", "西服", "外套", "夹克", "风衣", "大衣", "羽绒", "衬衫", "卫衣", "毛衣", "针织",
    "T恤", "t恤", "POLO", "马甲", "背心", "连衣裙", "连衣", "长裙", "短裙", "半裙", "包臀",
    "A字裙", "百褶", "礼服", "旗袍", "套装", "制服", "汉服", "演出服", "薄纱", "吊带", "抹胸", "露肩",
    "上衣", "上装", "下装", "裤子", "牛仔", "西裤", "阔腿", "短裤", "运动裤", "瑜伽", "运动服",
    "泳装", "内衣", "鞋", "靴", "高跟鞋", "运动鞋", "板鞋", "凉鞋", "球鞋", "配饰", "首饰",
    "耳饰", "耳环", "项链", "手链", "手表", "围巾", "腰带", "帽子", "贝雷帽", "发带", "胸针",
    "穿搭", "着装", "服装", "服饰", "工装",
    # 常见口语写法：缺了这几个，写「穿着白色衣服 / 长袖衬衫」会被误判成缺穿搭，白跑一趟
    "衣服", "衣着", "长袖", "短袖",
)
_HAIR_WORDS = ("发型", "头型", "头发", "辫", "马尾", "刘海", "短发", "长发", "卷发", "盘发",
               "编发", "丸子头", "寸头", "中分", "齐肩", "披发", "扎发", "发髻", "碎发",
               "黑长直", "大波浪", "披肩发", "直发")
# 发饰/配饰（软提示：有更好，没有只告警）
_HAIR_ACC_WORDS = ("发带", "发卡", "发箍", "发饰", "耳饰", "耳环", "耳钉", "项链", "帽子",
                   "贝雷帽", "棒球帽", "头饰", "发夹")


def check_outfit(prompt, role, allow_no_outfit=False):
    """【仅当直接引用自有角色底图(000/001)时】硬校验提示词必写【穿搭/服装】+【头型/发型】。

    触发条件：role 指向自有角色（woman/man/both/自定义参考图相对路径 → `_role_ref(role)` 非空），
    即"直接拿我们那个裸肩头像底图出图"。此时参考图只管脸，衣服/发型必须靠提示词补——漏写会出
    「肩膀以上原始裸露」的图。
    免检情况：role=None（纯场景），或二次生成/场景融合（输入已是穿好衣服的角色图）。
    返回 True（通过）；缺穿搭或缺发型抛 ValueError；缺发饰/配饰仅打印告警。
    确需无衣着/无发型描述显式传 allow_no_outfit=True。
    """
    if allow_no_outfit:
        return True
    # 没有直接引用自有角色底图 → 免检（纯场景 / 二次生成融合图）
    try:
        ref = _role_ref(role)
    except Exception:
        ref = None
    if not ref:
        return True
    if not any(w in prompt for w in _OUTFIT_WORDS):
        raise ValueError(
            "直接引用自有角色底图出图，提示词缺少【穿搭/服装】描述：参考图 000/001 是裸肩头像原始图，"
            "只固定五官，衣服必须靠提示词补。\n"
            "  请按取景写明衣着：半身→上半身穿搭；全身→上装+下装+鞋+配饰。\n"
            "  确需无衣着描述请显式传 allow_no_outfit=True。\n"
            "  当前提示词：%s" % prompt)
    if not any(w in prompt for w in _HAIR_WORDS):
        raise ValueError(
            "直接引用自有角色底图出图，提示词缺少【头型/发型】描述：参考图只固定五官，发型必须靠提示词补。\n"
            "  请写明发型（如：黑长直发自然垂落 / 齐肩短发 / 低马尾 / 大波浪卷发 …）。\n"
            "  确需无发型描述请显式传 allow_no_outfit=True。\n"
            "  当前提示词：%s" % prompt)
    if not any(w in prompt for w in _HAIR_ACC_WORDS):
        print("[gen] ⚠ 提示词未写【发饰/配饰】（耳饰/项链/发带/帽子等），建议补上让人物更精致：%s"
              % prompt[:50], flush=True)
    return True


# =====================================================================
# 多参考图 → 提示词必须包含对应的 N 个引用标签 <image1>..<imageN>（硬校验，铁律）
#
# 背景（2026-10-12 双人封面事故）：role="both" 传了两张参考图，但提示词里一个引用标签
# 都没有，模型不知道哪张脸对应谁——男主完全没锁上、女主偶然锁上，封面翻车。
#
# 机制（源码级）：Qwen-Image-2.1 edit 的图片绑定靠提示词里的 <imageN> 标记——
# comfy/text_encoders/qwen_image21.py 传 N 张图时在模板里按图片列表顺序插入
# `<image1><vision_block> <image2><vision_block> ...`（编号从 1 开始，与 _role_ref
# 返回的逗号分隔图片列表顺序一致）。提示词不写 <imageN> = 不告诉模型哪张图是谁，
# 模型只能自由发挥。这是"传了参考图却锁不上人"的标准根因。
#
# 规则：
#   · N = _role_ref(role) 里的图片张数（空 role → N=0 → 免检）；
#   · 提示词必须【逐个】包含 <image1> .. <imageN>，缺任何一个都算不通过；
#   · N>1 默认强制（多图正是错配高发区）；N<=1 默认只告警不拦（既有单图链路
#     如四视图 / MV 单场景不炸）；strict=True 强制 / strict=False 免检。
# =====================================================================
def check_image_refs(prompt, role, strict=None):
    """校验：传了 N 张参考图，提示词里就必须有 N 个引用标签 <image1>..<imageN>。
    返回 True（通过）；缺标签时 N>1 抛 ValueError，N<=1 打印告警后返回 True。"""
    try:
        ref = _role_ref(role)
    except Exception:
        ref = ""
    n = len([p for p in (ref or "").split(",") if p.strip()])
    if n == 0:
        return True
    missing = [i for i in range(1, n + 1) if ("<image%d>" % i) not in prompt]
    if not missing:
        return True
    msg = (
        "生图传了 %d 张参考图，提示词缺少引用标签 %s：%d 张图就必须有 %d 个标签，缺一个都不行。\n"
        "  请在提示词里用 <imageN> 把每张参考图显式绑到具体人物/元素上，例如：\n"
        "  「<image1> 是画面右侧的古装男子（外貌…）；<image2> 是画面左侧的古装女子（外貌…）」。\n"
        "  标签编号 = 参考图列表顺序（_role_ref('both') = 男主图在前、女主图在后，即 <image1>=男、<image2>=女）。\n"
        "  当前提示词：%s" % (n, "、".join("<image%d>" % i for i in missing), n, n, prompt))
    if strict is None:
        strict = n > 1
    if strict:
        raise ValueError(msg)
    print("[gen] ⚠ %s" % msg.replace("\n", " ")[:200], flush=True)
    return True
