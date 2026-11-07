#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fxai-weixin-gzh-publisher: WeChat Official Account (MP) publishing CLI.\n\nPublish and maintain articles on a verified WeChat Service Account (服务号)\nvia the official MP API. Only Python standard library is used.\n\nSubcommands:\n  precheck         安装/首次使用自检：没配全局 ~/.workbuddy/fxai_global.json 的 weixin_gzh 就提示去凤希AI伴侣「公众号运营」配置授权（含下载地址）\n  check            验证中转配置可用（draftcount）\n  upload-cover     Upload a permanent image (for article cover) -> media_id\n  material-add     Upload a permanent material (image/voice/video); video needs --title/--introduction -> media_id\n  draft-add        Create a draft from an HTML file (external images auto-uploaded)\n  draft-get        Fetch one draft\n  draft-list       List drafts\n  draft-delete     Delete a draft\n  publish          Submit a draft for publishing (freepublish; does NOT push to followers, no mass-send quota used)\n  publish-status   Query publish result by publish_id\n  publish-delete   Delete a published article by article_id\n  mass             Mass-send to followers (mpnews by default, or --msgtype video for pure-video; 4 times/month)\n  mass-status      Query mass-send status by msg_id\n"""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"
# 公众号凭据落点：用户级 ~/.workbuddy/fxai_global.json 的 weixin_gzh 字段
GLOBAL_CONFIG_PATH = Path.home() / ".workbuddy" / "fxai_global.json"

# 本技能内置的【发布主机域名】（中转服务地址），公开、写死。
# 这是技能的公开服务端点，不属于用户私密凭据，必须随技能分发（写进源码/进包），
# 否则用户拿到不含全局配置的分享包时，连发布地址都不知道，无法发布。
# 全局 fxai_global.json.weixin_gzh.host 若存在则覆盖此默认值（供自建中转的用户覆盖）。
RELAY_HOST_DEFAULT = "https://www.fxai.site/api/"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ERR_HINTS = {
    48001: "该账号没有此接口权限：未认证的服务号没有发布/群发接口权限，需先完成微信认证",
    45009: "接口调用频率超限，请稍后再试",
    45002: "内容超过长度限制（标题 64 字以内、正文有限额），请精简后重试",
    40007: "media_id 无效：封面图未上传成功，或传入的草稿/素材 ID 有误",
    53401: "草稿正被其他窗口编辑锁定，请稍后重试",
    53402: "图文内容存在违规或敏感信息，被平台拦截",
    53404: "草稿数量已达上限（1000 篇），请先清理草稿箱",
    200013: "频繁调用接口，请稍后再试",
    65320: "个性化菜单 matchrule 含已废除字段（性别/地区/语言已下线）：只接受 tag_id 与 "
            "client_platform_type，且至少填一项",
    40018: "menuid 无效或不存在：用 menu-selfinfo / menu-get 查现有 menuid 再操作",
    46003: "菜单不存在：先 menu-create 建默认菜单，再做个性化菜单",
}


# ------------------------------------------------- 未配置时的统一引导（安装/首次使用/调用中）
# 没配置时【必须】把这段完整提示给用户（含下载地址），失败一律带引导返回。
DOWNLOADS = [
    ("GitHub 开源地址", "https://github.com/fxai666/fxai-toolkit"),
    ("夸克网盘", "https://pan.quark.cn/s/a1213641f8c2#/list/share/5e75665b0a764d4d9a9adbd2ec4b71f7"),
    ("迅雷网盘", "https://pan.xunlei.com/s/VOm0BR-N1g-SuFr_RceCmVKzA1?pwd=qnc3"),
    ("百度网盘", "https://pan.baidu.com/s/1xt_93bsji6g76pvWZt4GZw?pwd=7r73"),
]


def _gzh_target():
    """公众号凭据唯一落点：用户级 ~/.workbuddy/fxai_global.json 的 weixin_gzh 字段。"""
    return str(GLOBAL_CONFIG_PATH) + " 的 weixin_gzh 字段"


def setup_guide(reason):
    """未配置 / 配置不完整时的完整引导：下载凤希AI伴侣 → 首页「公众号运营」授权 → 写入 fxai_global.json 的 weixin_gzh。"""
    lines = [
        "【未配置公众号 API，暂时发不了文章】" + reason,
        "这个技能走的是「凤希AI伴侣 → 公众号运营」中转，必须先在软件里配置授权。请按下面 3 步做一次：",
        "1) 下载安装「凤希AI伴侣」（第一次用必须先装它）：",
    ]
    lines += ["   %s：%s" % (n, u) for n, u in DOWNLOADS]
    lines += [
        "2) 打开凤希AI伴侣，进【首页】的「公众号运营」，按提示完成公众号授权配置；",
        "3) 在「公众号运营」导出（下载/复制）配置给我，我会写入：%s" % _gzh_target(),
        '   配好之后回一句「配置好了」，我接着往下执行。'
    ]
    return "\n".join(lines)


def fail_setup(reason):
    """未配置：fail 但带完整引导（guide 字段），智能体原样说给用户，别自己删掉下载地址。"""
    fail(-1, reason, {"guide": setup_guide(reason)})


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def fail(code, msg, extra=None):
    payload = {"ok": False, "errcode": code, "errmsg": msg}
    if code in ERR_HINTS:
        payload["hint"] = ERR_HINTS[code]
    if extra:
        payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(1)


# ---------------------------------------------------------------- relay mode (RssEasy 中转)
# 所有接口走中转。鉴权：host + 登录态(tokenmyid/usertoken) + configid + appsecret。
# 登录态不写进配置：从 <software_install_dir>/userinfo.json 现读（客户端每次登录重写），
# 配置里的 weixin_gzh.login 只给老配置兜底。签名统一 v3：签名 key = usertoken。
SIGNVERSION_DEFAULT = "v3"
# 配置缺失或字段缺失：fail 并带完整引导，别静默跳过。


def load_cfg_soft():
    """读取技能本地 config.json；缺失/损坏返回 {}。
    合并全局 ~/.workbuddy/fxai_global.json 的 weixin_gzh.publish 作为默认值
    （本地 config.json 值优先）。这样把发布配置统一收归全局后，删除本地
    config.json 也不会丢失 default_author / content_source_url 等发布默认值。"""
    g = load_global_cfg()
    publish_defaults = ((g.get("weixin_gzh") or {}).get("publish")) or {}
    if not isinstance(publish_defaults, dict):
        publish_defaults = {}
    try:
        local = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        local = {}
    if not isinstance(local, dict):
        local = {}
    merged = dict(publish_defaults)
    merged.update(local)
    return merged


