#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPU 提交前门禁 + 500/OOM 自我修复。

判据正文见 references/01-产出铁律.md「GPU 提交前门禁」。
一处实现多处指针：提交公共层在 POST /prompt 前调用 gate()，失败时调用 self_heal()。
内置 POST /free 只置标记、由 prompt_worker 下轮消费，本门禁以同步硬回收接口为准。

顺序（用户口径）：释放前先探测 → 残留低于阈值直接放行 → 超阈值才卸载一次
→ 立刻验证 → 达标即走，验证未过才进入下一轮。500 / OOM 走 self_heal() 自愈重投。

用法：
    python -B check_gpu_gate.py [--url URL] [--wait-queue 秒] [--retries N]
                                [--phys-device N] [--report 路径] [--json]
    python -B check_gpu_gate.py --heal --error "HTTP 500" [--json]

退出码：0 放行 / 2 门禁未过（停手） / 3 队列被占用且不等 / 4 服务不健康
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True

GUARD_STATUS = "/fxai/guard/status"
GUARD_RECOVER = "/fxai/guard/recover"
GUARD_PREFLIGHT = "/fxai/guard/preflight"

# 「残留显存」= 物理卡占用（本地）或 进程内占用减去空载固有开销（远端）。
# 空载时进程内约 1.11 G 是 CUDA 上下文 + aimdo 虚拟地址保留，属固有开销。
SKIP_RECOVER_GIB = float(os.environ.get("FXAI_GATE_SKIP_GIB", "1.0"))
IDLE_USED_GIB = float(os.environ.get("FXAI_GATE_IDLE_GIB", "1.2"))
PHYS_MAX_GIB = float(os.environ.get("FXAI_GATE_PHYS_GIB", "1.0"))
MIN_FREE_RATIO = float(os.environ.get("FXAI_GATE_FREE_RATIO", "0.90"))
MAX_PINNED_GIB = float(os.environ.get("FXAI_GATE_PINNED_GIB", "0.01"))
STABLE_DELTA_GIB = float(os.environ.get("FXAI_GATE_DELTA_GIB", "0.10"))
RETRIES = int(os.environ.get("FXAI_GATE_RETRIES", "2"))
MAX_HEAL = int(os.environ.get("FXAI_GATE_MAX_HEAL", "1"))

OOM_HINTS = (
    "aimdo memory compile error", "hostbuffer", "host buffer",
    "out of memory", "cuda error", "cuda out of memory",
    "memory allocation", "not enough memory", "pinned memory",
    "acceleratorerror", "显存", "内存不足",
)

EXIT_OK = 0
EXIT_GATE = 2
EXIT_QUEUE = 3
EXIT_OFFLINE = 4

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class GateError(RuntimeError):
    """门禁未过，携带退出码；调用方据此停手。"""

    def __init__(self, message, code=EXIT_GATE):
        super().__init__(message)
        self.code = code


def default_base():
    """地址取全局配置的 comfy_url，缺失时回落本机地址。"""
    cfg = os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            u = json.load(f).get("comfy_url")
        if u:
            return str(u).rstrip("/")
    except (OSError, ValueError, AttributeError):
        pass
    return "http://127.0.0.1:8188"


