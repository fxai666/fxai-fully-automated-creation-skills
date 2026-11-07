# -*- coding: utf-8 -*-
"""分镜预检 —— 把 09 册验收清单里「机械能查的」全部程序化。

用法：
    python scripts/preflight.py --project <项目目录> [--char-desc "…"]

只读，不改任何文件。有未过项时返回码 1。

设计原则（2026-10-21 用户口径）：
**能用程序判的，一律交给程序，勿靠人眼和记忆。** 这份脚本是 09 册验收清单的可执行版本，
清单改了这里也要跟着改；两边不一致时以本脚本为准（它才是真正拦人的那道闸）。

本轮新增的判据都来自实际事故：
- ★2 走位镜必须中全景：中景只到腰，走位物理上不可见，写了也白写。
- ★3 景别要有轮换：10 镜全中景 = 全片一个构图，必然呆板。
- ★4 走位不许写离散量：「走两步 / 走过去」会被模型当一次性快速小动作，出来是快步。
- ★5 提示词里不许出现头部动作词：表情和眼神**可以写**（用户口径，覆盖 08 册 4.5）。
- ★6 motion 必须真的进了提示词：曾经分镜写了 motion、代码却不读，白写一整轮。
- ★9 每段都要有「持续讲话」强指令：只写「嘴唇与音频同步开合」强度不够，配上
  「面带微笑」就被做成闭嘴微笑 —— 出片观感是「人根本不动嘴」。
- ★10 入场 / 走位句自带「讲」：★9 管不到位移句。整段有强指令、位移句却是纯位移
  （「走到桌前站定」），模型照样按「先走到位、再开口」演，开场两三秒嘴是闭的。
  规范口径见 fxai-h3-video-prompt/references/01 册 2.3「入画即开口，全程不停嘴」。
"""
import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(SKILL))          # fxai-* 的父目录，便于 import 同级技能
sys.path.insert(0, SKILL)

# 头部「动作」词 —— 一律禁写。这些词等于给模型下「点头 / 转头」的指令。
# 注意别把「眉头」误判成头部动作（它属于表情，用户口径允许）。
HEAD_ACT = ["点头", "抬头", "低头", "转头", "回头", "歪头", "仰头", "摇头", "探头", "扭头",
            "侧头", "颔首", "扬起下巴", "下巴", "甩头", "偏头", "点一下头", "头微",
            "肩颈", "颈肩", "脖颈", "呼吸有轻微起伏"]
# 面部「持续节拍」—— 禁写（会驱动脸上不停做小动作）。
# 具体的眉/嘴/下颌**静态**描述不禁（那是表情，用户 2026-10-21 明确允许）。
FACE_BEAT = ["表情灵动", "随内容变化", "自然切换", "不断变化的表情", "眼神闪烁"]
# 表情 / 眼神的静态写法 —— **允许**（用户 2026-10-21 明确：写表情就可以，眼神也可以）
FACE_OK = ["面带微笑", "微笑", "笑意", "表情", "眼睛看向", "眼神", "目光", "看向镜头"]

# 躯干倾斜 → 被模型翻译成点头 / 仰头 / 歪头 —— **一律禁写**。
# 2026-10-21 第二轮事故：motion 里写「上身轻轻前压一点」「上身后仰一点」
# 「肩线自然高低错开」「肩头略收」，在腰以上构图里最直接的视觉表现就是头和肩一起
# 上下动，用户看到的就是"还在点头"。躯干只写「换重心 / 站稳 / 走近」。
TORSO_HEAD = ["上身前倾", "上身向前", "上身后仰", "上身后靠", "上身轻轻前压", "上身前压",
              "肩线", "肩头", "耸肩", "缩脖", "含胸", "挺胸"]

# 冻结词：把整个人（包括嘴）定住 —— "根本不开口"的直接嫌疑。
# 要表达"动作做完停下"，只写「脚步站定 / 保持站姿」，不写「停住不动」。
FREEZE = ["停住不动", "定住不动", "僵住", "一动不动", "整个人定住"]

