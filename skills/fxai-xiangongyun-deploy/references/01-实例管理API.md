# 实例管理 API（开机/关机/释放GPU/存镜像/查余额）

`scripts/xgy_api.py` —— 走开放平台 HTTPS API，**不用 SSH**，本机就能远程开关实例。

**认证**：`Authorization: Bearer <访问令牌>`，基址 `https://api.xiangongyun.com/open`。
令牌在控制台「访问令牌」页生成（形如 `eyJ...`），**不是 SSH 密码、不是 api_key**。

**凭据只有全局配置这一处**：`~/.workbuddy/fxai_global.json`。
| 用途 | 字段 |
|---|---|
| 访问令牌 | `xgy_api_token`（`--token` / `XGY_API_TOKEN` 优先于它） |
| SSH 连接信息 | `xgy_ssh`：`host` / `port` / `user` / `password` / `key` |
| SSH 连接信息读取顺序 | 全局配置 `xgy_ssh` → 脚本相对路径找 `secrets/xiangongyun.json` |

SSH 信息由 `xgy_cfg.ssh_cred()` 统一提供（`deploy.py` / `remote.py` / `xgy_launch.py` 等都走它）。
不读 `secrets/xiangongyun.json` 里的 profile（那是老一套）。`FXAI_GLOBAL_CONFIG` 可另指全局配置路径。

```bash
python xgy_api.py list                       # 所有实例（id/状态/GPU/磁盘/单价/SSH/web_url）
python xgy_api.py info <id>                  # 单个实例详情
python xgy_api.py ssh <id>                   # 打印 SSH 连接串
python xgy_api.py comfy-url <id>             # 打印 ComfyUI 的 8188 地址
python xgy_api.py verify-ui <id>             # 验ComfyUI：/fxai/health + /system_stats
python xgy_api.py verify-services <id>       # 验四服务：ComfyUI 8188 + 语音 8182/8086/8185（deploy-image 收尾走这个）
python xgy_api.py price [--image <镜像ID>]   # 单价采样：GPU基准/镜像挂载/停机保留费
python xgy_api.py balance                    # 账户余额
python xgy_api.py whoami                     # 昵称 / uuid / 脱敏手机号
python xgy_api.py user                       # 账户概览（whoami + 余额 + 实例清单）
python xgy_api.py images                     # 私有镜像列表
python xgy_api.py image-info <image_id>      # 镜像详情
python xgy_api.py public-images              # 打印公共镜像 ID / GPU 型号 / 镜像类型枚举
python xgy_api.py deploy-image <镜像ID>       # 按镜像 ID 建实例（只打印请求体）
python xgy_api.py deploy-image <镜像ID> --yes  # 同上，真建（会扣费）。缺卡时默认自动抢卡：每 5 秒重试一次、最多 30 次（约 2.5 分钟），仍无卡才报失败且不扣费；--no-grab 关闭、--grab N 改次数
python xgy_api.py recharge --amount 10 --payment alipay   # 创建充值订单并打印支付链接
python xgy_api.py boot <id>                  # 开机（默认轮询到 running）
python xgy_api.py shutdown <id>              # 关机但**保留 GPU** —— 卡还占着，照全价计费
python xgy_api.py release-gpu <id>           # 关机**释放 GPU** —— 最省钱的停法
python xgy_api.py wait <id> --name running   # 阻塞等状态（--name 就是目标状态，别 positional 传）
python xgy_api.py save-image <id> --name my-img    # 存成私有镜像
```

## 🔴 release-gpu 之后先等冷却再 boot（平台冷却期）

`release-gpu` 关机后紧接着 `boot`，平台会拒绝，返回 `code=1000 该实例暂时无法进行操作`。

**正确顺序**：`release-gpu` → **等实例状态真正回到 `shutdown`**（`python xgy_api.py list` 看）
→ 再 `boot`。开机 booting→running 约 60 秒。

