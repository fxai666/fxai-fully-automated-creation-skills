#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fxai-auto-update · 更新器：拉官网索引 → 下整包 zip → 逐文件落盘。

命令：
  check     取官网版本，比对本机版本
  update    下载索引给出的压缩包，解压后逐文件落盘；本地改过的进待合并
  tasks     列出待合并任务（基线 / 本地 / 上游三份路径）
  status    本机版本、已记录文件数、待合并份数、备份列表
  expert    检查或安装随包专家（未安装 / 版本落后 / 未注册时补齐）
  rollback  把备份拷回技能目录（--to 指定时间戳）

落点：状态 <state_dir>/fxai_update_state.json、基线 <state_dir>/fxai_update_baseline/、
备份与暂存 <state_dir>/fxai_update_backup/<时间戳>/。

整包内 专家包/ 不落进技能根：专家装到 <配置根>/plugins/marketplaces/my-experts/plugins/，
由包内 install_expert.py 执行；本机未安装、版本落后或市场清单未登记时才动手。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

UA = "fxai-auto-update/1.0"
DEFAULT_SITE = "www.fxai.site"
DEFAULT_PREFIX = "upgrade"
EXPERT_TOP = "专家包/"
EXPERT_INSTALLER = "install_expert.py"
EXPERT_MARKET = "my-experts"
EXPERT_FALLBACK_ID = "fxai-suite-manager"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------- 配置
def _home():
    return os.path.expanduser("~")


def _global_cfg():
    p = os.environ.get("FXAI_GLOBAL_CONFIG",
                       os.path.join(_home(), ".workbuddy", "fxai_global.json"))
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def cfg_site():
    c = _global_cfg()
    site = ((c.get("update") or {}).get("site")
            or (c.get("brand") or {}).get("official_site") or DEFAULT_SITE)
    site = str(site).strip()
    if "://" in site:
        site = site.split("://", 1)[1]
    return site.strip("/")


def cfg_prefix():
    return str((_global_cfg().get("update") or {}).get("prefix") or DEFAULT_PREFIX).strip("/")


def cfg_skills_dir():
    d = (_global_cfg().get("update") or {}).get("skills_dir")
    return os.path.normpath(d) if d else os.path.join(_home(), ".workbuddy", "skills")


def cfg_state_dir():
    d = (_global_cfg().get("update") or {}).get("state_dir")
    return os.path.normpath(d) if d else os.path.join(_home(), ".workbuddy")


