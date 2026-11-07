# OneThingAI 实例管理 API

基址 `https://api-lab.onethingai.com`　鉴权 `Authorization: Bearer <开发者密钥>`
密钥在平台「账户中心 → 开发者密钥」自助创建。

## 🚨 与仙宫云的关键差异（照抄必错）
| 项 | 仙宫云 | OneThingAI |
|---|---|---|
| 成功判据 | `code=200` 或 `success=true` | **只有 `code=0`** |
| 开机/关机 | POST `/instance/boot` | **PUT** `/api/v1/app/operate/boot/{appId}` |
| 卸载 | 不封装 | DELETE `/api/v1/app/{appId}`（本技能**刻意不封装**） |
| 实例列表 | GET `/instances` | GET `/api/v2/app`，**必须带 page/pageSize** |
| 停机语义 | 关机 / 关机释放 GPU 两种 | **只有关机一种**，即释放计费 |
| CORS | 不支持，**必须**走客户端代理 | 支持，但**只放行 GET/POST** |

## CORS 的坑（实测）
预检响应固定是：
```
access-control-allow-methods: POST,GET
access-control-allow-origin: https://fxai.rsseasy.com
access-control-allow-headers: authorization
```
**PUT / DELETE 会被浏览器/WebView2 在预检阶段拦掉**，请求发不出去，控制台报 CORS 错（不是 405）。
所以开关机必须由客户端代理代发 —— 已为此新增：
- C#：`HttpProxy.Request(method,url,data,...)` + `JsAdapter.proxyHttpRequest`
- JS：`fxaitoolkit.proxyHttpRequest(method, url, data, back, headersJson)`

## 命令表（`python scripts/otai_api.py <action>`）
| action | 说明 |
|---|---|
| `list` | 实例列表（账号级，不需要实例 ID） |
| `info <id>` | 单台详情 |
| `boot <id>` | 开机，PUT，默认轮询到运行中 |
| `shutdown <id>` | 关机，PUT，默认轮询到已关机 |
| `wait <id>` | 轮询状态（`--kind run` 运行中 / 否则已关机） |
| `balance` | 余额 / 代金券 / 累计消费 |
| `consume` | 消费账单 |
| `private-images` | 我的镜像 |
| `public-images` | 官方 + 社区镜像（前 60 条） |
| `resources` | 资源原始返回（不过滤） |
| `available` | **只列 maxGpuNum>0 的可建组合** |
| `resolve-image` | 定位凤希自有镜像 ID |
| `resolve-instance` | 定位凤希自有运行中实例 |
| `create` | 建实例，**不加 `--yes` 只打印请求体** |
| `monitor <id>` | 监控，`--kind cpuutil/memory/gpuutil/gpumemory`，`--minutes` 上限 30 |
| `comfy-url` | 打印端口映射信息 |

## 常用参数
`--token` / `OTAI_API_TOKEN` 环境变量 / 全局配置 `otai` 节点的 `api_token`；
`--yes`（create 真建）、`--no-wait`（只下发不等）、`--grab N`（缺卡重试上限）、`--port 8188,http`。

## 实例状态码
`100` 部署中　`200` 开机中　`300` 运行中　`400` 关机中　`500` 重置中
`600` 更换镜像中　`700` 释放资源中　`800` 已关机

## 建实例要点
- 凤希自有镜像（区域 8）：**`PRIVATE_cz80fhheabvmlvio-ev1hdtjm-ne1i54io`**
  - ⚠ **控制台只显示后半段 `cz80fhheabvmlvio-ev1hdtjm-ne1i54io`，那不是完整 ID**。
    拿裸 ID 查 `/api/v2/resources` 会返回一堆 `maxGpuNum=0`，看起来像"没资源"，
    实际是 ID 不完整。`otai_api.py` 已加 `normalize_image_id()` 自动补 `PRIVATE_` 前缀。
- `billType`：1 包月 / 2 包天 / 3 按量（默认 3）
- `regionId` **用镜像所属区域**，用私有镜像时必须与镜像的 regionId 一致
- `customPort` 最多 5 个公网端口，超出只前 5 个生效
- **实时扣费**，务必先给用户看请求体

## ✅ customPort：控制台能加，API 不得（2026-10-15 实测）
**先在控制台给实例加好端口，API 就能读到 `customPort` 里的 `subDomain`，地址直接可用。**
所以「必须重建实例」这个判断是错的 —— 不用重建，加端口即可。

