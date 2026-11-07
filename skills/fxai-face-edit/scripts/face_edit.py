# -*- coding: utf-8 -*-
"""人脸替换 / 修复 / 增强：把任务交给云端人脸服务，取回产物到本机。

后端可替换：全局配置 `face.backend` 指当前用哪一套，默认 `facefusion`。
本模块只按地址发 HTTP，不启停任何服务；探不通就把开机命令打回给人。

配置（用户级全局配置 ~/.workbuddy/fxai_global.json）：按「云服务商」分档，
一台机器一档，`face.active` 指当前用哪一档（换别人的 GPU 就再加一档，不动老的）：
    "face": {
        "backend": "facefusion",              # 默认后端，档里没写时用它
        "active": "xgy",                      # 当前档
        "profiles": {
            "xgy": {                          # 仙宫云
                "label": "仙宫云 · FaceFusion",
                "provider": "xiangongyun",
                "backend": "facefusion",
                "instance_id": "rwk8ewpc07nv86ep",
                "instance_name": "凤希AI-人脸替换",
                "scheme": "https",
                "cloud_url": "https://<实例ID>-80.container.x-gpu.com",
                "ui_cloud_url": "https://<实例ID>-7860.container.x-gpu.com",
                "local_url": "",
                "prefer": "cloud"
            }
        }
    }
取法与 ComfyUI 那套一致：--url > 环境变量 FXAI_FACE_URL > 当前档 cloud_url > face.cloud_url（老写法）。
换档：--profile <档名>（或环境变量 FXAI_FACE_PROFILE），也可 `use <档名>` 改默认档。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILLS = os.path.dirname(os.path.dirname(_HERE))
for _d in (os.path.join(_SKILLS, "fxai-common"),):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import fxai_common as fxc  # noqa: E402

fxc.allow_local()
_OP = fxc.opener()

_GLOBAL_CFG_PATH = os.environ.get(
    "FXAI_GLOBAL_CONFIG", os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))

DEFAULT_BACKEND = "facefusion"
DEFAULT_INSTANCE_NAME = "凤希AI-人脸替换"
DEFAULT_PROFILE = "xgy"          # 缺省档：仙宫云；换别的云再加档，不覆盖老档
_PROFILE = ""                    # 命令行 --profile 指定；空 = 用 face.active

# 处理器：中文名 → 提交用的 key。换后端只换这一份表，调用侧不动。
PROCESSORS = {
    "facefusion": {
        "换脸": "face_swapper",
        "深度换脸": "deep_swapper",
        "人脸增强": "face_enhancer",
        "年龄修改": "age_modifier",
        "表情还原": "expression_restorer",
        "人脸编辑": "face_editor",
        "画面上色": "frame_colorizer",
        "画面增强": "frame_enhancer",
        "唇形同步": "lip_syncer",
        "背景移除": "background_remover",
        "人脸调试": "face_debugger",
    }
}
# 素材位 → 接口返回的键名（接口用复数）
LIB_KEY = {"source": "sources", "target": "targets", "audio": "audios"}
# 必须给头像（源脸）的处理器
NEED_SOURCE = {"face_swapper", "deep_swapper"}
# 必须给音频的处理器
NEED_AUDIO = {"lip_syncer"}

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".avi")
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg")


# ---------- 配置 ----------
def global_cfg():
    """用户级全局配置。"""
    try:
        with open(_GLOBAL_CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_global_cfg(cfg):
    """整份写回全局配置（先读后改再写，别拿局部对象覆盖别人的键）。"""
    d = os.path.dirname(_GLOBAL_CFG_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = _GLOBAL_CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _GLOBAL_CFG_PATH)


def face_cfg():
    """全局配置里的 face 段。"""
    c = global_cfg().get("face")
    return c if isinstance(c, dict) else {}


def profiles():
    """所有配置档：{档名: {...}}。"""
    p = face_cfg().get("profiles")
    return {k: v for k, v in p.items() if isinstance(v, dict)} if isinstance(p, dict) else {}


def profile_name(explicit=""):
    """当前档名：--profile > FXAI_FACE_PROFILE > face.active > 第一个档。没有档返回空串。"""
    want = str(explicit or _PROFILE or os.environ.get("FXAI_FACE_PROFILE")
               or face_cfg().get("active") or "").strip()
    allp = profiles()
    if want and want in allp:
        return want
    if want and allp:
        raise RuntimeError("没有名为「%s」的配置档。现有：%s" % (want, "、".join(allp)))
    if allp:
        return DEFAULT_PROFILE if DEFAULT_PROFILE in allp else sorted(allp)[0]
    return ""


def profile(explicit=""):
    """当前档的内容 + face 段里的全局默认（backend / instance_name）。"""
    name = profile_name(explicit)
    p = dict(profiles().get(name) or {})
    p["_name"] = name
    if not p.get("backend"):
        p["backend"] = face_cfg().get("backend") or DEFAULT_BACKEND
    if not p.get("instance_name"):
        p["instance_name"] = face_cfg().get("instance_name") or DEFAULT_INSTANCE_NAME
    return p


def backend(prof=""):
    """当前后端名，缺省 facefusion。"""
    return str(profile(prof).get("backend") or DEFAULT_BACKEND).strip().lower()


def base_url(url="", prof=""):
    """服务基址：--url > 环境变量 > 当前档（prefer=local 时优先 local_url）> 老写法 face.cloud_url。"""
    u = str(url or "").strip()
    if not u:
        u = str(os.environ.get("FXAI_FACE_URL") or "").strip()
    if not u:
        p = profile(prof)
        if str(p.get("prefer") or "").lower() == "local" and p.get("local_url"):
            u = str(p.get("local_url")).strip()
        else:
            u = str(p.get("cloud_url") or p.get("local_url") or "").strip()
    if not u:
        u = str(face_cfg().get("cloud_url") or "").strip()
    if not u:
        p = profile(prof)
        raise RuntimeError(
            "没拿到人脸服务地址。在全局配置 face.profiles.%s.cloud_url 里写，或用 --url 现给；\n"
            "  机器上还没建实例的话，用 fxai-xiangongyun-deploy 建一台（实例名用 %s），\n"
            "  或跑本脚本的 resolve 子命令按名字定位已有实例，定位到再加 --save 写回配置。"
            % (p.get("_name") or DEFAULT_PROFILE, p.get("instance_name")))
    return fxc.norm_url(u)


def ui_url(prof=""):
    """可视化界面地址（没配就按主入口端口换算不出来，返回空串）。"""
    return fxc.norm_url(str(profile(prof).get("ui_cloud_url") or ""))


def proc_key(name, be=""):
    """处理器名归一：中文或英文 key 都收，返回提交用的 key。"""
    be = be or backend()
    table = PROCESSORS.get(be) or PROCESSORS[DEFAULT_BACKEND]
    n = str(name or "").strip()
    if n in table:
        return table[n]
    low = n.lower()
    for cn, key in table.items():
        if key == low:
            return key
    raise RuntimeError("未知的处理器「%s」。%s 可用：%s"
                       % (n, be, "、".join(table)))


def proc_cn(key, be=""):
    """key → 中文名，未在表里时原样返回。"""
    be = be or backend()
    for cn, k in (PROCESSORS.get(be) or {}).items():
        if k == key:
            return cn
    return key


# ---------- HTTP ----------
def _get(path, url="", timeout=30):
    u = base_url(url) + path
    try:
        with _OP.open(u, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        note = ""
        if e.code in (502, 503, 504):
            note = "（网关层：实例多半是关机或还在启动）\n" + _boot_hint()
        raise RuntimeError("接口返回 %s：%s %s" % (e.code, _detail(e), note))
    except Exception as e:
        raise RuntimeError("连不上 %s（%s）。%s" % (u, e, _boot_hint()))
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return raw


def _boot_hint():
    """探不通时给的开机提示：按当前档的 provider 出对应的命令，不一律套仙宫云。"""
    p = profile()
    prov = str(p.get("provider") or "xiangongyun").lower()
    iid = str(p.get("instance_id") or "<实例ID>")
    if prov in ("onethingai", "otai"):
        return ("服务没起就先开机（OneThingAI）：\n"
                "  python <fxai-onethingai-deploy>/scripts/otai_api.py boot %s" % iid)
    if prov in ("xiangongyun", "xgy", "xg"):
        return ("服务没起就先开机（仙宫云）：\n"
                "  python <fxai-xiangongyun-deploy>/scripts/xgy_api.py boot %s\n"
                "  python <fxai-xiangongyun-deploy>/scripts/xgy_api.py wait %s running" % (iid, iid))
    return "该档（%s）不是按云平台接的，确认机器在线、地址与端口对得上。" % (p.get("_name") or "?")



def _post(path, payload, url="", timeout=60):
    u = base_url(url) + path
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(u, data=body, headers={"Content-Type": "application/json"})
    try:
        with _OP.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("接口返回 %s：%s" % (e.code, _detail(e)))
    except Exception as e:
        raise RuntimeError("调用失败 %s：%s" % (u, e))


def _detail(e):
    try:
        return e.read().decode("utf-8", "replace")[:300]
    except Exception:
        return str(e)


def _ok(res, what):
    if not isinstance(res, dict) or not res.get("ok"):
        raise RuntimeError("%s失败：%s" % (what, (res or {}).get("error") or res))
    return res


# ---------- 能力 ----------
def health(url=""):
    """服务探活：队列长度、显卡、剩余磁盘。"""
    return _ok(_get("/api/health", url), "探活")


def library(kind="", url=""):
    """素材库：头像 source / 目标 target / 音频 audio。接口返回的键是复数形式。"""
    res = _ok(_get("/api/library", url), "读素材库")
    if not kind:
        return res
    return res.get(LIB_KEY.get(kind, kind))


def jobs(url=""):
    """最近 100 条任务。"""
    return _ok(_get("/api/jobs", url), "读任务列表").get("jobs") or []


def status(job_id, url=""):
    """单条任务详情，含服务端日志尾部。"""
    return _ok(_get("/api/jobs/%s" % urllib.parse.quote(job_id), url), "查任务").get("job") or {}


def faces(target, frame=0, url="", **kw):
    """目标某一帧检出的人脸列表，供挑「换哪张脸」。"""
    body = {"target": target, "frame": int(frame or 0)}
    body.update({k: v for k, v in kw.items() if v not in (None, "")})
    return _ok(_post("/api/faces", body, url, timeout=240), "人脸检测")


def upload(file_path, kind, url=""):
    """上传本地素材，返回服务端文件名。kind：source / target / audio。"""
    if kind not in ("source", "target", "audio"):
        raise RuntimeError("kind 只能是 source / target / audio")
    if not os.path.isfile(file_path):
        raise RuntimeError("本地文件不存在：%s" % file_path)
    name = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        data = f.read()
    b = "----fxai%s" % os.urandom(8).hex()
    body = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n" % (b, name)).encode("utf-8")
            + data + ("\r\n--%s--\r\n" % b).encode("utf-8"))
    u = "%s/api/upload?kind=%s" % (base_url(url), kind)
    req = urllib.request.Request(
        u, data=body, headers={"Content-Type": "multipart/form-data; boundary=%s" % b})
    try:
        with _OP.open(req, timeout=600) as r:
            res = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("上传返回 %s：%s" % (e.code, _detail(e)))
    except Exception as e:
        raise RuntimeError("上传失败 %s：%s" % (u, e))
    items = _ok(res, "上传").get("items") or []
    if not items:
        raise RuntimeError("上传没回文件名：%s" % res)
    return items[0].get("name")


def submit(target, processors, source="", audio="", face_pos=None, frame=0,
           options=None, url=""):
    """提交任务，返回 job_id。素材名指服务端素材库里已有的名字。"""
    be = backend()
    procs = [proc_key(p, be) for p in (processors or [])]
    if not procs:
        raise RuntimeError("至少给一个处理器")
    if not target:
        raise RuntimeError("缺少目标")
    keys = set(procs)
    if keys & NEED_SOURCE and not source:
        raise RuntimeError("「%s」需要头像（--source）"
                           % "、".join(proc_cn(p, be) for p in sorted(keys & NEED_SOURCE)))
    if keys & NEED_AUDIO and not audio:
        raise RuntimeError("唇形同步需要音频（--audio），且目标必须是视频")
    body = {"target": target, "processors": procs, "options": options or {}}
    if source:
        body["source"] = source
    if audio:
        body["audio"] = audio
    if face_pos is not None and int(face_pos) >= 0:
        body["face_pos"] = int(face_pos)
        if int(frame or 0) > 0:
            body["face_frame"] = int(frame)
    return _ok(_post("/api/jobs", body, url), "提交任务").get("job", {}).get("id")


def wait(job_id, timeout=1800, interval=5, url="", quiet=False):
    """轮询等终态，返回终态 job。终态：succeeded / failed / cancelled / interrupted。"""
    done = ("succeeded", "failed", "cancelled", "interrupted")
    t0 = time.time()
    last = ""
    while True:
        j = status(job_id, url)
        st = j.get("status") or ""
        msg = j.get("message") or ""
        if not quiet and (msg != last or st in done):
            print("[%s] %s%% %s" % (st, j.get("progress") or 0, msg))
            last = msg
        if st in done:
            return j
        if time.time() - t0 > timeout:
            raise RuntimeError("等 %s 超过 %s 秒没到终态（当前 %s）。任务仍在服务端跑，"
                               "稍后用 status 子命令接着看" % (job_id, timeout, st))
        time.sleep(interval)


def download(job_id, out_dir="", name="", url=""):
    """下载产物到 out_dir，返回本地路径。产物文件头异常直接抛错。"""
    j = status(job_id, url)
    out_name = name or j.get("output_name") or (job_id + ".bin")
    dst_dir = os.path.abspath(out_dir or os.getcwd())
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, out_name)
    u = "%s/download/%s" % (base_url(url), urllib.parse.quote(job_id))
    try:
        with _OP.open(u, timeout=900) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError("下载返回 %s：%s" % (e.code, _detail(e)))
    with open(dst, "wb") as f:
        f.write(data)
    _check_file(dst)
    return dst


def _check_file(path):
    """产物完整性：图片看结束块，视频看 ftyp 与体积。"""
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(12)
    if ext in (".png",):
        with open(path, "rb") as f:
            f.seek(max(0, size - 8))
            tail = f.read(8)
        if b"IEND" not in tail:
            raise RuntimeError("产物 PNG 不完整（缺 IEND）：%s" % path)
    elif ext in (".jpg", ".jpeg"):
        if not head.startswith(b"\xff\xd8"):
            raise RuntimeError("产物 JPEG 文件头不对：%s" % path)
    elif ext in (".mp4", ".mov"):
        if b"ftyp" not in head and b"ftyp" not in head[:32]:
            raise RuntimeError("产物不是 MP4（缺 ftyp）：%s" % path)
        if size < 10240:
            raise RuntimeError("产物视频仅 %d 字节，判为坏片：%s" % (size, path))
    elif size < 1024:
        raise RuntimeError("产物体积异常（%d 字节）：%s" % (size, path))


def cancel(job_id, url=""):
    """取消排队或运行中的任务。"""
    return _ok(_post("/api/jobs/%s/cancel" % urllib.parse.quote(job_id), {}, url), "取消任务")


def delete(job_id, url=""):
    """删掉任务记录与它的产物。"""
    return _ok(_post("/api/jobs/%s/delete" % urllib.parse.quote(job_id), {}, url), "删除任务")


def kind_of(path):
    """按扩展名猜素材位：图片/视频 → target，音频 → audio。"""
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext in AUDIO_EXT:
        return "audio"
    return "target"


def run(target_file, processors, source_file="", audio_file="", face_pos=None,
        frame=0, options=None, out_dir="", url="", timeout=1800, quiet=False):
    """一站式：传本地素材 → 提交 → 等终态 → 下载产物，返回本地路径。"""
    tgt = upload(target_file, "target", url)
    src = upload(source_file, "source", url) if source_file else ""
    aud = upload(audio_file, "audio", url) if audio_file else ""
    jid = submit(tgt, processors, source=src, audio=aud, face_pos=face_pos,
                 frame=frame, options=options, url=url)
    if not quiet:
        print("任务已提交：%s" % jid)
    j = wait(jid, timeout=timeout, url=url, quiet=quiet)
    if j.get("status") != "succeeded":
        raise RuntimeError("任务 %s 终态是 %s：%s" % (jid, j.get("status"), j.get("message")))
    return download(jid, out_dir=out_dir, url=url)


# ---------- 实例定位 ----------
def _xgy():
    """仙宫云实例管理脚本（同技能包内），没装就返回 None。"""
    d = os.path.join(_SKILLS, "fxai-xiangongyun-deploy", "scripts")
    if not os.path.isfile(os.path.join(d, "xgy_api.py")):
        return None
    if d not in sys.path:
        sys.path.insert(0, d)
    import xgy_api  # noqa: E402
    return xgy_api


def _otai():
    """OneThingAI 实例管理脚本（同技能包内），没装就返回 None。"""
    d = os.path.join(_SKILLS, "fxai-onethingai-deploy", "scripts")
    if not os.path.isfile(os.path.join(d, "otai_api.py")):
        return None
    if d not in sys.path:
        sys.path.insert(0, d)
    import otai_api  # noqa: E402
    return otai_api


def url_of(web_url, port, scheme="https"):
    """实例 web_url → 目标端口地址：`-80.container.` 换成 `-<port>.container.`。

    平台的对外地址是按端口拼的，实测端口不同子域不同，照端口换那一段最稳。
    """
    import re
    u = fxc.norm_url(web_url)
    if not u:
        return ""
    if "://" in u:
        sch, _, tail = u.partition("://")
        u = (scheme or sch) + "://" + tail
    if re.search(r"-\d+\.container\.", u):
        return re.sub(r"-\d+\.container\.", "-%d.container." % int(port), u)
    m = re.match(r"^(https?://[^/:]+)(:\d+)?(/.*)?$", u)
    if m and int(port) != 80:
        return "%s:%d%s" % (m.group(1), int(port), m.group(3) or "")
    return u


def resolve(keyword="", token="", save=False):
    """按实例名定位机器：返回 [{id, name, status, web_url, provider}]。

    名字取当前档的 instance_name（默认 %s）；命中多台就把候选全列出来由人挑。
    按档里的 provider 选控制面：xiangongyun 走 xgy_api，onethingai 走 otai_api。
    save=True 时把命中唯一一台的地址写回当前档（cloud_url / ui_cloud_url）。
    """ % DEFAULT_INSTANCE_NAME
    p = profile()
    provider = str(p.get("provider") or "xiangongyun").strip().lower()
    kw = keyword or str(p.get("instance_name") or DEFAULT_INSTANCE_NAME)
    out = []

    if provider in ("xiangongyun", "xgy", "xg"):
        api = _xgy()
        if api is None:
            raise RuntimeError("没找到 fxai-xiangongyun-deploy 技能，装它才能按名字查实例")
        tk = token or str(global_cfg().get("xgy_api_token") or "").strip()
        if not tk:
            raise RuntimeError("全局配置缺 xgy_api_token")
        # 先认档里记的实例 ID（最准；实例名可能被人改空，实测有 name 为空的情况）
        iid = str(p.get("instance_id") or "").strip()
        if iid:
            try:
                inst = api.pick_instance(api.get_instance(tk, iid), iid)
            except Exception:
                inst = None
            if inst:
                out.append({"id": str(inst.get("id") or iid), "name": inst.get("name") or "",
                            "status": inst.get("status") or "",
                            "web_url": inst.get("web_url") or "", "provider": "xiangongyun"})
        if not out:
            for h in (api.search_instances(tk, kw) or []):
                hid = str(h.get("id") or "").strip()
                if not hid:
                    continue
                out.append({"id": hid, "name": h.get("name") or "", "status": h.get("status") or "",
                            "web_url": h.get("web_url") or "", "provider": "xiangongyun"})
    elif provider in ("onethingai", "otai"):
        api = _otai()
        if api is None:
            raise RuntimeError("没找到 fxai-onethingai-deploy 技能，装它才能按名字查实例")
        tk = token or str((global_cfg().get("otai") or {}).get("api_token")
                          or global_cfg().get("otai_api_token") or "").strip()
        if not tk:
            raise RuntimeError("全局配置缺 otai.api_token")
        rows, _pg = api.list_instances(tk, page_size=100)
        for inst in (rows or []):
            b = api.brief(inst)
            if kw and kw not in str(b.get("name") or ""):
                continue
            out.append({"id": b.get("appId") or "", "name": b.get("name") or "",
                        "status": b.get("statusName") or "",
                        "web_url": inst.get("webUIAddress") or "", "provider": "onethingai"})
    else:
        raise RuntimeError(
            "当前档 %s 的 provider 是「%s」，脚本还没接这一家的按名查实例。\n"
            "  直接把地址写进该档的 cloud_url / ui_cloud_url 即可照常用；"
            "provider 目前支持 xiangongyun / onethingai。"
            % (p.get("_name") or DEFAULT_PROFILE, provider))

    if not out:
        raise RuntimeError("没搜到名字含「%s」的实例" % kw)

    if save:
        if len(out) > 1:
            raise RuntimeError("命中 %d 台，先定哪一台再 --save（由人来挑）" % len(out))
        ports = p.get("ports") if isinstance(p.get("ports"), dict) else {}
        api_port = int(ports.get("api") or 80)
        ui_port = int(ports.get("ui") or 0)
        scheme = p.get("scheme") or "https"
        cfg = global_cfg()
        node = (cfg.get("face") or {}).get("profiles") or {}
        cur = node.get(p["_name"]) if isinstance(node.get(p["_name"]), dict) else {}
        cur["cloud_url"] = url_of(out[0]["web_url"], api_port, scheme)
        if ui_port:
            cur["ui_cloud_url"] = url_of(out[0]["web_url"], ui_port, scheme)
        cur["instance_id"] = out[0]["id"]
        cur.setdefault("provider", provider)
        node[p["_name"]] = cur
        face = cfg.get("face") if isinstance(cfg.get("face"), dict) else {}
        face["profiles"] = node
        cfg["face"] = face
        save_global_cfg(cfg)
        out[0]["saved_to"] = p["_name"]
    return out


def list_profiles():
    """列出所有档，标出当前档与解析到的地址。"""
    active = profile_name()
    rows = []
    for name, p in sorted(profiles().items()):
        rows.append({
            "profile": name,
            "active": name == active,
            "label": p.get("label") or "",
            "provider": p.get("provider") or "",
            "backend": p.get("backend") or face_cfg().get("backend") or DEFAULT_BACKEND,
            "instance_id": p.get("instance_id") or "",
            "instance_name": p.get("instance_name") or "",
            "cloud_url": p.get("cloud_url") or "",
            "ui_cloud_url": p.get("ui_cloud_url") or "",
            "prefer": p.get("prefer") or "",
        })
    return rows


def use(name):
    """切换默认档（写 face.active）。只是指路，不验证机器在不在线。"""
    name = str(name or "").strip()
    if name not in profiles():
        raise RuntimeError("没有名为「%s」的配置档。现有：%s"
                           % (name, "、".join(profiles()) or "（空）"))
    cfg = global_cfg()
    face = cfg.get("face") if isinstance(cfg.get("face"), dict) else {}
    face["active"] = name
    cfg["face"] = face
    save_global_cfg(cfg)
    return list_profiles()



# ---------- CLI ----------
def _kv(s):
    """--option k=v 解析。"""
    if "=" not in (s or ""):
        raise argparse.ArgumentTypeError("option 要写成 k=v")
    k, _, v = s.partition("=")
    for cast in (int, float):
        try:
            return k.strip(), cast(v)
        except Exception:
            pass
    return k.strip(), v


def main():
    global _PROFILE
    ap = argparse.ArgumentParser(description="人脸替换 / 修复 / 增强")
    ap.add_argument("--url", default="", help="服务基址，缺省读当前档的 cloud_url")
    ap.add_argument("--profile", default="", help="用哪个配置档（缺省 face.active）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("health", help="探活")
    sub.add_parser("profiles", help="列出配置档，标出当前档")
    p = sub.add_parser("use", help="切换默认配置档（写 face.active）")
    p.add_argument("name")
    p = sub.add_parser("library", help="素材库")
    p.add_argument("--kind", default="", choices=["", "source", "target", "audio"])
    p = sub.add_parser("upload", help="上传本地素材")
    p.add_argument("--file", required=True)
    p.add_argument("--kind", default="target", choices=["source", "target", "audio"])
    p = sub.add_parser("faces", help="目标里检出的人脸")
    p.add_argument("--target", required=True)
    p.add_argument("--frame", type=int, default=0)
    p.add_argument("--order", default="", help="排序方式，如 left-right / best-worst")
    p.add_argument("--gender", default="", choices=["", "male", "female"])
    p.add_argument("--race", default="", help="按人种过滤")
    p.add_argument("--age-start", type=int, default=0)
    p.add_argument("--age-end", type=int, default=0)
    p = sub.add_parser("submit", help="提交任务")
    p.add_argument("--target", required=True)
    p.add_argument("--processors", required=True, help="逗号分隔，中文名或 key")
    p.add_argument("--source", default="")
    p.add_argument("--audio", default="")
    p.add_argument("--face-pos", type=int, default=None)
    p.add_argument("--frame", type=int, default=0)
    p.add_argument("--option", action="append", default=[], type=_kv)
    p.add_argument("--no-wait", action="store_true")
    p.add_argument("--out-dir", default="")
    p.add_argument("--timeout", type=int, default=1800)
    p = sub.add_parser("status", help="查任务")
    p.add_argument("job_id")
    p = sub.add_parser("download", help="下载产物")
    p.add_argument("job_id")
    p.add_argument("--out-dir", default="")
    p.add_argument("--name", default="")
    sub.add_parser("jobs", help="任务列表")
    p = sub.add_parser("cancel", help="取消任务")
    p.add_argument("job_id")
    p = sub.add_parser("delete", help="删任务与产物")
    p.add_argument("job_id")
    p = sub.add_parser("run", help="一站式：上传→提交→等→下载")
    p.add_argument("--target-file", required=True)
    p.add_argument("--processors", required=True)
    p.add_argument("--source-file", default="")
    p.add_argument("--audio-file", default="")
    p.add_argument("--face-pos", type=int, default=None)
    p.add_argument("--frame", type=int, default=0)
    p.add_argument("--option", action="append", default=[], type=_kv)
    p.add_argument("--out-dir", default="")
    p.add_argument("--timeout", type=int, default=1800)
    p = sub.add_parser("resolve", help="按实例名定位机器")
    p.add_argument("--keyword", default="")
    p.add_argument("--token", default="")
    p.add_argument("--save", action="store_true",
                   help="命中唯一一台时，把地址写回当前档（cloud_url / ui_cloud_url / instance_id）")

    a = ap.parse_args()
    if not a.cmd:
        ap.print_help()
        return 0
    _PROFILE = a.profile
    url = a.url
    try:
        if a.cmd == "health":
            print(json.dumps(health(url), ensure_ascii=False, indent=1))
        elif a.cmd == "profiles":
            print(json.dumps(list_profiles(), ensure_ascii=False, indent=1))
        elif a.cmd == "use":
            print(json.dumps(use(a.name), ensure_ascii=False, indent=1))
        elif a.cmd == "library":
            print(json.dumps(library(a.kind, url), ensure_ascii=False, indent=1))
        elif a.cmd == "upload":
            print(upload(a.file, a.kind, url))
        elif a.cmd == "faces":
            print(json.dumps(faces(a.target, a.frame, url, order=a.order, gender=a.gender,
                                   race=a.race, age_start=a.age_start, age_end=a.age_end),
                             ensure_ascii=False, indent=1))
        elif a.cmd == "submit":
            procs = [x for x in str(a.processors).replace("，", ",").split(",") if x.strip()]
            jid = submit(a.target, procs, source=a.source, audio=a.audio,
                         face_pos=a.face_pos, frame=a.frame,
                         options=dict(a.option), url=url)
            print(jid)
            if a.no_wait:
                return 0
            j = wait(jid, timeout=a.timeout, url=url)
            if j.get("status") != "succeeded":
                raise RuntimeError("终态 %s：%s" % (j.get("status"), j.get("message")))
            print(download(jid, out_dir=a.out_dir, url=url))
        elif a.cmd == "status":
            print(json.dumps(status(a.job_id, url), ensure_ascii=False, indent=1))
        elif a.cmd == "download":
            print(download(a.job_id, out_dir=a.out_dir, name=a.name, url=url))
        elif a.cmd == "jobs":
            print(json.dumps(jobs(url), ensure_ascii=False, indent=1))
        elif a.cmd == "cancel":
            print(json.dumps(cancel(a.job_id, url), ensure_ascii=False))
        elif a.cmd == "delete":
            print(json.dumps(delete(a.job_id, url), ensure_ascii=False))
        elif a.cmd == "run":
            procs = [x for x in str(a.processors).replace("，", ",").split(",") if x.strip()]
            print(run(a.target_file, procs, source_file=a.source_file,
                      audio_file=a.audio_file, face_pos=a.face_pos, frame=a.frame,
                      options=dict(a.option), out_dir=a.out_dir, url=url, timeout=a.timeout))
        elif a.cmd == "resolve":
            print(json.dumps(resolve(a.keyword, a.token, save=a.save), ensure_ascii=False, indent=1))
    except RuntimeError as e:
        print("错误：%s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