**平台限流，技能代码无需处理**；手动验证 / 连续跑流程时留足冷却，
否则第二次开机必失败。关机后 SSH 要等十几秒才恢复，boot 后立刻连会报
`Error reading SSH protocol banner` —— 这时改用公网 HTTP 探测
（`https://<实例ID>-<端口>.container.x-gpu.com`）验状态更稳。

## 等实例/等模型的规矩
- 云端开机、模型下载、依赖安装都是分钟级长任务：**后台跑 + 等完成通知**，
  主线程改用 `xgy_progress.py` 或监听器的 `progress.html` 面板看进度（纪律见 `fxai-common`/SKILL.md「等任务纪律」）。

## 已封装的接口（只留常规动作）
| 分类 | 接口 | 方法 | 路径 | 请求字段 |
|---|---|---|---|---|
| 实例 | 实例列表 | GET | `/open/instances` | 无 |
| 实例 | 实例详情 | GET | `/open/instance/{id}` | — |
| 实例 | 实例储存的镜像 | GET | `/open/instance/{id}/images` | — |
| 实例 | 部署实例 | POST | `/open/instance/deploy` | `gpu_model` `gpu_count` `data_center_id` `image` `image_type` `storage?` `storage_mount_path?` `ssh_key?` `system_disk_expand?` `system_disk_expand_size?` `name?` |
| 实例 | 开机 | POST | `/open/instance/boot` | `id` `gpu_model?` `gpu_count?` |
| 实例 | 关机保留GPU | POST | `/open/instance/shutdown` | `id` |
| 实例 | 关机释放GPU | POST | `/open/instance/shutdown_release_gpu` | `id` |
| 实例 | 储存镜像 | POST | `/open/instance/saveimage` | `id` |
| 镜像 | 镜像列表 | GET | `/open/images` | 无 |
| 镜像 | 镜像信息 | GET | `/open/image/{id}` | — |
| 账号 | 获取用户信息 | GET | `/open/whoami` | 无 |
| 账号 | 充值下单 | POST | `/open/recharge/order` | `amount` `payment`(alipay/wechat) |

只读接口 `GET /open/balance` 查余额。

「关机并销毁 / 销毁实例 / 储存镜像并销毁 / 销毁镜像」四个接口**一律不封装** ——
不可恢复的操作不进技能，要用那种停法就人去控制台点。

## 🔴 V1.9 必须重新创建实例（升级用户先读这一节）

V1.9 更换了**模型与工作流**：MV 底座换成 PRISM、超分换成 RelayVSR，旧底座与下线 LoRA 已从镜像移除，
去油 LoRA 以新文件名重新上线。模型和自定义节点装在**实例的系统盘里**，技能包升级只改本机技能文件、
碰不到已创建的实例 —— **旧实例永远补不齐这批新模型**，重下模型、改工作流里的模型名、重启 ComfyUI 都救不回来。

| 你的情况 | 必须做的 |
|---|---|
| 从 V1.8 或更早升到 V1.9，**用仙宫云** | **用同一个镜像 ID 重新创建一台新实例**（`deploy-image`，内容已被覆盖为 V1.9），不得沿用旧实例、不得只重启、不得只做「更新技能包」 |
| 首次使用 V1.9 | 直接按该镜像 ID 建实例即可 |
| 本机整合包部署 | 不受重建约束，按 `assets/models.json` 补齐本版模型即可 |

**镜像 ID 固定不变**：`9463d4ca-2f86-45cf-b0a3-a81ba6f3b27c`（凤希AI-ComfyUI全自动短剧MV数字人生成）。
凤希的更新方式是**用新版镜像覆盖同一个 ID**，所以 V1.9 与 V1.8 的 ID 完全相同 —— 但镜像内容已经换成新的。

🔴 **ID 不变 ≠ 实例会自动升级**：实例的系统盘是**创建那一刻的镜像快照**，镜像后来被覆盖，已有实例仍跑旧内容，
仙宫云实例也**不支持换镜像**。所以必须 `deploy-image` 用同一个 ID **新建一台实例** —— 新建才会拉取当前镜像层
（含 V1.9 新模型）。别把「ID 没变」理解成「重启一下就行」。

