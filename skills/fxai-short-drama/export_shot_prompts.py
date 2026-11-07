# -*- coding: utf-8 -*-
"""把某集 run_*.py 里的六段式提示词导出成交付文档 `镜头六段式提示词.md`。

**为什么必须导出**：六段式提示词是 H3 出片的真值，只躺在 run 脚本里时人看不见、改不动、
也没法交付。分镜脚本（人读）与六段式（机器读）是两回事，每集都要各一份、同镜号一一对应。

用法：
    python export_shot_prompts.py --run "<剧集目录>/run_<slug>_ep<N>.py"
    python export_shot_prompts.py --run <run.py> -o 别的名字.md

镜头名从同集 `完整分镜脚本.md` 的 `## 镜头N · 名（场景 · Ns）` 抓，抓不到用「镜N」兜底。
"""
import os, re, sys, json, importlib.util, argparse

SIX = ("主体定义", "摘要", "延续分析", "详细描述", "整体音效", "非叙事音乐")

# 英文口径别名（MV / 口播管线用）：切段时两套都认，中文行为不变。
_SIX_ALIAS = {
    "主体定义": "subject_definitions",
    "摘要": "summary",
    "延续分析": "retention_analysis",
    "详细描述": "detailed_description",
    "整体音效": "overall_soundscape",
    "非叙事音乐": "non_diegetic_music",
}


def load_shots(run_file):
    spec = importlib.util.spec_from_file_location("run_ep", run_file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)          # __name__ != "__main__"，main 不执行 → 不提交、不渲染
    return m.shots, m


def shot_names(board_md):
    """从 `完整分镜脚本.md` 的 `## 镜头N · 名字（场景 · Ns）` 抓中文镜名。

    有些分镜脚本里根本没写中文名、直接写的 `## 镜头1 · 0–8 秒 · 8s`，那种是时间轴不是名字，
    抓出来会让六段式标题变成「镜头1 · 0–8 秒 · 8s（8s）」这种废话，必须丢掉。
    """
    out = {}
    if os.path.isfile(board_md):
        for line in open(board_md, encoding="utf-8"):
            mm = re.match(r"^##\s*镜头\s*(\d+)\s*·\s*([^（(]+)", line.strip())
            if not mm:
                continue
            nm = mm.group(2).strip()
            if re.match(r"^\s*\d+\s*[–\-~]\s*\d+\s*秒", nm):     # 纯时间区间 = 不是镜名
                continue
            out[int(mm.group(1))] = nm
    return out


def index_names(ep_dir):
    """服务端编号 -> 中文名（project_def.json 的 local 字段反推）：000 -> 凤希、009 -> 大堂。"""
    out = {}
    for cand in (os.path.join(ep_dir, "..", "project_def.json"),
                 os.path.join(ep_dir, "project_def.json")):
        if not os.path.isfile(cand):
            continue
        d = json.load(open(cand, encoding="utf-8"))
        for src in ("characters", "scenes"):
            for _k, v in (d.get(src) or {}).items():
                try:
                    si = "%03d" % int(v.get("server_index", 0))
                except (TypeError, ValueError):
                    continue
                local = str(v.get("local") or "").replace("\\", "/")
                out[si] = os.path.splitext(os.path.basename(local))[0] if local else _k
        break
    return out


def split_segments(prompt):
    """按六段章节名切段。中文口径（短剧）与英文口径（MV/口播）都认。

    标题行必须独占一行；英文口径允许带冒号（`detailed_description:`）。
    """
    marks = []
    for name in SIX:
        alias = _SIX_ALIAS.get(name)
        if alias:
            pat = r"^[ \t]*(?:%s|%s)[ \t]*:?[ \t]*$" % (name, alias)
        else:
            pat = r"^[ \t]*%s[ \t]*$" % name
        mm = re.search(pat, prompt, re.M)
        if mm:
            marks.append((mm.start(), mm.end(), name))
    if not marks:
        return [("", prompt.strip())]
    segs = []
    for idx, (st, ed, name) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(prompt)
        segs.append((name, _dedent(prompt[ed:end].strip().strip("\n"))))
    return sorted(segs, key=lambda x: SIX.index(x[0]) if x[0] in SIX else 99)