def cfg_config_root():
    """宿主配置根：环境变量优先，否则由技能根反推（skills 的父目录）。

    技能装在哪，专家就装在同一配置根下的专家市场；不得写死产品目录名。
    """
    env = os.environ.get("WORKBUDDY_CONFIG_DIR", "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.dirname(os.path.abspath(cfg_skills_dir()))


def state_path():
    return os.path.join(cfg_state_dir(), "fxai_update_state.json")


def baseline_dir():
    return os.path.join(cfg_state_dir(), "fxai_update_baseline")


def backup_root():
    return os.path.join(cfg_state_dir(), "fxai_update_backup")


# ---------------------------------------------------------------- 工具
def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_rel(rel):
    """相对路径合法性：不含 .. / 盘符 / 绝对路径。"""
    if not rel or rel.startswith("/") or rel.startswith("\\"):
        return False
    if ".." in rel.replace("\\", "/").split("/"):
        return False
    if len(rel) > 1 and rel[1] == ":":
        return False
    return True


def vkey(v):
    """版本号归一：`V1.2` 与 `1.2` 同值，按数字段比较。"""
    got = [int(x) for x in re.findall(r"\d+", str(v or "").strip().lstrip("Vv"))]
    return tuple(got) if got else (0,)


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """重定向只在同主机内跟随，跨主机一律拒绝。"""

    def __init__(self, host):
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).hostname != self.host:
            raise urllib.error.URLError("拒绝跨主机重定向：%s" % newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url, timeout=30, host=None, binary=False):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if host:
        op.add_handler(_SameHostRedirect(host))
    op.addheaders = [("User-Agent", UA)]
    with op.open(url, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8")


def decode_body(raw):
    """官网返回三种形态都认：UTF-8 带 BOM（utf-8-sig，BOM 会卡住 json.loads）、
    纯 UTF-8、GB18030。按此顺序试，第一个解通的即用。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def index_url():
    return "https://%s/%s/skill.json" % (cfg_site(), cfg_prefix())


def file_url(rel):
    """压缩包地址：索引返回的相对路径拼在 upgrade/ 下，中文按百分号编码。"""
    return "https://%s/%s/%s" % (cfg_site(), cfg_prefix(),
                                 urllib.parse.quote(rel.lstrip("/"), safe="/"))


def fetch_index():
    """取官网索引：必认 version 与 path，其余字段原样带回（如 sha256 / size）。"""
    raw = http_get(index_url(), timeout=20, host=cfg_site(), binary=True)
    m = json.loads(decode_body(raw))
    version = str(m.get("version") or "").strip()
    path = str(m.get("path") or "").strip()
    if not version:
        raise RuntimeError("索引缺 version：%s" % index_url())
    if not path:
        raise RuntimeError("索引缺 path（压缩包相对路径）：%s" % index_url())
    extra = {k: v for k, v in m.items() if k not in ("version", "path")}
    return {"version": version, "path": path, "extra": extra}


# ---------------------------------------------------------------- 状态与基线
def load_state():
    try:
        with open(state_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": "", "updated_at": "", "files": {}}


def save_state(st):
    p = state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def base_file(rel):
    return os.path.join(baseline_dir(), rel.replace("/", os.sep))


def write_baseline(rel, src):
    p = base_file(rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    shutil.copyfile(src, p)


def newest_backup():
    root = backup_root()
    if not os.path.isdir(root):
        return ""
    dirs = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    return os.path.join(root, dirs[-1]) if dirs else ""


def pending_path(bdir=None):
    bdir = bdir or newest_backup()
    return os.path.join(bdir, "pending_merge.json") if bdir else ""


def load_pending(bdir=None):
    p = pending_path(bdir)
    if not p or not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def add_pending(bdir, version, rel):
    p = pending_path(bdir)
    data = load_pending(bdir) or {"version": version,
                                  "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "backup": bdir, "tasks": []}
    if rel not in {t["path"] for t in data["tasks"]}:
        data["tasks"].append({
            "path": rel,
            "base": base_file(rel),
            "ours": os.path.join(cfg_skills_dir(), rel.replace("/", os.sep)),
            "theirs": os.path.join(bdir, "stage", "full", rel.replace("/", os.sep)),
        })
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 落盘
def put_file(rel, src, bdir):
    """备份本地 → 落盘 → 刷新基线与状态。"""
    skills = cfg_skills_dir()
    dst = os.path.join(skills, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isfile(dst):
        bak = os.path.join(bdir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        shutil.copyfile(dst, bak)
    shutil.copyfile(src, dst)
    write_baseline(rel, dst)
    st = load_state()
    st["files"][rel] = {"sha256": sha256_file(dst), "size": os.path.getsize(dst)}
    save_state(st)


def local_touched(rel):
    """本地文件是否被改过：与基线副本比对；基线缺失按未改动处理。"""
    bp = base_file(rel)
    if not os.path.isfile(bp):
        return False
    dst = os.path.join(cfg_skills_dir(), rel.replace("/", os.sep))
    return sha256_file(dst) != sha256_file(bp)


# ---------------------------------------------------------------- 专家
def expert_pkg(full):
    """读整包内 专家包/.codebuddy-plugin/plugin.json；缺件返回 None。"""
    root = os.path.join(full, EXPERT_TOP.rstrip("/"))
    meta = os.path.join(root, ".codebuddy-plugin", "plugin.json")
    if not os.path.isfile(meta):
        return None
    try:
        with open(meta, encoding="utf-8-sig") as f:
            d = json.load(f)
        eid, ver = str(d.get("name") or "").strip(), str(d.get("version") or "").strip()
    except Exception:
        return None
    return {"dir": root, "id": eid, "version": ver} if eid and ver else None


def expert_market(cfg):
    return os.path.join(cfg, "plugins", "marketplaces", EXPERT_MARKET)


def expert_local(cfg, eid):
    """只读检查本机专家：目录、版本、市场清单登记。"""
    market = expert_market(cfg)
    d = os.path.join(market, "plugins", eid)
    ver = ""
    meta = os.path.join(d, ".codebuddy-plugin", "plugin.json")
    if os.path.isfile(meta):
        try:
            with open(meta, encoding="utf-8-sig") as f:
                ver = str(json.load(f).get("version") or "").strip()
        except Exception:
            pass
    reg = False
    mp = os.path.join(market, ".codebuddy-plugin", "marketplace.json")
    if os.path.isfile(mp):
        try:
            with open(mp, encoding="utf-8-sig") as f:
                reg = any(x.get("name") == eid for x in json.load(f).get("plugins", []))
        except Exception:
            pass
    return {"dir": d, "exists": os.path.isdir(d), "version": ver, "registered": reg}


def expert_state(pkg, loc):
    if not loc["exists"]:
        return "need-install"
    if not loc["registered"]:
        return "need-register"
    if vkey(loc["version"]) < vkey(pkg["version"]):
        return "need-upgrade"
    return "ok"


def expert_sync(pkg, cfg, install=False, force=False):
    """检查并按状态安装专家；install=False 只判定不落盘。"""
    loc = expert_local(cfg, pkg["id"])
    state = expert_state(pkg, loc)
    head = "本机 %s / 包内 V%s / %s" % (
        ("V" + loc["version"]) if loc["exists"] else "未安装", pkg["version"], state)
    if not install or state == "ok":
        return state, head + "（无需处理）"
    script = os.path.join(pkg["dir"], EXPERT_INSTALLER)
    if not os.path.isfile(script):
        return "no-installer", head + "（包内缺 %s，按 智能体安装规程.md 手动安装）" % EXPERT_INSTALLER
    cmd = [sys.executable, script, "--config-root", cfg]
    if force:
        cmd.append("--force")
    elif state == "need-upgrade":
        cmd.append("--upgrade")
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    if out:
        print(out, flush=True)
    return ("done" if r.returncode == 0 else "failed"), head + "（安装脚本返回 %d）" % r.returncode


def unpack(zpath, dest):
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError("压缩包损坏：%s" % bad)
        z.extractall(dest)


# ---------------------------------------------------------------- 命令
def cmd_check(a):
    idx = fetch_index()
    st = load_state()
    up_sha = str((idx.get("extra") or {}).get("sha256") or "").lower()
    print("[CHECK] 官网版本 %s" % idx["version"], flush=True)
    print("[CHECK] 本机版本 %s" % (st.get("version") or "unknown"), flush=True)
    print("[CHECK] 压缩包 %s" % file_url(idx["path"]), flush=True)
    eid = (st.get("expert") or {}).get("id") or EXPERT_FALLBACK_ID
    loc = expert_local(cfg_config_root(), eid)
    print("[CHECK] 专家 %s：%s" % (
        eid, ("已安装 V%s%s" % (loc["version"], "" if loc["registered"] else "（未登记市场清单）"))
        if loc["exists"] else "未安装（更新时会自动安装）"), flush=True)
    if vkey(idx["version"]) > vkey(st.get("version")):
        print("[CHECK] 有新版本：python scripts/update.py update", flush=True)
    elif (vkey(idx["version"]) == vkey(st.get("version")) and up_sha
          and up_sha != str(st.get("zip_sha256") or "").lower()):
        print("[CHECK] 官网同版本（%s）内容有更新（发布方重打了包）：python scripts/update.py update"
              % idx["version"], flush=True)
    else:
        print("[CHECK] 已是最新", flush=True)
    return 0


def cmd_update(a):
    idx = fetch_index()
    st = load_state()
    up_sha = str((idx.get("extra") or {}).get("sha256") or "").lower()
    has_state = str(st.get("zip_sha256") or "").lower()
    newer = vkey(idx["version"]) > vkey(st.get("version"))
    redo = bool(vkey(idx["version"]) == vkey(st.get("version"))
                and up_sha and up_sha != has_state)
    if not newer and not redo and not a.force:
        print("[UPDATE] 已是最新（本机 %s / 官网 %s）" % (st.get("version") or "unknown",
                                                   idx["version"]), flush=True)
        return 0
    if redo:
        print("[UPDATE] 官网同版本（%s）内容有更新，按更新处理" % idx["version"], flush=True)

    bdir = os.path.join(backup_root(), time.strftime("%Y%m%d-%H%M%S"))
    stage = os.path.join(bdir, "stage")
    os.makedirs(stage, exist_ok=True)
    print("[BACKUP] %s" % bdir, flush=True)

    zp = os.path.join(stage, os.path.basename(idx["path"]) or "update.zip")
    print("[GET] %s" % file_url(idx["path"]), flush=True)
    data = http_get(file_url(idx["path"]), timeout=600, host=cfg_site(), binary=True)
    want = idx["extra"].get("sha256")
    if want and sha256_bytes(data) != want:
        print("[ERROR] 压缩包 sha256 不符，中止", flush=True)
        return 3
    with open(zp, "wb") as f:
        f.write(data)
    print("[GET] %d 字节 → %s" % (len(data), zp), flush=True)

    full = os.path.join(stage, "full")
    unpack(zp, full)

    skills = cfg_skills_dir()
    done, pend, same = 0, 0, 0
    seen = set()
    for root, dirs, names in os.walk(full):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(names):
            src = os.path.join(root, name)
            rel = os.path.relpath(src, full).replace(os.sep, "/")
            if not safe_rel(rel) or name.endswith(".pyc"):
                continue
            if rel.startswith(EXPERT_TOP):
                continue
            seen.add(rel)
            dst = os.path.join(skills, rel.replace("/", os.sep))
            if os.path.isfile(dst) and sha256_file(dst) == sha256_file(src):
                if rel not in (load_state().get("files") or {}):
                    stt = load_state()
                    stt["files"][rel] = {"sha256": sha256_file(dst), "size": os.path.getsize(dst)}
                    save_state(stt)
                same += 1
                continue
            if os.path.isfile(dst) and local_touched(rel):
                add_pending(bdir, idx["version"], rel)
                pend += 1
                print("  [MERGE] %s" % rel, flush=True)
            else:
                put_file(rel, src, bdir)
                done += 1
                print("  [OK] %s" % rel, flush=True)

    st = load_state()
    pkg = expert_pkg(full)
    if pkg:
        cfg = cfg_config_root()
        print("[EXPERT] 宿主配置根 %s" % cfg, flush=True)
        est, emsg = expert_sync(pkg, cfg, install=True)
        print("[EXPERT] 专家 %s V%s：%s" % (pkg["id"], pkg["version"], emsg), flush=True)
        if est in ("failed", "no-installer"):
            print("[EXPERT][WARN] 专家未就绪；技能更新不受影响，可手动执行："
                  "python scripts/update.py expert --install", flush=True)
        st["expert"] = {"id": pkg["id"], "version": pkg["version"],
                        "state": est, "config_root": cfg}
    else:
        print("[EXPERT] 包内无 专家包/，跳过专家检查", flush=True)
    save_state(st)

    only_local = [r for r in (st.get("files") or {}) if r not in seen]
    if only_local:
        print("[LOCAL-ONLY] 仅本机有、包里没有（保留不删）%d 个：" % len(only_local), flush=True)
        for rel in only_local[:20]:
            print("  %s" % rel, flush=True)

    if pend == 0:
        st["version"] = idx["version"]
        if up_sha:
            st["zip_sha256"] = up_sha
        st["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(st)
        print("[UPDATE] 完成：落盘 %d、已一致 %d，版本 %s" % (done, same, idx["version"]), flush=True)
    else:
        print("[UPDATE] 落盘 %d、已一致 %d、待合并 %d" % (done, same, pend), flush=True)
        print("[NEXT] python scripts/update.py tasks", flush=True)
    return 0


def cmd_tasks(a):
    d = load_pending()
    if not d or not d.get("tasks"):
        print("[TASKS] 没有待合并任务", flush=True)
        return 0
    print("[TASKS] 上游版本 %s  待合并 %d" % (d.get("version", ""), len(d["tasks"])), flush=True)
    for t in d["tasks"]:
        print("\n- %s" % t["path"], flush=True)
        print("    base  : %s" % t["base"], flush=True)
        print("    ours  : %s" % t["ours"], flush=True)
        print("    theirs: %s" % t["theirs"], flush=True)
    print("\n[NEXT] 合并后：python scripts/apply_merge.py apply --file <相对路径> --merged <合并后文件>",
          flush=True)
    return 0


def cmd_status(a):
    st = load_state()
    print("[STATUS] 本机版本 %s（%s）" % (st.get("version") or "unknown", st.get("updated_at", "")),
          flush=True)
    print("[STATUS] 技能目录 %s" % cfg_skills_dir(), flush=True)
    print("[STATUS] 官网索引 %s" % index_url(), flush=True)
    print("[STATUS] 已记录文件 %d" % len(st.get("files") or {}), flush=True)
    e = st.get("expert") or {}
    print("[STATUS] 专家 %s" %
          ("%s V%s（%s）" % (e.get("id"), e.get("version"), e.get("state"))
           if e else "未记录（跑 update 或 expert --check）"), flush=True)
    d = load_pending()
    print("[STATUS] 待合并 %d" % (len(d.get("tasks", [])) if d else 0), flush=True)
    root = backup_root()
    if os.path.isdir(root):
        dirs = sorted(x for x in os.listdir(root) if os.path.isdir(os.path.join(root, x)))
        print("[STATUS] 备份 %d 份，最近：%s" % (len(dirs), ", ".join(dirs[-3:])), flush=True)
    return 0


def cmd_rollback(a):
    root = backup_root()
    if not os.path.isdir(root):
        print("[ERROR] 没有备份目录：%s" % root, flush=True)
        return 2
    dirs = sorted(x for x in os.listdir(root) if os.path.isdir(os.path.join(root, x)))
    if not dirs:
        print("[ERROR] 没有备份", flush=True)
        return 2
    bdir = os.path.join(root, a.to) if a.to else os.path.join(root, dirs[-1])
    if not os.path.isdir(bdir):
        print("[ERROR] 备份不存在：%s" % bdir, flush=True)
        return 2
    skills = cfg_skills_dir()
    n = 0
    for walk_dir, dirs_, names in os.walk(bdir):
        dirs_[:] = [d for d in dirs_ if d != "stage"]
        for name in names:
            src = os.path.join(walk_dir, name)
            rel = os.path.relpath(src, bdir).replace(os.sep, "/")
            if rel == "pending_merge.json":
                continue
            dst = os.path.join(skills, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            write_baseline(rel, dst)
            st = load_state()
            st["files"][rel] = {"sha256": sha256_file(dst), "size": os.path.getsize(dst)}
            save_state(st)
            n += 1
    print("[ROLLBACK] 已还原 %d 个文件（源自 %s）" % (n, os.path.basename(bdir)), flush=True)
    return 0


def cmd_expert(a):
    cfg = os.path.abspath(os.path.expanduser(a.config_root)) if a.config_root else cfg_config_root()
    full = a.from_dir
    if not full:
        bdir = newest_backup()
        full = os.path.join(bdir, "stage", "full") if bdir else ""
    pkg = expert_pkg(full) if full and os.path.isdir(full) else None
    eid = pkg["id"] if pkg else (load_state().get("expert") or {}).get("id") or EXPERT_FALLBACK_ID
    loc = expert_local(cfg, eid)
    print("[EXPERT] 配置根 %s" % cfg, flush=True)
    print("[EXPERT] 专家 %s：本机 %s，市场登记 %s" %
          (eid, "V%s" % loc["version"] if loc["exists"] else "未安装", loc["registered"]),
          flush=True)
    if not pkg:
        print("[EXPERT] 未找到整包解压目录（--from 指定）；只报本机状态", flush=True)
        return 0
    print("[EXPERT] 包内 V%s（%s）" % (pkg["version"], full), flush=True)
    state = expert_state(pkg, loc)
    print("[EXPERT] 判定 %s" % state, flush=True)
    if not a.install:
        if state != "ok":
            print("[NEXT] python scripts/update.py expert --install", flush=True)
        return 0
    est, emsg = expert_sync(pkg, cfg, install=True, force=a.force)
    print("[EXPERT] %s" % emsg, flush=True)
    st = load_state()
    st["expert"] = {"id": pkg["id"], "version": pkg["version"],
                    "state": est, "config_root": cfg}
    save_state(st)
    return 0 if est in ("done", "ok") else 1


def main():
    ap = argparse.ArgumentParser(description="fxai 技能包更新器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="比对官网版本").set_defaults(fn=cmd_check)
    p = sub.add_parser("update", help="下载整包并逐文件落盘")
    p.add_argument("--force", action="store_true", help="版本不高于本机也重新拉取")
    p.set_defaults(fn=cmd_update)
    sub.add_parser("tasks", help="列出待合并任务").set_defaults(fn=cmd_tasks)
    sub.add_parser("status", help="本机版本与状态").set_defaults(fn=cmd_status)
    p = sub.add_parser("expert", help="检查或安装随包专家")
    p.add_argument("--check", action="store_true", help="只报现状，不改动（缺省行为）")
    p.add_argument("--install", action="store_true", help="未装 / 版本落后 / 未登记时安装")
    p.add_argument("--from", dest="from_dir", default="",
                   help="整包解压目录（缺省取最近备份的 stage/full）")
    p.add_argument("--config-root", default="", help="指定宿主配置根")
    p.add_argument("--force", action="store_true", help="无条件覆盖同名文件")
    p.set_defaults(fn=cmd_expert)
    p = sub.add_parser("rollback", help="还原备份")
    p.add_argument("--to", default="", help="备份时间戳目录名，缺省用最近一份")
    p.set_defaults(fn=cmd_rollback)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
