# -*- coding: utf-8 -*-
"""短剧分镜脚本验收（通用，换项目只改 --root）。

用法:
    python check_storyboard.py --root "<分镜目录>"
    python check_storyboard.py --root <项目根> --min-seconds 60 --pattern "*完整分镜脚本.md"

检查: 每集文件存在 / 镜头数 / 序号连续 / 总时长 >= 阈值 / 资产清单与技术注意小节 /
      第1镜时长>0(3秒钩子在位) / 中英混排 / 头部规格行。
退出码: 0=全绿或仅 WARN; 1=有 ERROR(先修完再进渲染)。
"""
import argparse
import glob
import os
import re
import sys

# 标准格式: ## 镜头N · 镜名（场景 · Ns），场景内可嵌套全角括号（如 新场景（夜）· 7s）
SHOT_RE = re.compile(r"^## 镜头(\d+) · (.+?)（(.+?) ·?\s*(\d+)s）", re.M)
# 老格式变体（历史项目遗留，不计时长/小节）
LOOSE_RES = [
    re.compile(r"^#{1,3}\s*镜头\s*(\d+)\s*[·｜:：\-–]\s*(.+?)\s*$", re.M),
    re.compile(r"^#{1,3}\s*镜头\s*(\d+)\s*[（(](\d+)s[)）]\s*(.+?)\s*[—\-–]\s*(.+)$", re.M),
]
# 表格型（老项目把分镜写成 markdown 表）：须有含 Ns 的列，序号取首个「镜号」列
TABLE_ROW_RE = re.compile(r"^\|(.*)\|\s*$")
ASSET_RE = re.compile(r"^#{1,3}\s*(资产清单|资产清单表|参考图素材管理)", re.M)
NOTE_RE = re.compile(r"^#{1,3}\s*(本集技术注意|技术注意|反真人措施|反.*措施)", re.M)
HEAD_RE = re.compile(r"^>\s*\*\*(主场景|核心剧情|规格)", re.M)
# 已知的技术英文代号，不算中英混排
TECH_WORDS = {"H3", "shots", "srt", "mp4", "pid", "tts", "server_index",
              "shortdrama", "json", "subdir", "rel", "ffprobe", "merged", "raw"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_table(src):
    rows = []
    for line in src.splitlines():
        m = TABLE_ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        dur = next((int(re.match(r"(\d+)", c).group(1)) for c in cells
                    if re.fullmatch(r"\d+\s*s", c)), None)
        seq = None
        for c in cells:
            nums = re.findall(r"\d+", c)
            if nums and ("镜" in c or len(nums) == 1):
                seq = int(nums[0])
                break
        if dur is not None and seq is not None:
            rows.append((seq, dur))
    return rows if len(rows) >= 3 else []


def parse_shots(src):
    """返回 (pairs, legacy)。pairs=[(序号, 秒数)...]；legacy=True 表示历史写法，只做宽松校验。"""
    strict = SHOT_RE.findall(src)
    if strict:
        return [(int(a), int(d)) for a, _, _, d in strict], False
    rows = parse_table(src)
    if rows:
        return rows, True
    matched = []
    for rx in LOOSE_RES:
        matched = rx.findall(src)
        if len(matched) >= 3:
            break
    # 老格式的秒数是全有或全无，任一项拿不到就整集按「无秒数」处理
    pairs = [(int(a), int(d.rstrip("s")) if re.fullmatch(r"\d+s", d) else None)
             for a, d in matched]
    if any(d is None for _, d in pairs):
        return [(a, None) for a, _ in pairs], True
    return pairs, True


def check_file(path, min_seconds):
    src = load(path)
    issues, warns = [], []
    pairs, legacy = parse_shots(src)
    if not pairs:
        issues.append("解析不到任何镜头（不支持的标题格式；仅认标准式与老格式，见 SKILL.md）")
        return 0, None, issues, warns

    loose = legacy or any(d is None for _, d in pairs)
    seq = [a for a, _ in pairs]
    total = sum(d for _, d in pairs if d is not None)
    if len(pairs) < 7:
        issues.append(f"镜数不足({len(pairs)})，建议 10 镜")
    if seq != list(range(seq[0], len(seq) + seq[0])):
        issues.append(f"镜头序号不连续: {seq}")
    if not loose:
        if total < min_seconds:
            issues.append(f"总时长 {total}s < {min_seconds}s")
        if pairs[0][1] <= 0:
            issues.append("第1镜时长为0（3秒钩子不在位）")
    if not HEAD_RE.search(src):
        warns.append("缺头部信息块（主场景/阵容/核心剧情/规格）")
    for rx, name in ((ASSET_RE, "「资产清单」"), (NOTE_RE, "「技术注意」")):
        if not rx.search(src):
            (warns if loose else issues).append(f"缺{name}小节")
    if loose:
        warns.append("历史写法分镜：只校验镜数与序号，时长/小节校验降级为提示")

    # 中英混排
    for line in src.splitlines():
        for w in re.findall(r"[A-Za-z]{2,}", line):
            if w in TECH_WORDS or re.match(r"^[A-Za-z]+$", w) and w.lower() in {"s", "h3"}:
                continue
            ctx = line.strip()
            if re.search(r"[一-龥]", ctx) and w not in TECH_WORDS:
                warns.append(f"中文行里的英文词 {w}: {ctx[:36]}")
                break

    return len(pairs), (None if loose else total), issues, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="项目根目录（向下找分镜脚本）")
    ap.add_argument("--pattern", default="*完整分镜脚本.md", help="分镜文件名通配")
    ap.add_argument("--min-seconds", type=int, default=60, help="单集时长下限，默认 60")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.root, "**", args.pattern), recursive=True))
    if not files:
        print(f"没找到分镜脚本: {args.root}/**/{args.pattern}")
        return 1

    n_err = 0
    for p in files:
        ep = os.path.basename(os.path.dirname(p))
        n, total, issues, warns = check_file(p, args.min_seconds)
        flag = "OK  " if not issues else "ERR "
        n_err += len(issues)
        total_txt = " --s(老格式)" if total is None else f"{total:>3}s"
        print(f"{flag} {ep:<16} 镜{n:>2} 总{total_txt}"
              + ("  | " + "; ".join(issues) if issues else ""))
        for w in warns[:3]:
            print(f"     WARN {w}")
    print(f"---- 项目: {args.root}\n---- 分镜文件 {len(files)} 份，ERROR {n_err} 条"
          f"（{'可进入渲染' if n_err == 0 else '不许进渲染，先修'}）")  # std:allow
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
