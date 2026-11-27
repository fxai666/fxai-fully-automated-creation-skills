# -*- coding: utf-8 -*-
"""把本地「发布版」专用工作流目录单向同步到线上 ComfyUI（仙宫云实例）。

🔴 唯一来源（用户 2026-11-13 定）：本机 ComfyUI 工作区的
   user/default/workflows/发布版 目录（旧名「仙宫云」，2026-11-15 改名）。
   线上 user/default/workflows/ 只做接收端，不在线上直接编辑。

用法：
    python sync_workflows.py                  # 同步（只推有差异的，幂等）
    python sync_workflows.py --dry-run        # 只比对，不写（--check 同义）
    python sync_workflows.py --profile <实例ID>   # 指定凭据 profile
    python sync_workflows.py --src <目录> --remote <远端目录>
    python sync_workflows.py --prune          # 额外报告远端独有的文件（默认只提示不删）

来源目录解析顺序（最新标准，只读 workflows 块）：--src → 环境变量 FXAI_XGY_WF_SRC
→ 全局配置 ~/.workbuddy/fxai_global.json 的 workflows.release_dir（配了 comfy_root 时按它拼接）
→ 当前目录下的 user/default/workflows/发布版。
凭据读取顺序：环境变量 XGY_HOST/XGY_PORT/XGY_PASS → --profile → 全局配置 workflows.secrets
"""
from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import os
import posixpath
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 发布版化不依赖外部工具：本脚本默认 --release 在上传时把占位图名换成真实品牌图名
# （只改上传字节，不动本地文件）；来源目录指向开发版目录也能直接推。
PLACEHOLDER = "fxai_workflow_banner_placeholder.png"
REAL = "fxai_workflow_banner.png"


def swap_banner_bytes(text: bytes, src: str, dst: str) -> bytes:
    """把工作流 JSON 文本里的 banner 图片名 src 换成 dst。无匹配则原样返回。"""
    return text.replace(src.encode("utf-8"), dst.encode("utf-8"))

SECRET_NAME = "xianggongyun.json"
DEFAULT_REMOTE = "/root/ComfyUI/user/default/workflows"
# 发布版排除名单：源目录下这个 txt，一行一个文件名，# 开头为注释。
# 名单里的工作流只在本地保留，不推线上（与 repo_sync.py 共用同一份名单）。
EXCLUDE_FILE = "发布版排除名单.txt"


def load_exclude(src: str) -> set:
    path = os.path.join(src, EXCLUDE_FILE)
    if not os.path.exists(path):
        return set()
    names = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if not s.endswith(".json"):
                s += ".json"
            names.add(s)
    return names


def _workflows_cfg() -> dict:
    """读全局配置 workflows 块（~/.workbuddy/fxai_global.json，FXAI_GLOBAL_CONFIG 可改位置）。

    最新标准：工作流发布相关路径统一放 workflows 块（comfy_root + dev_dir/release_dir + banner_*），
    旧扁平键 xgy_workflow_src / xgy_secrets 已停用。
    """
    import os as _os
    p = _os.environ.get(
        "FXAI_GLOBAL_CONFIG",
        _os.path.join(_os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))
    try:
        with open(p, encoding="utf-8") as f:
            g = json.load(f) or {}
    except Exception:
        return {}
    wf = g.get("workflows")
    return wf if isinstance(wf, dict) else {}


def default_src():
    """发布版工作流目录的解析顺序（不写死本机路径，最新标准只读 workflows 块）。

    1) 环境变量 FXAI_XGY_WF_SRC（显式覆盖）
    2) 全局配置 workflows.release_dir（配了 comfy_root 时按 comfy_root 拼接）
    3) 当前目录下 user/default/workflows/发布版
    """
    env = os.environ.get("FXAI_XGY_WF_SRC", "")
    if env and os.path.isdir(env):
        return env
    wf = _workflows_cfg()
    release_dir = wf.get("release_dir") or ""
    if release_dir:
        if os.path.isabs(release_dir):
            cand = release_dir
        else:
            base = wf.get("comfy_root") or os.getcwd()
            cand = os.path.join(base, release_dir)
        if os.path.isdir(cand):
            return cand
    cand = os.path.join(os.getcwd(), "user", "default", "workflows", "发布版")
    if os.path.isdir(cand):
        return cand
    return ""


def find_secret_file():
    """secrets/xianggongyun.json 的解析：环境变量 → 全局配置 workflows.secrets → 脚本目录逐级向上找。"""
    env = os.environ.get("FXAI_XGY_SECRETS", "")
    if env and os.path.isfile(env):
        return env
    wf = _workflows_cfg()
    v = wf.get("secrets") or ""
    if v and os.path.isfile(v):
        return v
    here = os.path.dirname(os.path.abspath(__file__))
    for up in range(0, 7):
        p = os.path.join(*([here] + [".."] * up), "secrets", SECRET_NAME)
        if os.path.isfile(p):
            return p
    return ""