def default_phys_device():
    """物理卡序号取环境变量；取不到返回 None，此时按进程内占用增量判定。"""
    raw = os.environ.get("FXAI_GATE_PHYS_DEVICE",
                         os.environ.get("FXAI_CUDA_DEV", ""))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def req(base, method, path, payload=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    with _OPENER.open(r, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else {"raw": raw}


def is_local(base):
    host = (urllib.parse.urlparse(base).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def nvidia_info(device):
    """本机物理卡实况（GiB）；取不到返回空列表，此项只作交叉打印与残留判据。"""
    if device is None:
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20).stdout
        rows = []
        for line in out.splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 4 and parts[0].isdigit() and parts[2].isdigit():
                rows.append({"index": int(parts[0]), "name": parts[1],
                             "used_gib": int(parts[2]) / 1024.0,
                             "total_gib": int(parts[3]) / 1024.0})
        return rows
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def nvidia_used(device):
    rows = nvidia_info(device)
    return next((r["used_gib"] for r in rows if r["index"] == device), None)


def wait_online(base, max_wait=300, phys_device=None):
    """探活；5xx 时先做一次显存自愈再复查，仍不通即把启动方式交回给人。"""
    t0 = time.time()
    healed = False
    while True:
        try:
            req(base, "GET", "/system_stats", timeout=8)
            return
        except urllib.error.HTTPError as exc:
            if 500 <= exc.code < 600 and not healed:
                healed = True
                try:
                    h = self_heal(base=base, tag="探活自愈",
                                  error_text="HTTP %d @ /system_stats" % exc.code,
                                  http_code=exc.code, phys_device=phys_device)
                except Exception as exc2:
                    h = {"healed": False, "reason": "自愈探测异常 %r" % (exc2,)}
                if h.get("healed"):
                    continue
            raise GateError("服务不健康（HTTP %s），启动方式交回给人；%s"
                            % (exc.code, h.get("reason") if healed else ""),
                            EXIT_OFFLINE) from None
        except Exception as exc:
            if time.time() - t0 >= max_wait:
                raise GateError("服务不健康，启动方式交回给人：%r" % (exc,),
                                EXIT_OFFLINE) from None
            time.sleep(10)


def wait_queue_empty(base, max_wait):
    t0 = time.time()
    while True:
        q = req(base, "GET", "/queue", timeout=10)
        running = len(q.get("queue_running") or [])
        pending = len(q.get("queue_pending") or [])
        if running == 0 and pending == 0:
            return {"running": 0, "pending": 0, "waited": int(time.time() - t0)}
        if time.time() - t0 >= max_wait:
            return {"running": running, "pending": pending,
                    "waited": int(time.time() - t0)}
        time.sleep(15)


def unload(base, reason):
    """同步硬回收全部模型；任一步失败即视为 CUDA 上下文已脏。"""
    rec = req(base, "POST", GUARD_RECOVER,
              {"reason": reason, "shrink": False}, timeout=180)
    steps = rec.get("steps") or []
    failed = [s.get("step") for s in steps if not s.get("ok")]
    return {"ok": bool(rec.get("ok")) and not failed, "failed": failed,
            "steps": steps}


def sample(base, phys_device):
    """一次采集：守卫状态 + 本机物理卡实况 + 错误日志摘要。"""
    st = req(base, "GET", GUARD_STATUS, timeout=20)
    v = st.get("vram") or {}
    free, total = v.get("free_gb"), v.get("total_gb")
    numeric = isinstance(free, (int, float)) and isinstance(total, (int, float)) and total > 0
    hw = nvidia_info(phys_device) if is_local(base) else []
    phys = next((r["used_gib"] for r in hw if r["index"] == phys_device), None)
    return {
        "free_gb": free, "total_gb": total,
        "used_gb": (total - free) if numeric else None,
        "free_ratio": (free / total) if numeric else None,
        "pinned_gb": st.get("pinned_gb"),
        "recover_count": st.get("recover_count"),
        "models_loaded": st.get("models_loaded"),
        "residual_gib_service": st.get("residual_gib"),
        "phys_used_gib": phys,
        "hardware": hw,
        "last_error": st.get("last_error"),
        "last_error_kind": st.get("last_error_kind"),
        "error_log": st.get("error_log"),
        "guard_installed": bool(st.get("installed")),
        "errors_seen": st.get("errors_seen"),
    }


def residual_gib(cur):
    """残留显存：优先取物理卡实况，其次取守卫算好的增量，最后按进程内空载基线推算。"""
    phys = cur.get("phys_used_gib")
    if isinstance(phys, (int, float)):
        return float(phys)
    svc = cur.get("residual_gib_service")
    if isinstance(svc, (int, float)):
        return float(svc)
    used = cur.get("used_gb")
    if isinstance(used, (int, float)):
        return max(0.0, float(used) - IDLE_USED_GIB)
    return None


def is_clean(cur, skip_gib=SKIP_RECOVER_GIB, free_min=MIN_FREE_RATIO,
             pinned_max=MAX_PINNED_GIB):
    """放行判据：残留显存低于阈值 + 空闲占比达标 + staged 记账归零。"""
    res = residual_gib(cur)
    ratio = cur.get("free_ratio")
    pinned = cur.get("pinned_gb")
    checks = {
        "有设备级读数": res is not None and ratio is not None,
        "残留显存达标": res is not None and res <= skip_gib,
        "空闲占比达标": ratio is not None and ratio >= free_min,
        "staged记账归零": isinstance(pinned, (int, float)) and pinned <= pinned_max,
    }
    return all(checks.values()), checks


def evaluate(cur, prev=None, phys_max=PHYS_MAX_GIB, free_min=MIN_FREE_RATIO,
             pinned_max=MAX_PINNED_GIB, delta_max=STABLE_DELTA_GIB):
    """兼容旧调用：一轮采样即可判定；prev 只用于记录占用变化。"""
    ok, checks = is_clean(cur, phys_max, free_min, pinned_max)
    if prev is not None and isinstance(cur.get("used_gb"), (int, float)):
        checks["占用变化_G"] = round(cur["used_gb"] - prev, 3)
        checks["变化在阈值内"] = abs(cur["used_gb"] - prev) <= delta_max
    return ok, checks


def looks_like_memory_error(text):
    low = (text or "").lower()
    return any(h in low for h in OOM_HINTS)


def self_heal(base="", tag="", error_text="", http_code=0, phys_device=None,
              skip_gib=SKIP_RECOVER_GIB, free_min=MIN_FREE_RATIO,
              pinned_max=MAX_PINNED_GIB):
    """500 / OOM 自我修复：读显存实况与错误日志判因 → 卸载一次 → 验证回落 → 交回调用方重投。

    返回 healed=True 时调用方立即重投；need_restart=True 时停手，把重启交回给人。
    """
    base = (base or default_base()).rstrip("/")
    if phys_device is None:
        phys_device = default_phys_device()
    out = {"tag": tag, "base": base, "healed": False, "need_restart": False,
           "reason": "", "http_code": http_code, "error": (error_text or "")[:800],
           "before": {}, "after": {}, "recover": {}, "checks": {}}
    try:
        st = req(base, "GET", GUARD_STATUS, timeout=20)
    except Exception as exc:
        out["need_restart"] = True
        out["reason"] = "守卫状态接口不可达（%r），服务不健康，启动方式交回给人" % (exc,)
        return out

    before = sample(base, phys_device)
    out["before"] = before
    last_error = (st.get("last_error") or "")
    err_log = " ".join(str(x) for x in (st.get("error_log") or []))
    mem_hint = (looks_like_memory_error(error_text)
                or looks_like_memory_error(last_error)
                or looks_like_memory_error(err_log))
    res = residual_gib(before)
    pressure = res is not None and res > skip_gib
    pinned = before.get("pinned_gb")
    staged = isinstance(pinned, (int, float)) and pinned > pinned_max

    if http_code >= 500 and not (mem_hint or pressure or staged):
        out["need_restart"] = True
        out["reason"] = ("HTTP %d 但显存实况正常（残留 %s GiB）且无显存错误日志，"
                         "判为服务不健康，启动方式交回给人" % (http_code, res))
        return out
    if not (mem_hint or pressure or staged or http_code >= 500):
        out["reason"] = "错误非显存类且显存已回落，交回调用方按原错误处理"
        return out

    rec = unload(base, "%s·500/OOM 自愈卸载全部模型" % (tag or "提交链"))
    out["recover"] = {"ok": rec["ok"], "failed": rec["failed"]}
    if not rec["ok"]:
        out["need_restart"] = True
        out["reason"] = "自愈卸载有失败步骤 %s，CUDA 上下文已脏，停手请人重启" % rec["failed"]
        return out
    time.sleep(3)
    after = sample(base, phys_device)
    ok, checks = is_clean(after, skip_gib, free_min, pinned_max)
    out["after"] = after
    out["checks"] = checks
    if ok:
        out["healed"] = True
        out["reason"] = ("判为显存类（错误日志=%s / 残留 %.2f GiB），已卸载全部模型，"
                         "显存回落 %.2f GiB，允许重投一次"
                         % ("命中" if mem_hint else "未命中", res or 0.0,
                            residual_gib(after) or 0.0))
    else:
        out["need_restart"] = True
        out["reason"] = ("卸载一次后显存仍未回落（空闲比 %s / pinned %s），"
                         "停手请人重启，重投会再次堆积" % (after.get("free_ratio"),
                                                    after.get("pinned_gb")))
    return out


def gate(base="", wait_queue=3600, retries=RETRIES, tag="run",
         phys_device=None, reason="提交前卸载全部模型",
         skip_gib=SKIP_RECOVER_GIB, free_min=MIN_FREE_RATIO,
         pinned_max=MAX_PINNED_GIB):
    """释放前先探测；已干净直接放行，超阈值才卸载，一轮验证即走。"""
    base = (base or default_base()).rstrip("/")
    if phys_device is None:
        phys_device = default_phys_device()
    out = {"tag": tag, "base": base, "local": is_local(base),
           "reason": reason, "samples": [], "steps": {}, "ok": False,
           "message": "", "recover_calls": 0}

    wait_online(base, phys_device=phys_device)
    out["steps"]["探活"] = "正常"
    q = wait_queue_empty(base, wait_queue)
    out["steps"]["队列"] = q
    if q["running"] or q["pending"]:
        raise GateError("队列占用 running=%s pending=%s，排队等它结束，不打断、不抢占"
                        % (q["running"], q["pending"]), EXIT_QUEUE)

    status = req(base, "GET", GUARD_STATUS, timeout=20)
    if not status.get("installed"):
        raise GateError("服务端缺少显存守卫能力，无法验证全量卸载，停手请人补环境")

    before = sample(base, phys_device)
    ok, checks = is_clean(before, skip_gib, free_min, pinned_max)
    before["轮次"] = 0
    before["阶段"] = "释放前探测"
    before["判据"] = checks
    out["samples"].append(before)
    if ok:
        out["steps"]["卸载"] = "跳过（释放前探测已低于阈值）"
        out["ok"] = True
        out["message"] = ("释放前探测残留 %.2f GiB ≤ %.2f GiB，模型已卸载，"
                          "跳过回收直接提交（0 次卸载）"
                          % (residual_gib(before) or 0.0, skip_gib))
        return out

    out["steps"]["卸载"] = "释放前探测残留 %.2f GiB > %.2f GiB，开始回收" % (
        residual_gib(before) or 0.0, skip_gib)
    for i in range(1, retries + 1):
        rec = unload(base, reason)
        out["recover_calls"] += 1
        out["steps"]["卸载轮次%d" % i] = {"ok": rec["ok"], "failed": rec["failed"]}
        if not rec["ok"]:
            raise GateError("卸载全部模型有失败步骤 %s，CUDA 上下文可能已脏，停手请人重启"
                            % rec["failed"])
        time.sleep(2)
        cur = sample(base, phys_device)
        ok, checks = is_clean(cur, skip_gib, free_min, pinned_max)
        cur["轮次"] = i
        cur["阶段"] = "卸载后验证"
        cur["判据"] = checks
        out["samples"].append(cur)
        if ok:
            out["ok"] = True
            out["message"] = ("第 %d 轮卸载后残留 %.2f GiB 达标，立即放行（共 %d 次卸载）"
                              % (i, residual_gib(cur) or 0.0, out["recover_calls"]))
            return out
    last = out["samples"][-1]
    raise GateError("连续 %d 轮卸载后显存仍未回落（残留 %s GiB、空闲比 %s、pinned %s），"
                    "停手请人重启，重投会继续堆积"
                    % (retries, residual_gib(last), last.get("free_ratio"),
                       last.get("pinned_gb")))


def main():
    ap = argparse.ArgumentParser(description="GPU 提交前门禁 / 500 自愈")
    ap.add_argument("--url", default="", help="ComfyUI 地址；缺省取全局配置")
    ap.add_argument("--wait-queue", type=int, default=3600, help="队列占用时的等待上限秒")
    ap.add_argument("--retries", type=int, default=RETRIES, help="卸载轮数上限")
    ap.add_argument("--phys-device", type=int, default=None,
                    help="本机 nvidia-smi 物理卡序号；给出才取硬件实况")
    ap.add_argument("--skip-gib", type=float, default=SKIP_RECOVER_GIB)
    ap.add_argument("--free-min", type=float, default=MIN_FREE_RATIO)
    ap.add_argument("--pinned-max", type=float, default=MAX_PINNED_GIB)
    ap.add_argument("--heal", action="store_true", help="走 500/OOM 自愈分支")
    ap.add_argument("--error", default="", help="自愈分支的错误文本")
    ap.add_argument("--http-code", type=int, default=0)
    ap.add_argument("--report", default="", help="验收记录 JSON 落点（项目临时目录）")
    ap.add_argument("--json", action="store_true", help="结果以 JSON 打印")
    a = ap.parse_args()

    out = {"ok": False, "message": "", "samples": [], "steps": {}}
    code = EXIT_OK
    try:
        if a.heal:
            out = self_heal(base=a.url, tag="命令行自愈", error_text=a.error,
                            http_code=a.http_code, phys_device=a.phys_device,
                            skip_gib=a.skip_gib, free_min=a.free_min,
                            pinned_max=a.pinned_max)
            out["message"] = out["reason"]
            code = EXIT_OK if out["healed"] else EXIT_GATE
        else:
            out = gate(base=a.url, wait_queue=a.wait_queue, retries=a.retries,
                       phys_device=a.phys_device, skip_gib=a.skip_gib,
                       free_min=a.free_min, pinned_max=a.pinned_max)
    except GateError as exc:
        out["message"] = str(exc)
        code = exc.code
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out["message"] = "接口读取失败：%r" % (exc,)
        code = EXIT_OFFLINE
    finally:
        if a.report:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
                with open(a.report, "w", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
            except OSError as exc:
                print("[gpu-gate] 验收记录写入失败：%r" % (exc,), file=sys.stderr)

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("[gpu-gate] %s；退出码 %d" % (out.get("message") or "放行", code))
        for s in out.get("samples") or []:
            print("  %s 轮次 %s 残留 %s GiB 空闲比 %s pinned %s 达标 %s"
                  % (s.get("阶段"), s.get("轮次"), residual_gib(s),
                     s.get("free_ratio"), s.get("pinned_gb"),
                     all(v for k, v in (s.get("判据") or {}).items()
                         if isinstance(v, bool))))
    return code


if __name__ == "__main__":
    sys.exit(main())