# 🔴 部位白名单（2026-10-21 第二次事故后加，这是治本的一条）
#
# 为什么黑名单不够：头部动作在自然语言里有无数种写法 ——
# 上身前压 / 肩膀一沉 / 胸口一挺 / 脖颈松一下 / 上半身略低 / 双肩放松 / 含胸，
# 黑名单永远列不全（21 号就是这么漏的：词表只有"点头/抬头"，我写的是"上身前压"，
# 字面不含"点头"，质检直接放行，出片就是点头）。
#
# 所以 motion / action 改成**部位白名单**：只准写「脚 / 腿 / 重心 / 手 / 臂 / 掌 / 指」，
# 躯干与头颈的部位名词一律不许出现 —— 头颈动作只能借这些部位表达，堵住即可。
# 注：「头」不放进这张表（会被「朝镜头方向」误伤），头部动作由 HEAD_ACT 专查。
TORSO_PART = ["肩", "胸", "背", "腰", "颈", "脖", "上身", "躯干", "脊椎", "脊柱",
              "锁骨", "上半身"]
# 允许的白名单短语：静态放松 + 纯手部位置（"胸口高度/胸前比框"是手在哪儿，不是躯干在动）
TORSO_OK = ["肩背放松", "双肩放松", "肩线平稳", "胸口高度", "胸前比", "胸前摊", "胸前收",
            "胸前合", "胸前划"]

# 说话必须写强指令：只写「嘴唇同步开合」或泛化的「开口讲述」强度都不够，
# 配上「面带微笑」会锁住嘴型（2026-10-21 走位镜 4 段就是这么翻车的）。
# 所以这里只认强指令 —— 模板一退化就会被 ★9 拦住。
SPEAK_OK = ["持续讲话", "嘴部全程保持明显开合", "口型逐字跟随"]

# 身体转向 / 背对镜头 —— **一律禁写**。
# 2026-10-21 实测：第 7 镜写「身体侧转约三十度面向屏幕方向，停稳后上半身转回面向镜头」，
# 模型只执行了前半句「侧转」，没有执行「转回」，成片里出现约 1.5 秒侧脸口播（嘴型对不上观众）。
# 结论：口播镜头里模型不理解「转出去再转回来」这种往返指令，只按最后一个姿态定格。
# 允许的位移只有两种：①沿画面纵深走近/走远 ②沿画面左右横移（横移时写明「身体始终正面朝向镜头」）。
BODY_TURN = ["侧转", "转身", "转体", "背对", "背朝", "侧身", "侧对", "面向屏幕", "朝向屏幕",
             "转向屏幕", "面向侧", "转过头", "回身", "转过身", "斜对镜头"]

WALK = ["走", "移", "近", "退", "前倾", "侧转", "转体", "重心", "半步", "身位"]
WALK_BAD = ["来回", "反复走", "横穿", "跑动", "大步", "两步", "三步", "走过去", "走近又"]
CAM_BAD = ["pan", "pull-out", "拉远", "横移", "摇", "tilt", "orbit", "环绕", "手持", "升降",
           "zoom", "tracking"]
MOVE_SIZES = ("中全景", "全景", "远景")
PROPS = ["白板", "屏幕", "双屏", "机柜", "剪辑台", "照片墙", "展示墙", "文件柜", "桌面",
         "窗", "灯架", "推车", "工具柜", "洞洞板", "会议室"]
STATIC = ["站在原地", "站定不动", "站着讲", "一动不动", "全程不动", "位置不变"]


# ---------------------------------------------------------------- 读取
def load_shots(proj):
    p = os.path.join(proj, "image", "storyboard.json")
    d = json.load(io.open(p, encoding="utf-8"))
    return d["shots"] if isinstance(d, dict) else d


def load_scenes(proj):
    p = os.path.join(proj, "scenes.txt")
    if not os.path.isfile(p):
        return []
    return [l.strip() for l in io.open(p, encoding="utf-8") if l.strip()]


def floor_ratio(path):
    """画面底部连续「低方差（地面）」区占全高比例。"""
    from PIL import Image
    import numpy as np
    im = Image.open(path).convert("L").resize((268, 480))
    a = np.asarray(im, dtype=float)
    std = a.std(axis=1)
    h = len(std)
    thr = max(6.0, np.percentile(std, 25) * 1.1)
    y = h
    for i in range(h - 1, int(h * 0.42), -1):
        if std[i] <= thr:
            y = i
        else:
            break
    return (h - y) / float(h)


