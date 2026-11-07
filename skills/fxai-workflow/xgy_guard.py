# -*- coding: utf-8 -*-
# =====================================================================
# 仙宫云实例看门狗（公共层 fxai-workflow 配套，生图 / 四视图 / 场景 / MV / 短剧 / vocal 全用）
# ---------------------------------------------------------------------
# 仙宫云容器实例多半关机，开工前先探活，探不通自动开机等就绪。
#
# 开机遇平台 GPU 不足(code=1003) 会自动持续抢卡直到开起来（标准输出反复提示「正在抢 GPU 开机」，
# 无需人工干预）；实例不存在/已销毁则提示去控制台实例化（部署镜像包见 fxai-xiangongyun-deploy 技能文档）。
# 抢卡逻辑只写在这一处（生图/四视图/场景/MV/短剧/TTS/Whisper 的云端开机全走本文件）。
#
# 依赖：公共客户端 fxai-xiangongyun-deploy/scripts/xgy_api.py（开机 / 关机 / 查实例都走它）。
# 只需要「访问令牌」：优先级 环境变量 XGY_API_TOKEN > 项目 secrets > 数据目录 sysconfig/xgy_*.json，
# 三处都逐个验证，取能用的那个（光看「有值」可能拿到失效令牌）。
#
# 实例 ID 的取法（依次）：配置 xgy_instance_id > comfy_url 域名反解 > 令牌下已有机器的唯一那台。
# 初次用「python xgy_guard.py use <实例ID>」把选中的实例写进 ~/.workbuddy/fxai_global.json，之后就固定用它。
# =====================================================================

import glob
import json
import os
import re
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEPLOY = os.path.join(os.path.dirname(_HERE), "fxai-xiangongyun-deploy", "scripts")
if _DEPLOY not in sys.path:
    sys.path.insert(0, _DEPLOY)

import xgy_api  # noqa: E402

# 本机开着 HTTP 代理，直连仙宫云的两类地址必须绕开，否则 urllib 把请求也送去代理、连不上：
#  · open API（api.xiangongyun.com）—— xgy_api 内部用的 urllib 会读 no_proxy 环境，故补这里；
#  · 容器地址（*.container.x-gpu.com）—— 自己探活用下面的 _NO_PROXY_OPENER，不依赖环境。
for _h in ("NO_PROXY", "no_proxy"):
    _e = os.environ.get(_h, "")
    if ".xiangongyun.com" not in _e:
        os.environ[_h] = (".xiangongyun.com" if not _e else _e + ",.xiangongyun.com")

XGPU_HOST = ".container.x-gpu.com"

# 没令牌时给人的指引：控制台「访问令牌」页拿，或先在凤希 AI 的仙宫云管理台里存一份
TOKEN_HELP = (
    "缺少仙宫云访问令牌，自动开机需要令牌。二选一：\n"
    "  1) 在凤希 AI 的「仙宫云管理台」里点设置令牌、粘上令牌（存本机 sysconfig/xgy_*.json）；\n"
    "  2) 或直接设环境变量 XGY_API_TOKEN=xxx（控制台「访问令牌」页生成，不是 SSH 密码）。"
)


def is_xgpu(url):
    """地址是不是仙宫云容器（只有这类才需要自动开机 / 关机）。"""
    return bool(url) and XGPU_HOST in url


def instance_id_of(url):
    """从容器域名反解实例 ID：abc-8188.container.x-gpu.com → abc（去掉 -端口 那截）。"""
    m = re.match(r"https?://([^/:?#]+)", str(url or ""))
    if not m:
        return ""
    head = m.group(1).split(".")[0]
    parts = [p for p in head.split("-") if p and not p.isdigit()]
    return "-".join(parts)


def _token_ok(t):
    """令牌是否可用只认 whoami 结果（存过的旧令牌光看「有值」无效）。"""
    try:
        xgy_api.get_whoami(t)
        return True
    except Exception:
        return False


