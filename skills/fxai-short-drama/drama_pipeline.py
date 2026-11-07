# -*- coding: utf-8 -*-
"""\nfxai-short-drama 短剧自动化管线（唯一真相源）\n\n把「一集短剧的分镜+对白」变成「批量生成的多镜头视频并合并成片」。\n视频工作流复用共享 fxai-workflow/shortdrama-minimax-filmdrama-api.json（Ref2VA 多参考 + For 循环批量 + 视频合并；与 fxai-mv-pipeline 同一套 fxai_ 占位符工作流，统一放在 skills/fxai-workflow/ 公共目录）。\n\n规则：\n- 不写死任何本机绝对路径（如 <某ComfyUI本机路径>、<某数据目录>）。机器相关路径放 config.local.json；不再自带 config.local.json。\n- 地址/模板只认全局配置 comfy_url，不读环境变量（临时换服务器走 comfy_client 的 url 参数）。\n\n节点配置：\n- 993 FxAiGeneratorController.结束索引 必须 = -1（生成全部分段；模板默认 0 会截断）\n- 1155 FxAiMiniMaxImageToVideoV2.音频模式：宠物/卡通项目用 '系统生成'（无引用音频）\n\n产物查询：fxai-toolkit 节点不写 /history，用 POST /fxai/tasks/result 按 prompt_id 查。\n\n生成速度（16G 显存）：\n- 单卡 RTX 5060 Ti（16G）≈ 1 秒视频 / 1 分钟生成。即总时长 N 秒 ≈ 预计 N 分钟。\n  见 estimate_minutes() 与 GEN_SPEED_NOTE。\n"""

import json
import os
import re
import shutil
import sys
import time
import subprocess
import datetime
import urllib.request
import urllib.error

# 共享 ComfyUI 客户端（与 fxai-* 技能同级的公共目录 fxai-workflow/），用于 HTTP 上传/下载
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fxai-workflow"))
import comfy_client as C

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))