def cred_from_profile(profile=""):
    """从 secrets 文件读某台实例的 SSH 信息；profile 空则用 default。"""
    p = find_secret_file()
    if not p:
        return {}
    try:
        d = json.load(open(p, encoding="utf-8")) or {}
    except Exception:
        return {}
    if profile:
        prof = d.get(profile)
        return prof if isinstance(prof, dict) and prof.get("host") else {}
    prof = d.get("default")
    if isinstance(prof, dict) and prof.get("host"):
        return prof
    return {}


def resolve_cred(profile=""):
    host = os.environ.get("XGY_HOST", "")
    if host:
        return {
            "host": host,
            "port": int(os.environ.get("XGY_PORT", "22")),
            "user": os.environ.get("XGY_USER", "root"),
            "password": os.environ.get("XGY_PASS", ""),
        }
    if profile:
        c = cred_from_profile(profile)
        if c:
            return c
        raise SystemExit("secrets 里找不到 profile: %s" % profile)
    try:
        import xgy_cfg
        c = xgy_cfg.ssh_cred()
        if c and c.get("host"):
            return c
    except Exception:
        pass
    c = cred_from_profile("")
    if c:
        return c
    raise SystemExit("拿不到 SSH 凭据：设 XGY_HOST/XGY_PASS，或用 --profile <实例ID>")


def md5_bytes(b):
    return hashlib.md5(b).hexdigest()


def comfy_root_of(path):
    """把 .../user/default/workflows 这样的路径规整为 ComfyUI 根目录。"""
    r = path
    for _ in range(3):
        r = os.path.dirname(r)
    return r


def _banner_bytes(cwd="", src=""):
    """取真实品牌图的字节：先本地 input/，再本技能包 assets 的 base64。返回 (bytes, 来源描述) 或 (b"", "")。"""
    cands = []
    if cwd:
        cands.append(os.path.join(cwd, "input", REAL))
    if src:
        cands.append(os.path.join(comfy_root_of(os.path.abspath(src)), "input", REAL))
    cands.append(os.path.join(comfy_root_of(os.path.abspath(cwd or ".")), "input", REAL))
    lp = next((c for c in cands if os.path.isfile(c)), "")
    if lp:
        return open(lp, "rb").read(), "本地 input"
    # 别人只装技能包、本机 input/ 没图时，从技能包自带 base64 解码
    assets = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
    bp = os.path.join(assets, REAL + ".b64")
    if os.path.isfile(bp):
        try:
            return base64.b64decode(open(bp, "rb").read()), "技能包 assets base64"
        except Exception as e:
            print("  ! 品牌图 base64 解码失败：%s" % e)
    return b"", ""


def upload_release_banner(sftp, remote, src="", cwd=""):
    """最佳努力把真实品牌图上传到云端 input/（非致命，失败只告警）。"""
    data, origin = _banner_bytes(cwd, src)
    if not data:
        print("  · 未找到 %s（本地 input 与技能包 assets 都没有），跳过品牌图上传" % REAL)
        return
    # remote 是云端 POSIX 路径，必须用 posixpath 拼；用 os.path 在 Windows 上
    # 会生成 /root/ComfyUI\input\xxx 这种带反斜杠的怪文件名。
    rp = posixpath.join(comfy_root_of(remote).replace("\\", "/"), "input", REAL)
    try:
        with sftp.open(rp, "wb") as f:
            f.write(data)
        sftp.chmod(rp, 0o644)
        print("  · 已上传品牌图 %s -> %s（来源：%s）" % (REAL, rp, origin))
    except Exception as e:
        print("  ! 品牌图上传失败（非致命，可手动补）：%s" % e)


