# -*- coding: utf-8 -*-
"""发布标题：与 `segments.json` 同批产出，不等成片。

标题内容由调用方（写稿的模型）给：`--main` 一条主推 + `--alts` 三条备选，打法互不相同；
脚本校验（字数 / 联系方式 / 违禁词）后落 `发布文案.md`（人读）+ `titles.json`（机器读），
并写进交付清单「发布信息」栏（`delivery_block()`）。

`--main` 缺席时走兜底：从规格单抽素材（主题词、旧做法动词、新做法词、工时对比），
画面大字与模板两路候选一起评分，取最高分作主推。

主推标题全平台通用：去空白后 ≤22 字、含主题词、叠数字或痛点。

用法：
    python sv_title.py <segments.json> [--out <目录>] [--topic <词>] [--solution <词>]
                       [--main "<标题>"] [--alts "a|b|c"] [--force]

退出码：0 = 主标题合规；1 = 主标题超长或含联系方式。
"""
import argparse
import collections
import json
import pathlib
import re
import sys

import sv_compliance as C

JSON_NAME = "titles.json"
MD_NAME = "发布文案.md"

WORD = re.compile(r"[A-Za-z][A-Za-z0-9._+-]{2,}")
NUM = re.compile(r"\d+(?:\.\d+)?\s*(?:小时|分钟|秒|天|周|月|年|道坎|道|个|条|步|倍|%)")
PAIN_WORDS = ["手搓", "手写", "手动", "搬运", "重复劳动"]
SOLU_WORDS = ["智能体", "技能包", "自动化", "工作流自动化"]
GENERAL_TAGS = ["AI创作", "AI工作流", "效率工具"]
# 署名与装饰位不参与取词：brand / chapter / badge / hint / foot
SKIP_KEYS = {"brand", "chapter", "badge", "tag", "hint", "foot", "site_bar"}


def _cn_en_space(s):
    """中西文之间补一个空格（不影响字数统计）。"""
    s = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9])", r"\1 \2", s)
    return re.sub(r"([A-Za-z0-9])([\u4e00-\u9fff])", r"\1 \2", s)


clen = C.clen            # 标题字数口径：去空白后的字符数


def content_texts(specs):
    """取词与成句专用：排除署名与装饰位后的正文串。"""
    out = []
    for seg in specs:
        fields = {k: v for k, v in (seg.get("fields") or {}).items() if k not in SKIP_KEYS}
        for _p, raw in C.walk_strings({"text": seg.get("text") or "", "fields": fields}):
            s = C.strip_tags(str(raw))
            if s:
                out.append(s)
    return out


def extract(specs, topic=None, solution=None):
    """抽取标题素材。"""
    body = content_texts(specs)
    lines = [C.strip_tags(str(s.get("text") or "")) for s in specs]   # 口播句
    blob = "\n".join(body)

    if not topic:
        head = C.strip_tags(str((specs[0].get("fields") or {}).get("title", "")))
        m = WORD.search(head)
        if m:
            topic = m.group(0)
    if not topic:
        cnt = collections.Counter(w for t in body for w in WORD.findall(t))
        topic = cnt.most_common(1)[0][0] if cnt else ""

    if not solution:
        cnt = collections.Counter(w for w in SOLU_WORDS for t in body
                                  for _m in re.finditer(re.escape(w), t))
        solution = cnt.most_common(1)[0][0] if cnt else ""

    pain = ""
    for w in PAIN_WORDS:
        if w in blob:
            pain = w
            break

    pair, count = None, None
    for t in lines:
        found = NUM.findall(t)
        if len(found) >= 2:
            pair = (found[0].strip(), found[1].strip())
            break
    for seg in specs:
        st = (seg.get("fields") or {}).get("steps")
        if isinstance(st, list) and st:
            count = len(st)
            break

    intro = []
    hook = C.strip_tags(str((specs[0].get("fields") or {}).get("hook", "")))
    if hook:
        intro.append(hook)
    pair_text = _pick_pair_text(lines)
    if pair_text:
        intro.append(pair_text)
    asset = _pick_asset_text(lines, solution, skip=intro)
    if asset:
        intro.append(asset)
    tail = lines[-1] if lines else ""
    if tail and tail not in intro:
        intro.append(tail)

    cover = [_cn_en_space(x) for x in (title_rows(specs[0]) if specs else [])]

    return {"topic": topic, "solution": solution, "pain": pain, "pair": pair,
            "count": count, "cover": cover[:3], "intro": intro,
            "brand": C.strip_tags(str((specs[0].get("fields") or {}).get("brand", "")))}


