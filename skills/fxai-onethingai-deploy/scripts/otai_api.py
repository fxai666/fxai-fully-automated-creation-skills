"""OneThingAI 实例管理 API 客户端。

与仙宫云 xgy_api.py 是同一套骨架，但**接口完全不同**，不是换域名就能跑：
  · 成功码是 code=0（不是 200，也没有 success 字段）
  · 实例开关机是 PUT，卸载是 DELETE —— 不是 POST
  · 实例列表必须带 page / pageSize，没有默认值
  · 资源是否可建要查 /api/v2/resources 的 maxGpuNum>0，不得只看 GPU 型号在不在
  · webUIAddress 常常是空串，ComfyUI 地址要从 customPort 的 subDomain 拼
鉴权只有一个头：Authorization: Bearer <开发者密钥>。

**这里刻意不封装「卸载实例」**：那不可恢复，技能侧不该有能力一键调它。
需要销毁时人工去控制台操作。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from otai_cfg import api_token

BASE = "https://api-lab.onethingai.com"

# 凤希自有镜像（区域 8 = 我们的机器所在区域）。建实例默认用它。
#
# ⚠ 镜像 ID 必须带 PRIVATE_ 前缀才是完整 ID：
#     完整：PRIVATE_cz80fhheabvmlvio-ev1hdtjm-ne1i54io
#     裸 ID：cz80fhheabvmlvio-ev1hdtjm-ne1i54io  ← 传这个会查不到资源
#   控制台上可能只显示后半段，那是展示形式，不是完整 ID。
OWN_IMAGE_ID = "PRIVATE_cz80fhheabvmlvio-ev1hdtjm-ne1i54io"
OWN_IMAGE_NAME = "凤希AI全自动创作全能镜像包"
OWN_REGION_ID = 8
DEFAULT_DEPLOY_NAME = "凤希AI"
DEFAULT_GPU = "NVIDIA-GEFORCE-RTX-4090"

# 平台限制：customPort 最多 5 个公网端口，超出只前 5 个生效
MAX_CUSTOM_PORT = 5

# 统一服务端口表 —— 与仙宫云（tool/xgy 管理台 / fxai-xiangongyun-deploy）完全同一套，不另立。
# 建实例时一次性申请这 4 个，管理台卡片按同一张表逐行显示服务地址。
# 顺序即页面显示顺序：ComfyUI 在最上。
SERVICE_PORTS = [
    (8188, "ComfyUI", "comfy_url / comfy_cloud_url"),
    (8086, "千问语音", "tts.engines.qwen3tts.cloud_url"),
    (8182, "CosyVoice3", "tts.engines.cosyvoice.cloud_url"),
    (8185, "Whisper转写", "whisper.cloud_url"),
]
# 拼给 --port 用的字符串（create 时直接传）
SERVICE_PORT_ARG = " ".join(f"{p},http" for p, _, _ in SERVICE_PORTS)


# 实例状态码
STATES = {
    100: "部署中", 200: "开机中", 300: "运行中", 400: "关机中",
    500: "重置中", 600: "更换镜像中", 700: "释放资源中", 800: "已关机",
}
RUNNING, STOPPED = 300, 800


class ApiError(RuntimeError):
    pass


def get_token(args=None):
    t = getattr(args, "token", "") if args else ""
    if t:
        return t
    t = api_token()
    if not t:
        raise SystemExit(
            "未设置 OneThingAI 开发者密钥。\n"
            "  · 登录 OneThingAI 平台 → 账户中心 → 开发者密钥 → 自助创建\n"
            "  · 然后 --token <密钥>，或设环境变量 OTAI_API_TOKEN\n"
            "  · 或写进 ~/.workbuddy/fxai_global.json 的 otai 节点 api_token 字段")
    return t


def call(method, path, token, body=None, timeout=40, params=None):
    """统一请求。返回解包后的 data（失败抛 ApiError，带上 msg 文字）。

    坑：认证失败和「资源不存在」都可能是 HTTP 200 + 非 0 code，
    所以错误提示里必须带 msg 原文，不得只报 code。
    """
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        raise ApiError(f"HTTP {e.code} {method} {path}\n{raw[:400]}")
    except Exception as e:
        raise ApiError(f"请求失败 {method} {path}：{e}")
    try:
        j = json.loads(raw)
    except Exception:
        raise ApiError(f"返回非 JSON：{raw[:300]}")
    if j.get("code") != 0:
        raise ApiError(f"code={j.get('code')} msg={j.get('msg')}")
    return j.get("data")


# ---------------- 账号 ----------------
def get_balance(token):
    d = call("GET", "/api/v1/account/wallet/detail", token) or {}
    # 余额接口返的是字符串，必须转 float 再算，否则前端拿到 "0.00" 会显示成 NaN
    def num(k):
        try:
            return float(d.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0
    return {
        "availableBalance": num("availableBalance"),
        "availableVoucherCash": num("availableVoucherCash"),
        "consumeCashTotal": num("consumeCashTotal"),
    }


def get_consume(token, page=1, page_size=50, app_id="", business_type=None):
    d = call("GET", "/api/v2/account/wallet/consume/query", token, params={
        "page": page, "pageSize": page_size, "appId": app_id,
        "businessType": business_type}) or {}
    return d.get("orderList") or []


# ---------------- 镜像 ----------------
def list_private_images(token, app_image_name=""):
    d = call("GET", "/api/v2/app/private/image/list", token,
             params={"appImageName": app_image_name}) or {}
    return d.get("privateImageList") or []


def list_public_images(token, app_image_name="", app_image_author=""):
    d = call("GET", "/api/v1/app/publish/image/list", token, params={
        "appImageName": app_image_name, "appImageAuthor": app_image_author}) or {}
    return d.get("publishImageList") or []


def resolve_own_image_id(token, explicit="", name=OWN_IMAGE_NAME):
    """定位凤希自己的镜像 ID。显式给了就用，否则按名字在私有镜像里找。"""
    if explicit:
        return explicit, "", "显式指定"
    for it in list_private_images(token):
        if name in (it.get("appImageName") or ""):
            return it.get("appImageId") or "", it.get("appImageName") or "", "私有镜像按名匹配"
    return OWN_IMAGE_ID, OWN_IMAGE_NAME, "内置默认（未在私有镜像里找到同名）"


# ---------------- 资源 ----------------
def normalize_image_id(image_id):
    """补全镜像 ID 前缀。

    控制台常只显示后半段（如 cz80f...-ne1i54io），直接拿去查资源会返回空列表，
    看起来像「没资源」，实际是 ID 不完整。这里按已知前缀补齐。
    """
    i = (image_id or "").strip()
    if not i:
        return OWN_IMAGE_ID
    if i.startswith("PRIVATE_") or i.startswith("PUBLIC_"):
        return i
    if i.startswith("cz80") or "-" in i and i.endswith(("io", "j", "k")) and "_" not in i:
        return "PRIVATE_" + i
    return i


def list_resources(token, app_image_id=OWN_IMAGE_ID, region_id=None, gpu_type=None):
    """查资源。**只有 maxGpuNum>0 的组合真能建实例**，其余一律跳过。"""
    d = call("GET", "/api/v2/resources", token, params={
        "appImageId": normalize_image_id(app_image_id), "regionId": region_id,
        "gpuType": gpu_type}) or {}
    return d.get("resourceList") or []


def available_slots(token, app_image_id=OWN_IMAGE_ID, region_id=OWN_REGION_ID):
    """返回当前真能建的 (gpuType, regionId, maxGpuNum) 列表，已过滤掉 0。"""
    rows = list_resources(token, app_image_id, region_id)
    return [r for r in rows if int(r.get("maxGpuNum") or 0) > 0]


def is_gpu_shortage(exc):
    """资源不足的报错识别：换 msg 文字，不看 code（code 不唯一）。"""
    m = str(exc)
    return any(k in m for k in (
        "资源", "不足", "无资源", "没有资源", "无可用", "库存", "售罄", "已满", "gpuType"))


# ---------------- 实例 ----------------
def list_instances(token, page=1, page_size=100, app_id="", status=None,
                   gpu_type=None, app_image_id=None, group_id=None):
    d = call("GET", "/api/v2/app", token, params={
        "page": page, "pageSize": page_size, "appId": app_id, "status": status,
        "gpuType": gpu_type, "appImageId": app_image_id, "groupId": group_id}) or {}
    return d.get("appList") or [], (d.get("pagination") or {})


def get_instance(token, app_id):
    lst, _ = list_instances(token, app_id=app_id)
    for it in lst:
        if it.get("appId") == app_id:
            return it
    raise ApiError(f"查不到实例 {app_id}（可能已被释放）")


def exists_instance(token, app_id):
    try:
        get_instance(token, app_id)
        return True
    except Exception:
        return False


def resolve_own_instance_id(token, explicit="", name_keyword="凤希"):
    """定位凤希自己的运行中实例（按 customName 匹配）。"""
    if explicit:
        return explicit, "显式指定"
    lst, _ = list_instances(token)
    for it in lst:
        if name_keyword in (it.get("customName") or ""):
            return it.get("appId") or "", f"按名匹配「{name_keyword}」"
    for it in lst:
        if int(it.get("status") or 0) == RUNNING:
            return it.get("appId") or "", "无同名，取唯一一台运行中的"
    return "", "没找到"


def create_instance(token, app_image_id, gpu_type=DEFAULT_GPU, gpu_num=1,
                     region_id=OWN_REGION_ID, bill_type=3, duration=0,
                     group_id="", custom_port=None, envs=None, name=""):
    body = {
        "appImageId": normalize_image_id(app_image_id),
        "billType": int(bill_type),
        "gpuNum": int(gpu_num),
        "regionId": int(region_id),
        "gpuType": gpu_type,
        "duration": int(duration or 0),
        "groupId": group_id or "",
    }
    if envs:
        body["envs"] = envs
    if custom_port:
        body["customPort"] = custom_port
    d = call("POST", "/api/v2/app", token, body=body, timeout=60) or {}
    out = {"appId": d.get("appId") or "", "groupId": d.get("groupId") or ""}
    if name and out["appId"]:
        # 文档未列改名接口，名字只能随建实例时的默认带；这里仅回显提示
        out["_nameHint"] = name
    return out


def boot(token, app_id):
    return call("PUT", f"/api/v1/app/operate/boot/{app_id}", token, timeout=60)


def shutdown(token, app_id):
    return call("PUT", f"/api/v1/app/operate/shutdown/{app_id}", token, timeout=60)


def wait_state(token, app_id, want=RUNNING, timeout=600, interval=10):
    """轮询到目标状态。注意关机有 400 过渡态，实例正在切换时直接等。"""
    want_name = "运行中" if want == RUNNING else "已关机"
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            st = int((get_instance(token, app_id) or {}).get("status") or 0)
        except Exception as e:
            last = f"查询失败：{e}"
            time.sleep(interval)
            continue
        label = STATES.get(st, str(st))
        if st == want:
            print(f"[{time.time()-t0:5.0f}s] 已到「{want_name}」")
            return st
        if st != last:
            print(f"[{time.time()-t0:5.0f}s] {label}")
            last = st
        time.sleep(interval)
    raise SystemExit(f"等「{want_name}」超时（{timeout}s），当前：{STATES.get(last, last)}")


# ---------------- 监控 ----------------
def monitor(token, app_id, kind, minutes=30):
    """取近 N 分钟监控。kind: cpuutil / memory / gpuutil / gpumemory。只支持近 30 分钟。"""
    minutes = max(1, min(30, int(minutes)))
    end = int(time.time())
    start = end - minutes * 60
    d = call("GET", f"/api/v1/app/{app_id}/monitor/{kind}", token,
             params={"startTime": start, "endTime": end}) or {}
    return d.get("list") or []


# ---------------- 展示 ----------------
def brief(inst):
    st = int(inst.get("status") or 0)
    return {
        "appId": inst.get("appId") or "",
        "name": inst.get("customName") or "(未命名)",
        "image": inst.get("appImageName") or "",
        "gpuType": inst.get("gpuType") or "",
        "status": st,
        "statusName": STATES.get(st, str(st)),
        "regionId": inst.get("regionId"),
        "price": inst.get("price"),
        "runtime": inst.get("runtime"),
        "errCode": inst.get("errCode"),
        "disk": f"{inst.get('systemDiskSizeUsed')}/{inst.get('systemDiskSize')} MB",
    }


def show(inst):
    b = brief(inst)
    print(f"  实例名  {b['name']}   [{b['statusName']}]")
    print(f"  ID      {b['appId']}")
    print(f"  镜像    {b['image']}")
    print(f"  GPU     {b['gpuType']}  区域 {b['regionId']}")
    print(f"  单价    {b['price']}/min   已运行 {b['runtime']}s   磁盘 {b['disk']}")
    if b["errCode"]:
        print(f"  ⚠ 错误码 {b['errCode']}")
    for line in service_urls(inst):
        print("  " + line)
    if inst.get("webUIAddress"):
        print(f"  WebUI   {inst['webUIAddress']}")


def service_urls(inst):
    """按统一端口表输出服务地址行；没映射的端口不编造，直接不列。

    与管理台 tool/otai 的卡片显示同一张表（SERVICE_PORTS），
    命令行看到的四行 = 页面上看到的四行，不会两边不一致。
    """
    ports = inst.get("customPort") or []
    by_port = {str(p.get("localPort")): p for p in ports}
    lines = []
    for port, label, cfgkey in SERVICE_PORTS:
        p = by_port.get(str(port))
        if not p or not p.get("subDomain"):
            continue
        lines.append(f"{label:<12} https://{p['subDomain']}  → {cfgkey}")
    if not lines:
        lines = ["⚠ 没有 customPort 映射 —— 服务地址拿不到。",
                 "加端口要在控制台操作（API 改不了，见 references/01）。"]
    return lines


def main():
    p = argparse.ArgumentParser(description="OneThingAI 实例管理 API 客户端")
    p.add_argument("action", help="list/info/boot/shutdown/wait/balance/consume/images/"
                                  "public-images/private-images/resources/available/"
                                  "resolve-instance/resolve-image/create/monitor/"
                                  "comfy-url/verify")
    p.add_argument("instance_id", nargs="?", default="")
    p.add_argument("--token", default="", help="开发者密钥（或用 OTAI_API_TOKEN）")
    p.add_argument("--image", default="", help="create: 镜像 ID（默认凤希自有镜像）")
    p.add_argument("--gpu", default=DEFAULT_GPU, help="create: GPU 型号（要完整名）")
    p.add_argument("--gpu-num", type=int, default=1, help="create: GPU 卡数")
    p.add_argument("--region", type=int, default=OWN_REGION_ID, help="create: 区域 ID")
    p.add_argument("--bill-type", type=int, default=3, help="create: 1包月 2包天 3按量")
    p.add_argument("--duration", type=int, default=0, help="create: 包月/包天时长")
    p.add_argument("--port", default="",
                   help="create: 自定义端口。多个用空格或逗号分隔，如 "
                        "'8188,http 8086,http 8182,http 8185,http'；"
                        "或直接用 --services 一次性申请统一四个服务端口")
    p.add_argument("--services", action="store_true",
                   help=f"create: 一次性申请统一服务端口（{' '.join(str(p) for p, _, _ in SERVICE_PORTS)}），"
                        "与管理台卡片显示的完全一致。建实例时用这个，别手工拼")
    p.add_argument("--kind", default="gpuutil", help="monitor: cpuutil/memory/gpuutil/gpumemory")
    p.add_argument("--minutes", type=int, default=30, help="monitor: 回看分钟数（上限 30）")
    p.add_argument("--timeout", type=int, default=600, help="wait 的超时秒数")
    p.add_argument("--no-wait", action="store_true", help="只下发命令就返回，不等执行完")
    p.add_argument("--yes", action="store_true",
                   help="create: 确认真建实例才加（不加只打印请求体）")
    p.add_argument("--grab", type=int, default=30, help="create: 缺卡时重试上限（0 关闭）")
    p.add_argument("--grab-interval", type=int, default=5, help="create: 抢卡间隔秒")
    a = p.parse_args()
    t = get_token(a)
    act = a.action

    if act == "balance":
        print(json.dumps(get_balance(t), ensure_ascii=False, indent=2))
        return
    if act == "consume":
        for o in get_consume(t):
            print(f"  {o.get('createdAt')}  ¥{o.get('consumeCash')}  "
                  f"{o.get('event') or ''}  app={o.get('appId') or '-'}")
        return
    if act == "public-images":
        for i in list_public_images(t)[:60]:
            print(f"  {i.get('appImageId'):<16} {i.get('appImageName')}  {i.get('appImageVersion','')}")
        return
    if act == "private-images":
        for i in list_private_images(t):
            print(f"  {i.get('appImageId'):<44} {i.get('appImageName')}  "
                  f"区域{i.get('regionId')}  {i.get('appImageTotalSize')}MB  状态{i.get('appImageStatus')}")
        return
    if act == "resources":
        print(json.dumps(list_resources(t, a.image or OWN_IMAGE_ID, a.region or None),
                         ensure_ascii=False, indent=2))
        return
    if act == "available":
        rows = available_slots(t, a.image or OWN_IMAGE_ID, a.region or OWN_REGION_ID)
        if not rows:
            print("该镜像在此区域当前无可用资源（maxGpuNum 全为 0）")
            return
        for r in rows:
            print(f"  {r.get('gpuType'):<34} 区域{r.get('regionId')}  最多 {r.get('maxGpuNum')} 卡")
        return
    if act == "resolve-image":
        iid, nm, src = resolve_own_image_id(t, a.image)
        print(f"镜像 ID：{iid}\n镜像名：{nm}\n来源：{src}")
        return
    if act == "resolve-instance":
        iid, src = resolve_own_instance_id(t, a.instance_id)
        print(f"实例 ID：{iid or '(没找到)'}\n来源：{src}")
        return
    if act == "create":
        img = a.image or OWN_IMAGE_ID
        # --services：直接用统一端口表，别手工拼（管理台卡片也用同一张表）
        raw_port = SERVICE_PORT_ARG if a.services else (a.port or "")
        # --port 支持两种写法，空格与逗号等价：
        #   "8188,http 8086,http"（--services 生成的这种）
        #   "8188,8086,8182,8185"（裸端口号列表）
        # 不得简单 replace(',', ' ')——那样 "8188,http" 会变成 "8188 http"，
        # http 变成独立一段、int() 直接炸。正确做法：先按空格切段，段内再按逗号切「端口,类型」。
        ports = []
        for seg in [s for s in raw_port.split() if s.strip()]:
            parts = [x for x in seg.split(",") if x.strip()]
            if len(parts) == 1:
                lp, ty = parts[0], "http"
            elif len(parts) == 2 and parts[0].isdigit():
                lp, ty = parts[0], parts[1]
            else:
                # 裸端口号列表：8188,8086,8182,8185 → 逐个都是「端口:http」
                if all(x.isdigit() for x in parts):
                    for x in parts:
                        ports.append({"localPort": int(x), "type": "http"})
                    continue
                raise SystemExit(f"--port 段 {seg!r} 看不懂，"
                                 f"写法：'8188,http 8086,http' 或 '8188,8086,8182,8185'")
            ports.append({"localPort": int(lp), "type": ty})
        if len(ports) > MAX_CUSTOM_PORT:
            print(f"⚠️ customPort 上限 {MAX_CUSTOM_PORT} 个，超出只前 {MAX_CUSTOM_PORT} 个生效："
                  f"已丢弃 {[p['localPort'] for p in ports[MAX_CUSTOM_PORT:]]}")
            ports = ports[:MAX_CUSTOM_PORT]
        body = {
            "appImageId": img, "billType": a.bill_type, "gpuNum": a.gpu_num,
            "regionId": a.region, "gpuType": a.gpu, "duration": a.duration,
        }
        if ports:
            body["customPort"] = ports
        print(f"镜像：{img}\n区域：{a.region}\n请求体：")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        if not a.yes:
            print("\n[演练] 建实例实时扣费，确认无误再加 --yes。")
            return
        grab_max = max(0, a.grab)
        try:
            r = create_instance(t, **body)
            attempt = 1
            while True:
                try:
                    r = create_instance(t, **body)
                    break
                except ApiError as e:
                    if not (grab_max and is_gpu_shortage(e) and attempt < grab_max):
                        raise
                    attempt += 1
                    print(f"[抢卡 {attempt-1}/{grab_max}] {e}；{a.grab_interval}s 后重试…")
                    time.sleep(a.grab_interval)
        except ApiError as e:
            print(f"\n[失败] {e}")
            if is_gpu_shortage(e):
                print("提示：可用资源查 resources/available，或换个区域/显卡型号。")
            return
        print("\n→ " + json.dumps(r, ensure_ascii=False))
        app_id = r.get("appId") or ""
        if app_id:
            print(f"\n新实例：{app_id}")
            if not a.no_wait:
                wait_state(t, app_id, RUNNING, a.timeout)
        return

    # list / monitor 之外的动作都要一个具体实例；list 是账号级的，先放行
    if act == "list":
        lst, pg = list_instances(t)
        print(f"共 {pg.get('total', len(lst))} 个实例\n")
        for it in lst:
            show(it)
            print()
        return

    if not a.instance_id:
        raise SystemExit(f"action '{act}' 需要实例 ID")
    if act == "info":
        show(get_instance(t, a.instance_id))
    elif act == "boot":
        print("→", json.dumps(boot(t, a.instance_id), ensure_ascii=False))
        if not a.no_wait:
            wait_state(t, a.instance_id, RUNNING, a.timeout)
    elif act == "shutdown":
        print("→", json.dumps(shutdown(t, a.instance_id), ensure_ascii=False))
        if not a.no_wait:
            wait_state(t, a.instance_id, STOPPED, a.timeout)
    elif act == "wait":
        wait_state(t, a.instance_id, RUNNING if (a.kind == "run") else STOPPED, a.timeout)
    elif act == "monitor":
        rows = monitor(t, a.instance_id, a.kind, a.minutes)
        if not rows:
            print("无数据（实例可能处于非运行态，或超出 30 分钟窗口）")
            return
        for r in rows[-30:]:
            print(f"  {r.get('time')}  {r.get('category'):<16} {r.get('value')}")
    elif act == "verify":
        rows = monitor(t, a.instance_id, "gpuutil", 5)
        print("GPU 采样：", json.dumps(rows[-3:], ensure_ascii=False))
        lst, _ = list_instances(t, app_id=a.instance_id)
        if lst:
            show(lst[0])
    elif act == "comfy-url":
        inst = get_instance(t, a.instance_id)
        print(json.dumps({
            "webUIAddress": inst.get("webUIAddress") or "",
            "customPort": inst.get("customPort") or [],
            "提示": "ComfyUI 端口以 customPort 里 localPort=8188 的 subDomain 为准；"
                    "镜像默认可能跑在 7860，先看 customPort 实际映射",
        }, ensure_ascii=False, indent=2))
    else:
        raise SystemExit(f"未知 action: {act}")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        msg = str(e)
        auth_hit = any(k in msg.lower() for k in
                       ("unauthorized", "forbidden", "token", "密钥", "认证", "未授权"))
        if auth_hit:
            print(f"\n[认证失败] {msg}\n"
                  "  · 密钥在 OneThingAI 平台「账户中心 → 开发者密钥」自助创建\n"
                  "  · --token <密钥> / 环境变量 OTAI_API_TOKEN / 全局配置 otai.api_token", file=sys.stderr)
            sys.exit(2)
        print(f"\n[接口报错] {msg}", file=sys.stderr)
        sys.exit(1)
