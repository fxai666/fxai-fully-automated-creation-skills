#!/usr/bin/env python
"""仙宫云开放平台 API 客户端 —— 实例生命周期 / 镜像 / 账号。

文档：https://api-playground.xiangongyun.com/
基址：https://api.xiangongyun.com/open
认证：Authorization: Bearer <访问令牌>   （不是 api_key，是控制台「访问令牌」）

接口清单（2026-10 从官方 Playground 逐个抓全，只封装常规动作）：
  实例  GET  /instances                      实例列表
        GET  /instance/{id}                  单个实例信息
        GET  /instance/{id}/images           实例储存的镜像
        POST /instance/deploy                部署实例
        POST /instance/boot                  开机（可换 GPU 型号/数量）
        POST /instance/shutdown              关机保留 GPU（照常收费）
        POST /instance/shutdown_release_gpu  关机释放 GPU（系统盘按量计费）
        POST /instance/saveimage             储存镜像
  镜像  GET  /images                         镜像列表
        GET  /image/{id}                     镜像信息
  账号  GET  /whoami                         昵称 / uuid / 脱敏手机号
        GET  /balance                        余额
        POST /recharge/order                 创建充值订单（alipay/wechat）

⚠️ 「关机并销毁 / 销毁实例 / 储存镜像并销毁 / 销毁镜像」这四个接口**一律不封装**——
   那类操作不可恢复，技能侧不提供调用入口，让实例在手滑里被销毁的风险为零。
   真要做这类动作，人去仙宫云控制台点。

**所有写操作都是异步的**：接口返回 success=true 只代表「命令已下发」，
实际结果要轮询 get_instance 看 status 字段。

用法：
  python xgy_api.py list
  python xgy_api.py info <instance_id>
  python xgy_api.py boot <instance_id> --gpu "NVIDIA GeForce RTX 4090" --gpu-count 1
  python xgy_api.py shutdown      <instance_id>     # 关机保留 GPU（还占着卡）
  python xgy_api.py release-gpu   <instance_id>     # 关机释放 GPU（省钱，系统盘按量计费）
  python xgy_api.py wait <instance_id> running      # 阻塞等到指定状态
  python xgy_api.py save-image    <instance_id>
  python xgy_api.py balance
  python xgy_api.py whoami
  python xgy_api.py public-images                    # 打印公共镜像 ID / GPU 型号枚举
  python xgy_api.py ssh <instance_id>               # 打印该实例的 SSH 连接串
  python xgy_api.py comfy-url <instance_id>         # 打印 ComfyUI 的 web_url

令牌读取优先级：
  1) --token 参数
  2) 环境变量 XGY_API_TOKEN
  3) 全局配置 ~/.workbuddy/fxai_global.json 的 xgy_api_token
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xgy_cfg import api_token, cfg_path  # noqa: E402
import urllib.error
import urllib.request

BASE = "https://api.xiangongyun.com/open"  # 官方固定基址，不给环境变量开关

# 实例 status 全部取值（文档「实例 status 状态」表）
STATUS = {
    "deploying": "正在部署",
    "running": "正在运行",
    "booting": "正在开机",
    "shutting_down": "正在关机",
    "shutdown": "已关机",
    "destroying": "正在销毁",
    "destroyed": "已销毁",
    "saving_image": "正在储存镜像",
    "freezing": "正在冻结",
    "freeze": "已冻结",
    "replacing_image": "正在更换镜像",
}

# 终态：等这些状态不会再变
TERMINAL = {"running", "shutdown", "destroyed", "freeze"}

# 写操作的「目标状态」，用来等命令执行完
TARGET = {
    "boot": "running",
    "shutdown": "shutdown",
    "release_gpu": "shutdown",
}

# 公共镜像 ID（官方文档 instance/3 列出）。deploy 的 image 字段填这个 ID。
PUBLIC_IMAGES = {
    "02b564d3-9510-4f80-b56d-409900dab8da": "Miniconda3_3.12 · ubuntu22.04 · CUDA 12.8",
    "7fd1f863-c2e2-4af6-b41b-cf92a034c2d3": "Miniconda3_3.13 · ubuntu22.04 · CUDA 12.6",
    "5bea96c9-5b0d-4531-b75d-661cc1646e0b": "Miniconda3_3.12 · ubuntu22.04 · CUDA 12.6",
    "c0cf4899-24f9-4132-a45d-4dc5f13b009b": "Miniconda3_3.10 · ubuntu22.04 · CUDA 12.4",
    "a0607405-d0fd-4efc-b34e-de0872d4a633": "Miniconda3_3.10 · ubuntu22.04 · CUDA 12.2",
    "2f98442f-1e6e-4531-8b92-88a09d5d8a20": "Miniconda3_3.10 · ubuntu22.04 · CUDA 12.2 TensorRT",
    "260cd46a-12aa-44ed-b47a-88f770b49041": "Miniconda3_3.10 · ubuntu22.04 · CUDA 12.1",
    "9115617d-3d2a-494d-af82-597319953e46": "Miniconda3_3.10 · ubuntu22.04 · CUDA 11.8",
    "8b73bf90-d6a2-4f21-9521-8aed8ea7d2c9": "Miniconda3_3.10 · centos7 · CUDA 11.4",
    "438c59fd-886b-4cda-8396-12a9cd4a3b97": "Miniconda3_3.8 · ubuntu20.04 · CUDA 11.8",
    "e7a66296-fe56-4fd1-a67d-cff5613b6372": "Miniconda3_3.8 · ubuntu20.04 · CUDA 11.1",
}

# deploy 支持的 GPU 型号（官方文档枚举）
GPU_MODELS = [
    "NVIDIA GeForce RTX 4090",
    "NVIDIA GeForce RTX 4090 D",
    "NVIDIA GeForce RTX 4090 D 48G",
]

IMAGE_TYPES = ["public", "community", "private"]

# 凤希自己的 ComfyUI 全家桶镜像：开箱即用（千问2.1 生图 / MiniMax H3 视频 / YuE2 配乐 /
# Mel-Band 人声分离 + 108 个自研节点 + 约 80GB 模型）。deploy-image 不传参数就按它建。
# 它是本账号的镜像（visibility=1、original_owner=true），image_type 用 community 可查到。
OWN_IMAGE_ID = "9463d4ca-2f86-45cf-b0a3-a81ba6f3b27c"
DEFAULT_DEPLOY_NAME = "测试"  # deploy 的 name 参数（为部署的实例设定实例名）
DEFAULT_DEPLOY_GPU = "NVIDIA GeForce RTX 4090 D"

# 凤希自己的实例：默认就写死成这台，装技能包的人不用自己去列表里挑。
# 「是咱们的」的判定 = 拿令牌查这个 ID，接口返回的 id 与它完全相等；查不到就换关键字找。
OWN_INSTANCE_ID = "lxtulpeek8ov7doj"
OWN_KEYWORD = "凤希AI"


class ApiError(RuntimeError):
    pass




def get_token(args):
    if args.token:
        return args.token
    tok = api_token()
    if tok:
        return tok
    raise SystemExit(
        "缺少访问令牌。用 --token <令牌>，或设环境变量 XGY_API_TOKEN，\n"
        f"或在全局配置 {cfg_path()} 的 xgy_api_token 字段里写。\n"
        "令牌在仙宫云控制台「访问令牌」页生成（注意不是 SSH 密码，也不是 api_key）。")


def call(method, path, token, body=None, timeout=30):
    url = f"{BASE.rstrip('/')}/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", "replace")[:300]
        raise ApiError(f"HTTP {e.code} {method} {url} → {body_txt}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"连不上 {url}：{e.reason}（检查网络/代理）") from None
    try:
        j = json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(f"返回不是 JSON：{raw[:300]}") from None
    # 统一错误判定。
    # 成功是 code=200（不是 0）且 success=true；失败是 code=1000「未找到访问令牌」这类。
    ok = j.get("success")
    if ok is None:
        # 没给 success 字段时退回看 code
        ok = j.get("code") in (0, 200)
    if not ok:
        raise ApiError(f"{method} {path} 失败：code={j.get('code')} msg={j.get('msg')}")
    return j


# ---------- 接口封装 ----------

def list_instances(t):
    return call("GET", "/instances", t)


def get_instance(t, iid):
    return call("GET", f"/instance/{iid}", t)


def boot(t, iid, gpu_model=None, gpu_count=None):
    """开机。gpu_model / gpu_count 缺省时自动从实例信息里取当前值。"""
    body = {"id": iid}
    if gpu_model or gpu_count:
        if not (gpu_model and gpu_count):
            cur = get_instance(t, iid)
            inst = pick_instance(cur, iid)
            gpu_model = gpu_model or inst.get("gpu_model")
            gpu_count = gpu_count if gpu_count is not None else inst.get("gpu_used")
        # API 要求 gpu_count 是字符串
        body["gpu_model"] = gpu_model
        body["gpu_count"] = str(gpu_count)
    return call("POST", "/instance/boot", t, body)


def shutdown(t, iid):
    """关机保留 GPU —— 卡还被占着，还按 GPU 全价计费。"""
    return call("POST", "/instance/shutdown", t, {"id": iid})


def shutdown_release_gpu(t, iid):
    """关机释放 GPU —— 最省钱的停法。系统盘按已用空间计费（约 ¥0.00003/GB/小时）。"""
    return call("POST", "/instance/shutdown_release_gpu", t, {"id": iid})


def deploy(t, **kw):
    """部署实例。

    官方文档（instance/3）的字段名，注意是 data_center_id 不是 data_center：
      gpu_model / gpu_count / data_center_id / image / image_type
      storage / storage_mount_path / ssh_key / system_disk_expand / system_disk_expand_size / name
    镜像可填公共镜像 ID、社区镜像 ID 或私有镜像 ID；公共镜像 ID 见 PUBLIC_IMAGES。

    ⚠️ gpu_count / data_center_id 必须是**整型**：传成字符串（"1"）服务端直接
    code=1000 Validate error，且不指名字段，光看报错查不出是哪个。只发 image+gpu_model
    还会被 '最少需部署1张GPU' 挡（gpu_count 是必填字段，默认值兜不住）。
    返回 data.id 就是新实例 ID（接口本身就是新建实例，不是往已有实例上盖）。
    """
    body = {k: v for k, v in kw.items() if v is not None and v != ""}
    body["gpu_count"] = int(body.get("gpu_count") or 1)
    body.setdefault("data_center_id", 1)
    body["data_center_id"] = int(body["data_center_id"])
    return call("POST", "/instance/deploy", t, body)


def is_gpu_shortage(r):
    """部署返回是否「可用 GPU 不足」（此时应触发抢卡重试，而不是判失败）。

    服务端对缺卡返回 HTTP 200 + code=1000 + msg 含「可用GPU不足」之类文字；
    和「未认证 / 镜像不存在 / 参数校验错」是同一个 code=1000，必须看 msg 文字区分。
    海外节点偶尔回英文 insufficient / no available，一并兜底。
    """
    if not isinstance(r, dict):
        return False
    if r.get("code") not in (1000, "1000"):
        return False
    msg = " ".join(str(r.get(k, "")) for k in ("msg", "message", "error", "err"))
    m = msg.lower()
    return ("gpu" in m) and (
        "不足" in msg or "insufficient" in m or "no available" in m or "unavailable" in m
    )


def _short_msg(r):
    """从部署返回里抽一句可读的错误信息，打抢卡日志用。"""
    if not isinstance(r, dict):
        return str(r)
    for k in ("msg", "message", "error", "err"):
        v = r.get(k)
        if v:
            return str(v)
    return f"code={r.get('code')}"


def comfy_host(inst, port=8188):
    """容器域名 → ComfyUI 主机名。

    坑：容器域名把暴露端口写在主机名末尾（{实例ID}-80.container.x-gpu.com），
    web_url 那个 80 是容器页面，不是 ComfyUI。要按目标端口换掉那个 -80 段
    （换成 -8188），光在后面拼 :8188 会连到 80 那个服务去。
    """
    wu = inst.get("web_url") or ""
    host = wu.split("//", 1)[-1].split("/", 1)[0].split(":")[0] if wu else ""
    host = host or inst.get("ssh_domain", "")
    import re
    return re.sub(r"-\d+\.container\.", f"-{port}.container.", host)


def price(t, iid="", image_id=""):
    """查价格明细：单个实例（或账号下所有实例）的 GPU/镜像/停机单价 + 镜像自身的 price。

    坑：仙宫云**没有按 GPU 型号报价的接口**，文档 instance/0..10 那 11 个全是列表/详情/部署/
    开关机/存镜像，没有资费类。price_per_hour 只能从「已经开过的实例」上读回来，或者从
    /image/{id} 读 image.price（那是镜像的钱，不是显卡的）。
    所以这里采样账号下所有实例，横向对比出「显卡型号 → base_price」这张表，供前端估算用。
    """
    out = {"sampled_instances": []}
    r = list_instances(t)
    src = r.get("data") or {}
    lst = src.get("list") or (src.get("data") or {}).get("list") or []
    for it in lst:
        _id = it.get("id") or it.get("instance_id") or ""
        if iid and _id != iid:
            continue
        if not _id:
            continue
        g = get_instance(t, _id)
        d = g.get("data") or {}
        inner = d.get("data") if isinstance(d.get("data"), dict) else d
        if not inner:
            continue
        out["sampled_instances"].append({
            "id": _id,
            "gpu_model": inner.get("gpu_model", ""),
            "base_price": inner.get("base_price"),
            "image_price": inner.get("image_price"),
            "price_per_hour": inner.get("price_per_hour"),
            "retain_price": inner.get("retain_price"),
        })
    if image_id:
        img = get_image(t, image_id)
        di = img.get("data") or {}
        inneri = di.get("data") if isinstance(di.get("data"), dict) else di
        out["image_id"] = image_id
        out["image_price"] = (inneri or {}).get("price")
    return out


def verify_ui(t, iid, timeout=20):
    """验ComfyUI 通不通：探 /fxai/health、/system_stats 两个接口都要 200。

    只该在实例已经 running 之后调 —— 部署/启动期间容器还没起来，这时探必然连不上（502 / 拒绝），
    那个失败不代表装坏了。所以这是 deploy 收尾的验收动作，不是随时都能跑的探活。
    """
    import urllib.error

    inst = pick_instance(get_instance(t, iid), iid)
    host = comfy_host(inst, 8188)
    if not host:
        raise RuntimeError(f"拿不到 {iid} 的容器域名，验不了ComfyUI")

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    out = {"instance_id": iid, "host": host}
    for path in ("/fxai/health", "/system_stats"):
        # 容器域名是 https，走 http 会被网关挡成 503（实例关机时也是这个表现，别误判成装坏了）
        u = f"https://{host}{path}"
        try:
            with opener.open(u, timeout=timeout) as r:
                out[path] = {"http": r.status, "ok": True}
        except urllib.error.HTTPError as e:
            out[path] = {"http": e.code, "ok": False}
        except Exception as e:
            out[path] = {"ok": False, "err": f"{type(e).__name__}: {e}"}
    out["all_ok"] = all(v.get("ok") for v in out.values() if isinstance(v, dict))
    return out


def verify_services(t, iid, timeout=20):
    """验一台实例的**四个服务**：ComfyUI(8188) + CosyVoice3(8182) + 千问TTS(8086) + Whisper(8185)。

    和 verify_ui 一样只该在 running 之后调。区别是语音服务也探：
    只验 ComfyUI 会放过「镜像里缺语音服务 / 自启失败」—— 管理台配置口令里是写了
    tts.engines.qwen3tts.cloud_url（8086）的，那个地址不通，用户装完全链路却是断的。
    探活只证明服务在；内容是否正确（如 CosyVoice3  transformers 版本错会「能起不报错内容全错」）
    要另外用 whisper 转写合成产物核对，探活代替不了。
    """
    import urllib.error

    inst = pick_instance(get_instance(t, iid), iid)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    out = {"instance_id": iid}
    # 容器域名同一个，只换主机名里的端口段（comfy_host 把 -80 换成 -<端口>）
    plan = (
        ("comfyui", 8188, ("/fxai/health", "/system_stats")),
        ("cosyvoice", 8182, ("/health",)),
        ("qwen3tts", 8086, ("/health",)),
        ("whisper", 8185, ("/health",)),
    )
    for name, port, paths in plan:
        host = comfy_host(inst, port)
        if not host:
            out[name] = {"ok": False, "err": "拿不到容器域名"}
            continue
        res = {"host": host}
        for path in paths:
            u = f"https://{host}{path}"
            try:
                with opener.open(u, timeout=timeout) as r:
                    res[path] = {"http": r.status, "ok": True}
            except urllib.error.HTTPError as e:
                res[path] = {"http": e.code, "ok": False}
            except Exception as e:
                res[path] = {"ok": False, "err": f"{type(e).__name__}: {e}"}
        res["ok"] = all(v.get("ok") for v in res.values() if isinstance(v, dict))
        out[name] = res
    out["all_ok"] = all(v.get("ok") for v in out.values() if isinstance(v, dict))
    return out


def save_image(t, iid, image_name=""):
    """储存镜像。

    官方文档里这个接口的 body **只有 id 一个必填字段**，没写 image_name。
    多传一个字段不一定被拒，但为了稳，默认只发 id；给了名字才带上。
    """
    body = {"id": iid}
    if image_name:
        body["image_name"] = image_name
    return call("POST", "/instance/saveimage", t, body)


def list_instance_images(t, iid):
    return call("GET", f"/instance/{iid}/images", t)


def list_images(t):
    return call("GET", "/images", t)


def get_image(t, image_id):
    """镜像详情。不存在的 ID 也返回 HTTP 200 + code=1000「未找到该镜像」，
    所以异常由 call() 统一抛。"""
    return call("GET", f"/image/{image_id}", t)


def search_instances(t, keyword):
    """按名字关键字找实例（只查已存在的，不新建）。返回命中的实例 dict 列表。"""
    lst = (list_instances(t).get("data") or {}).get("list") or []
    return [i for i in lst if keyword in str((i.get("name") or ""))]


def exists_image(t, image_id):
    """这个镜像 ID 现在还存不存在（只判有/没有，不比别的）。

    规则和用户给的镜像一样：先证明它确实在，才拿它去建实例。
    """
    if not image_id:
        return False
    try:
        get_image(t, image_id)
        return True
    except Exception:
        return False


def search_images(t, keyword):
    """按镜像名关键字找镜像（只查已存在的）。返回命中的镜像 dict 列表。"""
    lst = (list_images(t).get("data") or {}).get("list") or []
    return [i for i in lst if keyword in str((i.get("name") or ""))]


def _uniq(ids):
    return [x for x in dict.fromkeys(str(i or "").strip() for i in ids) if x]


def resolve_own_image_id(t, explicit="", keyword=OWN_KEYWORD, fallback=OWN_IMAGE_ID):
    """定「用哪个镜像建实例」，依次：

      1. 人给了 explicit → **先查它存在不存在**；存在才用（不存在就往下走）；
      2. 默认镜像 fallback（代码里写死的）存在 → 用它；
      3. 都不存在 → 用 keyword 搜镜像名：唯一命中 → 用它；
      4. 命中多条 → 抛错把候选列出来，**必须让人选**，由人来定第几个；
      5. 一条都没有 → 抛错。

    返回 (镜像ID, 镜像名, 来源说明)。
    """
    if explicit and exists_image(t, explicit):
        name = str(((get_image(t, explicit).get("data") or {}).get("name") or ""))
        return str(explicit).strip(), name, "人指定的镜像（已确认存在）"

    if fallback and exists_image(t, fallback):
        name = str(((get_image(t, fallback).get("data") or {}).get("name") or ""))
        return str(fallback).strip(), name, "默认镜像（代码里写死的）"

    if explicit:
        print(f"[提示] 你给的镜像 {explicit} 已不存在，改用关键字「{keyword}」再找。")

    hits = search_images(t, keyword)
    ids = _uniq(h.get("id") for h in hits)
    name = str(((hits[0].get("data") or {}).get("name")\
                if hits else ""))
    if not ids:
        raise RuntimeError(
            f"默认镜像 {fallback} 和你给的 {explicit or '无'} 都不存在，"
            f"也没搜到名字含「{keyword}」的镜像。\n"
            f"  当前账户下的镜像：%s\n"
            "  选一个给他：python xgy_api.py resolve-image <镜像ID>"
            % (json.dumps([{k: h.get(k) for k in ("id", "name", "status")} for h in
                           ((list_images(t).get("data") or {}).get("list") or [])],
                          ensure_ascii=False) or "（无）"))
    if len(ids) == 1:
        return ids[0], str(hits[0].get("name") or ""), f"按关键字「{keyword}」搜到唯一一个"
    raise RuntimeError(
        "按关键字「%s」搜到多个镜像，必须选一个（不会替你挑第一个）：\n%s\n"
        "  选定后：python xgy_api.py resolve-image <镜像ID>"
        % (keyword, "\n".join("  %s  %s" % (h.get("id"), h.get("name") or "") for h in hits)))


def exists_instance(t, iid):
    """这个实例 ID 现在还存不存在（只判存在与否，不做别的比对）。

    故意只留「有 / 没有」这一层判断：默认 ID 一旦在服务器端被删掉，
    再去比「接口返回的 id 跟写死的相不相等」就只会恒不成立、反倒成 bug。
    """
    if not iid:
        return False
    try:
        pick_instance(get_instance(t, iid), iid)
        return True
    except Exception:
        return False


def resolve_own_instance_id(t, explicit="", keyword=OWN_KEYWORD, fallback=OWN_INSTANCE_ID):
    """定「该用哪台实例」，依次：

      1. 人明确给了 explicit → 直接用（最优先，覆盖一切）；
      2. fallback（代码里写死的默认实例 ID）还存着 → 直接用它；
      3. 默认 ID 已不存在 → 用 keyword 搜实例名：唯一命中 → 用它；
      4. 命中多条 → 抛错把候选列出来，**必须让人选**，由人来定第几个；
      5. 一条都没有 → 抛错。

    返回 (实例ID, 来源说明)。
    """
    if explicit:
        return str(explicit).strip(), "人指定的实例 ID"

    if fallback and exists_instance(t, fallback):
        return str(fallback).strip(), "默认实例 ID（代码里写死的）"

    hits = search_instances(t, keyword)
    ids = []
    for h in hits:
        iid = str(h.get("id") or "").strip()
        if iid and iid not in ids:
            ids.append(iid)
    if not ids:
        raise RuntimeError(
            f"没找到默认的实例 {fallback}，也没搜到名字含「{keyword}」的实例。\n"
            "  当前令牌下的实例：%s\n"
            "  选一台给出来：python xgy_api.py resolve-instance <实例ID>"
            % (json.dumps([{k: h.get(k) for k in ("id", "name", "status")} for h in
                           (list_instances(t).get("data") or {}).get("list") or []],
                          ensure_ascii=False) or "（无）"))
    if len(ids) == 1:
        hit = next(h for h in hits if str(h.get("id") or "").strip() == ids[0])
        return ids[0], f"按关键字「{keyword}」搜到唯一一台（{hit.get('name') or ''}）"
    raise RuntimeError(
        "按关键字「%s」搜到多台实例，必须选一台（不会替你挑第一个）：\n%s\n"
        "  选定后写进配置：python xgy_api.py resolve-instance <实例ID>"
        % (keyword, "\n".join(
            "  %s  %s  %s" % (h.get("id"), h.get("name") or "", h.get("status") or "")
            for h in hits)))


def get_balance(t):
    return call("GET", "/balance", t)


def get_whoami(t):
    """账户信息。官方接口是 GET /open/whoami。

    返回：{"code":200,"success":true,"data":{"nickname":"...","uuid":"...","phone":"158****8908"}}

    更正记录：早先这里判定「平台未开放用户信息接口」是错的 ——
    当时只探了 /user、/account、/user/info 等路径，没试对真正的 /whoami。
    """
    return call("GET", "/whoami", t)


def create_recharge_order(t, amount, payment="alipay"):
    """创建充值订单。payment: alipay | wechat。

    返回的 url：alipay 可直接在浏览器打开；wechat 需自己转二维码给微信 APP 扫。
    """
    return call("POST", "/recharge/order", t, {"amount": float(amount), "payment": payment})


def get_user_info(t):
    """账户概览 = whoami + 余额 + 实例清单。"""
    me = (get_whoami(t).get("data") or {})
    bal = (get_balance(t).get("data") or {})
    try:
        insts = (list_instances(t).get("data") or {}).get("list") or []
    except ApiError:
        insts = []
    return {
        "nickname": me.get("nickname"),
        "uuid": me.get("uuid"),
        "phone": me.get("phone"),
        "balance": bal.get("balance"),
        "instance_count": len(insts),
        "instances": [{"id": i.get("id"), "status": i.get("status"),
                       "gpu": f"{i.get('gpu_model')}×{i.get('gpu_used')}"} for i in insts],
    }


# ---------- 辅助 ----------

def pick_instance(j, iid=None):
    """响应实际结构是 {code, success, data:{list:[...]}} 或 {code,success,data:{...}}。

    坑：文档 Playground 里写的是 {list:[...]}，但真实响应把 list 包在 data 里。
    所以两种都要认，否则取不到实例。
    """
    if not isinstance(j, dict):
        return {}
    # 逐层往下找候选对象
    scopes = [j]
    for k in ("data", "result", "instance"):
        v = j.get(k)
        if isinstance(v, dict):
            scopes.append(v)
    for sc in scopes:
        if sc.get("id") and (not iid or sc.get("id") == iid):
            return sc
        for key in ("list", "instances", "items"):
            lst = sc.get(key)
            if isinstance(lst, list):
                for it in lst:
                    if isinstance(it, dict) and (not iid or it.get("id") == iid):
                        return it
                if lst and isinstance(lst[0], dict) and not iid:
                    return lst[0]
    return {}


def brief(inst):
    st = inst.get("status", "?")
    return {
        "id": inst.get("id"),
        "name": inst.get("name"),
        "status": st,
        "状态": STATUS.get(st, st),
        "GPU": f"{inst.get('gpu_model', '?')} × {inst.get('gpu_used', '?')}",
        "系统盘": f"{(inst.get('system_disk_size') or 0) / 2**30:.0f} GB",
        "数据盘": f"{(inst.get('data_disk_size') or 0) / 2**30:.0f} GB",
        "内存": f"{(inst.get('memory_size') or 0) / 2**30:.0f} GB",
        "单价": f"{inst.get('price_per_hour')} /小时",
        "ssh": f"{inst.get('ssh_user', 'root')}@{inst.get('ssh_domain')} -p {inst.get('ssh_port')}",
        "web_url": inst.get("web_url"),
    }


def wait_state(t, iid, want="running", timeout=600, interval=10):
    """轮询直到进入目标状态。want 传 list 也能匹配。"""
    wants = {want} if isinstance(want, str) else set(want)
    end = time.time() + timeout
    last = None
    while time.time() < end:
        st = pick_instance(get_instance(t, iid), iid).get("status", "?")
        if st != last:
            print(f"  [{time.strftime('%H:%M:%S')}] 状态 = {st}（{STATUS.get(st, '')}）")
            last = st
        if st in wants:
            print(f"✓ 已进入 {st}")
            return st
        time.sleep(interval)
    print(f"✗ 等待 {wants} 超时（{timeout}s），当前 {last}", file=sys.stderr)
    return last


def show(inst):
    b = brief(inst)
    w = max(len(k) for k in b)
    print("-" * (w + 46))
    for k, v in b.items():
        print(f"{k:<{w}}  {v}")
    print("-" * (w + 46))


def main():
    p = argparse.ArgumentParser(description="仙宫云 API 客户端")
    p.add_argument("action", help="list/info/boot/shutdown/release-gpu/wait/save-image/"
                                  "instance-images/images/image-info/balance/whoami/user/recharge/"
                                  "public-images/resolve-instance/resolve-image/"
                                  "deploy/deploy-image/ssh/comfy-url/verify-ui/verify-services")
    # 只留常规动作：查用户信息 / 查实例 / 查 ID / 开关机。
    # 一切「销毁实例 / 销毁镜像」的接口一律不封装 —— 那类操作不可恢复，技能侧不该有能力调它。
    p.add_argument("instance_id", nargs="?", default="")
    p.add_argument("--token", default="", help="访问令牌（或用 XGY_API_TOKEN）")
    p.add_argument("--profile", default="default", help="凭据文件里的配置名")
    p.add_argument("--gpu", default="", help="开机/deploy: GPU 型号（要完整名，如 'NVIDIA GeForce RTX 4090'）")
    p.add_argument("--gpu-count", default="", help="开机/deploy: GPU 数量 0~8")
    p.add_argument("--name", default="", help="镜像名（save-image）/ 实例名（deploy）")
    p.add_argument("--image", default="", help="deploy: 镜像 ID（见 public-images）/ 镜像名")
    p.add_argument("--image-type", default="", help="deploy: public/community/private"
                                                   "（deploy-image 默认 community）")
    p.add_argument("--data-center", default="", help="deploy: 数据中心 ID（文档默认 1）")
    p.add_argument("--storage", action="store_true", help="deploy: 挂载云储存")
    p.add_argument("--storage-path", default="", help="deploy: 云储存挂载路径")
    p.add_argument("--expand-system-disk", action="store_true", help="deploy: 扩展系统盘")
    p.add_argument("--expand-size", default="", help="deploy: 系统盘扩展大小（字节）")
    p.add_argument("--amount", default="", help="recharge: 充值金额")
    p.add_argument("--payment", default="alipay", help="recharge: alipay/wechat")
    p.add_argument("--timeout", type=int, default=600, help="wait 的超时秒数")
    p.add_argument("--no-wait", action="store_true",
                   help="只下发命令就返回，不轮询等它执行完（写操作默认都会等）")
    p.add_argument("--yes", action="store_true",
                   help="deploy-image: 确认要真建实例才加（不加就是只打印请求体）")
    p.add_argument("--no-verify", action="store_true",
                   help="deploy-image: 建完不做服务验收（默认等 running 后自动探四个服务："
                        "ComfyUI 8188 的 /fxai/health+/system_stats，语音 8182/8086/8185 各 /health）")
    p.add_argument("--grab", type=int, default=30,
                   help="deploy-image: 缺卡时自动抢卡的重试上限次数（默认 30，约 2.5 分钟；"
                        "0 或 --no-grab 关闭）。每次间隔 --grab-interval 秒（默认 5）")
    p.add_argument("--grab-interval", type=int, default=5,
                   help="deploy-image: 抢卡重试间隔秒数（默认 5）")
    p.add_argument("--no-grab", action="store_true",
                   help="deploy-image: 关闭缺卡自动抢卡（等价 --grab 0），缺卡即直接报告失败")
    p.add_argument("--keyword", default=OWN_KEYWORD,
                   help="resolve-instance/resolve-image: 搜不到时的关键字（默认凤希AI）")
    a = p.parse_args()
    t = get_token(a)
    act = a.action

    if act == "list":
        r = list_instances(t)
        lst = None
        if isinstance(r, dict):
            d = r.get("data")
            if isinstance(d, dict):
                lst = d.get("list")
            if lst is None:
                lst = r.get("list")
        if not lst:
            print(json.dumps(r, ensure_ascii=False, indent=2)[:2000])
            return
        total = (r.get("data") or {}).get("total") or r.get("total") or len(lst)
        print(f"共 {total} 个实例\n")
        for it in lst:
            show(it)
            print()
        return

    # 账号级接口不针对具体实例，先于「需要实例 ID」的校验处理
    if act == "balance":
        print(json.dumps(get_balance(t), ensure_ascii=False, indent=2))
        return
    if act == "whoami":
        print(json.dumps(get_whoami(t), ensure_ascii=False, indent=2))
        return
    if act == "user":
        print(json.dumps(get_user_info(t), ensure_ascii=False, indent=2))
        return
    if act == "images":
        print(json.dumps(list_images(t), ensure_ascii=False, indent=2)[:3000])
        return
    if act == "public-images":
        print("公共镜像 ID（deploy --image 填这个）\n")
        for i, desc in PUBLIC_IMAGES.items():
            print(f"  {i}  {desc}")
        print(f"\nGPU 型号：{', '.join(GPU_MODELS)}")
        print(f"镜像类型：{', '.join(IMAGE_TYPES)}")
        return
    if act == "recharge":
        if not a.amount:
            raise SystemExit("recharge 需要 --amount <金额>，如 --amount 10 --payment alipay")
        r = create_recharge_order(t, a.amount, a.payment)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        url = ((r.get("data") or {}) or {}).get("url") or r.get("url") or ""
        if url:
            print(f"\n充值链接（{a.payment}）：{url}")
            if a.payment == "wechat":
                print("提示：微信支付链接需自行转成二维码，用微信 APP 扫码。")
        return
    if act == "image-info":
        if not a.instance_id:
            raise SystemExit("image-info 需要镜像 ID")
        print(json.dumps(get_image(t, a.instance_id), ensure_ascii=False, indent=2)[:2000])
        return

    if act == "resolve-instance":
        r = resolve_own_instance_id(t, explicit=a.instance_id, keyword=a.keyword)
        print(f"实例 ID：{r[0]}\n来源：{r[1]}")
        return
    if act == "resolve-image":
        r = resolve_own_image_id(t, explicit=a.image, keyword=a.keyword)
        print(f"镜像 ID：{r[0]}\n镜像名：{r[1] or '(无)'}\n来源：{r[2]}")
        return
    # price 不针对某个实例（账号下没实例时也要能查镜像价），所以排在「需要实例 ID」校验前面
    if act == "price":
        print(json.dumps(price(t, a.instance_id, a.image), ensure_ascii=False, indent=2))
        return

    if act == "deploy-image":
        # 建实例不针对某个现实例，所以要排在「需要实例 ID」校验前面。
        img = a.image or a.instance_id or OWN_IMAGE_ID
        body = {
            "name": a.name or DEFAULT_DEPLOY_NAME,
            "image": img,
            "image_type": a.image_type or "community",
            "gpu_model": a.gpu or DEFAULT_DEPLOY_GPU,
            # 这两个必须是 int：传字符串服务端只回 Validate error，不指名字段
            "gpu_count": int(a.gpu_count or 1),
            "data_center_id": int(a.data_center or 1),
        }
        if a.storage:
            body["storage"] = True
        if a.storage_path:
            body["storage_mount_path"] = a.storage_path
        if a.expand_system_disk:
            body["system_disk_expand"] = True
        if a.expand_size:
            body["system_disk_expand_size"] = a.expand_size
        print(f"镜像 ID：{img}（POST /instance/deploy）")
        if img == OWN_IMAGE_ID:
            print("（不传镜像 ID 就默认用凤希自己的 ComfyUI 全家桶镜像）")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        if not a.yes:
            print("\n[演练] 上面就是要下发的请求体。建实例会扣费，确认无误再加 --yes。")
            return
        grab_max = 0 if a.no_grab else (a.grab or 0)
        r = deploy(t, **body)
        attempt = 1
        while grab_max and is_gpu_shortage(r) and attempt < grab_max:
            attempt += 1
            print(f"\n[抢卡 {attempt-1}/{grab_max}] 服务端暂无可用 GPU（{_short_msg(r)}），"
                  f"{a.grab_interval} 秒后重试…")
            time.sleep(a.grab_interval)
            r = deploy(t, **body)
        if is_gpu_shortage(r):
            print("\n→", json.dumps(r, ensure_ascii=False))
            print(f"\n[失败] 已尝试 {attempt} 次仍无可用 GPU，放弃。未扣费。")
            return
        print("\n→", json.dumps(r, ensure_ascii=False))
        iid = ""
        try:
            iid = ((r.get("data") or {}) or {}).get("id") or r.get("id") or ""
        except Exception:
            pass
        if iid:
            print(f"\n新实例：{iid}")
            print(f"绑定进全局配置：python xgy_guard.py use {iid}")
        if not a.no_wait:
            wait_state(t, iid, "running", a.timeout)
        if iid and not a.no_verify:
            # ComfyUI 只在跑起来之后才探得通：部署期间必然 502/拒绝，那是正常的，
            # 别在 wait 之前去探，否则永远验不成「装好了没」。
            # 四个服务全探：ComfyUI(8188) + CosyVoice3(8182) + 千问TTS(8086) + Whisper(8185)。
            # 只验 ComfyUI 会放过「镜像缺语音服务 / 自启失败」—— 配置口令里可是写了 8086 的。
            print("\n服务验收（只在这台 running 之后做；ComfyUI + 三个语音服务全探）：")
            print(json.dumps(verify_services(t, iid), ensure_ascii=False, indent=2))
        return

    if not a.instance_id:
        raise SystemExit(f"action '{act}' 需要实例 ID")

    if act == "info":
        show(pick_instance(get_instance(t, a.instance_id), a.instance_id))
    elif act == "boot":
        print("→", json.dumps(boot(t, a.instance_id, a.gpu, a.gpu_count), ensure_ascii=False))
        if not a.no_wait:
            wait_state(t, a.instance_id, "running", a.timeout)
    elif act == "shutdown":
        print("→", json.dumps(shutdown(t, a.instance_id), ensure_ascii=False))
        if not a.no_wait:
            wait_state(t, a.instance_id, "shutdown", a.timeout)
    elif act == "release-gpu":
        print("→", json.dumps(shutdown_release_gpu(t, a.instance_id), ensure_ascii=False))
        if not a.no_wait:
            wait_state(t, a.instance_id, "shutdown", a.timeout)
    elif act == "wait":
        wait_state(t, a.instance_id, a.name or "running", a.timeout)
    elif act == "save-image":
        print("→", json.dumps(save_image(t, a.instance_id, a.name), ensure_ascii=False))
        if not a.no_wait:
            wait_state(t, a.instance_id, "running", a.timeout)  # 存完镜像仍是运行态
    elif act == "instance-images":
        print(json.dumps(list_instance_images(t, a.instance_id), ensure_ascii=False, indent=2)[:3000])
    elif act == "deploy":
        print("→", json.dumps(deploy(
            t, name=a.name, image=a.image, image_type=a.image_type or "public",
            data_center_id=a.data_center or "1", gpu_model=a.gpu, gpu_count=a.gpu_count,
            storage=True if a.storage else None, storage_mount_path=a.storage_path,
            system_disk_expand=True if a.expand_system_disk else None,
            system_disk_expand_size=a.expand_size), ensure_ascii=False))
    elif act == "ssh":
        inst = pick_instance(get_instance(t, a.instance_id), a.instance_id)
        print(f"{inst.get('ssh_user', 'root')}@{inst.get('ssh_domain')} -p {inst.get('ssh_port')}")
    elif act == "verify-ui":
        print(json.dumps(verify_ui(t, a.instance_id), ensure_ascii=False, indent=2))
    elif act == "verify-services":
        # 四服务全探（ComfyUI 8188 + 语音 8182/8086/8185），deploy-image 收尾走的就是它
        print(json.dumps(verify_services(t, a.instance_id), ensure_ascii=False, indent=2))
    elif act == "comfy-url":
        inst = pick_instance(get_instance(t, a.instance_id), a.instance_id)
        # 坑：web_url 指向容器暴露的那个端口（本例是 80），而 ComfyUI 自己 listen 在 8188。
        #     两者不是一回事，直接把 web_url 当 ComfyUI 地址会连不上。
        #     comfy_host() 会把主机名里的 -80 段换成 -8188 再拼。
        wu = inst.get("web_url") or ""
        host = comfy_host(inst, 8188)
        print(f"容器页面(未必是ComfyUI): {wu or '(无)'}")
        print(f"ComfyUI (8188):         https://{host}")
        if inst.get("jupyter_url"):
            print(f"Jupyter:               {inst['jupyter_url']}")
    else:
        raise SystemExit(f"未知 action: {act}")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        # 认证失败最常见，单独给一句能照做的提示，别甩 traceback。
        # code=1000 **不是**「未认证」的专属码 —— 「查不存在的镜像」也是
        # HTTP 200 + code=1000 + msg=「未找到该镜像」。所以必须看 msg 的文字，
        # 必须看 msg 的文字，只看 code 会把资源不存在误报成令牌有问题、白折腾。
        msg = str(e)
        auth_hit = any(k in msg for k in
                       ("未找到访问令牌", "访问令牌无效", "令牌已过期", "未授权", "Unauthorized"))
        if auth_hit:
            print(f"\n[认证失败] {msg}\n"
                  "  · 令牌要在仙宫云控制台「访问令牌」页生成，形如 eyJ...\n"
                  "  · 用 --token <令牌> 或设 XGY_API_TOKEN 环境变量传进来\n"
                  "  · API 只认访问令牌，SSH 密码与 api_key 走的是另一条通道", file=sys.stderr)
            sys.exit(2)
        print(f"[API 错误] {msg}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        sys.exit(130)