def _dedent(body):
    """抽掉段落内部多出来的空行与行首缩进，保留 H3 提示词本来的换行（换行是提示词结构，并成一行会破坏结构）。"""
    lines = [ln.rstrip() for ln in body.split("\n")]
    lines = [ln for ln in lines if ln.strip()]          # 段内空行对 H3 无意义，抽掉
    return "\n".join(ln.strip() for ln in lines)


# 结构性行首：这些行永远另起一行，不参与软换行合并
# `At MM:SS.mmm,` 是官方切点制的时间轴行；`X.XX 秒` 是旧区间制，一并保住
_STRUCT = re.compile(r"^(\[Shot\s*\d|At\s+\d{1,3}:\d{1,2}(?:\.\d{1,3})?\s*[,，]|\d+\.\d\d\s*秒|- |#{1,3}\s|negative_constraints|world_setting)")


def _unwrap(body):
    """把段内"上一行以标点收尾、下一行是同一句话的续行"并成一句一行。

    提示词源码里的人为折行只是可读性，H3 把换行当空白；交付文档合并后读起来是正常句子，
    与提交给 H3 的提示词只差换行位置，语义完全相同。
    """
    out = []
    for ln in (x.strip() for x in body.split("\n") if x.strip()):
        if out and re.search(r"[，、；：]$", out[-1]) and not _STRUCT.match(ln):
            out[-1] = out[-1] + ln
        else:
            out.append(ln)
    return "\n".join(out)


def ref_note(images, idx_names):
    if not images:
        return "（无参考图）"
    parts = []
    for rel in images:
        base = os.path.basename(rel)
        stem = base.split(".")[0]
        cn = idx_names.get(stem, "")
        parts.append("%s（%s）" % (cn, base) if cn else base)
    return "、".join("`%s`" % p for p in parts)


def _negative_block():
    """提交时 `build_workflow` 自动追加的负面约束段（six sections 之后，`shots` 里看不到）。

    六段式 md 要当完整真值交付，这段必须一并写在文首——否则人复核时
    只看到六段、以为全片没做字幕抑制。取不到时退回 None，文档这一节就不输出。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import drama_pipeline as D
        return getattr(D, "NEGATIVE_CONSTRAINTS_BLOCK", None)
    except Exception:
        return None


def _with_triggers(shot):
    """按 drama_pipeline 的同一规则补出触发词行——交付文档要与提交 H3 的提示词一致。

    `build_workflow` 提交时会自动注入触发词，run 脚本里的 shots 看不到那一行；
    本文件是「提示词真值」交付物，必须把同一行补出来，否则人复核时会以为没挂。
    取不到 drama_pipeline 时退回原始提示词（不报错，只是这一行不出现）。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import drama_pipeline as D
        return D.inject_trigger_words(shot.get("prompt", ""),
                                      D.resolve_trigger_words(shot))
    except Exception:
        return shot.get("prompt", "")


