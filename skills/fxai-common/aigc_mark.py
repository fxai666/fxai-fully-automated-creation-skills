# -*- coding: utf-8 -*-
"""跨技能共用的 AI 生成合成标识：写入（显式角标同封装）+ 回读验收。

共用实现放在 fxai-common，产线按 ../fxai-common 相对路径 import，只调用不复写字段。
规则正文见 fxai-standard/references/01-产出铁律.md「成片品牌标识」，字段按 GB 45438-2025 §6.1＋附录 E。
所有最终交付视频必须同时具备显式画面角标与本模块写入的隐式 AIGC 元数据；
写成后强制回读校验，验收不过即停在交付前。只写进中间产物的不算。

对外两个函数：
    embed(path, project, author=None)  写入并回读校验，返回 AIGC 字符串
    verify(path)                       只读回读校验，失败抛错，返回 tags

命令行（验收门，退出码 0=全过 / 1=有未过）：
    python -B aigc_mark.py <视频> [<视频> ...]
"""
import os, sys, json, subprocess, tempfile, datetime

GLOBAL_CFG = os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json")
LABEL = "1"
_SUB_FIELDS = ("Label", "ContentProducer", "ProduceID", "ContentPropagator", "PropagateID")


def load_author():
    try:
        with open(GLOBAL_CFG, encoding="utf-8") as fh:
            g = json.load(fh)
        return ((g.get("brand") or {}).get("ai_mark_text") or "凤希AI生成").strip() or "凤希AI生成"
    except Exception:
        return "凤希AI生成"


def probe_tags(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags",
                        "-of", "json", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    try:
        return json.loads(r.stdout).get("format", {}).get("tags", {}) or {}
    except Exception:
        return {}


def build_aigc(author, pid):
    return json.dumps({"AIGC": {"Label": LABEL, "ContentProducer": author,
                                "ProduceID": pid, "ReservedCode1": "",
                                "ContentPropagator": author, "PropagateID": pid,
                                "ReservedCode2": ""}},
                      ensure_ascii=False, separators=(",", ":"))


def verify(path):
    """只读验收：AIGC 键存在、Label=1、生成方/传播方/编号非空。失败抛错。"""
    tags = probe_tags(path)
    raw = tags.get("AIGC")
    if not raw:
        raise RuntimeError("隐式标识验收未过：%s 缺 AIGC 元数据键" % path)
    try:
        body = (json.loads(raw).get("AIGC") or {})
    except Exception:
        raise RuntimeError("隐式标识验收未过：%s 的 AIGC 非合法 JSON" % path)
    missing = [k for k in _SUB_FIELDS if not str(body.get(k) or "").strip()]
    if missing:
        raise RuntimeError("隐式标识验收未过：%s 的 AIGC 缺字段 %s" % (path, ",".join(missing)))
    if str(body.get("Label")) != LABEL:
        raise RuntimeError("隐式标识验收未过：%s 的 AIGC.Label≠1" % path)
    return tags


def embed(path, project, author=None):
    """写入隐式标识并回读校验；已有合规 AIGC 键则跳过（幂等）。-c copy 不动画面。"""
    author = author or load_author()
    tags = probe_tags(path)
    raw = tags.get("AIGC")
    if raw:
        try:
            b = json.loads(raw).get("AIGC") or {}
            if str(b.get("Label")) == LABEL and all(str(b.get(k) or "").strip() for k in _SUB_FIELDS):
                return raw
        except Exception:
            pass

    pid = "%s_%s" % (project, datetime.date.today().strftime("%Y%m%d"))
    aigc = build_aigc(author, pid)
    base, ext = os.path.splitext(str(path))
    d = os.path.dirname(os.path.abspath(str(path))) or "."
    fd, tmp = tempfile.mkstemp(prefix=".aigc_", suffix=ext or ".mp4", dir=d)
    os.close(fd)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
           "-metadata", "AIGC=" + aigc,
           "-metadata", "title=" + pid,
           "-metadata", "artist=" + author,
           "-metadata", "author=" + author,
           "-metadata", "copyright=" + author,
           "-metadata", "comment=AI生成合成内容",
           "-metadata", "description=本视频为AI生成合成内容，" + author,
           "-movflags", "+use_metadata_tags+faststart", "-c", "copy", tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise RuntimeError("写入元数据失败：%s" % (r.stderr or "")[-800:])
        verify(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return aigc


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("用法：python -B aigc_mark.py <视频> [<视频> ...]")
        return 2
    failed = 0
    for p in argv:
        try:
            verify(p)
            print("[通过] %s" % p)
        except Exception as e:
            failed += 1
            print("[未过] %s：%s" % (p, e))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
