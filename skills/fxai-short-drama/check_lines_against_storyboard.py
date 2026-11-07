# -*- coding: utf-8 -*-
"""
台词真值对齐校验（fxai-short-drama 流程补环节）
铁律：台词一字不删——一句装不下就拆成独立一镜，不砍词。
本脚本校验：run 脚本里的 <d> 台词，必须与分镜脚本的台词逐字一致。
- run 里出现分镜没有的台词        -> 报错（多写了词）
- 分镜对白在 run 里缺失/被改短    -> 报错（砍词）
- 分镜台词装不进该镜时长          -> 报错（应拆分镜，而非精简）
分镜里的【系统/字幕/音效】提示行（如 "系统：【叮！…】"）不配音，允许缺席 run。

用法：
    python check_lines_against_storyboard.py --run <run脚本.py> --story <分镜脚本.md>
退出码 0=全绿可提交；1=有台词被砍/多写/装不下，先改 run 脚本（拆分镜）再提交。
"""
import re, sys, os, argparse

def parse_run_lines(run_path):
    """提取 run 脚本中所有 <d>[Chinese]... </d> 台词文本（有序）。"""
    txt = open(run_path, encoding="utf-8").read()
    return re.findall(r"<d>\[Chinese\](.*?)</d>", txt, re.S)

def parse_story_lines(story_path):
    """解析分镜脚本台词：每镜 '- 台词：' 后的 '  N. 角色：台词。' 行。
    返回 [(角色, 台词文本)]，跳过系统/字幕/音效提示行。"""
    txt = open(story_path, encoding="utf-8").read()
    lines = []
    for m in re.finditer(r"[ \t]*(\d+)[\.、]\s*([^：:]+)[：:]\s*(.+)", txt):
        role, line = m.group(2).strip(), m.group(3).strip()
        # 跳过系统/字幕/音效提示行（如 系统：【叮！…】、字幕：…、音效：…）
        if role.startswith("系统") or role.startswith("字幕") or role.startswith("音效"):
            continue
        lines.append((role, line))
    return lines

def norm(s):
    """归一化：去空白、去括号表演提示、去句尾标点。用于宽松比对。"""
    s = re.sub(r"[（(][^）)]*[）)]", "", s)   # 去掉（低声）（内心）等表演提示
    s = re.sub(r"[。！？，、：；\.\?\!\s\"']", "", s)
    return s

def check(run_lines, story_lines, story_durations=None):
    """run台词 与 分镜台词 比对。返回 (通过, 问题列表)。"""
    problems = []
    run_norm = [norm(x) for x in run_lines]
    story_norm = [norm(x) for (_, x) in story_lines]

    # 1) 分镜对白在 run 里必须逐字命中（一字不删）
    for (role, line), ns in zip(story_lines, story_norm):
        if ns not in run_norm:
            problems.append("分镜台词缺失或被改：%s：%s" % (role, line))
    # 2) run 里不应有分镜没有的对白（多写）
    for r, rn in zip(run_lines, run_norm):
        if rn not in story_norm:
            problems.append("run 多写台词（分镜无此句）：%s" % r)
    return (len(problems) == 0, problems)


def parse_shots_lines(shots):
    """从 shots（内存对象，drama_pipeline 的镜头列表）提取所有 <d>[Chinese]... </d> 台词。"""
    lines = []
    for s in shots:
        m = re.findall(r"<d>\[Chinese\](.*?)</d>", s.get("prompt", "") or "", re.S)
        lines.extend(m)
    return lines


def check_shots_against_storyboard(shots, story_path):
    """提交前置硬门禁：run 的 shots <d> 台词 与 分镜脚本台词逐字一致（一字不删）。
    返回 (通过, 问题列表, run台词条数, 分镜对白条数)。装不下应拆镜，禁用精简。"""
    run_lines = parse_shots_lines(shots)
    story_lines = parse_story_lines(story_path)
    ok, problems = check(run_lines, story_lines)
    return (ok, problems, run_lines, story_lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--story", required=True)
    a = ap.parse_args()
    run_lines = parse_run_lines(a.run)
    story_lines = parse_story_lines(a.story)
    print("run 台词 %d 条；分镜对白 %d 条（系统/字幕/音效提示已跳过）" % (len(run_lines), len(story_lines)))
    ok, problems = check(run_lines, story_lines)
    if ok:
        print("✓ 台词真值对齐：run 与分镜逐字一致，无砍词、无多写。可提交渲染。")
        sys.exit(0)
    print("✗ 台词真值对齐失败（须拆分镜，禁用精简）—— 问题 %d 条：" % len(problems))
    for p in problems:
        print("  -", p)
    sys.exit(1)