def load_global_cfg():
    """读取用户级全局配置 ~/.workbuddy/fxai_global.json（comfy/ollama/tts 等共享）。读不到返回 {}。"""
    try:
        return json.loads(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_relay_data():
    """读取中转配置，返回 dict 或 None。唯一来源：用户级全局 ~/.workbuddy/fxai_global.json 的 weixin_gzh。"""
    g = load_global_cfg()
    wg = g.get("weixin_gzh")
    if isinstance(wg, dict):
        return dict(wg)
    return None


# ------------------------------------------------- 登录身份（tokenmyid / usertoken）
# 落点：本机客户端安装目录下的 userinfo.json，配置里那份 usertoken 快照只作兜底。
# 客户端每次登录成功都会整份重写该文件（js/app.js → fxaitoolkit.updateUserInfo → UserInfo.SetInstance），
# 所以现读即当前登录人，换账号自动跟着换，也不会像口令里拷来的快照那样过期。
# 安装目录取自 ~/.workbuddy/fxai_global.json 的 software_install_dir（客户端「本机配置 → 技能运行环境」生成）。
# 只取 myid / usertoken 两个字段；不打印 token、不写回任何配置。

def _norm_dir(v):
    p = str(v or "").strip().strip('"').strip("'")
    return p.rstrip("\\/") if p else ""


def load_login_identity():
    """返回 ({"tokenmyid","usertoken"}, 来源文件) 或 ({}, 读不到的原因)。"""
    g = load_global_cfg()
    install = _norm_dir(g.get("software_install_dir"))
    if not install:
        return {}, ("~/.workbuddy/fxai_global.json 里没有 software_install_dir：装好凤希AI伴侣后，"
                    "按站点「软件配置（全面使用凤AI必看）」与「视频教程」配一遍，"
                    "再打开客户端「本机配置 → 技能运行环境」点一次「复制智能体安装口令」")
    path = Path(install) / "userinfo.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}, ("%s 不存在：客户端装在这个目录、并在客户端登录一次后，该文件会自动生成" % path)
    except Exception as e:
        return {}, "%s 读不出来：%s" % (path, e)
    uid = str(data.get("myid") or "").strip()
    token = str(data.get("usertoken") or "").strip()
    if not uid or not token:
        return {}, "%s 里没有 myid / usertoken（客户端还没登录）" % path
    return {"tokenmyid": uid, "usertoken": token}, str(path)


def resolve_login(cfg_login):
    """登录身份：优先本机 userinfo.json（活的），回落到配置里的 weixin_gzh.login（老配置兼容）。"""
    live, why = load_login_identity()
    if live:
        live["_source"] = why
        return live
    cfg = {k: str((cfg_login or {}).get(k, "")).strip() for k in ("tokenmyid", "usertoken")}
    if cfg["tokenmyid"] and cfg["usertoken"]:
        cfg["_source"] = "weixin_gzh.login（旧快照；本机 userinfo.json 没读到：%s）" % why
        return cfg
    return {"tokenmyid": "", "usertoken": "", "_source": "无（%s）" % why}


# 多公众号：本次操作用哪个号。来源 --account 参数 > 环境变量 WX_ACCOUNT > 全局 weixin_gzh 里唯一的那个号。
ACCOUNT_NAME = None


def pick_account(data, name=None):
    '从配置里挑出本次要用的公众号（一个用户可维护多个号）。\n\n选取顺序：入参 name > 环境变量 WX_ACCOUNT > 唯一的那个号。\n多号且无法确定时【必须 fail 并列出可用号】，由调用方（智能体）回头问用户"给哪个公众号发"，\n由用户选定后再发。\n'


    top_cid = str(data.get("configid", "")).strip()
    top_sec = str(data.get("appsecret", "")).strip()
    if top_cid and top_sec:
        # 顶层直接带 configid/appsecret 的单号配置：原样生效
        data["configid"] = top_cid
        data["appsecret"] = top_sec
        data["account"] = str(data.get("name") or "").strip() or "default"
        return data

    accounts = data.get("accounts") or {}
    if not isinstance(accounts, dict) or not accounts:
        fail_setup("全局 ~/.workbuddy/fxai_global.json 的 weixin_gzh 里没有任何公众号配置（缺 accounts）："
                   "请到凤希AI伴侣【首页】的「公众号运营」完成公众号授权配置，再重新授权并写入全局 weixin_gzh")
    if not name:
        name = (os.environ.get("WX_ACCOUNT") or "").strip()
    # 不读配置里的 "default"：多号未点名必须问用户，每次执行现判
    name = str(name).strip()

    picked = None
    if name:
        if name in accounts:
            picked = name
        else:
            for k in accounts:
                # 名称近似匹配：子串互含即可命中
                if name in k or k in name:
                    picked = k
                    break
    if not picked:
        if len(accounts) == 1:
            picked = next(iter(accounts))
        else:
            fail(-1, "全局 weixin_gzh 里有多个公众号，必须指定用哪个：%s" % "、".join(accounts),
                 {"accounts": sorted(accounts),
                  "fix": "用 --account \"公众号名\"（或环境变量 WX_ACCOUNT=公众号名）指定；"
                         "本文件只有一个号时可直接省略"})
    a = accounts.get(picked) or {}
    data["configid"] = str(a.get("configid", "")).strip()
    data["appsecret"] = str(a.get("appsecret", "")).strip()
    data["account"] = picked
    return data


def relay_cfg():
    """读取并【强制校验】中转配置。host(发布主机域名)已作为技能内置默认值写死，无需用户提供；
    公众号凭据(accounts)来源：全局 ~/.workbuddy/fxai_global.json 的 weixin_gzh；
    登录身份(tokenmyid/usertoken)来源：本机 <software_install_dir>/userinfo.json（老配置的
    weixin_gzh.login 只作兜底）。"""
    data = load_relay_data()
    # 完全没配置给空 dict：host 用内置默认，私密字段缺失会触发下方 fail_setup 引导
    data = data or {}
    # host 发布主机域名：技能内置默认（公开），全局 weixin_gzh.host 可覆盖（供自建中转）
    data.setdefault("host", RELAY_HOST_DEFAULT)
    missing = []
    # 登录身份：优先本机客户端 userinfo.json（当前登录人，换账号自动跟着换），
    # 配置里的 weixin_gzh.login 只作为老配置的兜底。signversion 固定发 v3，不读配置。
    # 注意 host 已内置默认，无需强制必填
    login = resolve_login(data.get("login"))
    data["login"] = login
    for f in ("tokenmyid", "usertoken"):
        if not str(login.get(f, "")).strip():
            missing.append("登录身份 " + f)
    if missing:
        fail_setup("公众号配置不完整，缺少: " + ", ".join(missing)
                   + "。登录身份改从本机客户端读：`~/.workbuddy/fxai_global.json` 的 `software_install_dir`"
                     " + `/userinfo.json`（每次登录自动重写）。当前情况：" + login.get("_source", "未知"))
    # 多号：校验完公共字段后按 accounts 选中本次要用的号（多号未指定会 fail 并列出可选号）
    data = pick_account(data, ACCOUNT_NAME)
    if not data.get("configid") or not data.get("appsecret"):
        fail_setup("公众号「%s」缺 configid / appsecret："
                   "请到凤希AI伴侣【首页】的「公众号运营」重新授权并写入全局 weixin_gzh" % data.get("account"))
    return data


def _norm_param(v):
    """掐掉字符串首尾空白（空格 / 换行 / 制表 / 全角空格），正文内部换行不动。

    规则：签名与发送必须是同一份值，统一在 relay_call 入口掐这一次。
    """
    if isinstance(v, str):
        return v.strip()
    return v


def _norm_json_strings(o):
    """递归掐掉 JSON 结构里每个字符串的首尾空白（draft-update 的 articles / --ext 用）。"""
    if isinstance(o, str):
        return o.strip()
    if isinstance(o, dict):
        return {k: _norm_json_strings(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_norm_json_strings(v) for v in o]
    return o


def _relay_sign(params, signkey):
    """签名 key：v3 = login.usertoken；配置里若带 prikey，按 prikey+usertoken 拼。"""
    items = []
    for k in sorted(params.keys()):
        v = str(params[k]).strip()
        if v == "":
            continue
        s = urllib.parse.quote(v, safe="-._*").replace("~", "%7E").lower()
        items.append(k + "=" + s)
    raw = "&".join(items) + "&key=" + signkey
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _sign_key(rc):
    """v3 = 只认 usertoken（relay_cfg 已把本机 userinfo.json 的登录态合并进 rc["login"]）；
    配置里若带 prikey，拼在最前。"""
    prikey = str(rc.get("prikey") or "").strip()
    return prikey + str((rc.get("login") or {}).get("usertoken", ""))


def relay_call(path_suffix, params=None, file_path=None, file_field="file", ftype=None, timeout=90):
    """调用中转端点 store/weixin/<path_suffix>。自动带 configid+appsecret、签名、登录头。"""
    rc = relay_cfg()
    p = {}
    for k, v in (params or {}).items():
        if v is None:
            continue
        # 首尾空白只在这里掐一次：签名与发送共用这一份 p
        p[k] = _norm_param(v)
    if "configid" not in p or str(p["configid"]).strip() == "":
        p["configid"] = rc["configid"]
    if "appsecret" not in p or str(p["appsecret"]).strip() == "":
        p["appsecret"] = rc["appsecret"]
    # corpid 可选：配置里有就带上
    if "corpid" not in p or str(p["corpid"]).strip() == "":
        corp = str(rc.get("corpid") or "").strip()
        if corp:
            p["corpid"] = corp
    login = rc.get("login") or {}
    headers = {
        "tokenmyid": login.get("tokenmyid", ""),
        "usertoken": login.get("usertoken", ""),
        # 固定发 v3：请求头 signversion 恒为 v3，不读配置文件里的值
        "signversion": SIGNVERSION_DEFAULT,
    }
    url = rc["host"].rstrip("/") + "/store/weixin/" + path_suffix.lstrip("/")
    if file_path:
        fp = Path(file_path)
        if not fp.exists():
            fail(-1, f"文件不存在: {fp}")
        ctype = ftype or mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        boundary = "----wxb" + uuid.uuid4().hex
        signed = dict(p)
        signed["signtoken"] = _relay_sign(p, _sign_key(rc))
        chunks = []
        for k, v in signed.items():
            chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                           % (boundary, k, v)).encode("utf-8"))
        chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                       "Content-Type: %s\r\n\r\n" % (boundary, file_field, fp.name, ctype)).encode("utf-8"))
        chunks.append(fp.read_bytes())
        chunks.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
        req = urllib.request.Request(url, data=b"".join(chunks), method="POST", headers=dict(
            headers, **{"Content-Type": "multipart/form-data; boundary=%s" % boundary}))
    else:
        signed = dict(p)
        signed["signtoken"] = _relay_sign(p, _sign_key(rc))
        body = urllib.parse.urlencode(signed).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers=dict(
            headers, **{"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        fail(-1, "中转请求失败: %s" % e.reason, {"fix": "检查能否访问 %s" % rc["host"].rstrip("/")})
    try:
        res = json.loads(text)
    except Exception:
        fail(-1, "中转返回非 JSON", {"raw": text[:500]})
    if isinstance(res, dict) and res.get("rsscode"):
        fail(-1, res.get("rsscode"), {"relay": True, "raw": text[:300]})
    if isinstance(res, dict) and res.get("errcode") not in (None, 0):
        fail(res.get("errcode"), res.get("errmsg", "relay error"), {"relay": True})
    return res


def relay_upload_cover(filepath):
    res = relay_call("freepublish/uploadcover", file_path=filepath)
    return res.get("media_id"), res.get("url")


def relay_upload_content_image(filepath):
    res = relay_call("freepublish/uploadimage", file_path=filepath)
    return res.get("url")


def relay_process_html_images(html):
    """把正文里的 <img> 上传到中转图床：http 链接先下载，本地路径直接上传。"""
    counter = {"n": 0}

    def repl(m):
        src = m.group(1)
        if not src.strip() or src.strip().lower().startswith("data:"):
            return m.group(0)
        if src.lower().startswith("http"):
            fp = _download_to_tmp(src)
        else:
            fp = Path(src)
            if not fp.exists():
                fail(-1, "正文图片不存在: %s" % fp)
        url = relay_upload_content_image(str(fp))
        if src.lower().startswith("http"):
            try:
                fp.unlink()
            except OSError:
                pass
        counter["n"] += 1
        return m.group(0).replace(src, url)

    return IMG_RE.sub(repl, html), counter["n"]


def relay_build_and_add(args):
    """构建图文并建草稿，返回 media_id。"""
    cfg = load_cfg_soft()
    html = read_content(args.content)
    html, n_img = relay_process_html_images(html)
    cover = getattr(args, "cover", None)
    if not cover:
        fail(-1, "缺少封面图 --cover（公众号图文必须有封面）")
    thumb, _ = relay_upload_cover(cover)
    if not thumb:
        fail(-1, "封面图上传失败")
    params = {
        "title": str(args.title or "").strip(),
        "author": str(getattr(args, "author", "") or cfg.get("default_author", "") or "").strip(),
        "digest": str(getattr(args, "digest", "") or "").strip(),
        "content": html,
        "thumb_media_id": thumb,
        "need_open_comment": int(getattr(args, "comments", 1) or 0),
        "only_fans_can_comment": 0,
        "content_source_url": cfg.get("content_source_url", ""),
    }
    if cfg.get("need_open_comment") is not None:
        params["need_open_comment"] = int(cfg.get("need_open_comment", 1))
    if cfg.get("only_fans_can_comment") is not None:
        params["only_fans_can_comment"] = int(cfg.get("only_fans_can_comment", 0))
    res = relay_call("freepublish/draftadd", params)
    return res.get("media_id"), n_img


def relay_material_add(args):
    res = relay_call("material/add", file_path=args.file,
                     params={"type": args.type, "title": args.title, "introduction": args.introduction})
    out({"ok": True, "type": args.type, "media_id": res.get("media_id"), "url": res.get("url")})


def relay_draft_add(args):
    media_id, n_img = relay_build_and_add(args)
    out({"ok": True, "media_id": media_id, "images_uploaded": n_img,
         "title": str(args.title or "").strip(),
         "next": "用 publish --media-id 发布，或 mass --media-id 群发"})


def relay_draft_get(args):
    res = relay_call("freepublish/draftget", {"media_id": args.media_id})
    out({"ok": True, "draft": res})


def relay_draft_list(args):
    res = relay_call("freepublish/draftlist",
                     {"curpage": str(args.offset), "pagesize": str(args.count), "no_content": "1"})
    items = []
    for it in res.get("item", []) or []:
        ni = (it.get("content", {}).get("news_item") or [{}])[0]
        items.append({"media_id": it.get("media_id"), "update_time": it.get("update_time"),
                      "title": ni.get("title"), "digest": ni.get("digest")})
    out({"ok": True, "total": res.get("total_count"), "shown": len(items), "drafts": items})


def relay_draft_delete(args):
    relay_call("freepublish/draftdelete", {"media_id": args.media_id})
    out({"ok": True, "deleted": args.media_id})


def relay_publish(args):
    if args.media_id:
        media_id = args.media_id
    else:
        if not (args.title and args.content and args.cover):
            fail(-1, "要么给 --media-id，要么同时给 --title/--content/--cover")
        media_id, _ = relay_build_and_add(args)
    res = relay_call("freepublish/publish", {"media_id": media_id})
    out({"ok": True, "publish_id": res.get("publish_id"), "draft_media_id": media_id,
         "note": "freepublish 异步，不推送粉丝、不占群发额度；几分钟后用 publish-status 查"})


def relay_publish_status(args):
    res = relay_call("freepublish/publishget", {"publish_id": args.publish_id})
    out({"ok": True, "publish_status": res.get("publish_status"), "raw": res})


def relay_publish_delete(args):
    relay_call("freepublish/publishdelete", {"article_id": args.article_id, "index": str(args.index)})
    out({"ok": True, "deleted_article": args.article_id})


def relay_publish_list(args):
    """已发表文章列表（freepublish/publishlist）：configid + 接口密钥 + 登录态，不需要 corpid。"""
    res = relay_call("freepublish/publishlist",
                     {"curpage": str(args.offset), "pagesize": str(args.count), "no_content": "1"})
    items = []
    for it in res.get("item", []) or []:
        ni = (it.get("content", {}).get("news_item") or [{}])[0]
        items.append({"article_id": it.get("article_id"), "update_time": it.get("update_time"),
                      "title": ni.get("title"), "digest": ni.get("digest"), "url": ni.get("url")})
    out({"ok": True, "total": res.get("total_count"), "shown": len(items), "articles": items})


def relay_mass(args):
    res = relay_call("mass/sendall", {"media_id": args.media_id,
                                      "tag_id": args.tag_id if args.tag_id is not None else "",
                                      "clientmsgid": "", "send_ignore_reprint": "0"})
    out({"ok": True, "msg_id": res.get("msg_id"), "msg_data_id": res.get("msg_data_id"),
         "note": "群发会推送到粉丝会话列表；服务号每月仅可群发 4 次"})


def relay_mass_status(args):
    res = relay_call("mass/getstatus", {"msg_id": args.msg_id})
    out({"ok": True, "raw": res})


# ------------------------------------------------- 自定义菜单（7 端点全家桶）
# 官方 7 个菜单端点全部要认证账号：menu/create、menu/get、menu/delete、
# menu/addconditional、menu/delconditional、menu/trymatch、get_current_selfmenu_info。
# 全部走 relay_call 中转，提交前先过本地结构校验，把官方明确会拒的挡在本地。
MENU_TYPES = ("view", "click", "miniprogram", "scancode_push", "scancode_waitmsg",
              "pic_sysphoto", "pic_photo_or_album", "pic_weixin", "location",
              "article_id", "article_view_limited")
MENU_TOP_MAX = 3          # 一级菜单最多 3 个
MENU_SUB_MAX = 5          # 每个一级菜单下二级最多 5 个
MENU_NAME_TOP_BYTES = 16  # 一级菜单名 16 字节
MENU_NAME_SUB_BYTES = 60  # 二级菜单名 60 字节
MENU_KEY_BYTES = 128      # key 128 字节
MENU_URL_BYTES = 1024     # url 1024 字节
MENU_PLATFORM = {"1": "iOS", "2": "Android", "3": "Others"}
MENU_MATCHRULE_KEYS = ("tag_id", "sex", "country", "province", "city", "language",
                      "client_platform_type")
MENU_MATCHRULE_ALIVE = ("tag_id", "client_platform_type")
MENU_REQUIRED_ANY = {
    # click 两种触发方式二选一：key 走关键词自动回复，article_id 跳已发表图文
    "click": ("key", "article_id"),
    "view": ("url",),
    "miniprogram": ("appid", "pagepath"),
    "article_id": ("article_id",),
}


def _utf8_len(s):
    """官方按字节计长度：中文 1 字 3 字节，校验与报错都用它。"""
    return len(str(s).encode("utf-8"))


def read_menu_json(args):
    """菜单定义来源：--file 读文件、--json 直接给串，两个都给时以 --json 为准。"""
    raw = getattr(args, "json", "") or ""
    path = getattr(args, "file", "") or ""
    if not raw and not path:
        fail(40004, "--file 与 --json 至少给一个：菜单定义放文件用 --file，直接给串用 --json")
    if raw:
        text = raw
    else:
        p = Path(path)
        if not (p.exists() and p.is_file()):
            fail(40004, "菜单定义文件不存在：%s" % path)
        text = p.read_text(encoding="utf-8-sig", errors="replace")
    try:
        data = json.loads(text)
    except Exception as e:
        fail(40007, "菜单定义不是合法 JSON：%s" % e)
    if not isinstance(data, dict):
        fail(40007, "菜单定义顶层必须是对象（放 button 数组与 matchrule）")
    return data


def _menu_buttons(data):
    """取菜单数组：官方字段名 button，本技能同时接受 buttons 写法。"""
    btns = data.get("button")
    if btns is None:
        btns = data.get("buttons")
    if not isinstance(btns, list) or not btns:
        fail(40007, "菜单定义缺非空 button 数组（也可写 buttons）")
    return btns


def _sub_of(btn):
    return btn.get("sub_button") or btn.get("sub_buttons") or []


def validate_menu_buttons(btns, top=True):
    """本地结构校验：层级数量 / 字节长度 / type 枚举 / 必填字段 / url 协议头。

    带 sub_button 的一级项官方允许省略 type（它是容器），叶子项必须给合法 type。
    """
    cap = MENU_TOP_MAX if top else MENU_SUB_MAX
    what = "一级菜单" if top else "二级菜单"
    if len(btns) > cap:
        fail(40007, "%s最多 %d 个，当前 %d 个" % (what, cap, len(btns)))
    errs = []
    for i, b in enumerate(btns):
        where = "%s第 %d 项" % (what, i + 1)
        if not isinstance(b, dict):
            errs.append("%s 不是对象" % where)
            continue
        name = str(b.get("name", ""))
        nlimit = MENU_NAME_TOP_BYTES if top else MENU_NAME_SUB_BYTES
        if not name:
            errs.append("%s 缺 name" % where)
        elif _utf8_len(name) > nlimit:
            errs.append("%s name「%s」%d 字节，超上限 %d 字节"
                        % (where, name, _utf8_len(name), nlimit))
        sub = _sub_of(b)
        if top:
            if sub and not isinstance(sub, list):
                errs.append("%s sub_button 必须是数组" % where)
                sub = []
            elif len(sub) > MENU_SUB_MAX:
                errs.append("%s下二级菜单最多 %d 个，当前 %d 个" % (where, MENU_SUB_MAX, len(sub)))
            elif sub:
                validate_menu_buttons(sub, top=False)
        btype = str(b.get("type", ""))
        if not btype and sub:
            continue  # 容器项：官方允许省略 type
        if btype not in MENU_TYPES:
            errs.append("%s type「%s」不支持，可用：%s"
                        % (where, btype or "(空)", "、".join(MENU_TYPES)))
            continue
        if btype == "view":
            url = str(b.get("url", ""))
            if not url:
                errs.append("%s 缺少必填字段 url" % where)
            elif _utf8_len(url) > MENU_URL_BYTES:
                errs.append("%s url 超长：%d 字节，上限 %d 字节" % (where, _utf8_len(url), MENU_URL_BYTES))
            elif not url.lower().startswith(("http://", "https://")):
                errs.append("%s url 必须以 http:// 或 https:// 开头，当前「%s」" % (where, url))
        if btype == "click":
            key = b.get("key")
            if key and _utf8_len(key) > MENU_KEY_BYTES:
                errs.append("%s key 超长：%d 字节，上限 %d 字节"
                            % (where, _utf8_len(key), MENU_KEY_BYTES))
        needs = MENU_REQUIRED_ANY.get(btype)
        if needs and not any(b.get(x) for x in needs):
            errs.append("%s 缺少必填字段 %s（type=%s）" % (where, " 或 ".join(needs), btype))
    if errs:
        fail(40007, "菜单结构校验未通过：\n- " + "\n- ".join(errs))


def validate_matchrule(rule):
    """个性化菜单匹配规则：只收 tag_id / client_platform_type，且至少填一项。"""
    if not isinstance(rule, dict):
        fail(40007, "matchrule 必须是对象")
    dead = []
    unknown = []
    for k in rule:
        if k in MENU_MATCHRULE_ALIVE:
            continue
        (dead if k in MENU_MATCHRULE_KEYS else unknown).append(k)
    if dead:
        fail(40007, "matchrule 含已下线的字段 %s：性别/地区/语言类字段官方已废除，"
                    "带上会直接被拒（errcode 65320）；当前只接受 %s"
               % ("、".join(dead), "、".join(MENU_MATCHRULE_ALIVE)))
    if unknown:
        fail(40007, "matchrule 不支持的字段：%s；当前只接受 %s"
               % ("、".join(unknown), "、".join(MENU_MATCHRULE_ALIVE)))
    alive = {k: v for k, v in rule.items() if v not in (None, "")}
    if not alive:
        fail(40007, "matchrule 至少填 tag_id 或 client_platform_type 之一")
    if "client_platform_type" in alive:
        v = str(alive["client_platform_type"])
        if v not in MENU_PLATFORM:
            fail(40007, "client_platform_type 只支持 1(iOS) / 2(Android) / 3(Others)，当前「%s」" % v)
        alive["client_platform_type"] = v
    if "tag_id" in alive:
        try:
            alive["tag_id"] = int(alive["tag_id"])
        except Exception:
            fail(40007, "tag_id 必须是标签 ID（数字），当前「%s」" % rule["tag_id"])
    return alive


def relay_menu_create(args):
    btns = _menu_buttons(read_menu_json(args))
    validate_menu_buttons(btns, top=True)
    res = relay_call("menu/create", {"button": json.dumps(btns, ensure_ascii=False)})
    ok = res.get("errcode", 0) in (None, 0, "0")
    out({"ok": bool(ok), "raw": res,
         "summary": "默认菜单已创建（menu/create 整体覆盖，重建即替换旧菜单）" if ok
                    else "见 raw 里的 errcode / errmsg",
         "note": "菜单改动即时生效，不占群发次数；分人群的菜单用 menu-addconditional"})


def relay_menu_get(args):
    res = relay_call("menu/get", {})
    out({"ok": res.get("errcode", 0) in (None, 0, "0"), "raw": res,
         "note": "menu/get 会把 conditionalmenu 列表一并回传；个性化菜单没有编辑接口，"
                 "要改只能整体重发"})


def relay_menu_delete(args):
    res = relay_call("menu/delete", {})
    ok = res.get("errcode", 0) in (None, 0, "0")
    out({"ok": bool(ok), "raw": res,
         "summary": "菜单已清空（menu/delete 会删除默认菜单与所有个性化菜单）" if ok
                    else "见 raw 里的 errcode / errmsg"})


def relay_menu_addconditional(args):
    data = read_menu_json(args)
    btns = _menu_buttons(data)
    validate_menu_buttons(btns, top=True)
    if "matchrule" not in data or data.get("matchrule") in (None, ""):
        fail(40007, "个性化菜单必须带 matchrule（至少 tag_id 或 client_platform_type 一项）")
    rule = validate_matchrule(data["matchrule"])
    res = relay_call("menu/addconditional",
                     {"button": json.dumps(btns, ensure_ascii=False),
                      "matchrule": json.dumps(rule, ensure_ascii=False)})
    ok = res.get("errcode", 0) in (None, 0, "0")
    out({"ok": bool(ok), "raw": res, "menuid": res.get("menuid"), "matchrule": rule,
         "summary": "个性化菜单已创建" if ok else "见 raw 里的 errcode / errmsg",
         "note": "menuid 记下来；个性化菜单没有编辑接口，改它＝menu-delconditional 删掉再整体重发。"
                 "多个个性化菜单按创建时间从后往前优先匹配"})


def relay_menu_delconditional(args):
    menuid = str(getattr(args, "menuid", "") or "").strip()
    if not menuid:
        fail(40004, "--menuid 必填：填 menu-addconditional 返回的 menuid")
    res = relay_call("menu/delconditional", {"menuid": int(menuid)})
    ok = res.get("errcode", 0) in (None, 0, "0")
    out({"ok": bool(ok), "raw": res,
         "summary": "个性化菜单已删除" if ok else "menuid 无效或不存在时官方返回 40018，见 raw"})


def relay_menu_trymatch(args):
    uid = str(getattr(args, "user_id", "") or "").strip()
    if not uid:
        fail(40004, "--user-id 必填：填粉丝 openid 或微信号")
    res = relay_call("menu/trymatch", {"user_id": uid})
    out({"ok": res.get("errcode", 0) in (None, 0, "0"), "raw": res,
         "note": "返回该用户实际命中的菜单，用于验证个性化规则"})


def relay_menu_selfinfo(args):
    res = relay_call("get_current_selfmenu_info", {})
    out({"ok": res.get("errcode", 0) in (None, 0, "0"), "raw": res,
         "note": "本账号当前生效的默认菜单信息"})


def cmd_precheck(args):
    """安装 / 首次使用自检：先看本技能配置齐不齐；没配好就打完整引导（含凤希AI伴侣下载地址）并退出码 1。"""
    data = load_relay_data()
    if data is None:
        fail_setup("本机还没有公众号配置"
                   "（本技能必须经「凤希AI伴侣 → 公众号运营」配置授权后才能发文章）；"
                   "凭据应集中在 ~/.workbuddy/fxai_global.json 的 weixin_gzh 字段")
    missing = []
    if not str(data.get("host", "")).strip():
        missing.append("host")
    login = resolve_login(data.get("login"))
    for f in ("tokenmyid", "usertoken"):
        if not str(login.get(f, "")).strip():
            missing.append("登录身份 " + f)
    accounts = data.get("accounts") or {}
    legacy = bool(str(data.get("configid", "")).strip() and str(data.get("appsecret", "")).strip())
    if not accounts and not legacy:  # 顶层直接带 configid/appsecret 的单号写法也算配好了
        missing.append("accounts(一个公众号都没有)")
    if missing:
        fail_setup("公众号配置不完整，缺少：%s；这份配置只能从凤希AI伴侣【首页】的「公众号运营」重新导出，"
                   "并写入 ~/.workbuddy/fxai_global.json 的 weixin_gzh。登录身份情况：%s"
                   % ("、".join(missing), login.get("_source", "未知")))
    names = [str(k) for k in accounts]
    login_note = "登录身份：%s" % login.get("_source", "")
    if not accounts:
        out({"ok": True, "mode": "relay", "path": str(GLOBAL_CONFIG_PATH), "accounts": [],
             "login": login_note,
             "note": "当前是顶层 configid/appsecret 的单号配置：只有一个号，不用指定"})
        return
    out({"ok": True, "mode": "relay", "path": str(GLOBAL_CONFIG_PATH), "accounts": names,
         "login": login_note,
         "summary": "配置可用，共 %d 个公众号：%s。%s" % (
             len(names), "、".join(names),
             "多个号且你没点名，我会先问你用哪个。" if len(names) > 1
             else "只有一个号时不用指定，以后直接说发文章就行。")})


def relay_check(args):
    res = relay_call("freepublish/draftcount")
    ok = "rsscode" not in res and res.get("errcode", 0) in (None, 0, "0")
    out({"ok": bool(ok), "relay": True, "draftcount": res,
         "summary": "中转配置可用，可以发布文章" if ok else "见 draftcount 返回"})


def cmd_material_add(args):
    return relay_material_add(args)


def upload_cover(filepath):
    media_id, url = relay_upload_cover(filepath)
    out({"ok": True, "media_id": media_id, "url": url, "file": str(filepath)})


# 只抓 src，不挑协议：外链图下载后上传，本地绝对路径图直接上传
IMG_RE = re.compile('<img[^>]+?src=["\\\']([^"\\\']+)["\\\']', re.I)


def _download_to_tmp(url):
    tmp = SKILL_DIR / "_tmp_img"
    tmp.mkdir(exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    ext = ".jpg"
    ctype = resp.headers.get("Content-Type", "")
    if "png" in ctype:
        ext = ".png"
    elif "gif" in ctype:
        ext = ".gif"
    elif "webp" in ctype:
        ext = ".webp"
    fp = tmp / (uuid.uuid4().hex + ext)
    fp.write_bytes(data)
    return fp


# ---------------------------------------------------------------- drafts

def read_content(path):
    p = Path(path)
    if not p.exists():
        fail(-1, f"正文文件不存在: {p}")
    html = p.read_text(encoding="utf-8-sig", errors="replace")  # utf-8-sig：顺手吃掉 BOM
    if ".md" in p.suffix.lower() or p.suffix.lower() == ".markdown":
        fail(-1, "正文请提供 HTML 文件", {"fix": "先把 Markdown 转成 HTML（可直接让 AI 帮你转），微信正文只接受内联样式的 HTML"})
    return html.strip()


def cmd_draft_add(args):
    return relay_draft_add(args)


def cmd_draft_get(args):
    return relay_draft_get(args)


def cmd_draft_list(args):
    return relay_draft_list(args)


def cmd_draft_delete(args):
    return relay_draft_delete(args)


# ---------------------------------------------------------------- publish (freepublish)

def cmd_publish(args):
    return relay_publish(args)


def cmd_publish_status(args):
    return relay_publish_status(args)


def cmd_publish_delete(args):
    return relay_publish_delete(args)


def cmd_publish_list(args):
    return relay_publish_list(args)


# ---------------------------------------------------------------- mass send

def cmd_mass(args):
    return relay_mass(args)


def cmd_mass_status(args):
    return relay_mass_status(args)


# ---------------------------------------------------------------- 自定义菜单命令入口

def cmd_menu_create(args):
    return relay_menu_create(args)


def cmd_menu_get(args):
    return relay_menu_get(args)


def cmd_menu_delete(args):
    return relay_menu_delete(args)


def cmd_menu_addconditional(args):
    return relay_menu_addconditional(args)


def cmd_menu_delconditional(args):
    return relay_menu_delconditional(args)


def cmd_menu_trymatch(args):
    return relay_menu_trymatch(args)


def cmd_menu_selfinfo(args):
    return relay_menu_selfinfo(args)


# ---------------------------------------------------------------- newspic / draft-update / material-map

def read_plain(path_or_text):
    """贴图正文：给存在的文件路径就读文件（.md/.txt 都行），否则整串当纯文本。"""
    p = Path(path_or_text)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8-sig", errors="replace").strip()
    return str(path_or_text).strip()


def ensure_material_ids(images):
    """本地图片 -> 永久素材 media_id：先查素材索引，命中直接复用；没命中才上传并记账。

    返回 (ids, reused)。上传走 relay material/add，成功后 material_map.record 记账
    （项目级 素材索引.json + 全局 material_map.json）。
    """
    try:
        import material_map
    except ImportError:
        material_map = None
    ids, reused = [], 0
    for raw in images:
        p = Path(str(raw).strip())
        if not p.exists():
            fail(-1, f"图片不存在: {p}")
        hit = material_map.lookup(str(p)) if material_map else None
        if hit and hit.get("media_id"):
            ids.append(hit["media_id"])
            reused += 1
            continue
        res = relay_call("material/add", file_path=str(p), params={"type": "image"})
        mid, url = res.get("media_id"), res.get("url")
        if not mid:
            fail(-1, f"素材上传失败: {p.name}", {"raw": res})
        if material_map:
            try:
                material_map.record(str(p), mid, url or "", verified=True)
            except Exception:
                pass
        ids.append(mid)
    return ids, reused


def relay_draft_add_newspic(args):
    """贴图（article_type=newspic）建稿。首图即封面，封面裁剪参数一律不传。"""
    imgs = []
    for raw in (args.images or []):
        imgs.extend([s for s in str(raw).split(",") if s.strip()])
    if not imgs:
        fail(-1, "贴图至少要一张图：--images a.jpg b.jpg ...（一个 --images 后面跟多个路径）")
    ids, reused = ensure_material_ids(imgs)
    params = {
        "title": str(args.title or "").strip(),
        "content": read_plain(args.content),
        "image_media_id": ",".join(ids),
        "need_open_comment": 1 if args.comments is None else int(args.comments),
    }
    if getattr(args, "ext", ""):
        params["ext"] = args.ext
    res = relay_call("freepublish/draftaddnewspic", params)
    n = res.get("image_count", len(ids))
    out({"ok": True, "media_id": res.get("media_id"), "image_count": n,
         "images_uploaded": len(ids) - reused, "reused_from_index": reused,
         "title": str(args.title or "").strip(),
         "next": "核对 image_count 是否等于图片张数，再用 publish --media-id 发布"})


def cmd_draft_add_newspic(args):
    return relay_draft_add_newspic(args)


def relay_draft_update(args):
    """draft/update 整体替换：articles 是「对象」不是数组，缺字段即整体覆盖。"""
    # articles 是 JSON 字符串整体当参数发，字段首尾空白用 _norm_json_strings 递归掐掉
    art = {"content": read_plain(args.content)}
    if args.title:
        art["title"] = str(args.title).strip()
    if getattr(args, "author", ""):
        art["author"] = str(args.author).strip()
    if getattr(args, "digest", ""):
        art["digest"] = str(args.digest).strip()
    if getattr(args, "image_media_id", ""):
        ids = [i.strip() for i in args.image_media_id.split(",") if i.strip()]
        art["image_info"] = {"image_list": [{"image_media_id": i} for i in ids]}
        art.setdefault("article_type", "newspic")
    if getattr(args, "article_type", ""):
        art["article_type"] = str(args.article_type).strip()
    if getattr(args, "thumb", ""):
        art["thumb_media_id"] = str(args.thumb).strip()
    if getattr(args, "ext", ""):
        try:
            ext = json.loads(args.ext)
        except Exception:
            fail(-1, "--ext 不是合法 JSON")
        if not isinstance(ext, dict):
            fail(-1, "--ext 必须是 JSON 对象")
        art.update(_norm_json_strings(ext))
    res = relay_call("freepublish/draftupdate", {
        "media_id": args.media_id,
        "articles": json.dumps(art, ensure_ascii=False),
    })
    out({"ok": True, "media_id": args.media_id, "res": res})


def cmd_draft_update(args):
    return relay_draft_update(args)


def cmd_material_map(args):
    try:
        import material_map
    except ImportError:
        fail(-1, "material_map.py 不在脚本目录")
    print(material_map.dump(args.image_dir or None))


def cmd_secure(args):
    """收紧全局配置权限：POSIX 600；Windows 去掉继承、只留当前用户完全控制。"""
    p = GLOBAL_CONFIG_PATH
    if not p.exists():
        print(json.dumps({"ok": False, "errmsg": f"配置不存在：{p}"}, ensure_ascii=False, indent=2))
        return 1
    if os.name == "nt":
        user = os.environ.get("USERNAME", "")
        # icacls 输出是系统 ANSI（中文机为 GBK），必须按 mbcs 解，否则子线程解码报错
        r = subprocess.run(
            ["icacls", str(p), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True, text=True, encoding="mbcs", errors="replace",
        )
        ok = r.returncode == 0
    else:
        os.chmod(p, 0o600)
        ok = True
    mode = "仅当前用户" if os.name == "nt" else oct(p.stat().st_mode & 0o777)
    print(json.dumps(
        {"ok": ok, "path": str(p), "mode": mode,
         "hint": "凭据文件只留当前用户可读写；换机器或重新导出配置后再跑一次"},
        ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_check(args):
    return relay_check(args)


def main():
    ap = argparse.ArgumentParser(prog="wx_api.py", description="WeChat MP article publish/maintain CLI")
    ap.add_argument("--account", default=None,
                    help='多公众号时指定本次用哪个号（公众号名称）；也可设环境变量 WX_ACCOUNT。'
                         '全局 weixin_gzh 里只有一个号时不用传')
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("precheck", help="安装/首次使用自检：没配置就提示去凤希AI伴侣「公众号运营」配置授权")
    p.set_defaults(fn=cmd_precheck)

    p = sub.add_parser("check", help="验证中转配置可用（draftcount）")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("secure", help="收紧全局配置权限（POSIX 600 / Windows 只留当前用户）")
    p.set_defaults(fn=cmd_secure)

    p = sub.add_parser("upload-cover", help="upload permanent cover image")
    p.add_argument("--file", required=True)
    p.set_defaults(fn=lambda a: upload_cover(a.file))

    p = sub.add_parser("material-add", help="upload permanent material (image/voice/video)")
    p.add_argument("--file", required=True, help="local file path")
    p.add_argument("--type", required=True, choices=["image", "voice", "video"], help="material type")
    p.add_argument("--title", default="", help="video only: title shown")
    p.add_argument("--introduction", default="", help="video only: description shown")
    p.set_defaults(fn=cmd_material_add)

    p = sub.add_parser("draft-add", help="create draft from HTML")
    p.add_argument("--title", required=True)
    p.add_argument("--content", required=True, help="path to article HTML file")
    p.add_argument("--cover", required=True, help="path to cover image (jpg/png, <2MB, 1152x512 —— 用 scripts/make_cover.py 生成)")
    p.add_argument("--author", default="")
    p.add_argument("--digest", default="", help="摘要，不填则取正文前 54 字")
    p.add_argument("--comments", type=int, default=1, help="1=开启留言 0=关闭")
    p.set_defaults(fn=cmd_draft_add)

    p = sub.add_parser("draft-get", help="get one draft")
    p.add_argument("--media-id", required=True)
    p.add_argument("--no-content", action="store_true", help="hide body text in output")
    p.set_defaults(fn=cmd_draft_get)

    p = sub.add_parser("draft-list", help="list drafts")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--count", type=int, default=10)
    p.set_defaults(fn=cmd_draft_list)

    p = sub.add_parser("draft-delete", help="delete a draft")
    p.add_argument("--media-id", required=True)
    p.set_defaults(fn=cmd_draft_delete)

    p = sub.add_parser("draft-add-newspic",
                       help="创建贴图/图集草稿（newspic）：图自动转永久素材，命中素材索引则复用")
    p.add_argument("--title", required=True)
    p.add_argument("--content", required=True, help="纯文本，或文本文件路径（.md/.txt）")
    p.add_argument("--images", nargs="+", required=True,
                   help="图片路径，按展示顺序；一个 --images 后面跟多个路径")
    p.add_argument("--comments", type=int, default=1, help="1=开启留言（默认）0=关闭")
    p.add_argument("--ext", default="", help="额外 JSON，原样 merge 进 articles[0]")
    p.set_defaults(fn=cmd_draft_add_newspic)

    p = sub.add_parser("draft-update", help="修改已有草稿（整体替换；news/newspic 通吃）")
    p.add_argument("--media-id", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--content", required=True, help="正文：newspic 纯文本 / news HTML，或文件路径")
    p.add_argument("--author", default="")
    p.add_argument("--digest", default="")
    p.add_argument("--article-type", default="", choices=["", "news", "newspic"])
    p.add_argument("--thumb", default="", help="news 封面 media_id（newspic 不传）")
    p.add_argument("--image-media-id", default="", help="逗号分隔永久素材 ID（newspic 整组替换）")
    p.add_argument("--ext", default="", help="额外 JSON，原样 merge 进 articles[0]")
    p.set_defaults(fn=cmd_draft_update)

    p = sub.add_parser("material-map", help="查看永久素材索引（哪些图已上传可复用）")
    p.add_argument("image_dir", nargs="?", default="")
    p.set_defaults(fn=cmd_material_map)

    p = sub.add_parser("publish", help="publish a draft (freepublish)")
    p.add_argument("--media-id", default="", help="existing draft media_id")
    p.add_argument("--title", default="")
    p.add_argument("--content", default="")
    p.add_argument("--cover", default="")
    p.add_argument("--author", default="")
    p.add_argument("--digest", default="")
    p.set_defaults(fn=cmd_publish)

    p = sub.add_parser("publish-status", help="query publish result")
    p.add_argument("--publish-id", required=True)
    p.set_defaults(fn=cmd_publish_status)

    p = sub.add_parser("publish-delete", help="delete a published article")
    p.add_argument("--article-id", required=True)
    p.add_argument("--index", type=int, default=0)
    p.set_defaults(fn=cmd_publish_delete)

    p = sub.add_parser("publish-list", help="list published articles (freepublish/publishlist)")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--count", type=int, default=10)
    p.set_defaults(fn=cmd_publish_list)

    p = sub.add_parser("mass", help="mass-send to followers (4/month)")
    p.add_argument("--media-id", required=True, help="draft media_id (mpnews) or video material media_id (msgtype=video)")
    p.add_argument("--msgtype", default="mpnews", choices=["mpnews", "video"], help="mpnews=图文(默认) video=纯视频")
    p.add_argument("--tag-id", type=int, default=None, help="send only to this tag; omit = all followers")
    p.set_defaults(fn=cmd_mass)

    p = sub.add_parser("mass-status", help="query mass-send status")
    p.add_argument("--msg-id", required=True)
    p.set_defaults(fn=cmd_mass_status)

    # ---------------- 自定义菜单（7 端点；结构在本地先校验，提交走 relay_call 中转）
    p = sub.add_parser("menu-create", help="创建/覆盖默认菜单（menu/create 整体替换）")
    p.add_argument("--file", help="菜单定义 JSON 文件，如 菜单.json")
    p.add_argument("--json", help="菜单定义 JSON 字符串（与 --file 二选一）")
    p.set_defaults(fn=cmd_menu_create)

    p = sub.add_parser("menu-get", help="读取菜单（含个性化菜单列表）（menu/get）")
    p.set_defaults(fn=cmd_menu_get)

    p = sub.add_parser("menu-delete", help="删除全部菜单：默认菜单 + 所有个性化菜单（menu/delete）")
    p.set_defaults(fn=cmd_menu_delete)

    p = sub.add_parser("menu-addconditional", help="创建个性化菜单（menu/addconditional）")
    p.add_argument("--file", help="含 button 与 matchrule 的 JSON 文件")
    p.add_argument("--json", help="含 button 与 matchrule 的 JSON 字符串（与 --file 二选一）")
    p.set_defaults(fn=cmd_menu_addconditional)

    p = sub.add_parser("menu-delconditional", help="按 menuid 删个性化菜单（menu/delconditional）")
    p.add_argument("--menuid", required=True, help="menu-addconditional 返回的 menuid")
    p.set_defaults(fn=cmd_menu_delconditional)

    p = sub.add_parser("menu-trymatch", help="试算某用户命中哪个菜单（menu/trymatch）")
    p.add_argument("--user-id", required=True, help="粉丝 openid 或微信号")
    p.set_defaults(fn=cmd_menu_trymatch)

    p = sub.add_parser("menu-selfinfo", help="本账号当前生效的默认菜单（get_current_selfmenu_info）")
    p.set_defaults(fn=cmd_menu_selfinfo)

    args = ap.parse_args()
    global ACCOUNT_NAME
    ACCOUNT_NAME = args.account
    args.fn(args)


if __name__ == "__main__":
    main()