**运行中怎么判**：提交任务返回「模型不存在 / 找不到模型 / `value_not_in_list` 且服务端连候选都没有」，
即可判定仍连着旧镜像实例。`fxai-workflow/comfy_client.py` 的 `_fmt_model_fix()` 会自动打印
「必须新建实例（镜像 ID 不变，内容已覆盖为 V1.9）」的指引 —— 照提示引导用户新建实例，勿在旧实例上补模型或改名绕过。

**创建新实例的正确顺序**（建实例实时扣费，先给用户看请求体、明确同意后才加 `--yes`）：

```bash
python xgy_api.py deploy-image 9463d4ca-2f86-45cf-b0a3-a81ba6f3b27c          # 演练：只打印请求体，不建、不扣费
python xgy_api.py deploy-image 9463d4ca-2f86-45cf-b0a3-a81ba6f3b27c --yes    # 用户确认后才真建
python xgy_api.py comfy-url <新实例ID>                 # 拿新实例的 8188 地址
python xgy_guard.py use <新实例ID>                     # 写进全局配置，后续技能自动连这台
```

把本机 `~/.workbuddy/fxai_global.json` 的 `comfy_url` 指向**新实例**地址后再出片；
旧实例按需 `release-gpu` 停机（不销毁，保留可回滚），确认新链路跑通再处理。

🔴 **建实例会实时扣费**：技能只提示与给出入口，**绝不自动创建**；
`--yes` 必须由使用者明确点头，见 SKILL.md 铁律 2 / 13。

---

## 按镜像 ID 建实例

凤希自己的 ComfyUI 全家桶镜像（open 即用的千问2.1 / MiniMax H3 / YuE2 / Mel-Band
+ 108 个自研节点 + 约 80GB 模型）：

```
9463d4ca-2f86-45cf-b0a3-a81ba6f3b27c   凤希AI-ComfyUI全自动短剧MV数字人生成
```

默认参数：`image_type=community`、`gpu_model=NVIDIA GeForce RTX 4090 D`、`gpu_count=1`、
`data_center_id=1`、实例名 `测试`。

```bash
python xgy_api.py deploy-image                       # 演练：只打印请求体，不建
python xgy_api.py deploy-image 9463d4ca-2f86-45cf-b0a3-a81ba6f3b27c --yes
python xgy_api.py deploy-image --name 我的实例 --gpu-count 2 --yes
python xgy_api.py deploy-image --gpu "NVIDIA GeForce RTX 4090" --yes
```

`POST /open/instance/deploy` 就是**新建实例**：返回体 `data.id` 是全新 ID，建完能在控制台列表里看到。

硬约束：
- **`name` 要传**（可选字段，不传就是无名一台，列表里只显示 ID）。默认 `DEFAULT_DEPLOY_NAME = "测试"`。
- **`gpu_count` / `data_center_id` 必须整型**。传字符串服务端返回 `code=1000 msg=Validate error`，
  报错不指名字段。脚本内已强制 int。
- `gpu_count` 必填，只发 `image` + `gpu_model` 会被 `最少需部署1张GPU` 挡。
- 数据中心字段是 `data_center_id`（不是 `data_center`），默认 `1`。
- 🚨 **建实例会占 GPU 并实时扣费**：缺 `--yes` 时只打印请求体。用户明确说
  「建/开一个实例/按这个镜像建」这类话，`--yes` 前先把请求体给用户看一眼。
- 建完把返回的实例 ID 用 `python xgy_guard.py use <新实例ID>` 写进全局配置，
  之后的生图/四视图/场景/MV/短剧技能会自动开机到这台新实例。

**服务验收的顺序是死的**：`deploy-image --yes` → 等 `running` → 四服务全探 → 全 200 才算装好。
部署/启动期间容器没起来，探接口必然 503/10061，那不是装坏。
`deploy-image` 默认就走到验完（`verify_services()`），`--no-verify` 才跳过。