def build_doc(shots, ep_dir, w1="", board=None, width=1280, height=768, audio_mode="系统生成"):
    """拼出整份 md 文本（供命令行与 drama_pipeline 自动导出共用，单一实现）。"""
    board = board or os.path.join(ep_dir, "完整分镜脚本.md")
    names = shot_names(board)
    idx_names = index_names(ep_dir)
    total = sum(int(s["duration"]) for s in shots)

    L = ["# 镜头六段式提示词（H3 出片用）",
         "",
         "> 喂给 Hailuo H3 的**提示词真值**（从 `run_*.py` 导出，逐字一致），与 `完整分镜脚本.md` 是两回事：",
         "> - `完整分镜脚本.md` = 人读的：画面、机位、角色、台词（中文，后期烧字幕用）",
         "> - **本文件 = 机器读的**：六段式提示词，决定 H3 出什么画面/什么声音，可复现、可改词重跑",
         "> 改画面节奏动前者，改出片效果动后者；两者必须同镜号一一对应。",
         "> 段内软换行已合并成一句一行（只动换行位置，与提交 H3 的提示词语义相同）。",
         "",
         "**规格**：横屏 %d×%d ｜ %d 镜 / %d 秒 ｜ 音频=%s ｜ suppress_subtitles（画面禁字幕、后期烧录）"
         % (width, height, len(shots), total, audio_mode),
         ""]

    if w1:
        L += ["**世界观句（每镜都注入）**：", "", "> %s" % w1.strip(), ""]
    L += ["**每段正文都包在「text 代码块」里**：H3 的 `<Subject N>` / `<Picture N>` / `<d>[Chinese]…</d>` "
          "是模型指令标签，写在普通段落里会被 markdown 当 HTML 标签吞掉（表现为挤成一坨没分行）；"
          "代码块里原样保留换行与标签，可直接整段复制去喂 H3。",
          "", "---", ""]

    neg = _negative_block()
    if neg:
        L += ["# 负向约束段（每镜自动追加，不属于六段）", "",
              "`build_workflow` 提交时（`suppress_subtitles` 默认开）会在每条镜头提示词末尾自动追加下面这一块，"
              "**不写在本文件的六段里、也不用手动抄进 run 脚本**（抄了就重复两遍）：",
              "",
              "```text", neg.strip(), "```", "",
              "**范围只到「字幕 + 乱码文字」为止**，扩写成「任何文字 / 文本 / 标题」会把"
              "片尾 slogan、招牌、卷宗封面这些有意要生成的可见文字一起误杀。",
              "", "---", ""]

    L += ["# 触发词行（打斗镜自动挂载，不属于六段）", "",
          "`build_workflow` 提交时按镜型自动在**详细描述第一行**挂触发词："
          "打斗镜 `camera motion, prfight1`（终结镜再加 `prfin1`，慢镜终结再加 `prslow1`）；"
          "纯文戏镜整行不挂。**本文件已按同一规则补出**，与提交 H3 的提示词一致，"
          "run 脚本里看不到这一行是正常的。",
          "",
          "规则唯一权威来源：`fxai-h3-video-prompt/references/07-触发词挂载规范.md`。",
          "", "---", ""]

    for i, s in enumerate(shots, 1):
        nm = names.get(i)
        L += ["## 镜头%d%s（%ds）" % (i, " · " + nm if nm else "", int(s["duration"])), "",
              "**参考图**：%s" % ref_note(s.get("images") or [], idx_names), ""]
        for name, body in split_segments(_with_triggers(s)):
            L += ["### %s" % name, "", "```text", _unwrap(body), "```", ""]
        L += ["---", ""]

    L += ["## 附：本集提交参数", "",
          "- 提交命令：`python run_*.py`　（单镜返修追加镜号，只重那一镜，其余命中缓存）",
          "- 走线：本机 ComfyUI（参考图 rel 只有本机服务端有；提交到云端容器 = 素材不足整集白跑）",
          "- 校验：规划期已过单集 ≥60s 下限", ""]
    return "\n".join(L)


def export_md(shots, ep_dir, out=None, w1="", **kw):
    """写 `<集目录>/镜头六段式提示词.md`，返回落盘路径。"""
    out = out or os.path.join(ep_dir, "镜头六段式提示词.md")
    open(out, "w", encoding="utf-8").write(build_doc(shots, ep_dir, w1=w1, **kw))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="该集的 run 脚本（含 shots）")
    ap.add_argument("--board", default=None, help="完整分镜脚本.md，用来取镜头名")
    ap.add_argument("-o", default=None, help="输出 md，默认 <run 同目录>/镜头六段式提示词.md")
    a = ap.parse_args()

    run_file = os.path.abspath(a.run)
    ep_dir = os.path.dirname(run_file)
    board = a.board or os.path.join(ep_dir, "完整分镜脚本.md")
    out = a.o or os.path.join(ep_dir, "镜头六段式提示词.md")

    shots, mod = load_shots(run_file)
    total = sum(int(s["duration"]) for s in shots)

    w1 = getattr(mod, "W1", "") or getattr(mod, "WORLD", "") or getattr(mod, "world_constraint", "")
    export_md(shots, ep_dir, out=out, w1=w1, board=board)
    print("已导出 %d 镜 / %d 秒 -> %s" % (len(shots), total, out))


if __name__ == "__main__":
    main()