def main():
    ap = argparse.ArgumentParser(description="仙宫云工作流单向同步（本地 发布版/ → 线上）")
    ap.add_argument("--src", default="", help="本地来源目录；不给则按 环境变量/全局配置/当前目录 自动解析")
    ap.add_argument("--remote", default=DEFAULT_REMOTE)
    ap.add_argument("--profile", default="")
    ap.add_argument("--dry-run", "--check", dest="dry", action="store_true")
    ap.add_argument("--prune", action="store_true", help="报告远端独有的 json（不删，只列）")
    ap.add_argument("--no-release", dest="release", action="store_false",
                    help="默认推送前把占位图换为真实品牌图（发布版）；加此项则原样推送")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    src = a.src or default_src()
    if not src:
        raise SystemExit(
            "找不到工作流来源目录。三种指定方式（任选其一）：\n"
            "  1) --src <目录>\n"
            "  2) 环境变量 FXAI_XGY_WF_SRC=<目录>\n"
            "  3) 全局配置 ~/.workbuddy/fxai_global.json 的 workflows.release_dir（配 comfy_root 时按它拼接）")
    if not os.path.isdir(src):
        raise SystemExit("源目录不存在: %s" % src)

    files = sorted(glob.glob(os.path.join(src, "*.json")))
    if not files:
        raise SystemExit("源目录里没有 json: %s" % src)

    excluded = load_exclude(src)
    skipped = [os.path.basename(p) for p in files if os.path.basename(p) in excluded]
    files = [p for p in files if os.path.basename(p) not in excluded]
    if skipped:
        print("排除名单（只在本地，不推线上）：" + "、".join(skipped))

    local = {}
    print("源目录: %s" % src)
    print("本地 %d 个 JSON，逐个做合法性预检：" % len(files))
    for p in files:
        b = open(p, "rb").read()
        try:
            json.loads(b.decode("utf-8"))
        except Exception as e:
            raise SystemExit("JSON 不合法，先修好再推：%s (%s)" % (os.path.basename(p), e))
        if a.release:
            # 兜底发布版化：若来源里还是占位图（未经 make_release 生成的发布版目录），
            # 推送时临时换回真实品牌图（仅改上传字节，不动本地文件）。
            # 正常流程下 make_release.py 已在「发布版」目录里把图换成真实图，此步为幂等 no-op。
            b = swap_banner_bytes(b, PLACEHOLDER, REAL)
        name = os.path.basename(p)
        local[name] = (md5_bytes(b), len(b), p)
        print("  %-40s %8d B  %s" % (name, len(b), local[name][0][:12]))

    c = resolve_cred(a.profile)
    host, port = c["host"], int(c.get("port") or 22)
    user = c.get("user") or "root"
    print("\n目标: %s@%s:%d  %s" % (user, host, port, a.remote))

    try:
        import paramiko
    except ImportError:
        raise SystemExit("缺 paramiko：用部署 venv 的 python 跑，或 pip install paramiko")

    t = paramiko.Transport((host, port))
    kw = {"username": user}
    if c.get("password"):
        kw["password"] = c["password"]
    if c.get("key") and os.path.isfile(os.path.expanduser(c["key"])):
        kw["pkey"] = paramiko.Ed25519Key.from_private_key_file(os.path.expanduser(c["key"]))
    try:
        t.connect(**kw)
    except Exception as e:
        raise SystemExit(
            "连不上 %s@%s:%d —— %s: %s\n"
            "常见原因：实例已关机/已销毁（控制台确认），或端口变了（用 --profile <新实例ID>），"
            "或凭据过期（改 secrets/xianggongyun.json）。"
            % (user, host, port, type(e).__name__, e))
    sftp = paramiko.SFTPClient.from_transport(t)

    if a.release:
        # 发布版需要云端 input/ 里有真实品牌图，这里最佳努力补传一次
        upload_release_banner(sftp, a.remote, a.src, os.getcwd())

    new, changed, same = [], [], []
    try:
        existing = set(sftp.listdir(a.remote))

        for name, (lmd5, size, lp) in local.items():
            if name not in existing:
                new.append(name)
                continue
            with sftp.open(a.remote + "/" + name, "rb") as f:
                rmd5 = md5_bytes(f.read())
            (changed if rmd5 != lmd5 else same).append(name)

        print("\n差异：新增 %d / 变更 %d / 一致 %d" % (len(new), len(changed), len(same)))
        for n in new:
            print("  + %s" % n)
        for n in changed:
            print("  ~ %s" % n)
        if a.prune:
            extra = sorted(x for x in existing if x.endswith(".json") and x not in local)
            if extra:
                print("\n远端独有（未删除，需要删请手动确认）：")
                for n in extra:
                    print("  ? %s" % n)

        if a.dry:
            print("\n[DRY-RUN] 未写入任何文件。")
            return 0

        todo = new + changed
        if not todo:
            print("\n无需同步：远端已与本地一致。")
            return 0

        print("\n开始上传 %d 个：" % len(todo))
        for name in todo:
            lmd5, size, lp = local[name]
            rp = a.remote + "/" + name
            sftp.put(lp, rp)
            sftp.chmod(rp, 0o644)
            with sftp.open(rp, "rb") as f:
                rmd5 = md5_bytes(f.read())
            if rmd5 != lmd5:
                raise SystemExit("MD5 校验失败：%s local=%s remote=%s" % (name, lmd5, rmd5))
            print("  OK %-40s %s" % (name, "覆盖" if name in existing else "新增"))

        # 收尾：远端总数对账
        after = [x for x in sftp.listdir(a.remote) if x.endswith(".json")]
        print("\n完成：推送 %d 个，md5 全部一致；远端 json 总数 %d" % (len(todo), len(after)))
    finally:
        sftp.close()
        t.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
