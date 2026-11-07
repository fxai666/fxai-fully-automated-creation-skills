#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fxai-rvc-convert 客户端：全程 HTTP 接口，不启停服务、不加载模型。

地址与超时读全局配置 `~/.workbuddy/fxai_global.json` 的 `rvc` 段；
单次可用 `--url` 或环境变量 `FXAI_RVC_URL` 覆盖。

用法：
    python rvc_client.py health
    python rvc_client.py voices
    python rvc_client.py convert <音频> --voice <音色> [--pitch 0] [--out <目录>]
    python rvc_client.py job <id> [--wait]
    ...
"""
import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.dont_write_bytecode = True

GLOBAL_CFG = Path(os.environ.get("FXAI_GLOBAL_CONFIG")
                  or (Path.home() / ".workbuddy" / "fxai_global.json"))

CFG_DEFAULTS = {
    "local_url": "http://127.0.0.1:8187",
    "cloud_url": "",
    "prefer_local": True,
    "timeout": 900,
    "poll": 2.0,
    "train_epochs": 250,
    "train_batch": 8,
    "train_sr": 40000,
    "sep_backend": "melband",
}

HERE = Path(__file__).resolve()


class RvcError(RuntimeError):
    pass


# ---------------------------------------------------------------- 配置 / 地址
def global_cfg():
    try:
        with open(GLOBAL_CFG, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def rvc_cfg():
    g = global_cfg()
    blk = g.get("rvc")
    blk = blk if isinstance(blk, dict) else {}
    cfg = dict(CFG_DEFAULTS)
    cfg.update({k: v for k, v in blk.items() if v not in (None, "")})
    return cfg


def _probe(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def resolve_url(explicit=None):
    """--url > FXAI_RVC_URL > prefer_local 顺序取本机 / 线上；都探不通回本机地址。"""
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("FXAI_RVC_URL")
    if env:
        return env.rstrip("/")
    cfg = rvc_cfg()
    local = (cfg.get("local_url") or "").rstrip("/")
    cloud = (cfg.get("cloud_url") or "").rstrip("/")
    if cfg.get("prefer_local", True):
        order = [local, cloud]
    else:
        order = [cloud, local]
    order = [u for u in order if u]
    for u in order:
        if _probe(u):
            return u
    if not order:
        raise RvcError("全局配置里没有 rvc.local_url，也没给 --url")
    return order[0]


# ---------------------------------------------------------------- HTTP
def _open(url, method="GET", data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        msg = raw.decode("utf-8", "replace")
        try:
            msg = json.loads(msg).get("error") or msg
        except Exception:
            pass
        raise RvcError("HTTP %d：%s" % (e.code, msg)) from None
    except urllib.error.URLError as e:
        raise RvcError("连不上服务 %s（%s）" % (url, e.reason)) from None


def _json(url, method="GET", data=None, headers=None, timeout=60):
    _, _, raw = _open(url, method=method, data=data, headers=headers, timeout=timeout)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        raise RvcError("服务返回的不是 JSON：%s" % raw[:200].decode("utf-8", "replace"))


def _multipart(fields, files):
    """fields: {name: str}；files: [(name, filename, bytes)]"""
    boundary = "----fxairvc" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        if v is None:
            continue
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n"
                    % (boundary, k)).encode())
        out.append(str(v).encode("utf-8"))
        out.append(b"\r\n")
    for name, fname, blob in files:
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                    "Content-Type: %s\r\n\r\n" % (boundary, name, fname, ctype)).encode("utf-8"))
        out.append(blob)
        out.append(b"\r\n")
    out.append(("--%s--\r\n" % boundary).encode())
    body = b"".join(out)
    return body, "multipart/form-data; boundary=%s" % boundary


# ---------------------------------------------------------------- 基础接口
def health(url=None):
    return _json(resolve_url(url) + "/health", timeout=10)


def voices(url=None):
    return _json(resolve_url(url) + "/api/voices")


def job(jid, url=None):
    return _json(resolve_url(url) + "/api/job/" + urllib.parse.quote(jid))["job"]


def cancel(jid, url=None):
    return _json(resolve_url(url) + "/api/job/" + urllib.parse.quote(jid) + "/cancel",
                 method="POST", data=b"")


def jobs(limit=50, offset=0, url=None):
    return _json(resolve_url(url) + "/api/jobs?limit=%d&offset=%d" % (limit, offset))


def delete(jid, url=None):
    return _json(resolve_url(url) + "/api/jobs/" + urllib.parse.quote(jid), method="DELETE")


def clear(url=None):
    return _json(resolve_url(url) + "/api/jobs/clear", method="POST", data=b"")


def load(voice=None, url=None):
    q = "?voice=" + urllib.parse.quote(voice) if voice else ""
    return _json(resolve_url(url) + "/load" + q, method="POST", data=b"", timeout=300)


def unload(url=None):
    return _json(resolve_url(url) + "/unload", method="POST", data=b"", timeout=120)


# ---------------------------------------------------------------- 转换
def wait_job(jid, url=None, timeout=900, poll=2.0, quiet=False):
    t0, last = time.time(), None
    while True:
        j = job(jid, url)
        key = (j.get("stage"), j.get("progress"))
        if key != last and not quiet:
            print("[%s %3s%%] %s" % (j.get("status"), j.get("progress"),
                                     j.get("stage_text") or j.get("stage")), flush=True)
            last = key
        if j["status"] in ("done", "error", "canceled"):
            return j
        if time.time() - t0 > timeout:
            raise RvcError("等待超时（%ds），任务仍在运行：%s（可 job %s 继续查）"
                           % (timeout, jid, jid))
        time.sleep(poll)


def download(jid, name, out=None, url=None):
    path = resolve_url(url) + "/api/jobs/%s/file/%s?dl=1" % (
        urllib.parse.quote(jid), urllib.parse.quote(name))
    _, headers, raw = _open(path, timeout=1800)
    dest = Path(out) if out else Path.cwd() / name
    if dest.is_dir():
        dest = dest / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return dest


def convert(audio, voice, url=None, out=None, wait=True, **params):
    """上传 → 建任务 → 等终态 →（给了 out 就）下载成品，返回成品路径。"""
    p = Path(audio)
    if not p.is_file():
        raise RvcError("找不到音频：%s" % audio)
    cfg = rvc_cfg()
    fields = {
        "voice": voice,
        "pitch": params.get("pitch"),
        "index_rate": params.get("index_rate"),
        "protect": params.get("protect"),
        "volume_envelope": params.get("volume_envelope"),
        "f0_method": params.get("f0_method"),
        "sep_backend": params.get("sep_backend"),
        "inst_gain": params.get("inst_gain"),
        "vocal_only": 1 if params.get("vocal_only") else None,
        "save": 0 if params.get("save") is False else None,
    }
    body, ctype = _multipart(fields, [("file", p.name, p.read_bytes())])
    base = resolve_url(url)
    r = _json(base + "/api/convert", method="POST", data=body,
              headers={"Content-Type": ctype}, timeout=600)
    j = r["job"]
    jid = j["id"]
    ahead = r.get("queued_ahead") or 0
    print("已提交 %s → %s（前面排队 %d）" % (jid, voice, ahead), flush=True)
    if not wait:
        return jid
    j = wait_job(jid, url, timeout=int(params.get("timeout") or cfg["timeout"]),
                 poll=float(cfg.get("poll") or 2))
    if j["status"] != "done":
        raise RvcError("%s：%s" % (j["status"], j.get("error") or j.get("stage_text")))
    name = j.get("result") or ""
    if out:
        dest = download(jid, name, out, url)
        print("成品：%s（%.1fs，原曲 %.1fs）"
              % (dest, j.get("elapsed") or 0, j.get("duration") or 0), flush=True)
        return dest
    print("成品在服务端：%s%s" % (resolve_url(url), j.get("result_url")), flush=True)
    return j.get("result_url")


# ---------------------------------------------------------------- 训练
def train(audio, name, url=None, epochs=None, batch=None, sr=None, wait=True,
          set_id=None, set_name=None, set_owner=None, **kw):
    cfg = rvc_cfg()
    files = []
    for f in (audio or []):
        p = Path(f)
        if not p.is_file():
            raise RvcError("找不到素材：%s" % f)
        files.append(("file", p.name, p.read_bytes()))
    if not files and not set_id:
        raise RvcError("至少给一个素材音频，或用 --set 指定训练素材库")
    fields = {
        "name": name,
        "epochs": epochs or cfg["train_epochs"],
        "batch": batch or cfg["train_batch"],
        "sr": sr or cfg["train_sr"],
    }
    if set_id:
        fields["set_id"] = set_id
    else:
        # 素材一律先入库：不指定库名就用音色名当库名（服务端建库后开训）
        fields["set_name"] = set_name or name
        if set_owner:
            fields["set_owner"] = set_owner
    body, ctype = _multipart(fields, files)
    base = resolve_url(url)
    r = _json(base + "/api/train", method="POST", data=body,
              headers={"Content-Type": ctype}, timeout=1800)
    j = r["job"]
    jid = j["id"]
    lib = (r.get("set") or {}).get("name") or set_id
    print("已建训练任务 %s（%s，%s epoch%s）"
          % (jid, name, fields["epochs"], "，素材库 " + lib if lib else ""), flush=True)
    if not wait:
        return jid
    return wait_train(jid, url, timeout=kw.get("timeout") or 6 * 3600)


# ---------------------------------------------------------------- 训练素材库
def train_sets(url=None):
    """列出所有训练素材库（可复用，不用重传）。"""
    return _json(resolve_url(url) + "/api/train/sets")


def train_set(sid, url=None):
    """单套素材库详情（带文件清单）。"""
    return _json(resolve_url(url) + "/api/train/sets/" + urllib.parse.quote(sid))


def train_set_new(name, owner=None, note=None, url=None):
    """先建一个空素材库，之后往里传素材。"""
    return _json(resolve_url(url) + "/api/train/sets/new", method="POST",
                 data=json.dumps({"name": name, "owner": owner or "",
                                  "note": note or ""}).encode("utf-8"),
                 headers={"Content-Type": "application/json"})


def train_set_move(from_sid, to_sid, items, url=None):
    """把某个库里的文件（序号数组）移到另一个库。"""
    return _json(resolve_url(url) + "/api/train/sets/" + urllib.parse.quote(from_sid) + "/move",
                 method="POST",
                 data=json.dumps({"to": to_sid, "items": list(items)}).encode("utf-8"),
                 headers={"Content-Type": "application/json"})


def train_set_add(sid, audio, url=None):
    """往已有素材库追加素材（按内容去重）。"""
    files = []
    for f in (audio or []):
        p = Path(f)
        if not p.is_file():
            raise RvcError("找不到素材：%s" % f)
        files.append(("file", p.name, p.read_bytes()))
    if not files:
        raise RvcError("至少给一个素材音频")
    body, ctype = _multipart({}, files)
    return _json(resolve_url(url) + "/api/train/sets/" + urllib.parse.quote(sid) + "/files",
                 method="POST", data=body, headers={"Content-Type": ctype}, timeout=1800)


def train_set_adopt(url=None):
    """把服务器上跑过的 train/*/raw 收编成素材库（一次性）。"""
    return _json(resolve_url(url) + "/api/train/sets/adopt", method="POST")


# ---------------------------------------------------------------- 训练中间文件
def workspaces(url=None):
    """训练中间文件盘点（engine/logs/<slug>/，一次训练 1~1.5GB，占盘大头）。"""
    return _json(resolve_url(url) + "/api/train/workspaces")


def clean_workspaces(slugs, url=None):
    """删掉指定 slug 的中间目录。不影响 models/voices 里的 pth / index。"""
    return _json(resolve_url(url) + "/api/train/workspaces/clean", method="POST",
                 data=json.dumps({"slugs": list(slugs)}, ensure_ascii=False).encode("utf-8"),
                 headers={"Content-Type": "application/json"}, timeout=600)


def wait_train(jid, url=None, timeout=6 * 3600, poll=5.0):
    t0, last = time.time(), None
    while True:
        r = _json(resolve_url(url) + "/api/train/job/" + urllib.parse.quote(jid))
        j = r["job"]
        key = (j.get("stage"), int(j.get("progress") or 0))
        if key != last:
            eta = j.get("eta")
            print("[%s %5.1f%%] %s%s" % (j.get("status"), j.get("progress") or 0,
                                         j.get("stage_text") or j.get("stage"),
                                         "  剩 %ds" % eta if eta else ""), flush=True)
            last = key
        if j["status"] in ("done", "error", "canceled"):
            return j
        if time.time() - t0 > timeout:
            raise RvcError("训练等待超时（%ds），任务仍在跑：%s" % (timeout, jid))
        time.sleep(poll)


def pretrained(url=None, start=False):
    base = resolve_url(url)
    if start:
        return _json(base + "/api/train/pretrained", method="POST", data=b"")
    return _json(base + "/api/train/pretrained")


# ---------------------------------------------------------------- 输出
def show(obj, as_json=False):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return
    if isinstance(obj, dict) and "voices" in obj:
        for v in obj["voices"]:
            print("%-24s %-16s %6.1fMB  索引:%s"
                  % (v["id"], v.get("name") or "", v.get("size_mb") or 0,
                     v.get("index") or "无"))
        return
    if isinstance(obj, dict) and "jobs" in obj and "total" in obj:
        for j in obj["jobs"]:
            print("%s  %-28s %s  %s" % (j["id"], (j.get("name") or "")[:28],
                                        j.get("status"), j.get("stage_text") or ""))
        print("共 %d 条" % obj["total"])
        return
    if isinstance(obj, dict) and "sets" in obj:
        for s in obj["sets"]:
            label = (s["owner"] + " · " + s["name"]) if s.get("owner") else (s.get("name") or "")
            print("%-22s %-26s %3d段 %6.2f分钟 %6.1fMB  训过%d次%s"
                  % (s["id"], label[:26], s.get("count") or 0, s.get("minutes") or 0,
                     s.get("mb") or 0, s.get("used") or 0,
                     ("（最近 %s epoch）" % s["last_epochs"]) if s.get("last_epochs") else ""))
        print("共 %d 套，合计 %.1fMB" % (obj.get("total") or 0, obj.get("total_mb") or 0))
        return
    if isinstance(obj, list):
        for j in obj:
            print("%s  %-20s %-8s %5.1f%%  %s"
                  % (j["id"], j.get("name") or "", j.get("status"),
                     j.get("progress") or 0, j.get("stage_text") or ""))
        return
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _hoist(argv):
    """把 --url / --json 从任意位置提到命令行最前，子命令前后都能写。"""
    head, rest, i = [], [], 0
    while i < len(argv):
        t = argv[i]
        if t == "--url" and i + 1 < len(argv):
            head += [t, argv[i + 1]]
            i += 2
            continue
        if t.startswith("--url=") or t == "--json":
            head.append(t)
            i += 1
            continue
        rest.append(t)
        i += 1
    return head + rest


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="fxai-svc 音色转换客户端")
    ap.add_argument("--url", help="服务地址（覆盖全局配置）")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="服务状态")
    sub.add_parser("voices", help="音色列表")

    c = sub.add_parser("convert", help="换声：上传 → 等 → 下载")
    c.add_argument("audio")
    c.add_argument("--voice", required=True)
    c.add_argument("--pitch", type=int)
    c.add_argument("--index-rate", type=float)
    c.add_argument("--protect", type=float)
    c.add_argument("--volume-envelope", type=float)
    c.add_argument("--inst-gain", type=float)
    c.add_argument("--f0-method", choices=["rmvpe", "fcpe"])
    c.add_argument("--sep-backend", choices=["melband", "skill"])
    c.add_argument("--vocal-only", action="store_true", help="传入的已是干声，跳过分离")
    c.add_argument("--no-save", action="store_true", help="不进作品库，只留 12 小时")
    c.add_argument("--out", help="成品落点（目录或文件）")
    c.add_argument("--timeout", type=int, help="等待终态上限秒数")
    c.add_argument("--no-wait", action="store_true", help="只提交，立刻返回 job_id")

    j = sub.add_parser("job", help="查任务")
    j.add_argument("id")
    j.add_argument("--wait", action="store_true")

    x = sub.add_parser("cancel", help="取消转换")
    x.add_argument("id")

    ls = sub.add_parser("jobs", help="作品库列表")
    ls.add_argument("--limit", type=int, default=50)
    ls.add_argument("--offset", type=int, default=0)

    d = sub.add_parser("download", help="下载成品 / 原曲")
    d.add_argument("id")
    d.add_argument("--name", help="任务目录下的文件名，缺省取成品")
    d.add_argument("--out")

    rm = sub.add_parser("delete", help="删除一条作品")
    rm.add_argument("id")
    sub.add_parser("clear", help="清空作品库")

    ld = sub.add_parser("load", help="手动装载模型")
    ld.add_argument("--voice")
    sub.add_parser("unload", help="释放显存")

    t = sub.add_parser("train", help="音色训练")
    t.add_argument("audio", nargs="*", help="素材音频；用 --set 时可省略")
    t.add_argument("--name", required=True)
    t.add_argument("--set", dest="set_id", help="用已有训练素材库开训（不用重传文件）")
    t.add_argument("--set-name", help="上传素材时存进的素材库名，默认同音色名；库里已有同名会报错")
    t.add_argument("--set-owner", help="素材库归属（谁的声音 / 哪个角色）")
    t.add_argument("--epochs", type=int, help="20~600，默认 250")
    t.add_argument("--batch", type=int)
    t.add_argument("--sr", type=int)
    t.add_argument("--timeout", type=int)
    t.add_argument("--no-wait", action="store_true")

    sub.add_parser("sets", help="训练素材库列表")
    sd = sub.add_parser("set-detail", help="单个素材库详情（文件清单）")
    sd.add_argument("id")
    sn = sub.add_parser("set-new", help="新建空素材库")
    sn.add_argument("name")
    sn.add_argument("--owner")
    sn.add_argument("--note")
    sa = sub.add_parser("set-add", help="往素材库追加素材")
    sa.add_argument("id")
    sa.add_argument("audio", nargs="+")
    sm = sub.add_parser("set-move", help="把文件从一个素材库移到另一个")
    sm.add_argument("from_id")
    sm.add_argument("to_id")
    sm.add_argument("--items", required=True,
                    help="文件序号，1 起算，逗号分隔；序号见 set-detail，从 1 开始")
    sub.add_parser("set-adopt", help="把服务器上跑过的训练素材收编成素材库")

    sub.add_parser("ws", help="训练中间文件盘点（占盘大头，一次 1~1.5GB）")
    wc = sub.add_parser("ws-clean", help="清理训练中间文件（不影响已训好的音色）")
    wc.add_argument("slugs", nargs="+", help="工作区 slug，见 ws 输出")

    sub.add_parser("train-jobs", help="训练任务列表")
    tj = sub.add_parser("train-job", help="训练进度")
    tj.add_argument("id")
    tj.add_argument("--wait", action="store_true")
    tc = sub.add_parser("train-cancel", help="取消训练")
    tc.add_argument("id")
    p = sub.add_parser("pretrained", help="训练底模状态")
    p.add_argument("--download", action="store_true", help="开始下载底模")

    a = ap.parse_args(_hoist(list(sys.argv[1:] if argv is None else argv)))
    try:
        if a.cmd == "health":
            h = health(a.url)
            if a.json:
                show(h, True)
            else:
                print("服务：%s  %s" % (h.get("service"), h.get("state")))
                print("显卡：%s  占用 %sMB  设备 %s"
                      % (h.get("gpu") or "-", h.get("vram_mb"), h.get("device") or "-"))
                print("模型：%s%s  分离后端 %s（%s）"
                      % ("已装载" if h.get("model_loaded") else "未装载",
                         " / " + (h.get("loaded_voice") or "") if h.get("loaded_voice") else "",
                         h.get("sep_backend"),
                         "就绪" if (h.get("separator") or {}).get("ready") else "未就绪"))
                print("队列：跑 %s / 排 %s   空闲卸载 %ss"
                      % (h.get("in_flight"), h.get("queued"), h.get("idle_timeout")))
        elif a.cmd == "voices":
            show(voices(a.url), a.json)
        elif a.cmd == "convert":
            res = convert(a.audio, a.voice, a.url, a.out, not a.no_wait,
                          pitch=a.pitch, index_rate=a.index_rate, protect=a.protect,
                          volume_envelope=a.volume_envelope, inst_gain=a.inst_gain,
                          f0_method=a.f0_method, sep_backend=a.sep_backend,
                          vocal_only=a.vocal_only, save=not a.no_save,
                          timeout=a.timeout)
            if a.no_wait:
                show({"success": True, "job_id": res}, a.json)
        elif a.cmd == "job":
            show(wait_job(a.id, a.url) if a.wait else job(a.id, a.url), a.json)
        elif a.cmd == "cancel":
            show(cancel(a.id, a.url), a.json)
        elif a.cmd == "jobs":
            show(jobs(a.limit, a.offset, a.url), a.json)
        elif a.cmd == "download":
            jj = job(a.id, a.url)
            name = a.name or jj.get("result") or ""
            if not name:
                raise RvcError("任务还没有成品，稍后再试")
            print(download(a.id, name, a.out, a.url))
        elif a.cmd == "delete":
            show(delete(a.id, a.url), a.json)
        elif a.cmd == "clear":
            show(clear(a.url), a.json)
        elif a.cmd == "load":
            show(load(a.voice, a.url), a.json)
        elif a.cmd == "unload":
            show(unload(a.url), a.json)
        elif a.cmd == "train":
            r = train(a.audio, a.name, a.url, a.epochs, a.batch, a.sr,
                      not a.no_wait, set_id=a.set_id, set_name=a.set_name,
                      set_owner=a.set_owner, timeout=a.timeout)
            if isinstance(r, str):
                print("训练任务：%s" % r)
            else:
                print("训练结束：%s  %s" % (r.get("status"), (r.get("result") or {}).get("slug") or ""))
        elif a.cmd == "sets":
            show(train_sets(a.url), a.json)
        elif a.cmd == "set-detail":
            show(train_set(a.id, a.url), a.json)
        elif a.cmd == "set-new":
            r = train_set_new(a.name, a.owner, a.note, a.url)
            if a.json:
                show(r, True)
            else:
                print("已建素材库 %s：%s" % (r.get("id"), r.get("name")))
        elif a.cmd == "set-add":
            r = train_set_add(a.id, a.audio, a.url)
            if a.json:
                show(r, True)
            else:
                print("已追加 %s 个（跳过 %s 个），现在共 %s 段"
                      % (r.get("added", 0), r.get("skipped", 0), r.get("count", 0)))
        elif a.cmd == "set-move":
            idxs = [int(x) for x in str(a.items).replace("，", ",").split(",") if x.strip()]
            # 页面上的序号从 1 起，接口从 0 起
            r = train_set_move(a.from_id, a.to_id, [i - 1 for i in idxs], a.url)
            if a.json:
                show(r, True)
            else:
                print("移动 %s 个（跳过 %s 个）→ %s，源库剩 %s 段"
                      % (r.get("moved", 0), r.get("skipped", 0),
                         (r.get("to") or {}).get("name", a.to_id),
                         (r.get("from") or {}).get("count", 0)))
        elif a.cmd == "set-adopt":
            r = train_set_adopt(a.url)
            if a.json:
                show(r, True)
            else:
                print("收编了 %s 个素材库" % r.get("adopted", 0))
                show(r, False)
        elif a.cmd == "ws":
            d = workspaces(a.url)
            if a.json:
                show(d, True)
            else:
                items = d.get("items") or []
                print("训练中间文件共 %.1f MB（%s）" % (d.get("total_mb", 0), d.get("dir", "")))
                for x in items:
                    tag = "训练中·不可删" if x.get("locked") else ("音色在库" if x.get("voice") else "无对应音色")
                    print("  %-24s %8.1f MB  %4d 文件  %s  %s"
                          % (x["slug"], x.get("mb", 0), x.get("files", 0), tag,
                             (str(x.get("epochs")) + "ep") if x.get("epochs") else ""))
                if not items:
                    print("  没有可清理的中间文件")
        elif a.cmd == "ws-clean":
            r = clean_workspaces(a.slugs, a.url)
            if a.json:
                show(r, True)
            else:
                print("已释放 %.1f MB，删掉：%s"
                      % (r.get("freed_mb", 0), "、".join(r.get("removed") or []) or "无"))
                for s in r.get("skipped") or []:
                    print("  跳过 %s：%s" % (s.get("slug"), s.get("why")))
        elif a.cmd == "train-jobs":
            r = _json(resolve_url(a.url) + "/api/train/jobs")
            show(r.get("jobs") or [], a.json)
        elif a.cmd == "train-job":
            if a.wait:
                show(wait_train(a.id, a.url), a.json)
            else:
                r = _json(resolve_url(a.url) + "/api/train/job/" + urllib.parse.quote(a.id))
                show(r["job"], a.json)
        elif a.cmd == "train-cancel":
            r = _json(resolve_url(a.url) + "/api/train/job/%s/cancel"
                      % urllib.parse.quote(a.id), method="POST", data=b"")
            show(r, a.json)
        elif a.cmd == "pretrained":
            show(pretrained(a.url, a.download), a.json)
        return 0
    except RvcError as e:
        print("错误：%s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