def _pick_pair_text(lines):
    """第一句含两组「数字+单位」的口播句（前后对比句）。"""
    for t in [x for x in lines if 10 < len(x) < 60]:
        if len(NUM.findall(t)) >= 2:
            return t
    return ""


def _pick_asset_text(lines, solution, skip=()):
    """带新做法词 + 数字、且不与已选重复的口播句。"""
    for t in [x for x in lines if 10 < len(x) < 60]:
        if solution and solution in t and NUM.search(t) and t not in skip:
            return t
    return ""


def title_rows(seg):
    """画面大字（`fields.title`）按 `<br>` 折成的行。"""
    raw = str((seg.get("fields") or {}).get("title", ""))
    return [C.strip_tags(x).strip() for x in re.split(r"<br\s*/?>", raw) if x.strip()]


def big_titles(specs, limit=6):
    """画面大字候选：一行大字直接可当标题，与首帧/画面说的是同一句。"""
    out = []
    for seg in specs:
        rows = title_rows(seg)
        if not rows:
            continue
        # 首行是短语气断句（≤5 字）用空格接，是两个完整分句用「，」接
        joiner = " " if len(rows[0]) <= 5 else "，"
        t = _cn_en_space(re.sub(r"\s+", " ", joiner.join(rows)).strip())
        if 6 <= clen(t) <= C.TITLE_MAX and t not in out:
            out.append(t)
    return out[:limit]


def candidates(m):
    """候选标题（画面大字 + 模板），按得分降序返回。"""
    T, P, S = m["topic"], m["pain"], m["solution"]
    A, B = m["pair"] or ("", "")
    N = m["count"]
    out = []
    if A and B:
        out += [
            "%s %s %s，%s %s" % (P, T, A, S, B),
            "%s 别%s了：%s → %s" % (T, P, A, B),
            "还在%s %s？%s压到 %s" % (P, T, A, B),
        ]
    if N and S:
        out.append("%s 的 %d 道坎，%s一次过" % (T, N, S))
    if T and S:
        out.append("%s之后：%s全自动创作" % (T, S))
    if T and P and S:
        out += ["别再%s %s，试试%s" % (P, T, S), "%s还有必要%s吗" % (T, P)]
    out = [x for x in dict.fromkeys(out) if T in x]
    return sorted([_cn_en_space(x) for x in out], key=lambda t: score(t, m), reverse=True)


def score(t, m):
    """标题评分：短、带主题词 / 数字 / 对比 / 新旧做法词；违禁词一票否决。"""
    n = clen(t)
    s = 30 if n <= C.TITLE_MAX else (0 if n <= 26 else -40)
    if re.search(r"\d", t):
        s += 15
    if len(NUM.findall(t)) >= 2:          # 前后对比：两组「数字+单位」
        s += 10
    if m.get("topic") and m["topic"] in t:
        s += 15
    if m.get("topic") and t.startswith(m["topic"]):
        s += 5
    if m.get("solution") and m["solution"] in t:
        s += 8
    if m.get("pain") and m["pain"] in t:
        s += 6
    if "？" in t or "?" in t:
        s += 5
    for pat, _label in C.WORD_PAT:
        if re.search(pat, t, re.I):
            s -= 100
    return s - max(0, n - 26) * 2


def render_md(d, spec_name):
    """人读版 发布文案.md。"""
    L = ["# 发布文案 · %s" % d["project"], "",
         "主推标题全平台通用（抖音 / 快手 / 视频号 / B站 / 头条同用一条）。", "",
         "## 主推标题", "", "**%s**（%d 字）" % (d["main"], d["main_len"]), ""]
    if d.get("alts"):
        L += ["## 备选标题", ""]
        L += ["%d. %s（%d 字）" % (i, t, clen(t)) for i, t in enumerate(d["alts"], 1)]
        L += [""]
    if d.get("cover"):
        L += ["## 封面大字（≤3 行）", "", "```"]
        L += d["cover"]
        L += ["```", ""]
    if d.get("tags"):
        L += ["## 话题标签", "", " ".join("#%s" % t for t in d["tags"]), ""]
    if d.get("intro"):
        L += ["## 视频简介（公域版，零联系方式）", ""]
        L += ["> %s" % x for x in d["intro"]]
        L += [""]
    L += ["## 发布检查", "",
          "- [ ] 发布端勾选「含有 AI 生成内容」（AI 配音 / AI 绘制画面必选）",
          "- [ ] 画面与简介零联系方式",
          "- [ ] 标题与首帧钩子指向同一件事",
          "- [ ] 发布后前 2 小时逐条回复评论",
          "- [ ] 曝光低换标题关键词与标签；完播低改前 3 秒钩子", "", "---", "",
          "刷新：`python sv_title.py %s --main \"<主推>\" --alts \"a|b|c\" --force`" % spec_name, ""]
    return "\n".join(L)