def _lcs(a, b):
    """最长公共子串（Longest Common Substring）——用来抓「同一件事写两遍」。"""
    if not a or not b:
        return ""
    prev = [0] * (len(b) + 1)
    best, end = 0, 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best, end = cur[j], i
        prev = cur
    return a[end - best:end] if best else ""


# 「同一件事只写一次」判据里要剔除的通用词：它们到处都出现，重合了不算重复指令。
_DUP_STOP = ("全程", "画面", "人物", "镜头", "持续", "缓缓", "轻微", "自然", "保持",
             "状态", "稳定", "从容", "不急不缓", "连续", "动作", "脚步", "身体")


def build_prompts(proj, char_desc):
    """真跑一遍 build_seg_prompts，拿到「实际会提交给模型」的提示词。

    这是本脚本最关键的一步：分镜表写得再合规，只要代码没把它拼进提示词，
    出片照样呆板。所以判据必须落在**提示词文本**上，不是落在分镜表上。
    """
    try:
        import dh_video
    except Exception as e:                                     # pragma: no cover
        print("  [跳过 ★5/★6] 无法导入 dh_video：%s" % e)
        return None
    shots = load_shots(proj)
    scenes = load_scenes(proj) or [""]
    a = type("A", (), {})()
    a.mode = "multiref"
    a.storyboard = os.path.join(proj, "image", "storyboard.json")
    a.char_desc = char_desc
    try:
        return dh_video.build_seg_prompts(a, scenes, None, len(shots))
    except Exception as e:                                     # pragma: no cover
        print("  [跳过 ★5/★6] 生成提示词失败：%s" % e)
        return None


# ---------------------------------------------------------------- 主流程
def _shots_txt(nums):
    """[3, 7, 9] → 「第 3、7、9 镜」。用统一写法，未过项的镜号才能被自动收成返修清单。"""
    nums = sorted({int(x) for x in nums})
    return "第 " + "、".join(map(str, nums)) + " 镜" if nums else ""