def read_token():
    """按「环境变量 → 全局配置 → 项目 secrets → 本机 xgy_*.json」收集候选，逐个验证取可用的，全废返回空串。

    全局配置优先于本机 sysconfig（后者可能存过失效令牌）。
    """
    cands = []
    env = os.environ.get("XGY_API_TOKEN", "")
    if env:
        cands.append(env)
    # 全局部署配置里那份（~/.workbuddy/fxai_global.json 的 xgy_api_token），跟 comfy_url / 实例 ID 放一块好维护
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            g = str((json.load(f) or {}).get("xgy_api_token") or "").strip()
    except Exception:
        g = ""
    if g:
        cands.append(g)
    try:
        p = xgy_api.find_secrets()
        if p:
            with open(p, encoding="utf-8") as f:
                d = json.load(f) or {}
            sec = d.get("default") if isinstance(d.get("default"), dict) else d
            for k in ("api_token", "token", "api_key", "access_token"):
                if (sec or {}).get(k):
                    cands.append(sec[k])
    except Exception:
        pass
    # 仙宫云管理台存的令牌在数据目录的 sysconfig/ 下（文件名 xgy_{用户ID}.json）。
    # 数据目录名与盘符由安装者自定，代码从全局配置读，不写死。
    for p in sorted(glob.glob(os.path.join(_data_dir(), "sysconfig", "xgy_*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                tok = (json.load(f) or {}).get("token")
        except Exception:
            continue
        if tok:
            cands.append(tok)
    for t in cands:
        if _token_ok(t):
            return t
    return ""


# ---------- 实例 ID 的配置（~/.workbuddy/fxai_global.json） ----------
# 用哪个实例由人定：初次配好之后固定用它，别每次都去猜。
CFG_PATH = os.environ.get(
    "FXAI_GLOBAL_CONFIG",
    os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))
CFG_KEY = "xgy_instance_id"


def _data_dir():
    """数据目录（放 sysconfig / whisper 缓存等）。读全局配置的 software_data_dir，
    读不到就用环境变量，再读不到抛错——不猜盘符。"""
    d = (_read_cfg().get("software_data_dir") or "").strip()
    d = d or os.environ.get("FXAI_DATA_DIR", "").strip()
    if not d:
        raise RuntimeError("未配置数据目录：请在 ~/.workbuddy/fxai_global.json 设 software_data_dir，"
                           "或设环境变量 FXAI_DATA_DIR")
    return d


def _read_cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def read_instance_id():
    """配置里绑的实例 ID（没有就空串）。"""
    return str((_read_cfg() or {}).get(CFG_KEY) or "").strip()


def set_instance_id(iid):
    """把实例 ID 写进 ~/.workbuddy/fxai_global.json（用哪台实例，一次定死）。"""
    iid = str(iid or "").strip()
    if not iid:
        raise ValueError("实例 ID 必填")
    d = _read_cfg()
    d[CFG_KEY] = iid
    os.makedirs(os.path.dirname(CFG_PATH) or ".", exist_ok=True)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return iid


def list_instances(token=None):
    """列当前令牌下「已有」的实例（只查已有的，不新建）。返回实例 dict 列表。"""
    token = token or read_token()
    if not token:
        raise RuntimeError(TOKEN_HELP)
    j = xgy_api.list_instances(token)
    scopes = [j] if isinstance(j, dict) else []
    for k in ("data", "result", "instance"):
        v = scopes[0].get(k) if scopes else None
        if isinstance(v, dict):
            scopes.append(v)
    for sc in scopes:
        for key in ("list", "instances", "items"):
            lst = sc.get(key)
            if isinstance(lst, list):
                return [x for x in lst if isinstance(x, dict)]
    return []


def _inst_lines(insts):
    """把实例列成人看的几行：ID｜状态｜名字｜容器地址。"""
    return "\n".join(
        "  %s   %s   %s   %s" % ((x or {}).get("id", "?"),
                                 (x or {}).get("status", "?"),
                                 (x or {}).get("name") or "",
                                 (x or {}).get("web_url") or "")
        for x in insts) or "  (无)"


def _verify_iid(iid, token):
    '查这个实例 ID 归不归当前令牌（用于确保开的是本令牌下的机器）。\n返回 True=是它的 / False=不是 / None=查不了（网络或令牌问题）。'

    try:
        insts = list_instances(token)
    except Exception:
        return None
    return any(str((x or {}).get("id") or "").strip() == iid for x in insts)


def resolve_instance_id(url="", token=None):
    """解析该管哪个实例，依次：配置 xgy_instance_id > 地址域名反解 > 令牌下唯一那台。

    只有一台能自动用；多台或查不到就抛错，让人 `use <实例ID>` 定死（由人选，不猜）。
    """
    token = token or read_token()
    if not token:
        raise RuntimeError(TOKEN_HELP)

    cfg = read_instance_id()
    if cfg:
        # 配了但已不属于当前令牌（可能销毁了 / 换了令牌）→ 列出来让人重选
        if _verify_iid(cfg, token) is False:
            raise RuntimeError(
                "配置里的实例 %s 不在当前访问令牌名下（可能已销毁、或换了令牌）。"
                "当前令牌下的实例：\n%s\n选一台写进配置：python xgy_guard.py use <实例ID>"
                % (cfg, _inst_lines(list_instances(token))))
        return cfg

    iid = instance_id_of(url)
    if iid:
        return iid

    try:
        insts = list_instances(token)
    except Exception as e:
        raise RuntimeError("查实例列表失败（%s），没法判断该开机的是哪台。先 python xgy_guard.py list 看一眼，"
                           "再 python xgy_guard.py use <实例ID> 把实例 ID 绑死。" % e) from None
    if not insts:
        raise RuntimeError("当前令牌下没有任何实例（只查已有的，不会新建）。先去仙宫云控制台建一台，"
                           "或确认令牌对不对，再 python xgy_guard.py use <实例ID> 绑到那台上。")
    if len(insts) == 1:
        only = str(insts[0].get("id") or "").strip()
        print("[xgy] 还没绑实例 ID，先用令牌下唯一那台：%s" % only, flush=True)
        return only
    raise RuntimeError("当前令牌下有多台实例，不确定该用哪台：\n%s\n"
                       "选一台写进配置（之后固定用它）：python xgy_guard.py use <实例ID>"
                       % _inst_lines(insts))


# 探活用的直连 opener（不吃环境变量代理），与 comfy_client._OPENER 同源做法
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(url, timeout=8):
    """探活：直接问 ComfyUI 的 /fxai/health（与 comfy_client.check_comfyui 同一个接口）。"""
    try:
        with _NO_PROXY_OPENER.open(str(url).rstrip("/") + "/fxai/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_online(comfy_url, wait=300, probe_timeout=8, name="服务"):
    """开 job 前保证服务在线：仙宫云地址探不通就自动开机并等 running。

    实例 ID 走 resolve_instance_id（配置 > 地址反解 > 令牌下唯一那台）。
    返回 (是否在线, 说明文字)。非仙宫云地址只探活、不代管开关机。

    name 服务名，用于启动过程提示（调用方传「ComfyUI」「TTS」等各自的名字）。
    """
    if probe(comfy_url, probe_timeout):
        return True, "%s 已在线" % name
    if not is_xgpu(comfy_url):
        return False, "%s 连不上（%s）" % (name, comfy_url)
    token = read_token()
    if not token:
        return False, TOKEN_HELP
    try:
        iid = resolve_instance_id(comfy_url, token)
    except RuntimeError as e:
        return False, str(e)
    if not iid:
        return False, "看不出实例 ID（%s），没法自动开机" % comfy_url

    # 开机：遇平台 GPU 不足会自动持续抢卡直到抢到（见 ensure_booted）
    print("[xgy] %s 正在启动，预计 1~2 分钟，正在检测中…" % name, flush=True)
    try:
        ensure_booted(iid, token=token, wait=0, name=name)
    except RuntimeError as e:
        return False, str(e)
    print("[xgy] ✓ %s 已启动，开始继续后面的任务" % name, flush=True)
    # 开机即立「收工契约」：本次任务整套做完、准备交付之前，必须调 finish()（或 xgy_guard.py hint）
    # 问用户是否关实例。
    print("[xgy] 收工契约：本次任务全部做完、准备交付之前，务必调 finish() 或直接问用户是否关实例 —— "
          "① 释放 GPU（默认推荐，停计费，下次启动会慢）② 普通关机保 GPU（仍计费，下次基本秒起）。"
          "只问整套流程做完那一次，中间子任务跑完时跳过。", flush=True)
    if not probe(comfy_url, 8):
        print("[xgy] %s 正在启动中（容器已就绪，端口检测中）…" % name, flush=True)
        import time as _t
        for _ in range(20):
            _t.sleep(10)
            if probe(comfy_url, 8):
                break
    return probe(comfy_url, 8) or True, "已自动开机（实例 %s）" % iid


def ensure_booted(iid="", token=None, wait=0, name="服务", url=""):
    """保证实例进入 running：探不通就开机；遇平台 GPU 不足(code=1003) 持续抢卡直到抢到。

    这是「仙宫云开机」的通用逻辑（生图/四视图/场景/MV/短剧/TTS/Whisper 全走它），
    所以 GPU 不足时的抢卡循环只写在这一处。期间会在标准输出反复提示
    「正在持续抢 GPU 开机」，让用户知道在抢卡、不是卡死。

    wait=0 表示不限时（一直抢到开起来为止）；>0 为最长抢卡秒数，超时抛错提示稍后重试。
    非 1003 的致命错误（实例不存在/已销毁、令牌无效）直接抛出，并在异常里引导去
    仙宫云控制台实例化或部署镜像包（见 fxai-xiangongyun-deploy 技能文档）。
    """
    token = token or read_token()
    if not token:
        raise RuntimeError(TOKEN_HELP)
    if not iid:
        iid = resolve_instance_id(url, token)
    if not iid:
        raise RuntimeError("看不出实例 ID，没法开机。先 `python xgy_guard.py use <实例ID>` 绑定。")
    import time as _t
    interval = int(os.environ.get("XGY_BOOT_INTERVAL", "5"))
    deadline = 0 if not wait else _t.time() + wait
    while True:
        try:
            xgy_api.boot(token, iid)
            print("[xgy] ✓ 实例 %s 已下发开机指令，等待进入 running…" % iid, flush=True)
            xgy_api.wait_state(token, iid, "running",
                               timeout=(max(wait, 300) if wait else 3600))
            return True
        except xgy_api.ApiError as e:
            msg = str(e)
            # 1) GPU 不足：持续抢卡，不退出
            if "1003" in msg or "可用GPU" in msg or "GPU不足" in msg or "GPU 不足" in msg:
                print("[xgy] ⏳ 仙宫云 GPU 暂时不足（code=1003），正在持续抢 GPU 开机"
                      "（每 %ds 重试，直到抢到为止）…" % interval, flush=True)
                if deadline and _t.time() > deadline:
                    raise RuntimeError(
                        "抢卡超时（%ds）仍未开起来实例 %s；平台 GPU 持续不足，请稍后重试，"
                        "或到仙宫云控制台查看实例状态。" % (wait, iid))
                _t.sleep(interval)
                continue
            # 2) 实例不存在/已销毁：引导去实例化（部署镜像包）
            if ("404" in msg or "不存在" in msg or "已销毁" in msg or "资源不存在" in msg
                    or "not found" in msg.lower() or "not exist" in msg.lower()):
                raise RuntimeError(
                    "仙宫云实例 %s 不存在或已销毁。请先到仙宫云控制台实例化（部署镜像包），"
                    "再 `python xgy_guard.py use <实例ID>` 绑定；\n"
                    "部署/排障说明见 fxai-xiangongyun-deploy 技能"
                    "（参考 02-部署清单与下载管线.md / 03-装完与排障.md）。" % iid)
            # 3) 其它：原样抛出
            raise
    return False


def ensure_service(url, probe_fn, wait=300, name="服务"):
    """保证某个 http 服务可用：探不通就自动开机，开机后再等它真正就绪。

    ComfyUI（8188）与 CosyVoice（8182）等云端服务共用本函数。

    url       服务地址。云端容器地址会自动开机；本机地址只探测，不代管开关机。
    probe_fn  (url) -> bool，探测该服务是否已就绪
    wait      开机后等就绪的最长秒数
    name      服务名，用于打印
    返回 (是否可用, 说明)
    """
    if probe_fn(url):
        return True, "%s 已就绪" % name
    ok, msg = ensure_online(url, wait=wait, name=name)
    if not ok:
        return False, msg
    import time as _t
    deadline = _t.time() + wait
    while _t.time() < deadline:
        if probe_fn(url):
            return True, "%s 已就绪（已自动开机）" % name
        print("[xgy] %s 正在启动中…" % name, flush=True)
        _t.sleep(5)
    return False, "%s 开机后仍未就绪" % name


def stop(iid="", release=True, token=None):
    """收工关机。release=True → 关机并释放 GPU（停计费，下次开机要重新分配 GPU、启动变慢）；
    release=False → 普通关机（GPU 保留照常计费，下次基本秒起）。

    iid 留空时按 resolve_instance_id 解析（配置绑的那台优先）。
    """
    token = token or read_token()
    if not token:
        raise RuntimeError(TOKEN_HELP)
    if not iid:
        iid = resolve_instance_id(token=token)
    if not iid:
        raise RuntimeError("缺少实例 ID，没法关机")
    if release:
        xgy_api.shutdown_release_gpu(token, iid)
        return "已关机并释放 GPU（停止计费，下次启动会慢一些）"
    xgy_api.shutdown(token, iid)
    return "已关机（GPU 保留，仍计费，下次基本秒起）"


# 收工询问：整套流程（生成图片 / 视频 / MV / 短剧…）全部做完的那一步才问，
# 中间每个子任务跑完都不问。只管仙宫云地址，本机或其它地址连字都不打。
ASK_TIP = (
    "本次跑在仙宫云，整套流程已全部做完，问用户是否关实例（只给下面两档，不给开放题）：\n"
    "    1) 释放 GPU（默认、推荐）—— 停计费，下次要重新分配 GPU + 加载镜像，明显变慢\n"
    "       python xgy_guard.py stop <comfy地址>\n"
    "    2) 普通关机 —— GPU 保留、仍计费，下次基本秒起，只适合马上还要接着用\n"
    "       python xgy_guard.py stop <comfy地址> --keep-gpu\n"
    "  默认按第 1 档走（长时间不用就该释放 GPU 省这笔钱）；只有用户明确说要马上接着用才选第 2 档。"
)


def ask_hint(comfy_url=None, token=None):
    """整套流程跑完时调一下：只在仙宫云地址打印上面那段，别的地址什么都不做。."""
    url = comfy_url or str((_read_cfg() or {}).get("comfy_url") or "")
    if not is_xgpu(url):
        return False
    print("[xgy] " + ASK_TIP, flush=True)
    return True


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="仙宫云实例看门狗：list 列已有实例 / use 绑实例ID / ensure 开工前开机 / hint 收工询问 / stop 关机")
    ap.add_argument("act", choices=["ensure", "stop", "list", "use", "hint", "id"],
                    help="list=令牌下已有实例 / use=把实例ID写进配置 / ensure=开工前保证在线 / "
                         "hint=整套跑完的询问文案 / stop=关机 / id=反解实例 ID")
    ap.add_argument("url", nargs="?", default="", help="ensure|stop|id: ComfyUI 地址；use: 实例 ID")
    ap.add_argument("--iid", default="", help="stop: 实例 ID（不给则从配置 / 地址解析）")
    ap.add_argument("--keep-gpu", action="store_true", help="stop: 用普通关机（保留 GPU、仍计费、下次秒起）")
    ap.add_argument("--wait", type=int, default=300, help="ensure: 等开机的秒数")
    a = ap.parse_args()

    if a.act == "id":
        print(instance_id_of(a.url) or "(解析不出实例 ID)")
        return
    if a.act == "list":
        insts = list_instances()
        print("当前令牌下已有实例 %d 台：" % len(insts))
        print(_inst_lines(insts))
        print("选一台绑进配置：python xgy_guard.py use <实例ID>")
        return
    if a.act == "use":
        if not a.url:
            raise SystemExit("用法：python xgy_guard.py use <实例ID>")
        print("已把实例 %s 写进 %s（之后固定用它）" % (set_instance_id(a.url), CFG_PATH))
        return
    if a.act == "hint":
        ask_hint(a.url)
        return
    if a.act == "ensure":
        ok, msg = ensure_online(a.url, wait=a.wait)
        print(("OK " if ok else "FAIL ") + msg)
        sys.exit(0 if ok else 2)
    print(stop(a.iid or instance_id_of(a.url), release=not a.keep_gpu))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