实测拿到（实例 `cz80fhheabvmlvio-ev1hdtjm`，区域 8）：
```
8086  https://n84-dkefmz999ukjgya3f-3dtefdso-custom.service.onethingrobot.com  千问语音
8182  https://n84-dkefmz999ukjgya3f-3dtzfdsq-custom.service.onethingrobot.com  CosyVoice3
8185  https://n84-dkefmz999ukjgya3f-3dtzfdsv-custom.service.onethingrobot.com  Whisper转写
8188  https://n84-dkefmz999ukjgya3f-3dtzfdsd-custom.service.onethingrobot.com  ComfyUI
```
域名形态 `n84-<hash>-<hash>-custom.service.onethingrobot.com`，
与仙宫云的 `<id>-<port>.container.x-gpu.com` 完全不同 —— **勿按仙宫云的规则去拼/换端口**。

### 但 API 侧确实改不了（别再猜）
穷举过事后加端口，**全部失败**：

| 方法 | 路径 | 结果 |
|---|---|---|
| PUT | `/api/v1/app/port/{id}` | 404 |
| PUT | `/api/v1/app/{id}/port` | 404 |
| POST | `/api/v1/app/operate/port/{id}` | 405 |
| PUT | `/api/v2/app/{id}` | 404 |
| PUT | `/api/v1/app/operate/customPort/{id}` | **code=3 参数错误**（接口存在，参数形态猜不对） |

最后一条只返 `code=3` 不是 404，说明路径存在，但试了 9 种 body
（`ports` / `portList` / `port` / `customPort` 字符串与数组 / `localPort` 标量 /
`localPorts` / 数字数组…）全是同一个错。**结论：加端口走控制台，API 只负责读。**

`webUIAddress` 一直是空串，**别指望它** —— 地址一律从 `customPort[].subDomain` 取。

## 🚨 加了端口映射，服务也必须真的在 listen
`customPort` 只是把公网请求转到容器内对应端口，**容器里没人 listen 就是死的**。
镜像默认 ComfyUI 跑 **7860 且只 listen 127.0.0.1**，两个问题都要改：
1. 端口改成 8188（与映射一致）
2. 监听地址改成 `0.0.0.0`（`--listen 0.0.0.0`，只改端口不改成外部照旧进不来）

改完验收：`curl 127.0.0.1:8188/fxai/health` 通了，再从公网域名打一次确认链路通。

## 统一服务端口表（与仙宫云完全同一套，不另立）
| 端口 | 服务 | 页面标签 | 配置键 |
|---|---|---|---|
| 8188 | ComfyUI | ComfyUI | `comfy_url` / `comfy_cloud_url` |
| 8086 | 千问 TTS（Qwen3-TTS） | 千问语音 | `tts.engines.qwen3tts.cloud_url` |
| 8182 | CosyVoice3 | CosyVoice3 | `tts.engines.cosyvoice.cloud_url` |
| 8185 | Whisper 转写 | Whisper转写 | `whisper.cloud_url` |

建实例时用 `create --services` 一次性申请这 4 个（`customPort` 上限 5 个）。
`SERVICE_PORTS` / `SERVICE_PORT_ARG` 常量与 `otai_api.py create --services` 已实现。


## 🟢 平台自带 1PB 公共「模型」库 —— 但不得按名字映射，别惦记
`/.ot_u_public/onething-ai/models`（`df` 显示 `JuiceFS:yxyz-oss 1.0P`）
看着像免下载模型库、软链就能用，**实际是 MinIO 对象存储 + 内容寻址**：
目录 `models/<hash前2>/<hash后2>/<sha256>`，共 3191 个**文件**（不是目录），
**没有任何索引/清单**。所以 `find -iname '*qwen3*tts*'` 一律为空 ——
**没有名字就没法按模型名定位**，装模型一律走 hf-mirror 拉。
详见 `references/04-OneThingAI实例现状.md` 的深挖记录。



## 🚨 实例重建会丢所有手工改动
远端是容器（`/.dockerenv` 存在），`/root/ComfyUI` 下的改动**只存在于当前实例的可写层**。
以下情况一律回到初始镜像、全部丢失：
- 卸载实例后重建
- 换镜像 / 重置实例
- 迁移区域

所以**清完节点、升完 ComfyUI 之后必须保存成新镜像**，否则这次的工作白做。
平台 API 文档里没有"保存镜像"接口，需在控制台操作。

## 余额接口的坑
`availableBalance` 等字段返的是**字符串**（如 `"0.00"`），必须 `float()` 一次，
否则前端会显示 NaN。`otai_api.py` 里已处理。