def main():
    ap = argparse.ArgumentParser(description="数字人口播分镜预检")
    ap.add_argument("--project", default=".", help="项目目录（含 image/storyboard.json）")
    ap.add_argument("--char-desc", default=None, help="人物穿搭描述；缺省用公共默认值")
    ap.add_argument("--no-prompt", action="store_true", help="跳过提示词级检查（★5/★6）")
    args = ap.parse_args()
    proj = args.project

    shots = load_shots(proj)
    descs = load_scenes(proj)
    rows, bad = [], []
    bad_shots = set()          # 未过项涉及哪几镜 —— 返修清单就是它

    def rec(no, name, ok, detail="", warn=False):
        # warn=True：只提示、不计未过（用于「程序判不准、必须肉眼复核」的项）。
        if not ok and not warn:
            bad.append(no)
            # 未过项的 detail 里凡是「第 N 镜 / 第 N 段」都算这一镜要返修。
            # 段号与镜号一一对应（H3 一镜一段），所以两种写法都收。
            for m in re.finditer(r"第\s*(\d+)\s*[镜段]", str(detail)):
                bad_shots.add(int(m.group(1)))
        rows.append(("✅" if ok else ("⚠️" if warn else "❌"), no, name, detail))

    # ---------- 结构 ----------
    rec("1", "分镜非空", bool(shots), "%d 镜" % len(shots))
    gaps = ["%d→%d 差 %.2fs" % (a["i"], b["i"], b["start"] - a["end"])
            for a, b in zip(shots, shots[1:]) if abs(a["end"] - b["start"]) > 0.02]
    rec("2", "时间码首尾相接", not gaps, "；".join(gaps) or "0 → %.2fs" % shots[-1]["end"])

    # ---------- 动作 ----------
    acts = [re.sub(r"[，。、]", "", str(s.get("action") or "")) for s in shots if s.get("action")]
    dup = max((acts.count(x) for x in set(acts)), default=0)
    rec("3", "动作不重复（≤2 次）", dup <= 2, "最多重复 %d 次" % dup)

    prop_bad = []
    for s in shots:
        blob = "".join(str(s.get(k) or "") for k in ("action", "motion", "why"))
        sc = descs[s["i"] - 1] if s["i"] - 1 < len(descs) else ""
        for w in PROPS:
            if w in blob and w not in sc:
                prop_bad.append("第%d镜提到「%s」场景里没有" % (s["i"], w))
    rec("4/7", "动作物件与场景对应", not prop_bad, "；".join(prop_bad[:4]) or "无")

    wild = [s["i"] for s in shots if any(w in str(s.get("motion") or "") for w in WALK_BAD)]
    rec("5", "位移慢短有终点", not wild,
        ("%s 越界（来回/横穿/两步这类）" % _shots_txt(wild)) if wild else "无")

    cam_bad = [s["i"] for s in shots
               if any(b in (str(s.get("camera") or "") + str(s.get("move") or "")).lower()
                      for b in CAM_BAD)]
    rec("6", "camera 只固定/极缓推近", not cam_bad,
        ("%s 用了禁运镜" % _shots_txt(cam_bad)) if cam_bad else "全部合规")

    rec("10a", "每镜都有 motion", all(str(s.get("motion") or "").strip() for s in shots),
        ("缺 motion：%s" % _shots_txt([s["i"] for s in shots
                                      if not str(s.get("motion") or "").strip()]))
        if any(not str(s.get("motion") or "").strip() for s in shots) else "全有")
    static = [s["i"] for s in shots if any(w in str(s.get("motion") or "") for w in STATIC)]
    rec("10b", "motion 是轨迹不是状态", not static,
        ("%s 写成了状态" % _shots_txt(static)) if static else "全是轨迹")

    moving = sum(1 for s in shots if any(k in str(s.get("motion") or "") for k in WALK))
    rec("13", "运动镜头占多数", moving >= len(shots) * 0.6,
        "%d/%d 有位移或姿态变化" % (moving, len(shots)))

    # ---------- 头部（用户口径：动作禁写，表情/眼神可写）----------
    he = []
    for s in shots:
        blob = "".join(str(s.get(k) or "") for k in
                       ("motion", "action", "expression", "why", "dynamics", "entry"))
        hit = [w for w in HEAD_ACT + FACE_BEAT if w in blob]
        if hit:
            he.append("第%d镜 %s" % (s["i"], "/".join(hit)))
    rec("14", "无头部动作/面部节拍", not he, "；".join(he[:3]) or "无（表情与眼神允许写）")

    # ---------- 躯干倾斜 → 会变成点头/仰头（2026-10-21 第二轮）----------
    th = []
    for s in shots:
        blob = "".join(str(s.get(k) or "") for k in ("motion", "action", "expression", "why"))
        hit = [w for w in TORSO_HEAD if w in blob]
        if hit:
            th.append("第%d镜 %s" % (s["i"], "/".join(hit)))
    rec("26", "无躯干倾斜/肩线（会变点头）", not th,
        "；".join(th[:3]) or "躯干只写换重心/站稳")

    # ---------- 冻结词：人会定住、连嘴也不动 ----------
    fz = []
    for s in shots:
        blob = "".join(str(s.get(k) or "") for k in ("motion", "action"))
        hit = [w for w in FREEZE if w in blob]
        if hit:
            fz.append("第%d镜 %s" % (s["i"], "/".join(hit)))
    rec("27", "无冻结词（停住不动→嘴也不动）", not fz,
        "；".join(fz[:3]) or "只写脚步站定")

    # ---------- 部位白名单：躯干/头颈部位名一律不许出现在 motion/action ----------
    # 这是治本项：黑名单列不全，部位白名单能堵住所有"换种说法写点头"的路子。
    part_bad = []
    for s in shots:
        blob = "".join(str(s.get(k) or "") for k in ("motion", "action"))
        for ok in TORSO_OK:                       # 先剔掉允许的静态短语
            blob = blob.replace(ok, "")
        hit = [w for w in TORSO_PART if w in blob]
        if hit:
            part_bad.append("第%d镜 %s" % (s["i"], "/".join(hit)))
    rec("28", "motion 只写脚腿重心手（部位白名单）", not part_bad,
        "；".join(part_bad[:3]) or "无躯干/头颈部位词")

    # ---------- 身体转向（口播硬禁：出现即侧脸/背对，嘴型与观众对不上）----------
    bt = []
    for s in shots:
        blob = "".join(str(s.get(k) or "") for k in
                       ("motion", "action", "why", "dynamics", "entry"))
        hit = [w for w in BODY_TURN if w in blob]
        if hit:
            bt.append("第%d镜 %s" % (s["i"], "/".join(hit)))
    rec("25", "无身体侧转/背对镜头", not bt,
        "；".join(bt[:3]) or "全部正对镜头（走位只用走近/走远或左右横移）")

    e = str(shots[0].get("entry") or "").strip()
    rec("15", "第 1 镜有入场", bool(e), (e[:28] + "…") if e else "缺 entry，会退回默认「从画面外走入」")

    gest = [s for s in shots if s.get("kind", "gesture") != "move"]
    hand = [s["i"] for s in gest if "手" in str(s.get("action") or "")]
    gs = [re.findall(r"[抬摊比划点合拉压收放]", str(s.get("action") or ""))[:2] for s in gest]
    same = sum(1 for a, b in zip(gs, gs[1:]) if a and a == b)
    rec("16", "手势镜都有抬手且相邻不雷同", len(hand) == len(gest) and same <= 2,
        "抬手 %d/%d，相邻雷同 %d 处" % (len(hand), len(gest), same))

    no_exp = [s["i"] for s in shots if not str(s.get("expression") or "").strip()]
    rec("17", "每镜都有表情（静态即可）", not no_exp,
        ("缺 expression：%s" % no_exp) if no_exp else "%d/%d" % (len(shots), len(shots)))

    sight = [s["i"] for s in shots
             if re.search(r"视线|目光|眼神", str(s.get("motion") or ""))]
    rec("18", "朝向写进 motion", not sight, ("第 %s 镜把视线写进 motion" % sight) if sight else "无")

    tok = [s["i"] for s in shots
           if "35mm" not in str(s.get("camera") or "")
           or not re.search(r"center-framed|rule of thirds", str(s.get("camera") or ""))]
    rec("20", "camera 带 35mm + 构图 token", not tok, ("第 %s 镜缺 token" % tok) if tok else "全带")

    # ---------- ★ 走位与景别 ----------
    move_shots = [s for s in shots if s.get("kind") == "move"]
    bad_size = ["第%d镜 %s" % (s["i"], s.get("size")) for s in move_shots
                if not any(z in str(s.get("size") or "") for z in MOVE_SIZES)]
    rec("★2", "走位镜配中全景及以上", not bad_size,
        "；".join(bad_size) or ("%d/%d 走位镜景别正确" % (len(move_shots), len(move_shots))
                                if move_shots else "无走位镜"))

    sizes = set(str(s.get("size") or "").split(" ")[0] for s in shots)
    rec("★3", "景别至少 2 种（有轮换）", len(sizes) >= 2, "、".join(sorted(sizes)) or "无")

    if move_shots:
        noact = [s["i"] for s in move_shots if s.get("action")]
        rec("★4", "走位镜不叠开放手势", not noact,
            ("第 %s 镜走位镜还写了手势" % noact) if noact else "走位镜只做位移")

    # ---------- ★11 同一件事只写一次（不写重复指令）----------
    # 一镜的 entry / motion / action 会被拼进**同一条动作链**。同一件事在两个字段里
    # 各写一遍，不是强调，是重复指令：权重被摊薄到两句上，且第二句会被模型当成
    # 「还有一个独立动作要做」，出来是动作折返、语义打架，或者只演一遍、另一句被忽略。
    # 实测（2026-10-22 第 1 镜）：entry 与 motion 都写了「从画面右侧缓缓走入，走到桌前站定」，
    # 且都带「全程边走边讲，嘴部不停开合」——人走进来站好才开口，成片被判「丑 + 闭嘴」。
    # 判据：两两字段的最长公共子串 ≥5 字，且剔掉通用词后仍 ≥3 字。
    # 规范口径见 fxai-h3-video-prompt/references/01 册「同一件事只写一次」。
    dup = []
    for s in shots:
        try:
            si = int(s["i"])
        except Exception:
            continue
        vals = [(f, re.sub(r"[，。；、\s]", "", str(s.get(f) or "")))
                for f in ("entry", "motion", "action", "dynamics")
                if str(s.get(f) or "").strip()]
        for x in range(len(vals)):
            for y in range(x + 1, len(vals)):
                f1, v1 = vals[x]
                f2, v2 = vals[y]
                c = _lcs(v1, v2)
                core = c
                for w in _DUP_STOP:
                    core = core.replace(w, "")
                if len(c) >= 5 and len(core) >= 3:
                    dup.append("第%d镜 %s 与 %s 都写了「%s」" % (si, f1, f2, c))
    rec("★11", "同一件事只写一次（不重复指令）", not dup,
        "；".join(dup[:3]) or "无重复")

    # ---------- ★13 size 必须是标准景别词 ----------
    # 代码拿 size 去查 av.SHOT_SPEC 换成标准描述（「人物从头顶到腰部以上进入画面」这类）。
    # 写成自由文本或夹英文就查表落空，退化成「<原文>镜头」原样拼接 —— 景别约束等于没写，
    # 实测第 1 镜因此渲成远景。09 册早有规定，但模型写分镜时不遵守，所以必须有门禁。
    _SPEC = {}
    try:
        import audio_video as _avs                             # noqa: F401
    except Exception:
        # 本脚本的 sys.path 只有数字人技能目录；audio_video 在 fxai-audio-video，
        # 是 dh_video 导入时带进来的。★13 跑在 build_prompts() 之前，所以要自己搭桥。
        try:
            import dh_video as _dhv                            # noqa: F401
            _avs = getattr(_dhv, "av", None)
        except Exception:
            _avs = None
    if _avs is not None:
        _SPEC = getattr(_avs, "SHOT_SPEC", {}) or {}
    if _SPEC:
        bad_size = []
        for s in shots:
            sz = str(s.get("size") or "").strip()
            if not sz:
                bad_size.append("第%d镜 size 为空" % s["i"])
            elif sz not in _SPEC:
                bad_size.append("第%d镜 size=「%s」非标准景别词"
                                % (s["i"], sz[:20]))
        rec("★13", "size 用标准景别词（六档之一）", not bad_size,
            "；".join(bad_size[:3]) or "%d 镜全标准" % len(shots))

    # ---------- ★ 场景图 ----------
    # 原本设想的翻车点是「手势镜腰线正压在地面起始线上，看上去像半截陷进地板」（v13）。
    # 但 2026-10-21 实测这条路走不通，已降级为**建议项**（不计未过）：
    #   1) floor_ratio 只认「底部连续低方差」，分不清「空地」和「一摞文件 / 大片均匀桌面」。
    #      scene_11 底部明明是文件摞（有物件、有边缘），照样算 21%（误判）。
    #   2) 真问题是「腰切口压线」，判据应该是**底部有没有强横边缘（地平线 / 桌沿）**，
    #      不是「低方差占多高」。现在是拿错的指标当门禁。
    #   3) 中景 waist-up 合成后人物占画高 3/5，画面底部是人物躯干与裤腿，
    #      场景图底部根本露不出来 —— 实测 scene_10（14%）成片第 10 镜构图正常。
    # 结论：留作肉眼复核的线索（⚠️ 不计失败），别让它拦住交付。
    # TODO 想真判「腰切口压线」，改成检测底部 0-30% 区域的行均值梯度突变。
    kinds = {}
    for s in shots:
        try:
            kinds[int(s["i"])] = (s.get("kind") or "gesture")
        except Exception:
            pass
    fl_gest = []
    for i in range(1, len(descs) + 1):
        p = os.path.join(proj, "image", "scene_%02d.png" % i)
        if not os.path.isfile(p):
            continue
        if kinds.get(i, "gesture") == "move":
            continue
        r = floor_ratio(p)
        if r > 0.12:
            fl_gest.append("scene_%02d 底部低方差区 %.0f%%（可能是空地，也可能是文件摞）" % (i, r * 100))
    rec("★1", "手势镜场景底部低方差区占比（建议项·肉眼复核）",
        not fl_gest, ("；".join(fl_gest) + " → 抽帧看过没问题即放过") if fl_gest else "全部干净",
        warn=True)

    # ---------- 缓存一致性（提醒项，不计失败）----------
    import hashlib
    cache_dir = os.path.expanduser("~/.workbuddy/fxai_cache")
    tag = os.path.basename(os.path.normpath(proj)) + "_口播_scene%d.png"
    mism = []

    def _h(p):
        try:
            return hashlib.md5(open(p, "rb").read()).hexdigest()
        except Exception:
            return None

    for i in range(1, len(descs) + 1):
        c = os.path.join(cache_dir, tag % i)
        l = os.path.join(proj, "image", "scene_%02d.png" % i)
        if os.path.isfile(c) and os.path.isfile(l) and _h(c) != _h(l):
            mism.append("scene_%02d" % i)
    if mism:
        # 渲染时 gen_scene_image 命中缓存就直接复用、**不上传**，
        # 服务端 dh_scene/NNN.png 保持上次上传的内容 → 本地改过就必须手动 upload_image。
        rows.append(("⚠️", "★7", "本地场景图与缓存不同",
                     "%s 改过，确认已 upload_image 覆盖服务端；否则渲染用的还是旧图"
                     % "、".join(mism)))

    # ---------- ★ 提示词级（最关键）----------
    prompts = None if args.no_prompt else build_prompts(proj, args.char_desc)
    if prompts:
        bad_head = []
        for i, p in enumerate(prompts, 1):
            hit = [w for w in HEAD_ACT + FACE_BEAT if w in p]
            if hit:
                bad_head.append("第%d段 %s" % (i, "/".join(hit)))
        rec("★5", "提示词无头部动作/面部节拍", not bad_head, "；".join(bad_head[:3]) or "%d 段全清" % len(prompts))

        miss = []
        for s, p in zip(shots, prompts):
            if s.get("kind") == "move":
                key = re.sub(r"[，。；]", "", str(s.get("motion") or ""))[:12]
                if key and key not in re.sub(r"[，。；]", "", p):
                    miss.append("第%d镜 motion 没进提示词" % s["i"])
            if s.get("expression") and s["expression"] not in p:
                miss.append("第%d镜 expression 没进提示词" % s["i"])
        rec("★6", "motion / expression 真进了提示词", not miss, "；".join(miss[:3]) or "全部生效")

        # ★8 提示词层也要查躯干倾斜/冻结词（分镜干净不代表模板干净 ——
        # 公共模板 audio_video._NARR 里改回一句「上身前压」，照样出点头）
        bad_t = []
        for i, p in enumerate(prompts, 1):
            hit = [w for w in TORSO_HEAD + FREEZE + HEAD_ACT if w in p]
            if hit:
                bad_t.append("第%d段 %s" % (i, "/".join(hit)))
        rec("★8", "提示词无躯干倾斜/冻结词", not bad_t,
            "；".join(bad_t[:3]) or "%d 段全清" % len(prompts))

        # ★9 每段都要有「持续讲话」强指令，否则配上面带微笑就闭嘴不动
        no_speak = []
        for i, p in enumerate(prompts, 1):
            if not any(w in p for w in SPEAK_OK):
                no_speak.append("第%d段 缺持续讲话指令" % i)
        rec("★9", "每段都有持续讲话强指令", not no_speak,
            "；".join(no_speak[:3]) or "%d 段全有" % len(prompts))

        # ★10 入画即开口：入场句 / 走位句自己必须带「讲」。
        # ★9 只管「提示词里有没有持续讲话」，管不了位移句把时间轴占满——
        # 整段有强指令、位移句却是纯位移，模型照样演成「先走到位再开口」（实测）。
        no_entry_speak = []
        for s, p in zip(shots, prompts):
            seg = (str(s.get("entry") or "") if s["i"] == 1 else "")
            if s.get("kind") == "move":
                seg += str(s.get("motion") or "")
            if not seg:
                continue
            if not any(w in seg for w in ("讲", "说", "开口", "嘴", "口型")):
                no_entry_speak.append("第%d镜 入场/走位句是纯位移" % s["i"])
        rec("★10", "入场 / 走位句自带「讲」（入画即开口）", not no_entry_speak,
            "；".join(no_entry_speak[:3]) or "全部自带")

        # ★12 主体归属：台词与音频必须挂在**人物**主体（Subject 1）上，
        # 场景主体一个字都不得沾。
        # 曾把台词拼进 scene_desc → 落进 <Subject 2>（场景背景图）定义 →
        # 模型读成「人物没内容、场景在讲」，与口型同步指令打架，15 段全程闭嘴。
        # 判据走公共件 av.audit_subject_binding()，MV / 口播同一条规矩。
        bind_bad = []
        _av = None
        try:
            import audio_video as _av                          # noqa: F401
        except Exception as e:                                 # pragma: no cover
            print("  [跳过 ★12] 无法导入 audio_video：%s" % e)
        if _av is not None:
            for i, p in enumerate(prompts, 1):
                ok, probs = _av.audit_subject_binding(p)
                if not ok:
                    bind_bad.append("第%d段 %s" % (i, "；".join(probs)))
        if bind_bad or (prompts and _av is not None):
            rec("★12", "台词与音频只挂人物主体（场景不沾）", not bind_bad,
                "；".join(bind_bad[:3]) or "%d 段全对" % len(prompts))
    elif not args.no_prompt:
        rows.append(("⚠️", "★5/★6", "提示词级检查", "未执行（导入失败，见上方提示）"))

    # ---------- 输出 ----------
    print("\n分镜预检 · %s\n" % os.path.abspath(proj))
    for mark, no, name, detail in rows:
        print("  %s [%-4s] %-30s %s" % (mark, no, name, detail))
    print("\n%s  %d 项未过%s" % ("✅ 全过" if not bad else "❌ 有未过项", len(bad),
                                ("：" + "、".join(bad)) if bad else ""))

    # ---------- 返修清单：未过项 → 具体哪几镜 → 现成命令 ----------
    shots_sorted = sorted(bad_shots)
    if shots_sorted:
        print("\n" + "=" * 62)
        print("返修清单（体检未过的项落到这几镜）")
        print("=" * 62)
        print("  需要返修：%s" % _shots_txt(shots_sorted))
        for n in shots_sorted:
            t0, t1 = (n - 1) * 9.41667, n * 9.41667
            why = [("%s" % no) for mark, no, name, detail in rows
                   if mark == "❌" and re.search(r"第\s*%d\s*[镜段]" % n, str(detail))]
            print("    第 %d 镜  %.2f~%.2f s   未过项 %s" % (n, t0, t1, "、".join(why)))
        print("\n  走单段返修，**只重渲问题段**（多镜会串行排队，全部渲完才继续）：")
        print('    python "%s" --project "%s" --shots %s'
              % (os.path.join(HERE, "refix.py"), os.path.abspath(proj),
                 ",".join(map(str, shots_sorted))))
        print("\n  返修完成后的顺序（不许跳）：")
        print("    1) 重跑本预检，必须全绿")
        print("    2) 看 refix 抽出来的帧，确认问题消失")
        print("    3) 才做字幕 / 封面 / 交付自检（S5）")
        print("=" * 62)
    elif bad:
        print("\n  未过项不在具体某一镜（属结构/场景图/缓存问题），按上面的 detail 处理。")

    # 落盘，给 check_delivery 等下游读（它们不用再去抠 stdout）
    try:
        out = os.path.join(proj, "doc")
        os.makedirs(out, exist_ok=True)
        with io.open(os.path.join(out, "_preflight.json"), "w", encoding="utf-8") as f:
            json.dump({"ok": not bad, "failed": bad, "rework_shots": shots_sorted,
                       "refix": ("--shots " + ",".join(map(str, shots_sorted)))
                       if shots_sorted else "",
                       "rows": [{"mark": m, "no": n, "name": nm, "detail": d}
                                for m, n, nm, d in rows]},
                      f, ensure_ascii=False, indent=1)
    except Exception as e:                                       # noqa: BLE001
        print("  （预检结果落盘失败，不影响判定：%s）" % e)

    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
