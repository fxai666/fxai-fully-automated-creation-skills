#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fxai-standard 规范体检：过程叙述 / 否定式 / 本机绝对路径 / SKILL.md 体积 /
路由三要素 / 项目清单契约 / 垃圾文件。

用法：
    python check_standard.py <技能目录> [--junk] [--fix]
    python check_standard.py --skills-root <技能根目录> [--junk] [--fix]

--fix 只补「项目清单」指针段（幂等，已含 fxai_project.py 即跳过）。
判据与分级见 references/04-规范体检.md；退出码：有 ERROR 返回 1，否则 0。
"""
import argparse
import os
import re
import sys

sys.dont_write_bytecode = True

# SKILL.md 体积闸门：按文件字节数判定（右键属性即得，不必跑脚本）
# 中英混排实测字节/字符比 1.68~1.70，9216 B ≈ 5400 字符；纯中文 9216 B ≈ 3070 汉字
MAX_SKILL_BYTES = 9 * 1024    # 超此即 ERROR
WARN_SKILL_BYTES = 8 * 1024   # 超此提示细节降级到 references
SCAN_SUFFIX = (".md", ".py", ".json", ".js", ".sh")

# 过程叙述判定词（行内命中即 ERROR；豁免见 EXEMPT_CTX / ALLOW_MARKS）
NARRATIVE = [  # std:allow
    "已修", "踩坑", "试错", "症状", "复测",
    "实测数据", "演示产物", "成果演示", "上一版",
    "以前", "原来", "不再", "导致", "后来我们", "后来发现", "改成现在这样",
    "用户发现", "用户点名", "用户反馈",
    "示例", "样例", "范例", "示范", "范句",
    "心得",
]

# 否定式措辞（WARN；表禁用改用「不得 / 禁用 / 勿」）
NEGATION = ["不要", "禁止", "避免", "不能", "不宜"]  # std:allow

# 成句示范标记（ERROR）：文档里给「可直接照抄的整句」= 模板源，会被逐条套（02 册总则）。
# 规则只写判据与词级短语；枚举表的可用性标记（✅/❌ 运镜名）不算成句。
DEMO_MARK = ["正确写法", "错误写法", "写法示例", "正确示例", "错误示例", "照抄以下", "照此写"]  # std:allow

# 语境豁免：行内含这些词时不判过程叙述（平台用词 / 词表 / 功能名等合法表述）
EXEMPT_CTX = (
    "用词", "词表", "措辞", "标签", "话题", "可选", "枚举", "话术", "提示语",
    "感受词", "关键词", "违禁", "判据", "命中", "功能名", "接口名", "叙述", "规则",
)

# 标记豁免：行尾加隐藏标记显式放过
ALLOW_MARKS = ("<!-- std:allow -->", "# std:allow")

# 打包资产：正文即变更记录，不进正文判据
ASSET_SKIP = ("更新日志.md",)

# 垃圾文件 / 目录（--junk 时检查）
JUNK_DIRS = {"__pycache__", ".ipynb_checkpoints"}
JUNK_FILES = (".pyc", ".pyo", ".bak", ".DS_Store", "Thumbs.db")

# 路由三要素
ROUTE_TABLE_KEYS = ("文件", "什么时候读")
ROUTE_ONLY_LINE_KEYS = ("只读对应的那一册", "其余一律不读", "只读对应")

# ---------- 路径可移植 ----------
# 盘符绝对路径写进技能包即不可移植：字体 / 浏览器 / 私钥 / 输出目录一律按环境解析。
ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_/.:])[A-Za-z]:[\\/][A-Za-z][^\s\"'`,;)\]}]*")
# 系统目录缺省值放行（OS 定义，非本机物理路径）：根目录与其下子路径同样放行，
# 字体目录 C:/Windows/Fonts/... 是各机同位的系统路径，不属于本机私有盘符。
SYS_ROOT_OK = {"c:\\windows", "c:/windows"}
SYS_PREFIX_OK = ("c:\\windows\\", "c:/windows/")
PATH_HINT = "本机绝对路径（按环境变量 / 技能目录 / 工作区相对路径解析）"

# ---------- 项目清单契约 ----------
# 产出型技能 → 清单 category。新技能接入清单时在此登记一行（键=技能目录名）。
PRODUCERS = {
    "fxai-short-drama": "drama",
    "fxai-mv-pipeline": "mv",
    "fxai-ad-video": "ad",
    "fxai-poster": "image",
    "fxai-comfyui-image-gen": "image",
    "fxai-face-edit": "remake",
    "fxai-yue2": "music",
    "fxai-vocal-separate": "music",
    "fxai-rvc-convert": "music",
    "fxai-novel-writing": "novel",
    "fxai-digital-human-video": "voiceover",
    "fxai-voiceover": "voiceover",
    "fxai-slide-video": "voiceover",
    "fxai-weixin-gzh-publisher": "wechat",
    "fxai-video-upscale": "other",
}
EPISODIC = ("drama", "anime")

# SKILL.md 里必须出现的接入证据：脚本名 + init 调用；建档时机单独判 WARN
MANIFEST_KEYS = ("fxai_project.py", " init ")
MANIFEST_HINT = (
    "项目清单契约未接入：SKILL.md 缺 fxai_project.py 的 init 调用"
    "（跑 --fix 自动补齐）"
)
PRODUCER_HINT = "疑似产出型技能，未登记到 check_standard.py 的 PRODUCERS，清单契约未校验"
WHEN_HINT = "清单段落缺「建档」时机说明（口径：产物目录建好即建档、每完成一段更新）"

# ---------- GPU 提交前门禁契约 ----------
# 判据落在真正提交的东西上：公共层 POST /prompt 之前须调门禁，门禁实现须随本技能出包。
# 规则正文见 references/01-产出铁律.md「GPU 提交前门禁」。
GATE_SCRIPT_REL = os.path.join("scripts", "check_gpu_gate.py")
GATE_IMPL_MARKS = ("def gate(", "/fxai/guard/recover")
GATE_CALL_MARK = "gpu_gate(url=url"
PROMPT_SUBMIT_MARK = '+ "/prompt"'
GATE_IMPL_HINT = "GPU 提交前门禁实现缺失或不完整：scripts/check_gpu_gate.py 须提供 gate() 与同步硬回收"
GATE_MISSING_HINT = "GPU 提交前门禁未接线：comfy_client.py 的 POST /prompt 之前须调用 gpu_gate()"
GATE_ORDER_HINT = "GPU 提交前门禁接线位置有误：gpu_gate() 出现在 POST /prompt 之后"

# ---------- 500 / 显存自愈契约 ----------
# 探测优先、阈值短路、硬件实况判因、自愈一次重投须同时落在实现与提交点两处。
# 规则正文见 references/01-产出铁律.md「GPU 提交前门禁」。
HEAL_IMPL_MARKS = (
    "def self_heal(", "def is_clean(", "def residual_gib(",
    "def looks_like_memory_error(", "/fxai/guard/status",
    "释放前探测", "跳过（释放前探测已低于阈值）",
    "recover_calls", "SKIP_RECOVER_GIB",
    '"hardware": hw', '"error_log": st.get("error_log")',
    '"models_loaded": st.get("models_loaded")',
    "looks_like_memory_error(err_log)", "looks_like_memory_error(last_error)",
)
HEAL_SHORT_MARK = "跳过（释放前探测已低于阈值）"
HEAL_UNLOAD_MARK = "rec = unload(base, reason)"
HEAL_CLIENT_MARKS = (
    "def gpu_self_heal(", "G.self_heal(", "def _heal_and_retry(",
    "heal_rounds", "heal_used",
)
HEAL_FIVEXX_MARK = "e.code >= 500"
HEAL_FIVEXX_CALL = "if gpu_self_heal(url=url"
HEAL_NET_RETRY_MARK = "net_try += 1"
HEAL_TERMINAL_MARK = 'term.get("status") == "error"'
HEAL_TERMINAL_CALL = "_heal_and_retry(wf, timeout"
HEAL_IMPL_HINT = ("500/显存自愈实现不完整：scripts/check_gpu_gate.py 须提供 self_heal() "
                  "与释放前探测短路、硬件实况判因")
HEAL_PROBE_HINT = ("500/显存自愈实现顺序有误：gate() 须先探测短路放行，再按需卸载全部模型")
HEAL_CLIENT_HINT = ("500/显存自愈未接线：comfy_client.py 须提供 gpu_self_heal() "
                    "与 _heal_and_retry() 并在 5xx / 执行期错误两处调用")
HEAL_ORDER_HINT = ("500/显存自愈接线位置有误：5xx 分支须在计网络重试次数之前"
                   "调用 gpu_self_heal()")

# ---------- 视频帧数验收契约 ----------
# 判据落在真正产视频的脚本上：共用实现与对比片生成脚本两处同查。
# 规则正文见 references/01-产出铁律.md「视频帧数验收」。
FRAME_IMPL_REL = os.path.join("scripts", "video_frames.py")
FRAME_IMPL_MARKS = ("def decoded_frames(", "-count_frames")
FRAME_CALL_MARK = "assert_frames(out, n)"
FRAME_GEN_MARK = "run(cmd)"
FRAME_IMPL_HINT = "视频帧数验收实现缺失或不完整：scripts/video_frames.py 须提供 decoded_frames() 与 -count_frames 实测"
FRAME_MISSING_HINT = "视频帧数验收未接线：make_compare.py 生成对比片后须调 assert_frames()"
FRAME_ORDER_HINT = "视频帧数验收接线位置有误：assert_frames() 出现在生成命令之前"


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def check_gpu_gate_contract(root, name, errors):
    """门禁是代码行为：提交点接线与门禁实现两处同时受检。"""
    if name == "fxai-standard":
        path = os.path.join(root, GATE_SCRIPT_REL)
        text = _read_text(path)
        if not os.path.isfile(path) or any(m not in text for m in GATE_IMPL_MARKS):
            errors.append(GATE_IMPL_HINT)
        return
    if name == "fxai-workflow":
        path = os.path.join(root, "comfy_client.py")
        if not os.path.isfile(path):
            return
        text = _read_text(path)
        submit_at = text.find(PROMPT_SUBMIT_MARK)
        if submit_at < 0:
            return
        call_at = text.find(GATE_CALL_MARK)
        if call_at < 0:
            errors.append(GATE_MISSING_HINT)
        elif call_at > submit_at:
            errors.append(GATE_ORDER_HINT)


def check_gpu_self_heal_contract(root, name, errors):
    """探测优先与自愈重投是代码行为：实现与提交点两处同时受检。"""
    if name == "fxai-standard":
        path = os.path.join(root, GATE_SCRIPT_REL)
        text = _read_text(path)
        if not os.path.isfile(path) or any(m not in text for m in HEAL_IMPL_MARKS):
            errors.append(HEAL_IMPL_HINT)
        short_at = text.find(HEAL_SHORT_MARK)
        unload_at = text.find(HEAL_UNLOAD_MARK, short_at if short_at >= 0 else 0)
        if short_at < 0 or unload_at < 0:
            errors.append(HEAL_PROBE_HINT)
        return
    if name == "fxai-workflow":
        path = os.path.join(root, "comfy_client.py")
        if not os.path.isfile(path):
            return
        text = _read_text(path)
        if any(m not in text for m in HEAL_CLIENT_MARKS):
            errors.append(HEAL_CLIENT_HINT)
            return
        fivexx_at = text.find(HEAL_FIVEXX_MARK)
        call_at = text.find(HEAL_FIVEXX_CALL, fivexx_at if fivexx_at >= 0 else 0)
        net_at = text.find(HEAL_NET_RETRY_MARK)
        if fivexx_at < 0 or call_at < 0 or net_at < 0 or call_at > net_at:
            errors.append(HEAL_ORDER_HINT)
        term_at = text.find(HEAL_TERMINAL_MARK)
        term_heal_at = text.find(HEAL_TERMINAL_CALL, term_at if term_at >= 0 else 0)
        if term_at < 0 or term_heal_at < 0 or term_heal_at - term_at > 400:
            errors.append(HEAL_CLIENT_HINT)


def check_video_frame_contract(root, name, errors):
    """帧数判据落在真正产视频的脚本上：共用实现与对比片生成脚本两处同查。"""
    if name == "fxai-standard":
        path = os.path.join(root, FRAME_IMPL_REL)
        text = _read_text(path)
        if not os.path.isfile(path) or any(m not in text for m in FRAME_IMPL_MARKS):
            errors.append(FRAME_IMPL_HINT)
        return
    if name == "fxai-face-edit":
        path = os.path.join(root, "scripts", "make_compare.py")
        if not os.path.isfile(path):
            return
        text = _read_text(path)
        gen_at = text.find(FRAME_GEN_MARK)
        if gen_at < 0:
            return
        call_at = text.find(FRAME_CALL_MARK)
        if call_at < 0:
            errors.append(FRAME_MISSING_HINT)
        elif call_at < gen_at:
            errors.append(FRAME_ORDER_HINT)


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in sorted(filenames):
            if name.endswith(SCAN_SUFFIX):
                yield os.path.join(dirpath, name)


def line_exempt(line):
    if any(mark in line for mark in ALLOW_MARKS):
        return True
    return any(word in line for word in EXEMPT_CTX)


def check_text(path, rel, errors, warns):
    if os.path.basename(path) in ASSET_SKIP:
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        warns.append("%s: 读取失败 %s" % (rel, exc))
        return
    for lineno, line in enumerate(lines, 1):
        if any(mark in line for mark in ALLOW_MARKS):
            continue
        if not line_exempt(line):
            for word in NARRATIVE:
                if word in line:
                    errors.append("%s:%d: 过程叙述「%s」" % (rel, lineno, word))
        for word in NEGATION:
            if word in line:
                warns.append("%s:%d: 否定式「%s」" % (rel, lineno, word))
        for word in DEMO_MARK:
            if word in line:
                errors.append("%s:%d: 成句示范「%s」（改判据，见 02 册总则）" % (rel, lineno, word))
        for m in ABS_PATH_RE.finditer(line):
            hit = m.group(0).rstrip(".,:;")
            low = hit.lower()
            if low in SYS_ROOT_OK or low.startswith(SYS_PREFIX_OK):
                continue
            if any(c in hit for c in "*([+?|^$"):  # 正则字面量，非路径
                continue
            errors.append("%s:%d: %s「%s」" % (rel, lineno, PATH_HINT, hit))


def read_skill_md(root):
    path = os.path.join(root, "SKILL.md")
    if not os.path.isfile(path):
        return None, path
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), path
    except OSError:
        return None, path


def check_skill_md(root, errors, warns):
    text, path = read_skill_md(root)
    if text is None:
        errors.append("缺 SKILL.md")
        return
    try:
        size = os.path.getsize(path)  # 与资源管理器显示一致，不必解码全文
    except OSError:
        size = len(text.encode("utf-8"))
    if size > MAX_SKILL_BYTES:
        errors.append(
            "SKILL.md %d B 超上限（%d KB，约 %d 字符）：细节降级到 references，别压表述"
            % (size, MAX_SKILL_BYTES // 1024, len(text))
        )
    elif size > WARN_SKILL_BYTES:
        warns.append(
            "SKILL.md %d B，接近上限 %d KB：可考虑把细节挪进 references"
            % (size, WARN_SKILL_BYTES // 1024)
        )
    lines = text.splitlines()
    has_table = any(
        all(key in line for key in ROUTE_TABLE_KEYS) and line.lstrip().startswith("|")
        for line in lines
    )
    if not has_table:
        errors.append("路由三要素缺失：文件清单表（列头含「文件 | 什么时候读」）")
    if not any(any(key in line for key in ROUTE_ONLY_LINE_KEYS) for line in lines):
        errors.append("路由三要素缺失：未写「只读对应的那一册，其余一律不读」")


def manifest_block(skill, cat):
    """清单指针段：追加到 SKILL.md 末尾。技能侧只调脚本，不手写 JSON。"""
    if cat in EPISODIC:
        tail = ('python "$P" episode "<项目>" --no 1 --name <集名> --dir "1、<目录名>" '
                '--script 2 --image 0 --audio 0 --video 0')
    else:
        tail = 'python "$P" stage "<项目>" --script 2 --image 0 --audio 0 --video 0'
    return (
        "\n## 项目清单（页面按它显示进度）\n\n"
        "产物目录建好即建档、每完成一段更新；结构与全部子命令见 "
        "fxai-standard `references/01-产出铁律.md`。\n\n"
        "```bash\n"
        "P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py\n"
        'python "$P" init    "<项目>" --title <名称> --category %s --skill %s\n'
        "%s\n"
        'python "$P" memory  "<项目>" --add "<一条结论>" --tag 决策\n'
        "```\n\n"
        "记忆随建档生成，开工先读它；写法见 "
        "fxai-standard `references/01-产出铁律.md`「项目记忆」。\n" % (cat, skill, tail)
    )


def check_manifest_contract(root, name, errors, warns, fix=False):
    """产出型技能必须把清单契约写进 SKILL.md：脚本指针 + init 调用 + 建档时机。"""
    text, path = read_skill_md(root)
    if text is None:
        return
    cat = PRODUCERS.get(name)
    if cat is None:
        if ("产物只落" in text or "交付物" in text) and "fxai_project.py" not in text:
            warns.append(PRODUCER_HINT)
        return
    missing = [k for k in MANIFEST_KEYS if k not in text]
    if not missing:
        if "建档" not in text:
            warns.append(WHEN_HINT)
        return
    if not fix:
        errors.append("%s（缺：%s）" % (MANIFEST_HINT, " / ".join(missing)))
        return
    # 已有清单段落却不含脚本指针：段落可能已手写，改写留给人工，规避追加成两段
    if "## 项目清单" in text:
        errors.append("SKILL.md 已有「项目清单」段落但缺 fxai_project.py 指针，需人工修正该段")
        return
    block = manifest_block(name, cat)
    new = text.rstrip() + "\n" + block
    new_size = len(new.encode("utf-8"))
    if new_size > MAX_SKILL_BYTES:
        errors.append(
            "补清单指针后 SKILL.md 达 %d B，超上限 %d KB（精简正文后重跑 --fix）"
            % (new_size, MAX_SKILL_BYTES // 1024)
        )
        return
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
    except OSError as exc:
        errors.append("SKILL.md 写入失败：%s" % exc)
        return
    warns.append(
        "已补项目清单指针（%d B → %d B）" % (len(text.encode("utf-8")), new_size)
    )


def check_category_registry(errors, warns):
    """PRODUCERS 的类别取值须落在 categories.json 里。"""
    try:
        import catkit
    except ImportError:
        warns.append('类别表未加载：scripts/catkit.py 缺失')
        return
    cats = list(catkit.categories())
    default_skill = catkit.skill_to_category()
    for skill, cat in sorted(PRODUCERS.items()):
        if cat not in cats:
            errors.append('%s 登记的类别「%s」不在 categories.json（%s）'
                          % (skill, cat, ' / '.join(cats)))
        elif skill in default_skill and default_skill[skill] != cat:
            warns.append('%s 登记为 %s，类别表里 %s 的默认技能是 %s'
                         % (skill, cat, cat, default_skill[skill]))


def check_junk(root, errors, warns):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in dirnames:
            if name in JUNK_DIRS:
                warns.append("垃圾目录：%s" % os.path.relpath(os.path.join(dirpath, name), root))
        for name in filenames:
            if name.endswith(JUNK_FILES):
                warns.append("垃圾文件：%s" % os.path.relpath(os.path.join(dirpath, name), root))


def check_chrome_component(root, name, errors):
    """浏览器探测安装只归 fxai-standard，一处实现多处指针（见 11 册）。"""
    if name == "fxai-standard":
        return
    scripts = os.path.join(root, "scripts")
    if not os.path.isdir(scripts):
        return
    for entry in sorted(os.listdir(scripts)):
        if entry.lower().endswith(".py") and "chrome" in entry.lower():
            errors.append(
                "重复实现：scripts/%s；浏览器检测与补装统一调 "
                "fxai-standard/scripts/ensure_chrome.py，本技能只留指针" % entry
            )


def audit(root, junk=False, fix=False):
    name = os.path.basename(root.rstrip("\\/"))
    errors, warns = [], []
    check_skill_md(root, errors, warns)
    check_manifest_contract(root, name, errors, warns, fix=fix)
    check_category_registry(errors, warns)
    check_chrome_component(root, name, errors)
    check_gpu_gate_contract(root, name, errors)
    check_gpu_self_heal_contract(root, name, errors)
    check_video_frame_contract(root, name, errors)
    for path in iter_files(root):
        rel = os.path.relpath(path, root).replace("\\", "/")
        if rel.startswith("scripts/") and rel.endswith("check_standard.py"):
            continue  # 体检工具自身携带判定词表，跳过
        check_text(path, rel, errors, warns)
    if junk:
        check_junk(root, errors, warns)
    return name, errors, warns


def report(name, errors, warns):
    for item in errors:
        print("[ERROR] %s" % item)
    for item in warns:
        print("[WARN] %s" % item)
    print("----")
    print("%s: ERROR %d / WARN %d" % (name, len(errors), len(warns)))


def main():
    ap = argparse.ArgumentParser(description="fxai-standard 规范体检")
    ap.add_argument("skill_dir", nargs="?", help="技能目录（含 SKILL.md）")
    ap.add_argument("--skills-root", help="批量：扫该目录下全部 fxai-* 技能")
    ap.add_argument("--junk", action="store_true", help="加查垃圾文件与孤儿目录")
    ap.add_argument("--fix", action="store_true", help="自动补项目清单指针段（幂等）")
    args = ap.parse_args()

    if args.skills_root:
        base = os.path.abspath(args.skills_root)
        if not os.path.isdir(base):
            print("目录不存在：%s" % base)
            return 2
        targets = [
            os.path.join(base, d) for d in sorted(os.listdir(base))
            if d.startswith("fxai-") and os.path.isdir(os.path.join(base, d))
        ]
        if not targets:
            print("未找到 fxai-* 技能：%s" % base)
            return 2
        bad = 0
        for root in targets:
            name, errors, warns = audit(root, junk=args.junk, fix=args.fix)
            print("%-32s ERROR %-2d WARN %-2d" % (name, len(errors), len(warns)))
            for item in errors:
                print("    [ERROR] %s" % item)
            if errors:
                bad += 1
        print("----")
        print("合计 %d 个技能，%d 个有 ERROR" % (len(targets), bad))
        return 1 if bad else 0

    if not args.skill_dir:
        ap.print_help()
        return 2
    root = os.path.abspath(args.skill_dir)
    if not os.path.isdir(root):
        print("目录不存在：%s" % root)
        return 2
    name, errors, warns = audit(root, junk=args.junk, fix=args.fix)
    report(name, errors, warns)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