🔴 **四个服务全探，不只 ComfyUI**：ComfyUI（8188，`/fxai/health` + `/system_stats`）+
CosyVoice3 语音（8182，`/health`）+ 千问TTS（8086，`/health`）+ Whisper 转写（8185，`/health`）。
管理台配置口令里写了 `tts.engines.qwen3tts.cloud_url`（8086），只验 ComfyUI 会放过
「镜像缺语音服务 / 自启失败」—— 用户装完全链路却是断的。
⚠️ 探活只证明服务在；**内容是否正确探活验不出**（CosyVoice3 的 transformers 版本错会
「能起、不报错、内容全错」），要另用 whisper 转写合成产物核对文字。

**`verify-ui`（仅 ComfyUI）三条**：① 容器域名端口写在主机名末尾（`-80.container` → **`-8188.container`**）；
② 必须 **https**，走 http 会被网关挡成 503；③ 刚判 `running` 就探可能还 502，
容器里的 ComfyUI 比实例状态晚起几十秒到两分钟 —— 等约 1 分钟再验一次，连续两次 502 才算真有问题。

**价格以仙宫云官方页为准，界面不显示估算价。** 没有资费/报价接口
（`instance/0..10` 全是列表/详情/部署/销毁/开关机/存镜像），
能读到的只有 `GET /instance/{id}` 的 `base_price` / `image_price` / `price_per_hour` / `retain_price`
和 `GET /image/{id}` 的 `price`。采样回来的数不具权威性（同型号不同镜像能差一档），
**报价一律给官方镜像详情页链接**。`price` 命令只在对话里做核对用。

**`status` 取值**：`deploying` 部署中 / `running` 运行中 / `booting` 开机中 / `shutting_down` 关机中 /
`shutdown` 已关机 / `destroying` 销毁中 / `destroyed` 已销毁 / `saving_image` 存镜像中 /
`freezing` 冻结中 / `freeze` 已冻结 / `replacing_image` 换镜像中。

**计费**：关机释放 GPU 后系统盘按已用空间计费（约 ¥0.00003/GB/小时）；
「关机保留 GPU」卡还占着，不省钱；长时间不用用 `release-gpu`。

## API 判据
1. **成功码是 `code=200`**，不是 0。以 `success` 字段为准，缺失时才退回看 code。
2. **实例列表包在 `data.list` 里**（真实响应 `{code, success, data:{list:[...], total:N}}`）。
3. **`web_url` 不是 ComfyUI 地址**：它是容器暴露端口的页面（本例 80），ComfyUI 默认 **8188**。
   用 `comfy-url` 动作拼，别自己拼。
4. **「不存在」也返回 HTTP 200，且复用 `code=1000`**（body `未找到该镜像`）。
   **认证失败也是 `code=1000`**（`未找到访问令牌`）—— 提示**按 `msg` 文字分类**；按 code
   会把「镜像不存在」误报成「令牌有问题」。
5. 错误判定只看 body 的 `code`/`success`，不看 HTTP 状态码。

## 接口规范在哪查

Playground 没有公开 OpenAPI（`/api-docs`、`/openapi.json`、`/swagger.json`、`/v3/api-docs` 全 404）。
规范在三处：
1. **接口清单**在服务端 RSC payload 里，页面路由 `/<type>/<index>`。用 `curl <页面>` 后正则
   `\"endpoint\":{\"name\":\"..\",\"method\":\"..\",\"path\":\"..\"` 提取。已知 type：`instance`(0-10)、`image`(0-2)、`user`。
2. **单接口文档**在 `/md/<路径>.md`（去掉 `/open/` 前缀、`/` 换 `_`、`{id}` 去花括号）：
   `/open/instance/boot` → `https://api-playground.xiangongyun.com/md/instance_boot.md`
3. **部分 md 是 GBK 编码**，须 `iconv -f GBK -t UTF-8`（或 GB18030）转。
