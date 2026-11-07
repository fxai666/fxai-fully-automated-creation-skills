# 从分镜到渲染（分镜写完之后）

---

## 一、分镜 → shots（交给 fxai-short-drama）

单集 run 脚本里的 shots 结构：

```python
shots = [
  {"duration": 6, "prompt": "主体定义段\n<Subject 1> ...", "images": ["shortdrama_<slug>/000.png", "shortdrama_<slug>/009.png"]},
  ...
]
```

| 项 | 规则 |
|----|------|
| `duration` | = 该镜在分镜里写的秒数（6-8），**≤10**（硬件上限，超了截断） |
| `images` | 服务端 rel 路径（前缀 = `server_subdir`），**不是本地路径**；人物镜放人物三视图 + 场景图 |
| 总时长 | 各镜相加，**必须 ≥60 秒**（平台硬线） |
| 结束索引 | 提交工作流时**必须 = -1**（写 0 会截断，只生成第一镜） |
| 音频 | 系统生成（H3 TTS 出对白语音），台词写在 `<d>[Chinese]…</d>` 里驱动语音+口型 |
| 字幕 | 生成期 `suppress_subtitles=True` 压画面字幕，成片后期烧录 |

## 二、交接口（关键，别记错）

```python
import sys; sys.path.insert(0, r"<fxai-short-drama 技能目录>")
from drama_pipeline import render_episode, check_episode_duration, episode_seconds
check_episode_duration(shots)          # 规划期先算一次，只算不生成
pid, merged = render_episode(project_root, ep, shots)   # 内部默认卡 60 秒下限
```

- 提交前先核对 rel 齐全：缺参考图不静默降级，`_validate_media_tags` 会抛 `ValueError: 提示词引用的素材不足`，整集白跑
- 换服务器：本地 `assets/` 是定稿真值，**只覆盖上传**（`overwrite=True`），生图环节保持服务端已有版本（重跑会让形象漂移 + 白吃 GPU）
- 上传必须走 `comfy_client.upload_image()`（手发 multipart 会被代理网关改包 → 500 `Invalid HTTP header`）

## 三、分镜层就要替 H3 挡掉的坑

写分镜时把坑挡在剧本层，比渲染后返工便宜 40 分钟：

| 坑 | 分镜里怎么写 |
|----|--------------|
| **群演糊脸/穿帮** | 一律写剪影、蒙面、背面、低首 |
| **画面死住（背景全静止）** | 每镜「画面」里至少一句"谁在动" |
| **全动物项目混进真人** | 场景提示词写"只有动物在…" + 动作给动物主语 + 世界观句 |
| **比例崩坏的人** | 火海/战场不写"人奔跑特写"，写剪影 + 光点 |
| **形象漂移** | 角色锚点固定（某个簪/某道疤/某个纹样），每镜画面必带 |

## 四、出片验收

- 单镜返修：`build_workflow(shots, single_shot=N)`，5-6 分钟出结果，**merged 就是整集成片**（其余镜命中缓存），不必整集重提
- 逐镜抽帧目检（每镜取中点 1 帧），重点盯：UI/榜文镜、群像镜、打斗镜
- 段定位：`h3_actual_seconds(d)` 累加每镜区间 → 成片某秒落在哪一镜 → 只重那一镜