def build(project, specs, spec_name="segments.json", topic=None, solution=None,
          main=None, alts=None):
    """生成标题数据 dict（不落盘）。"""
    m = extract(specs, topic=topic, solution=solution)
    bigs = big_titles(specs)
    if main:
        picked, rest = main, [x for x in (alts or []) if x != main]
    else:
        cands = candidates(m)
        picked = cands[0] if cands else (bigs[0] if bigs else "")
        rest = [x for x in cands[1:] if x != picked][:2]
        rest += [b for b in bigs if b != picked and b not in rest][: max(0, 3 - len(rest))]
    tags = [x for x in [m["topic"], m["solution"]] if x]
    tags += [x for x in GENERAL_TAGS if x not in tags][: max(0, 4 - len(tags))]
    return {"project": project, "source": spec_name, "main": picked,
            "main_len": clen(picked), "alts": rest, "cover": m["cover"],
            "tags": tags, "intro": m["intro"], "topic": m["topic"],
            "solution": m["solution"], "pain": m["pain"]}


def delivery_block(d):
    """交付清单的「发布信息」栏。"""
    if not d or not d.get("main"):
        return None
    L = ["", "## 发布信息", "",
         "- 主推标题：%s（%d 字）" % (d["main"], d["main_len"])]
    if d.get("alts"):
        L.append("- 备选标题：%s" % " / ".join(d["alts"]))
    if d.get("tags"):
        L.append("- 话题标签：%s" % " ".join("#" + t for t in d["tags"]))
    L += ["- 六类必选标签：含有 AI 生成内容",
          "- 封面大字、视频简介、发布检查见 `发布文案.md`"]
    return L


def ensure(base, specs, project="", spec_name="segments.json", topic=None, solution=None,
           main=None, alts=None, force=False, dry=False):
    """标题落项目根：没有就生成，有了就复用（`--force` 重生成，`dry` 只算不写）。"""
    jp, mp = pathlib.Path(base) / JSON_NAME, pathlib.Path(base) / MD_NAME
    if jp.is_file() and mp.is_file() and not force:
        try:
            return json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            pass
    d = build(project or pathlib.Path(base).name, specs, spec_name=spec_name,
              topic=topic, solution=solution, main=main, alts=alts)
    if dry:
        print("[标题] dry-run 主推：%s（%d 字）" % (d["main"], d["main_len"]))
        return d
    jp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(render_md(d, spec_name), encoding="utf-8")
    print("[标题] 主推：%s（%d 字）" % (d["main"], d["main_len"]))
    for t in d["alts"]:
        print("       备选：%s（%d 字）" % (t, clen(t)))
    print("[标题] 落盘：%s / %s" % (mp.name, jp.name))
    return d


def main():
    ap = argparse.ArgumentParser(description="发布标题（与 segments.json 同批产出）")
    ap.add_argument("spec", help="分段规格 JSON")
    ap.add_argument("--out", help="落盘目录（默认与规格同目录）")
    ap.add_argument("--project", help="项目名（默认取目录名）")
    ap.add_argument("--topic", help="主题词（默认从首段文案提取）")
    ap.add_argument("--solution", help="新做法词（默认按词频提取）")
    ap.add_argument("--main", help="指定主标题（脚本只做校验与落盘）")
    ap.add_argument("--alts", help="备选标题，`|` 分隔")
    ap.add_argument("--force", action="store_true", help="标题文件已存在也重生成")
    a = ap.parse_args()

    with open(a.spec, encoding="utf-8") as f:
        data = json.load(f)
    specs = data if isinstance(data, list) else data.get("segments", [])

    out = pathlib.Path(a.out) if a.out else pathlib.Path(a.spec).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    name = pathlib.Path(a.spec).name
    d = ensure(out, specs, project=a.project, spec_name=name, topic=a.topic,
               solution=a.solution, main=a.main,
               alts=[x.strip() for x in (a.alts or "").split("|") if x.strip()],
               force=a.force)

    bad = 0
    if d["main_len"] > C.TITLE_MAX:
        print("[标题] WARN 主推 %d 字，超过 %d 字" % (d["main_len"], C.TITLE_MAX))
        bad = 1
    sys.exit(bad)


if __name__ == "__main__":
    main()