# ------------------------- 机器相关配置（不写死绝对路径） -------------------------
def _load_config():
    """读全局配置 ~/.workbuddy/fxai_global.json（与公共层 comfy_client 同一份）。

    配置只走全局 fxai_global.json，技能包不带 config.local.json。
    FXAI_GLOBAL_CONFIG 只用来改「这份全局配置本身的路径」，不是地址开关。
    """

    p = os.environ.get(
        "FXAI_GLOBAL_CONFIG",
        os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


_CFG = _load_config()

# ComfyUI 地址：只认全局配置 comfy_url，没有才回落到本机 8188（不读环境变量）
C.prefer_local(quiet=True)                     # 本机在跑就用本机（与 fxai-workflow 同一策略）
COMFY = C.comfy_url()                          # 规范化：去尾斜杠/反斜杠；不传覆盖 = 用上面选定的地址


def _load_watcher():
    """载入全局广播监听技能（fxai-comfyui-watcher）；没有就返回 None（自动回落轮询）。"""
    import importlib.util
    path = os.path.join(os.path.dirname(SKILL_DIR), "fxai-comfyui-watcher",
                        "scripts", "comfy_watch.py")
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("fxai_comfy_watch", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None

# 工作流模板：默认用共享 fxai-workflow（统一 fxai_ 前缀）
DEFAULT_WORKFLOW = (
    _CFG.get("workflow")
    or os.path.join(SKILL_DIR, "..", "fxai-workflow", "shortdrama-minimax-filmdrama-api.json")
)

# 服务器模式：资源全部在 ComfyUI 服务端 fxai/image/ 下，按相对路径引用，不经本机根目录。

# 生成速度（16G 显存）
GEN_SPEED_NOTE = (
    "16G 显存显卡：约 1 秒视频 / 1 分钟生成。"
    "总时长 N 秒 ≈ 预计 N 分钟；提交后按此估算等待，超时默认 90 分钟。"
)


def estimate_minutes(total_seconds):
    """按 16G 显存经验估生成耗时（分钟）。返回 int。"""
    return int(round(total_seconds))


# 单集时长下限 60 秒（平台最低要求）
MIN_EPISODE_SECONDS = 60


def episode_seconds(shots):
    """返回本集已规划总时长（秒）= 各镜头 duration 之和。"""
    return sum(int(s.get("duration", 0)) for s in shots)


def check_episode_duration(shots, min_seconds=MIN_EPISODE_SECONDS):
    """规划阶段校验单集总时长下限，不达标直接抛错——必须在 build_workflow 之前调用。

    返回本集总时长（秒）。min_seconds=None 关闭校验。
    """
    total = episode_seconds(shots)
    if min_seconds is None:
        return total
    if total < min_seconds:
        gap = min_seconds - total
        need_more = -(-gap // 10)   # 每镜 ≤10s，向上取整得出还差几镜
        raise ValueError(
            "单集时长 %ds < 下限 %ds，禁用提交渲染（成片后才发现要重跑整集）"  # std:allow
            "：还差 %d 秒 —— 按台词需用秒数补 %d 镜（镜长随台词定，单镜头上限 10 秒）；"
            "台词不得删减，时间轴按 speech_seconds() 逐句累加。" % (total, min_seconds, gap, need_more))
    return total


# ------------------------- 台词语速（排时间轴用）-------------------------
# 语速 3.3 字每秒 + 气口，据此排时间轴。
SPEECH_CHARS_PER_SECOND = 3.3
SPEECH_BREATH_SECONDS = 0.3        # 每两句之间的气口
PUNCT_RE = re.compile(r"[\s，。！？、；：…—·\"'“”()（）,.!?;:]")

# 时间轴行两种口径（切点制为准，旧区间制兼容读取）
#   切点制：`At 00:03.500, ……`  —— 只给起点，终点 = 下一条起点（末条 = 镜时长）；[Shot 1] 首句不写时刻
#   旧制：  `0.00 秒到 3.50 秒，……` —— 自带终点，仅为老分镜可读，新分镜一律用 At
_AT_LINE_RE = re.compile(r"^At\s+(\d{1,3}):(\d{1,2}(?:\.\d{1,3})?)\s*[,，]\s*")
_LEGACY_LINE_RE = re.compile(r"^(\d+\.\d+)\s*秒到\s*(\d+\.\d+)\s*秒\s*[，,]\s*")


def speech_seconds(text, rate=None):
    """一句台词说完需要几秒（不含气口）。rate=None 用语速 3.3 字每秒。"""
    rate = SPEECH_CHARS_PER_SECOND if rate is None else rate
    if rate <= 0:
        return 0.0
    return len(PUNCT_RE.sub("", text or "")) / rate


def fmt_at(seconds):
    """秒 → 切点写法 `At 00:03.500,`（MM:SS.mmm）。"""
    total_ms = int(round(float(seconds) * 1000))
    m, rem = divmod(total_ms, 60000)
    s_, ms = divmod(rem, 1000)
    return "At %02d:%02d.%03d," % (m, s_, ms)


def timeline_marks(prompt, duration=None):
    """抽出一条提示词里的时间轴标记 → [(start, end, line), ...]。

    切点制（`At MM:SS.mmm,`）只给起点，终点由**下一条起点**补，末条补到镜时长；
    区间制（`X.XX 秒到 Y.YY 秒，`）自带终点。两种可混排（按出现顺序）。

    🔴 `[Shot 1]` 首个事件**不写时刻**，所以「详细描述里第一个时间轴标记之前的
    正文」会作为 **0 起点那一条**补进来（含首句台词）——否则首句没有窗口可校验。
    末条且 duration 缺失时 end=None，调用方跳过该条的时长校验。
    """
    lines = str(prompt or "").splitlines()
    # head 只收「详细描述」标题之后的正文；没有该标题就收全文
    start_at = 0
    for k, ln in enumerate(lines):
        if ln.strip() in ("详细描述", "detailed_description:") or ln.strip().startswith("详细描述"):
            start_at = k + 1
            break
    marks, head = [], []
    for ln in (x.strip() for x in lines[start_at:]):
        if not ln:
            continue
        ma = _AT_LINE_RE.match(ln)
        if ma:
            marks.append([int(ma.group(1)) * 60 + float(ma.group(2)), None, ln])
            continue
        ml = _LEGACY_LINE_RE.match(ln)
        if ml:
            marks.append([float(ml.group(1)), float(ml.group(2)), ln])
            continue
        if not marks:
            head.append(ln)
    # 首个事件不写时刻：把标题下正文补成 0 起点那一条
    if head:
        first = marks[0][0] if marks else (float(duration) if duration is not None else None)
        if first is None or first > 0:
            marks.insert(0, [0.0, first, "\n".join(head)])
    for k, mk in enumerate(marks):
        if mk[1] is not None:
            continue
        if k + 1 < len(marks):
            mk[1] = marks[k + 1][0]
        elif duration is not None:
            mk[1] = float(duration)
    return [tuple(x) for x in marks]


def audit_shot_speech(shots, rate=None, breath=None):
    """三件事：①每句台词在自己那段时间窗里装不装得下（字数/语速 + 气口）；
    ②说话行之后紧跟的静场段有没有显式写成静场行；③切点是否递增、是否落在镜时长内。

    时间轴按切点制读（`At 00:03.500, ……`）：带 <d>[Chinese] 的是说话行，不带的就是
    静场/动作行；**紧跟在说话行后面的静场段**不写「静场」二字 → H3 会顺着上一句台词把这段
    当还在说话、自己吐人声，硬拦；隔了好几段才到的收尾静场风险低，不拦。
    老分镜的 `X.XX 秒到 Y.YY 秒，` 仍可读（终点自带）。
    返回问题条目列表，空列表 = 通过。
    """
    rate = SPEECH_CHARS_PER_SECOND if rate is None else rate
    breath = SPEECH_BREATH_SECONDS if breath is None else breath
    if rate <= 0:
        return []
    bad = []
    for i, s in enumerate(shots, 1):
        try:
            dur = float(s.get("duration")) if s.get("duration") is not None else None
        except (TypeError, ValueError):
            dur = None
        segs = timeline_marks(s.get("prompt", ""), dur)
        # ③ 切点递增 + 落在镜时长内
        for k, (st, en, line) in enumerate(segs):
            if k and st <= segs[k - 1][0]:
                bad.append("镜%d 时间轴切点没有递增（%.2fs 之后又出现 %.2fs）：%s"
                           % (i, segs[k - 1][0], st, line[:34]))
            if dur is not None and st >= dur:
                bad.append("镜%d 时间轴切点 %.2fs 超出本镜时长 %.2fs：%s" % (i, st, dur, line[:34]))
        for k, (st, en, line) in enumerate(segs):
            txt = re.search(r"<d>\s*\[Chinese\]\s*(.*?)\s*</d>", line)
            if txt:
                if en is None:
                    continue          # 末条缺镜时长，无法判，不误拦
                need = speech_seconds(txt.group(1), rate) + breath
                if need > en - st:
                    bad.append("镜%d [%.2f-%.2f]s 给了 %.1fs 只装得下 %.1fs（%d 字 ÷ %.1f 字每秒 + 气口 %.1fs）：%s"
                               % (i, st, en, en - st, need,
                                  len(PUNCT_RE.sub("", txt.group(1))), rate, breath, txt.group(1)[:24]))
                continue
            if k and re.search(r"<d>\s*\[Chinese\]", segs[k - 1][2]) and "静场" not in line:
                bad.append("镜%d [%.2f-%.2f]s 紧跟在说话行后面却没写成显式静场（H3 会顺着上一句自己吐人声，"
                           "要写「静场，只有……的一声，一句台词也不出／任何一个人都不出声」）：%s"
                           % (i, st, en, line.strip()[:34]))
    return bad


# ------------------------- 字幕策略 -------------------------
# ① 生成阶段在提示词末尾追加「负面约束段」，压掉画面里的自动字幕；
# ② 字幕条走后期烧录（复用 fxai-lyric-subtitle），以台词为唯一真值，ASR 只给时间戳。
#
# 本段只收一件事：画面里多出来的文字（叠加字幕条 + 乱码字形）。这两者是同一种失败——H3 的文字渲染能力不足。
# H3 为 guidance-distilled 检查点，无独立 negative_prompt 参数，本段按普通文本参与生成，条目按一行最小化；
# 时代感、画风、道具、构图写进六段正文，用正向锁定（写「只有什么 / 是什么」）。
# 人物画质不写否定句：身份靠 03 册 5.6 的外貌句逐字照抄 role prompt + <Subject N> 稳定 ID 正向锁死，
# 「脸别变形 / 手指数量正确」这类否定句对画质类失败没有约束力，反把「变形」「多余手指」当概念喂进去。  # std:allow
#
# 措辞口径两条（2026-10-23 修订）：
#   ① 用现代口语祈使句，不用文言单字「勿」。官方提示词的负面行是口语式（引号内为官方原句：
#      「不要添加字幕」/「无软溶解」）；「勿」是低频单字，模型读得懂，但遵从度不如常用词稳定。  # std:allow
#   ② 一条只说一件事，不换语言再说一遍。中英并列在自然语言通道里不是双保险，是重复指令
#      （H3 铁律：同一件事只写一次，重复会摊薄权重），混英文还会往提示词里多引入一种语言信号。
NEGATIVE_CONSTRAINTS_BLOCK = (
    "\n\nnegative_constraints:\n"
    "- 画面上不要出现叠加的字幕条、对白字幕，也不要出现乱码文字或扭曲不清的字形。"  # std:allow
)

# ---------- 全动物阵容（宠物/卡通版）专属约束：画面里只出现动物 ----------
# 用在本管线 no_humans=True（宠物/动物版短剧）时，追加到每条镜头提示词末尾。
# 作用：把真人/人群排除在动物短剧画面之外。
NO_HUMANS_BLOCK = (
    "\n\nnegative_constraints:\n"
    "- 本片角色全部是拟人化动物（橘猫、德国牧羊犬、仓鼠等），画面里不要出现人类、真人、人物剪影或人群。"  # std:allow
)

# 负面提示词候选值（模板暴露 negative 字段时注入；build_workflow 默认 None，不自动喂）。
# 字幕类 + 乱码类通用；人类类只配 no_humans=True 的宠物版，真人出镜的项目传 None。
DEFAULT_NEGATIVE_PROMPT = (
    "字幕,字幕条,软字幕,台词字幕,对白字幕,乱码文字,"
    "人类,真人,人物,人群,路人,人类剪影,"
    "subtitles,caption,dialogue caption,garbled text,human,people,person"
)

# 节点 1155（FxAiMiniMaxImageToVideoV2）可能暴露的负面提示词字段名（按实际模板而定）。
_NEG_FIELD_CANDIDATES = ["负面提示词", "反向提示词", "negative_prompt", "negative", "neg_prompt", "反义提示词"]


def _inject_negative_prompt(wf, negative_prompt):
    '把负面提示词写进视频生成节点（若模板暴露了对应字段）。返回是否成功注入。\n\n注意：双采版模板 1155 节点默认未暴露 negative 输入框，此时本函数不生效，\n抑制字幕主要依赖 NEGATIVE_CONSTRAINTS_BLOCK 写在提示词里（跨模板稳定）。\n'





    if not negative_prompt:
        return False
    node = wf.get("1155", {}).get("inputs", {})
    for key in _NEG_FIELD_CANDIDATES:
        if key in node:
            node[key] = negative_prompt
            return True
    return False


# ------------------------- ComfyUI 在线检测 -------------------------
def check_comfyui(timeout=5):
    try:
        urllib.request.urlopen(COMFY + "/system_stats", timeout=timeout)
        return True
    except Exception:
        return False


def wait_for_comfyui(max_wait=1800, poll=10):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        if check_comfyui(5):
            return True
        time.sleep(poll)
    return False


def require_comfyui():
    if not check_comfyui(5):
        print("⚠️ ComfyUI 还没启动，这活儿暂时干不了。请先通过【凤希本地AI控制台】启动，"
              "或运行 python main.py --listen 0.0.0.0 --port 8188。启动好了我后台自动等它在线再继续。")
        if not wait_for_comfyui():
            raise RuntimeError("ComfyUI 未在线")


# ------------------------- 项目档案（可分享约定） -------------------------
def project_asset_path(project_root, rel):
    """项目档案里的资源绝对路径。rel 形如 '角色四视图/xiaojuzi.png' 或 'drama/ep4/scene_office.png'。"""
    return os.path.join(project_root, "assets", rel) if not os.path.isabs(rel) else rel


def save_manifest(project_root, manifest, name="scenes_manifest.json"):
    """把场景/角色资源映射写入项目档案（project_root/assets/<name> 或 project_root/<name>）。"""
    out = os.path.join(project_root, name)
    json.dump(manifest, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return out


def sync_project_to_comfyui(project_root, rels, subdir=None):
    """
    把项目档案里的本地备份参考图经 HTTP 重新上传到 ComfyUI 服务端（覆盖模式）。
    仅用于「重做旧项目 / 服务端资源被清、需从本地备份恢复」的场景；
    正常生图流程无需依赖它（生图已在服务端完成，直接引用生成下来的相对路径即可）。

    - rels: 服务端相对路径列表，如 ["shortdrama_xjz/000.png", "shortdrama_xjz/001.png"]
            （与 manifest 中存储的相对路径一致；本地备份位于 project_root/assets/<rel>）
    - 上传到服务端 fxai/image/<subdir>/：subdir 取 rel 的第一段（即本系列的 server_subdir，如 shortdrama_xjz），
      文件名取 rel 末段，overwrite=True 直接覆盖同名文件（不递增）。
    - 本地备份路径经 project_def 的 server_index→local 映射解析（本系列本地真值在 assets/角色四视图/<名>.png 等，
      与服务器 rel 并非同名）；找不到 project_def 或映射缺失时，退化为 project_root/assets/<rel>。
    - 本地备份不存在的 rel 记入 missing 返回（不报错），便于调用方区分「待生成」与「待回传」。
    返回 missing 列表（rel 字符串）。
    """
    missing = []
    # 用 project_def 建立 server_index -> 本地资产路径 反查表（本地真值可能在 assets/角色四视图/<名>.png）
    _sr, _pd = load_project_def(project_root)
    idx_to_local = {}
    if _pd:
        for grp in ("characters", "scenes"):
            for _k, _info in _pd.get(grp, {}).items():
                try:
                    idx_to_local[int(_info.get("server_index", -1))] = (_sr, _info.get("local", ""))
                except Exception:
                    pass
    for rel in rels:
        rel = (rel or "").replace("\\", "/").strip("/")
        if not rel:
            continue
        parts = rel.split("/")
        sd = parts[0] if len(parts) > 1 else (subdir or "shortdrama")
        name = parts[-1]
        base, _ = os.path.splitext(name)
        idx = None
        try:
            idx = int(base)
        except Exception:
            idx = None
        src = None
        if idx is not None and idx in idx_to_local:
            _lr, _lrel = idx_to_local[idx]
            src = os.path.join(_lr, _lrel) if _lrel else None
        if not src or not os.path.exists(src):
            # 退化：直接按 rel 拼 assets/
            src2 = project_asset_path(project_root, rel)
            if os.path.exists(src2):
                src = src2
        if not src or not os.path.exists(src):
            missing.append(rel)
            continue
        try:
            C.upload_image(src, subdir=sd, filename=name, overwrite=True)
            print(f"已回传 {rel} -> 服务端 {sd}/{name}（覆盖）")
        except Exception as e:
            missing.append(rel)
            print(f"⚠️ 回传失败 {rel}: {e}")
    return missing


# ------------------------- 项目定义（系列级，向上查找） -------------------------
def load_project_def(project_root):
    """
    向上查找本系列的 project_def.json，返回 (series_root, def_dict)。找不到返回 (None, None)。
    project_def 放在「短剧系列根目录」（如 小橘子升职记/），分集子文件夹向上即可找到；
    独立一次性短剧则放在自己的项目根。def 记录 server_subdir（本系列服务端目录令牌）
    与 角色/场景 → server_index + 本地资产路径 的映射，是本地与服务器资源的对齐基准。
    """

    cur = os.path.abspath(project_root)
    for _ in range(6):
        cand = os.path.join(cur, "project_def.json")
        if os.path.exists(cand):
            try:
                return cur, json.load(open(cand, encoding="utf-8"))
            except Exception:
                return cur, None
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None, None


def upload_project_assets(project_root, overwrite=True, subdir=None):
    """
    按 project_def 把本系列本地资产覆盖上传到 ComfyUI 服务端对应 server_subdir（每项目独立目录）。
    用途：① 首次把本地备份灌入服务器（建立本系列的 shortdrama_<slug> 目录）；
          ② 重做旧项目 / 服务端资源被清，从本地备份恢复。
    上传顺序按 server_index 升序，空目录时递增命名即得到 000..N；
    若服务端已加载「覆盖参数」接口（重启后），则以 filename=<server_index>.png 强制精确覆盖。
    返回 [(key, server_subdir/filename), ...]。
    """

    series_root, pd = load_project_def(project_root)
    if not pd:
        raise RuntimeError("未找到 project_def.json（向上查找失败）。请先在本系列根建 project_def.json。")
    sd = subdir or pd.get("server_subdir") or "shortdrama"
    done = []
    for grp in ("characters", "scenes"):
        for key, info in pd.get(grp, {}).items():
            local = os.path.join(series_root, info.get("local", ""))
            sidx = int(info.get("server_index", 0))
            fname = f"{sidx:03d}.png"
            if not os.path.exists(local):
                print(f"⚠️ 本地资产缺失，跳过 {key}: {local}")
                continue
            C.upload_image(local, subdir=sd, filename=fname, overwrite=overwrite)
            done.append((key, f"{sd}/{fname}"))
            print(f"已上传 {key} -> 服务端 {sd}/{fname}（覆盖={overwrite}）")
    return done


def server_subdir_of(project_root):
    """返回本系列的服务器目录令牌（project_def.server_subdir），找不到返回默认 'shortdrama'。"""
    _, pd = load_project_def(project_root)
    return (pd or {}).get("server_subdir") or "shortdrama"


def gen_project_assets(project_root, force=False, prompts=None, timeout=600):
    """
    按 project_def 为本系列在服务器端生图（每项目独立目录 shortdrama_<slug>）。
    关键对齐规则：每个角色/场景用 project_def 里的 prompt 生成，并显式传
    subdir=server_subdir、num=server_index —— 服务端文件精确落到
    shortdrama_<slug>/<server_index>.png，与上传/引用完全同名对齐，不靠递增乱序。
    - prompts: 可选 {group: {key: prompt}}，覆盖 project_def 里的 prompt（便于分集临时指定）。
    - force=False 时本地已存在备份则跳过（不重复生图）；force=True 强制重生成并覆盖服务端。
    - 角色一律生成四视图；project_def 角色条目的 `ref` 指我们自己的参考图（自有角色必填，锁脸锁身段），空则按提示词纯生成。
    - 生成后下载备份到 series_root/assets/<角色四视图|场景图>/<本地名>，写回 project_def 的 local。
    依赖 fxai-fourview-gen / fxai-scene-gen 技能（懒加载，与本技能同级）。
    """

    series_root, pd = load_project_def(project_root)
    if not pd:
        raise RuntimeError("未找到 project_def.json（向上查找失败）。请先在本系列根建 project_def.json。")
    sd = pd.get("server_subdir") or "shortdrama"
    skill_dir = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.join(skill_dir, "..", "fxai-fourview-gen"),
              os.path.join(skill_dir, "..", "fxai-scene-gen")):
        if os.path.isdir(d):
            sys.path.insert(0, d)
    import fourview_gen, scene_gen
    prompts = prompts or {}
    done = []

    def _backup(mod, key, local):
        out = getattr(mod, "OUT_DIR", None)
        if not out:
            return False
        src = os.path.join(out, key + ".png")
        if not os.path.exists(src):
            return False
        os.makedirs(os.path.dirname(local), exist_ok=True)
        shutil.copyfile(src, local)
        return True

    # 角色四视图（一律四视图；自有角色靠 project_def 里的 ref 指到我们自己的参考图）
    for key, info in pd.get("characters", {}).items():
        local = os.path.join(series_root, info.get("local", ""))
        if os.path.exists(local) and not force:
            continue
        prompt = (prompts.get("characters", {}) or {}).get(key) or info.get("prompt")
        if not prompt:
            print(f"⚠️ 角色 {key} 无 prompt，跳过（请在 project_def 或 prompts 提供）")
            continue
        sidx = int(info.get("server_index", 0))
        fourview_gen.gen_char_view(prompt, ref=info.get("ref", ""), views=info.get("views", 4),
                                   subdir=sd, num=sidx, tag=key, timeout=timeout)
        if _backup(fourview_gen, key, local):
            done.append((key, f"{sd}/{sidx:03d}.png"))
            print(f"已生成角色 {key} -> 服务端 {sd}/{sidx:03d}.png（本地备份 {local}）")
    # 场景图
    for key, info in pd.get("scenes", {}).items():
        local = os.path.join(series_root, info.get("local", ""))
        if os.path.exists(local) and not force:
            continue
        prompt = (prompts.get("scenes", {}) or {}).get(key) or info.get("prompt")
        if not prompt:
            print(f"⚠️ 场景 {key} 无 prompt，跳过（请在 project_def 或 prompts 提供）")
            continue
        sidx = int(info.get("server_index", 0))
        scene_gen.gen_scene_image(prompt, role=info.get("role", None), subdir=sd, num=sidx,
                                  tag=key, timeout=timeout)
        if _backup(scene_gen, key, local):
            done.append((key, f"{sd}/{sidx:03d}.png"))
            print(f"已生成场景 {key} -> 服务端 {sd}/{sidx:03d}.png（本地备份 {local}）")
    return done


# ------------------------- lines_data 组装 -------------------------
# ------------------------- 触发词自动挂载（Combat V2 + Camera Motion）-------------------------
# 规则唯一权威来源：fxai-h3-video-prompt/references/07-触发词挂载规范.md
#   camera motion  运镜 LoRA 总开关（永远排最前）
#   prfight1       打斗镜基础词（有交手就挂）
#   prfin1         终结镜附加（本镜走完终结技），必须与 prfight1 同现
#   prslow1        慢镜终结附加（终结技要慢放），必须与 prfin1 同现
TRIGGER_CAMERA = "camera motion"
TRIGGER_FIGHT = "prfight1"
TRIGGER_FINISH = "prfin1"
TRIGGER_SLOW = "prslow1"
_TRIGGER_ORDER = (TRIGGER_CAMERA, TRIGGER_FIGHT, TRIGGER_FINISH, TRIGGER_SLOW)

# 六段式段落标题（中英两套：短剧管线用中文名，MV/口播管线用英文名）
_SEC_HEAD = (r"subject_definitions|summary|retention_analysis|detailed_description|"
             r"overall_soundscape|non_diegetic_music|主体定义|摘要|延续分析|详细描述|整体音效|非叙事音乐")
_DESC_HEAD_RE = re.compile(r"^[ \t]*(?:detailed_description|详细描述)[ \t]*[:：]?[ \t]*$", re.M)
_SEC_HEAD_RE = re.compile(r"^[ \t]*(?:%s)[ \t]*[:：]?[ \t]*$" % _SEC_HEAD, re.M)

# 打斗镜启发式关键词：只认「招式 / 兵器 / 身体对抗」这类强信号，
# 「端起酒杯 / 推开窗」这类日常动作归到文戏——误挂 prfight1 会让文戏镜凭空多出打斗动作。
_FIGHT_RE = re.compile(
    r"交手|对招|过招|拆招|接招|出招|一招|招式|连击|格挡|架住|架刀|硬接|硬挡|"
    r"劈砍|劈下|劈落|横斩|直刺|刺出|挑开|撩开|横扫|上架|卸力|沉肩|沉腰|"
    r"弓步|马步|虚步|腾挪|闪身|侧闪|后撤步|翻滚|翻身|旋身|"
    r"擒拿|锁住|绞住|绊倒|摔|投技|压制|逼退|抢攻|反击|反手|"
    r"兵刃|兵器|剑锋|剑尖|刀锋|刀光|剑光|枪尖|棍梢|长枪|朴刀|长剑|短刀|"
    r"锤|巨斧|战斧|长戟|铁鞭|钢鞭|铁锏|盾牌|盾|匕首|飞钩|铁叉|"
    r"拳风|掌风|重拳|飞踢|侧踢|回旋踢|肘击|膝撞|近身缠斗|缠斗|肉搏|厮打|"
    r"围攻|合围|包抄|围拢|群起|群殴|抢中线|"
    r"杀招|绝招|必杀|终结一击|致命一击|最后一击")
_FINISH_RE = re.compile(r"终结|绝招|杀招|必杀|致命一击|最后一击|一击定胜负|一击制敌|收招")
_SLOW_RE = re.compile(r"慢镜|慢动作|升格|高速摄影|时间放慢|缓缓放大|极慢|慢速")


def _order_triggers(words):
    """去重 + 按标准顺序（camera motion → prfight1 → prfin1 → prslow1）重排；
    非标准词原样保留在末尾，不丢。"""
    seen, out = set(), []
    for w in _TRIGGER_ORDER:
        for x in words:
            if x == w and w not in seen:
                seen.add(w)
                out.append(w)
    for x in words:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _desc_body(prompt):
    """取「详细描述 / detailed_description」段的正文；取不到就退化成整条提示词。"""
    m = _DESC_HEAD_RE.search(prompt)
    if not m:
        return prompt
    tail = prompt[m.end():]
    nxt = _SEC_HEAD_RE.search(tail)
    return tail[:nxt.start()] if nxt else tail


def resolve_trigger_words(shot):
    """判定本镜该挂哪些触发词，返回按标准顺序排列的 list（空 list = 不挂，即纯文戏镜）。

    判定优先级（高 → 低）：
    1. shot["trigger_words"]：显式指定，str（逗号分隔）或 list。
       - None / 缺省 → 落到下一级自动判定；
       - "" / [] → 明确表示「本镜不挂」，由人给值。
    2. shot["is_fight"] / shot["finisher"] / shot["slow"]：显式布尔开关，覆盖启发式；
       is_fight=False 明确表示文戏镜。
    3. 启发式：扫提示词「详细描述」段正文里的打斗 / 终结 / 慢镜关键词（保守，只认强信号）。
       注意：**「终结」命中即隐含打斗镜**——终结镜正文写的是「擦身 / 落地 / 定格 / 群敌溃散」
       这类结果，打斗词密度天然低，只靠 `_FIGHT_RE` 会整镜漏挂。
    """
    explicit = shot.get("trigger_words", None)
    if explicit is not None:
        if isinstance(explicit, str):
            words = [w.strip() for w in explicit.split(",") if w.strip()]
        else:
            words = [str(w).strip() for w in explicit if str(w).strip()]
        return _order_triggers(words)

    if "is_fight" in shot and shot.get("is_fight") is False:
        return []                                   # 显式文戏镜
    if ("is_fight" in shot) or ("finisher" in shot) or ("slow" in shot):
        is_fight = bool(shot.get("is_fight"))
        finisher = bool(shot.get("finisher"))
        slow = bool(shot.get("slow"))
    else:
        body = _desc_body(shot.get("prompt", "") or "")
        _f = bool(_FIGHT_RE.search(body))
        _fin = bool(_FINISH_RE.search(body))
        _slow = bool(_SLOW_RE.search(body))
        # 「终结」命中即隐含打斗镜：终结技是打斗的终结。
        # 一打多清场的终结镜正文写的是「群敌失去立足点跌出画面 / 兵器脱手」这类**结果**，
        # 打斗关键词密度天然低——_FIGHT_RE 没命中时也要按 _fin 保留整镜的触发词。
        is_fight = _f or _fin
        finisher = _fin
        slow = is_fight and _slow        # 慢镜仍要求是打斗镜，文戏里的慢镜头不挂
    if not is_fight:
        return []
    words = [TRIGGER_CAMERA, TRIGGER_FIGHT]
    if finisher or slow:
        words.append(TRIGGER_FINISH)                # prslow1 必须与 prfin1 同现
    if slow:
        words.append(TRIGGER_SLOW)
    return _order_triggers(words)


_TRIGGER_LINE_RE = re.compile(
    r"^[ \t]*(?:camera motion|prfight1|prfin1|prslow1)"
    r"(?:[ \t]*,[ \t]*(?:camera motion|prfight1|prfin1|prslow1))*[ \t]*$", re.M)


def inject_trigger_words(prompt, words):
    """把触发词行插进「详细描述 / detailed_description」标题的下一行（独占一行）。

    - words 为空 → 原样返回（纯文戏镜整行不写）。
    - 提示词里已有触发词行 → 就地合并（补齐缺的词、去重、按标准顺序重排），不另起一行。
    - 找不到该标题 → 退回插在整条提示词最前，并打印提示（正常六段式不会走到这一步）。
    """
    if not words:
        return prompt
    line = ", ".join(words)
    m = _TRIGGER_LINE_RE.search(prompt)
    if m:
        merged = _order_triggers([w.strip() for w in m.group(0).split(",") if w.strip()] + list(words))
        return prompt[:m.start()] + ", ".join(merged) + prompt[m.end():]
    h = _DESC_HEAD_RE.search(prompt)
    if h:
        return prompt[:h.end()] + "\n" + line + prompt[h.end():]
    print("⚠️ [触发词] 提示词里找不到「详细描述 / detailed_description」标题，触发词行改插在最前：%s" % line)
    return line + "\n" + prompt


def build_lines_data(shots, suppress_subtitles=True, no_humans=False, dynamic_action_check=True, world_constraint=None, auto_triggers=True):
    '\nshots: list of dict\n    duration : int (秒)\n    prompt   : str (六段式提示词，无引用模式下不含 <Audio>)\n    images   : list[str] 相对 fxai/image/ 的路径，逗号分隔拼进第3元素\n返回 JSON 字符串\n\nsuppress_subtitles: 默认 True。在每条提示词末尾追加 negative_constraints 段，\n    把画面字幕/文字排除在生成之外（字幕统一后期烧录）。\nno_humans: 宠物/动物版短剧用 True。在每条提示词末尾追加 NO_HUMANS_BLOCK，\n    画面只保留拟人化动物角色，人类/真人/人群全部排除。\ndynamic_action_check: 默认 True。轻量校验每条镜头的「摘要/详细描述」是否含动态动词\n    （走/跑/敲/搭/烤/追/扔/拍手/蹦跳…）。H3 不会自动给背景动物编动作，缺动态描述时\n    背景动物会像贴图静止——此检查在组装阶段就提醒补上（见 SKILL.md 三之二）。\n'














        # auto_triggers: 默认 True。自动给打斗镜挂触发词（Combat V2 + Camera Motion），
        # 落在「详细描述 / detailed_description」第一行；纯文戏镜整行不写。判定优先级：
        # shot["trigger_words"]（显式，"" / [] = 明确不挂） > shot["is_fight"]/["finisher"]/["slow"]
        # （显式布尔） > 关键词启发式。规则见 fxai-h3-video-prompt/references/07-触发词挂载规范.md。

    rows = []
    for s in shots:
        dur = int(s["duration"])
        prompt = s["prompt"]
        imgs = s["images"]
        # 无引用模式硬性校验：不应出现 <Audio
        if "<Audio" in prompt:
            raise ValueError("无引用音频模式下提示词不得包含 <Audio>；请改用 audio_mode='音色参考' 并在提示词写 Audio 段")
        # 轻量校验：内心独白/OS 包进 <d> 会被 TTS 念出来，提醒改掉（不阻断）
        import re as _re
        if _re.search(r"(内心|独白|\bOS\b)", prompt) and "<d>" in prompt:
            print("⚠️ 提示词疑似把「内心独白/OS」写进了 <d> 对白标签，会被念出声。请改成动作/表情描写（见 SKILL.md 第三章）。")
        # 轻量校验：宠物/动物版若提示词里出现「会画出真人」的正面表述，提醒确认（不阻断）。
        # 我们自己追加的 NO_HUMANS_BLOCK 是口语否定句（「画面里不要出现人类」），属正确写法，  # std:allow
        # 校验时按否定语境（不要/无/没有/禁用/规避/不含/空无/绝对/任何 紧邻人类词前）排除，只抓正面表述。  # std:allow
        if no_humans:
            _human_re = _re.compile(r"(人类|真人|人物|人群|路人|人类角色|男人|女人|人员)")
            for _m in _human_re.finditer(prompt):
                _ctx = prompt[max(0, _m.start() - 12):_m.start()]
                if _re.search(r"(不|无|没有|禁用|规避|不含|空无|绝对|任何)", _ctx):  # std:allow
                    continue   # 否定语境，属排除人类，跳过
                print("⚠️ [no_humans] 提示词里出现正面的人类相关表述「%s」，可能引真人入画，宠物版应只出现动物。"
                      % _m.group())
        # 轻量校验：场景参与者/动物要"动起来"
        # H3 不会自动给背景动物编动作；若摘要与详细描述都缺动态动词，背景动物会像贴图静止。
        if dynamic_action_check:
            _dyn_verbs = ("走|跑|动|敲|端|搭|烤|追|扔|拍|蹦|跳|晒|野|散|收|游|玩|聊|笑|闹|"
                          "挪|转|挥|举|坐|趴|扑|窜|聚|围|忙|喝|吃|睡|望|指|摆|晃|爬|"
                          "交头接耳|追逐|打闹|加油|欢呼|打盹|溜达|闲逛|嬉戏")
            _sum = _re.search(r"摘要\s*([\s\S]*?)(?=延续分析|详细描述)", prompt)
            _desc = _re.search(r"详细描述\s*([\s\S]*?)(?=整体音效|非叙事音乐)", prompt)
            _scene = "".join([(_sum.group(1) if _sum else ""), (_desc.group(1) if _desc else "")])
            if _scene and not _re.search(_dyn_verbs, _scene):
                print("⚠️ [动态] 该镜头「摘要/详细描述」里没检测到动态动作描写"
                      "（走/跑/敲/搭/烤/追/扔/拍手/蹦跳…），背景动物可能静止不动。"
                      "请在场景描述里加上「场景中的动物在做什么」（见 SKILL.md 三之二）。")
        # 触发词自动挂载：打斗镜 / 显式指定才挂；纯文戏镜整行不写。
        # 规则见 fxai-h3-video-prompt/references/07-触发词挂载规范.md
        if auto_triggers:
            _tw = resolve_trigger_words(s)
            if _tw:
                prompt = inject_trigger_words(prompt, _tw)
        if suppress_subtitles:
            prompt = prompt + NEGATIVE_CONSTRAINTS_BLOCK
        if no_humans:
            prompt = prompt + NO_HUMANS_BLOCK
        if world_constraint:
            prompt = prompt + "\n\nworld_setting:\n" + world_constraint
        rows.append([dur, prompt, ",".join(imgs)])
    return json.dumps(rows, ensure_ascii=False)


# ------------------------- 工作流组装 -------------------------
def build_workflow(shots,
                   workflow_path=None,
                   width=1280, height=768, fps=24,
                   audio_mode="系统生成",
                   save_dir="sucai",
                   start_index=0, end_index=-1,
                   single_shot=None,
                   segment_duration=None,
                   suppress_subtitles=True,
                   no_humans=False,
                   dynamic_action_check=True,
                   auto_triggers=True,
                   negative_prompt=None,
                   world_constraint=None,
                   min_episode_seconds=None,
                   export_six_prompt=True,
                   ep_dir=None):
    '组装短剧生成工作流。\n\nsingle_shot: 只重生成某一个镜头（用户口语"第N个镜头"，1-based）。\n    例如 single_shot=1 表示只重第1个镜头，内部自动设为 开始索引=结束索引=0（0-based）。\n    注意：双采版 merger 是整体一次性合并，单镜头重生成的产物是"那一个镜头的视频"，\n    不会自动替换回完整成片——要得到完整成片还需把单镜头产物拼回（见 SKILL.md「单镜头重生成」）。\n    若 start_index/end_index 已显式指定则 single_shot 被忽略。\n'







    # 可选的单集时长校验：默认关闭（本函数是通用入口，别的能力/技能也会走它）。
    # 短剧线路请走 render_episode（默认 min_episode_seconds=60），
    # 或直接传 min_episode_seconds=60 -> 不达标直接抛错，不提交渲染。
    if min_episode_seconds:
        planned = check_episode_duration(shots, min_episode_seconds)
        print("[时长校验] 本集已规划 %ds（下限 %ss，达标）" % (planned, min_episode_seconds))

    # 台词装不装得下（第4轮）：每句要 字数/3.3 + 气口 秒，塞不进它自己的时间轴区间就抛错。
    _speech_bad = audit_shot_speech(shots)
    for msg in _speech_bad:
        print("[台词校验] ✗ %s" % msg)
    if _speech_bad:
        raise ValueError(
            "有 %d 句台词在其时间轴区间里排不下（按 %.1f 字每秒 + %.1fs 气口算）：\n  %s\n"
            "把该句挪到更长的区间，或按 speech_seconds() 重算该段时长。"
            % (len(_speech_bad), SPEECH_CHARS_PER_SECOND, SPEECH_BREATH_SECONDS,
               "\n  ".join(_speech_bad)))

    require_comfyui()
    wf = json.load(open(workflow_path or DEFAULT_WORKFLOW, encoding="utf-8"))

    # 单镜头重生成：开始=结束=同一索引（0-based）
    if single_shot is not None:
        idx = int(single_shot) - 1
        start_index = idx
        end_index = idx

    # 1) 多场景数据（suppress_subtitles 在每条提示词末尾追加「画面字幕排除」约束；
    # no_humans 在宠物/动物版时追加"画面不得出现人类"约束；
    # auto_triggers 给打斗镜自动挂触发词（camera motion / prfight1…）到「详细描述」第一行）
    wf["1130"]["inputs"]["lines_data"] = build_lines_data(
        shots, suppress_subtitles=suppress_subtitles, no_humans=no_humans,
        dynamic_action_check=dynamic_action_check, world_constraint=world_constraint,
        auto_triggers=auto_triggers)

    # 2) 生成控制器：尺寸/帧率 + 结束索引=-1（核心）
    ctl = wf["993"]["inputs"]
    ctl["宽度"] = width
    ctl["高度"] = height
    ctl["帧率"] = fps
    ctl["开始索引"] = start_index
    ctl["结束索引"] = end_index   # -1 = 跑完全部分段，不截断
    if segment_duration is not None:
        ctl["场景分段时长"] = int(segment_duration)   # 不给则保留模板默认的 lines_data 联动

    # 3) 音频模式（宠物/卡通=系统生成，真人=音色参考/原音频）
    if audio_mode:
        wf["1155"]["inputs"]["音频模式"] = audio_mode

    # 4) 负面提示词：尽力注入到视频生成节点（模板暴露负面字段才生效；否则靠提示词内嵌抑制）
    if negative_prompt:
        ok = _inject_negative_prompt(wf, negative_prompt)
        if not ok:
            print("⚠️ 当前工作流 1155 节点未暴露负面提示词字段，已退回用提示词内嵌的"
                  "「负面约束段」(negative_constraints) 抑制字幕。如需原生负面提示词，请在工作流里手动加一个负面输入框。")

    # 5) 保存目录
    wf["1160"]["inputs"]["保存目录"] = save_dir

    # 6) 每集必交付：六段式提示词组装完成即落盘，不留到事后补
    if export_six_prompt:
        _export_six_prompt(shots, ep_dir=ep_dir or _caller_ep_dir(),
                           world_constraint=world_constraint,
                           width=width, height=height, audio_mode=audio_mode)
    return wf


def _caller_ep_dir():
    """自动定位「集目录」：沿调用栈找第一个不是本模块、且带剧集特征文件的目录。

    集目录特征：`assets_map.json` / `完整分镜脚本.md` / `project_def.json` 任一存在。
    非剧集调用（如纯 MV）基本不会命中，命中不了就静默跳过导出。
    """
    mine = os.path.dirname(os.path.abspath(__file__))
    try:
        f = sys._getframe()
        for _ in range(40):
            f = f.f_back
            if f is None:
                break
            path = f.f_code.co_filename
            if not path or path.startswith("<"):
                continue
            d = os.path.dirname(os.path.abspath(path))
            if d == mine:
                continue
            for probe in ("assets_map.json", "完整分镜脚本.md", "project_def.json"):
                if os.path.isfile(os.path.join(d, probe)):
                    return d
    except Exception:
        pass
    return None


def _export_six_prompt(shots, ep_dir, world_constraint="", width=1280, height=768,
                       audio_mode="系统生成"):
    """把六段式提示词写成 `<集目录>/镜头六段式提示词.md`（每集必交付项，失败只告警不拦渲染）。"""
    if not ep_dir:
        return None
    try:
        sys.path.insert(0, SKILL_DIR)
        import export_shot_prompts as EXP
        out = EXP.export_md(shots, ep_dir, w1=world_constraint or "",
                            width=width, height=height, audio_mode=audio_mode)
        total = sum(int(s["duration"]) for s in shots)
        print("[六段式] 已导出 %d 镜 / %d 秒 -> %s" % (len(shots), total, out), flush=True)
        return out
    except Exception as e:
        print("[六段式] 导出失败（不拦渲染）：%r" % (e,), flush=True)
        return None


# ------------------------- 提交与产物查询 -------------------------
def _shot_query_mod():
    """载入公共层分镜进度查询（fxai-workflow/shot_query.py），失败返回 None。"""
    import importlib.util
    path = os.path.join(SKILL_DIR, "..", "fxai-workflow", "shot_query.py")
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("fxai_shot_query", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def submit_workflow(wf):
    data = json.dumps({"prompt": wf}).encode("utf-8")
    req = urllib.request.Request(
        COMFY + "/prompt", data=data, headers={"Content-Type": "application/json"}
    )
    r = json.load(urllib.request.urlopen(req, timeout=60))
    pid = r.get("prompt_id")
    # 提交即落工作流快照：/history 是内存态，服务重启后查不回来，断线续跑靠这份快照
    if pid:
        try:
            sq = _shot_query_mod()
            if sq is not None:
                p = sq.save_snapshot(pid, wf, server=COMFY)
                print("[快照] 已存工作流快照（供 resume 续跑）:", p, flush=True)
        except Exception as e:
            print("[快照] 写入失败（不拦提交）: %r" % (e,), flush=True)
    return pid


def query_task_result_raw(prompt_id, timeout=30):
    """单次查询 /fxai/tasks/result（fxai-toolkit 节点不写 /history，结果存服务器任务库）。"""
    url = COMFY + "/fxai/tasks/result"
    body = json.dumps({"prompt_ids": prompt_id}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _find_merged(obj):
    """递归在任务结果里找包含 merged 目录名的路径（=最终合并片）。"""
    if isinstance(obj, str):
        return obj if "merged" in obj else None
    if isinstance(obj, dict):
        for v in obj.values():
            r = _find_merged(v)
            if r:
                return r
    if isinstance(obj, list):
        for v in obj:
            r = _find_merged(v)
            if r:
                return r
    return None


# 等待过程默认**安静**：进度只在记录里，不往对话/终端刷；要实时看就置 True。
WAIT_VERBOSE = False


def wait_for_merged(prompt_id, timeout=5400, poll=20):
    """等整集渲染+合并完成，返回 merged 路径（None=超时或失败）。

    优先走全局广播监听（技能 fxai-comfyui-watcher）：逐镜打印「第 N/M 个场景开始/已生成」，
    完成事件一到就返回；监听起不来或异常时回落到结果表轮询。
    """
    w = _load_watcher()
    if w is not None:
        try:
            lis = w.Listener(COMFY)
            if WAIT_VERBOSE:
                print("[短剧] 广播监听已连上 %s（clientId=%s）" % (COMFY, lis.cid), flush=True)
            try:
                t = lis.wait(prompt_id, timeout=timeout, verbose=WAIT_VERBOSE, tag="短剧",
                             idle_fallback=90)
            finally:
                lis.close()
            if t and t.get("status") == "success":
                for _ in range(4):                      # 完成后取一次 url 令牌
                    try:
                        path = _find_merged(query_task_result_raw(prompt_id))
                    except Exception:
                        path = None
                    if path:
                        return path
                    time.sleep(4)
                return None
            if t and t.get("status") in ("error", "interrupted"):
                print("[短剧] 任务未成功：%s" % (t.get("error") or t.get("status")), flush=True)
                return None
        except Exception as e:
            print("[短剧] 广播监听起不来，回落轮询：%r" % (e,), flush=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            path = _find_merged(query_task_result_raw(prompt_id))
            if path:
                return path
        except Exception:
            pass
        time.sleep(poll)
    return None


def gen_episode(shots, **kw):
    """组装+提交+等待合并完成，返回 (prompt_id, merged路径)。"""
    wf = build_workflow(shots, **kw)
    pid = submit_workflow(wf)
    print("提交 prompt_id:", pid, flush=True)
    res = wait_for_merged(pid)
    return pid, res


# ------------------------- 后期字幕烧录（复用 fxai-lyric-subtitle 技能） -------------------------
def _lyric_subtitle_script():
    """定位 fxai-lyric-subtitle 技能的烧录脚本（与 fxai-short-drama 同级的 skills 目录下）。"""
    if os.environ.get("LYRIC_SUBTITLE_SCRIPT"):
        return os.environ["LYRIC_SUBTITLE_SCRIPT"]
    cands = [
        os.path.join(SKILL_DIR, "..", "fxai-lyric-subtitle", "scripts", "gen_lyric_subtitle.py"),
        os.path.join(SKILL_DIR, "..", "..", "fxai-lyric-subtitle", "scripts", "gen_lyric_subtitle.py"),
    ]
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    return None


def burn_subtitles(merged_mp4, script_txt,
                   out_name=None, out_dir=None,
                   karaoke=False, no_burn=False,
                   python_exe=None, transcript=None):
    '\n后期给成片烧录字幕（MV 同款方案，最稳）。\n\n流程（委托 fxai-lyric-subtitle 技能脚本）：\n  抽音轨 → whisper 提时间戳 → 与我们的台词做强制对齐（显示文字一律用我们的台词，ASR 只给时间戳）\n  → 生成 SRT/ASS → (可选) ffmpeg(libass) 烧录。\n\n关键：显示的字幕文字 = 我们的台词 script_txt（不会用 ASR 识别错的字）；\n同时输出 _alignment_record.json（每行 {lyric:我们的词, whisper:模型听到的}），\n方便后期人工对照挑错别字。\n\n参数：\n  merged_mp4 : H3 生成的合并成片（画面应已无字幕）\n  script_txt : 台词文本路径，每行一句（按对话顺序，与音频对齐）\n  out_name   : 输出名（默认 <原文件名>_sub）\n  out_dir    : 输出目录（默认与成片同目录）\n  karaoke    : 是否做平均卡拉OK变色（默认 False=普通字幕）\n  no_burn    : 只生成 SRT/ASS 不烧录\n  transcript : 已有 whisper 分段文件可跳过识别\n返回：烧录后视频路径（no_burn 时返回 .ass 路径）。\n依赖：fxai-lyric-subtitle 技能 + 凤希AI伴侣的 whisper / 带 libass 的 ffmpeg（见其 config.json）。\n'





















    script = _lyric_subtitle_script()
    if not script:
        raise RuntimeError("找不到 fxai-lyric-subtitle 技能脚本，无法烧录字幕。"
                           "请确认已安装该技能，或用环境变量 LYRIC_SUBTITLE_SCRIPT 指定路径。")
    if not os.path.exists(script_txt):
        raise FileNotFoundError("台词文本不存在: " + script_txt)
    py = python_exe or sys.executable
    out_dir = out_dir or os.path.dirname(os.path.abspath(merged_mp4))
    out_name = out_name or (os.path.splitext(os.path.basename(merged_mp4))[0] + "_sub")
    cmd = [py, script, "--video", merged_mp4, "--lyrics", script_txt,
           "--out-dir", out_dir, "--out-name", out_name]
    if karaoke:
        cmd.append("--karaoke")
    if no_burn:
        cmd.append("--no-burn")
    if transcript:
        cmd += ["--transcript", transcript]
    print("▶ 烧录字幕:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    if no_burn:
        return os.path.join(out_dir, out_name + ".ass")
    return os.path.join(out_dir, out_name + ".mp4")


# ------------------------- 提交骨架（写 pid + 写 manifest） -------------------------
def render_episode(project_root, ep, shots, **kw):
    """组装+提交+等待合并+写 ep{N}_pid.txt + 写回 manifest。返回 (pid, merged)。
    world_constraint 默认从本系列 project_def.json 的 world_constraint 字段自动取（全动物世界观句），
    也可在 kw 里显式覆盖。
    """
    kw.setdefault("world_constraint", (load_project_def(project_root)[1] or {}).get("world_constraint"))
    # 短剧线路默认卡单集时长下限（60 秒），规划阶段就拦。
    kw.setdefault("min_episode_seconds", MIN_EPISODE_SECONDS)
    wf = build_workflow(shots, **kw)
    pid = submit_workflow(wf)
    pid_file = os.path.join(project_root, f"ep{ep}_pid.txt")
    with open(pid_file, "w", encoding="utf-8") as f:
        f.write(pid)
    print("提交 prompt_id:", pid, "->", pid_file, flush=True)
    merged = wait_for_merged(pid)
    _write_ep_manifest(project_root, ep, pid, merged)
    return pid, merged


def _write_ep_manifest(project_root, ep, pid, merged):
    mpath = os.path.join(project_root, "scenes_manifest.json")
    m = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {}
    m[f"ep{ep}_prompt_id"] = pid
    m["merged_url"] = merged or ""
    m["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    json.dump(m, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[归档] {mpath} 已更新（ep{ep}_prompt_id / merged_url）")


# ------------------------- 验收+烧录 SOP（短剧：镜头锚定对齐，不依赖 MV 三段式补齐） -------------------------
# ffmpeg/ffprobe 基准：以软件安装目录（fxai_global.json software_install_dir）为准，
# 不读可能指向目录的环境变量（目录会让 subprocess 报 WinError 5）。
# 候选优先级：全局配置 fxai_global.json 的 ffmpeg_path/ffprobe_path（真实文件）
#             > env FFMPEG_PATH/FFPROBE_PATH（仅当是真实文件，目录直接忽略）
#             > software_install_dir/ffmpeg/<name>
def _global_cfg():
    '软件安装/数据目录等同全局配置，直接复用模块顶部已加载的那一份，只读一次文件。'
    return _CFG


_GLOBAL = _global_cfg()


def _software_install_dir():
    return _CFG.get("software_install_dir") or _GLOBAL.get("software_install_dir") or ""


def _resolve_bin(name):
    '在候选路径里返回第一个真实存在的可执行文件；忽略指向目录/不存在的 env 覆盖。'
    env_key = "FFMPEG_PATH" if name == "ffmpeg.exe" else "FFPROBE_PATH"
    cfg_key = "ffmpeg_path" if name == "ffmpeg.exe" else "ffprobe_path"
    cands = []
    sid = _software_install_dir()
    if sid:
        cands.append(os.path.join(sid, "ffmpeg", name))
    # env 覆盖：仅当是真实文件才采用（过滤指向目录的 FFMPEG_PATH，防 WinError 5）
    env = os.environ.get(env_key)
    if env and os.path.isfile(env):
        cands.insert(0, env)
    # 全局配置显式指定（真实文件才采用）
    cfgp = _CFG.get(cfg_key)
    if cfgp and os.path.isfile(cfgp):
        cands.insert(0, cfgp)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return cands[0] if cands else name


def _ffmpeg_path():
    return _resolve_bin("ffmpeg.exe")


def _ffprobe_path():
    return _resolve_bin("ffprobe.exe")


SUBTITLE_STYLE = ("FontName=Microsoft YaHei,FontSize=18,PrimaryColour=&HFFFFFF,"
                  "OutlineColour=&H000000,Outline=2,Bold=1")


def fetch_merged_url(pid):
    """按 prompt_id 查任务结果，返回 merged 令牌（含 'merged' 的路径字符串）。"""
    res = query_task_result_raw(pid)
    return _find_merged(res) or ""


def verify_video(raw):
    """ffprobe 验收：返回 (时长秒 float, 有音轨 bool)。"""
    ffprobe = _ffprobe_path()
    dur = subprocess.check_output(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", raw]).decode().strip()
    streams = subprocess.check_output(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", raw]).decode().split()
    print(f"[验收] 时长={dur}s  视频={'video' in streams} 音频={'audio' in streams}")
    return float(dur), "audio" in streams


def extract_audio(raw, out):
    subprocess.run([_ffmpeg_path(), "-y", "-i", raw, "-vn",
                    "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", out], check=True)
    print(f"[验收] 音轨已抽 -> {out}")


def burn_subtitles_local(raw, srt, out, style=SUBTITLE_STYLE, denoise=False):
    """libass 烧录（传 srt 的 basename + cwd=输出目录，规避 ffmpeg 把路径冒号误当滤镜分隔符）。
    denoise=True 时叠加轻度去噪（视频 hqdn3d + 音频 afftdn），需 ffmpeg 带对应滤镜
    （本环境 full_build 自带，软件目录 ffmpeg 即此版本）。
    """
    srt_name = os.path.basename(srt)
    vf = f"subtitles={srt_name}:force_style='{style}'"
    if denoise:
        vf += ",hqdn3d=2:1:3:3"
    cmd = [_ffmpeg_path(), "-y", "-i", raw,
           "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-c:a", "aac", "-b:a", "192k", out]
    if denoise:
        cmd += ["-af", "afftdn=n=12"]
    subprocess.run(cmd, check=True, cwd=os.path.dirname(os.path.abspath(out)))
    print(f"[烧录] 完成 -> {out}" + ("（已叠加去噪）" if denoise else ""))


def acceptance_burn(project_root, ep, shots, title_hint=None, style=SUBTITLE_STYLE, denoise=False):
    """短剧验收+烧录一体化（镜头锚定对齐，不依赖 MV 三段式补齐 distribute_by_char）。
    流程：读 ep{N}_pid.txt -> 查 merged 令牌 -> HTTP 下载 -> ffprobe 验收 -> 段映射 ->
    抽音轨 -> gen_subtitle_aligned.align_standard 镜头锚定词级对齐 -> libass 烧录 -> 写回 manifest。
    - shots: 本集镜头列表（含 duration + <d> 台词），用于段映射与镜头锚定对齐。
    - title_hint: 成片文件名里的集名（如 '裁员风波'）。
    - denoise: True 时烧录叠加轻度去噪（需软件目录 ffmpeg 的 full_build 滤镜）。
    ffmpeg/ffprobe 基准以软件安装目录（fxai_global.json software_install_dir）为准，
    不写死本机绝对路径。
    返回 (成片路径, 字幕路径)。
    """
    sys.path.insert(0, SKILL_DIR)
    import gen_subtitle_aligned as SA

    pid_file = os.path.join(project_root, f"ep{ep}_pid.txt")
    pid = open(pid_file, encoding="utf-8").read().strip()
    if not pid:
        raise RuntimeError(f"未读到 prompt_id：{pid_file}")
    print("[prompt_id]", pid)

    merged_url = fetch_merged_url(pid)
    if not merged_url or "merged" not in merged_url:
        raise RuntimeError(f"未拿到 merged url（渲染可能未完成或超时）：{merged_url}")
    print("[查得] merged url =", merged_url)

    raw = os.path.join(project_root, f"fxai_ep{ep}_raw.mp4")
    C._download(merged_url, raw)
    print("[下载] 完成 ->", raw)

    verify_video(raw)
    # 段映射（仅参考，不卡验收）
    bounds = SA.shot_boundaries(shots)
    print(f"[段映射] 镜数={len(shots)} 累计={bounds[-1]:.3f}s")

    audio = os.path.join(project_root, f"audio_ep{ep}.wav")
    extract_audio(raw, audio)

    srt = os.path.join(project_root, f"subtitles_ep{ep}_word.srt")
    SA.align_standard(shots, srt, audio_path=audio)   # 短剧用镜头锚定，不用 MV 三段式补齐
    print("[对齐] 字幕 ->", srt)

    if title_hint:
        final = os.path.join(project_root, f"成片_ep{ep}_{title_hint}_sub.mp4")
    else:
        final = os.path.join(project_root, f"成片_ep{ep}_sub.mp4")
    burn_subtitles_local(raw, srt, final, style=style, denoise=denoise)

    mpath = os.path.join(project_root, "scenes_manifest.json")
    m = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {}
    m[f"ep{ep}_prompt_id"] = pid
    m["merged_url"] = merged_url
    m["final_video"] = os.path.basename(final)
    m["subtitle"] = os.path.basename(srt)
    m["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    json.dump(m, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[归档] scenes_manifest.json 已更新（final_video / subtitle）")

    print("\n== 验收+烧录完成 ==")
    print("成片:", final)
    print("字幕:", srt)
    print("请人工目检成片是否混入真人（本环境无法读图）。")
    return final, srt


# ------------------------- CLI -------------------------
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="fxai-short-drama 短剧管线")
    p.add_argument("--check", action="store_true", help="只检测 ComfyUI 与模板")
    p.add_argument("--lines-data", default=None, help="shots JSON 路径（list of {duration,prompt,images}）")
    p.add_argument("--audio-mode", default="系统生成", choices=["系统生成", "音色参考", "原音频"])
    p.add_argument("--save-dir", default="sucai")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=768)
    p.add_argument("--segment-duration", type=int, default=None, help="场景分段时长（秒），不填则按 lines_data 联动")
    p.add_argument("--no-suppress-subtitles", action="store_true",
                   help="关闭「生成阶段画面字幕排除」约束（默认开启，字幕统一后期烧录）")
    p.add_argument("--negative-prompt", default=None,
                   help="负面提示词（若工作流 1155 节点暴露负面字段则注入；默认不单独传，用内置 ANTI_SUBTITLE）")
    # 后期字幕烧录模式
    p.add_argument("--burn", action="store_true", help="烧录字幕模式：对已成片叠加字幕（需 --merged + --lyrics）")
    p.add_argument("--merged", default=None, help="--burn 模式：H3 合并成片路径")
    p.add_argument("--lyrics", default=None, help="--burn 模式：台词文本路径（每行一句，作为字幕真值）")
    p.add_argument("--out-dir", default=None, help="--burn 模式：输出目录（默认与成片同目录）")
    p.add_argument("--out-name", default=None, help="--burn 模式：输出名（默认 <成片名>_sub）")
    p.add_argument("--karaoke", action="store_true", help="--burn 模式：做平均卡拉OK逐字变色")
    p.add_argument("--no-burn", action="store_true", help="--burn 模式：只生成 SRT/ASS 不烧录")
    # 单镜头重生成（"只重某一级"）：开始索引=结束索引=同一镜头（1-based 第N个镜头）
    p.add_argument("--single-shot", type=int, default=None,
                   help="只重生成某一个镜头（1-based 第N个镜头）。例如 --single-shot 1 = 只重第1个镜头"
                        "（内部设为 开始索引=结束索引=该镜头下标）。注意：双采版 merger 整体合并，"
                        "单镜头产物是『单镜头视频』，不会自动替换回完整成片，需再用 ffmpeg 拼回。")
    args = p.parse_args()

    if args.burn:
        if not args.merged or not args.lyrics:
            print("ERROR: --burn 需要同时提供 --merged 与 --lyrics", file=sys.stderr)
            sys.exit(1)
        out = burn_subtitles(args.merged, args.lyrics,
                             out_name=args.out_name, out_dir=args.out_dir,
                             karaoke=args.karaoke, no_burn=args.no_burn)
        print("烧录产物:", out)
        sys.exit(0)

    if args.check:
        print("ComfyUI:", "online" if check_comfyui() else "OFFLINE")
        print("模板存在:", os.path.exists(DEFAULT_WORKFLOW))
        print("资源模式:", "服务器模式（fxai/image/ 相对路径，无本机根目录）")
        if args.negative_prompt:
            print("负面提示词(将尝试注入):", args.negative_prompt)

    # 提交模式：传入 shots JSON（--lines-data）即组装+提交+等待合并
    if args.lines_data:
        shots = json.load(open(args.lines_data, encoding="utf-8"))
        wf = build_workflow(
            shots,
            audio_mode=args.audio_mode,
            save_dir=args.save_dir,
            width=args.width, height=args.height,
            segment_duration=args.segment_duration,
            suppress_subtitles=not args.no_suppress_subtitles,
            negative_prompt=args.negative_prompt,
            single_shot=args.single_shot,
        )
        pid = submit_workflow(wf)
        print("提交 prompt_id:", pid, flush=True)
        res = wait_for_merged(pid)
        print("MERGED:", res)
        sys.exit(0)