# 09 · 音乐生成 · MiniMax Music 3（**对唱默认**）

> 何时读：用 MiniMax Music 3（`minimax3`）出歌时。**默认用于对唱（多人）**；独唱（单人）默认走 Yue2，见 `08-音乐生成-Yue2.md`。
> 本册只讲 M3 自己的格式与规则。

## 一、定位

| | |
|---|---|
| 模型 | `minimax_music3_*`（8B 全局 LLM + 0.6B 局部 LLM + 2.4B Flow Matching，本地 ComfyUI） |
| 工作流 | `fxai-workflow/music-minimax3-api.json` |
| 默认场景 | **对唱**（多人歌）——M3 是唯一能出**可控对唱**的模型 |
| 输出 | 32kHz 16-bit 立体声 wav |
| 时长 | 上限 300s（5 分钟），模型可在歌词唱完后提前结束 |

## 二、两个输入：`caption` + `lyrics`

**caption 管「怎么唱」，lyrics 管「唱什么」，两者不混写。**

### 2.1 Caption = 三段式结构化描述（固定三个标题、按顺序，约 300–500 字）

| 段 | 写什么 |
|---|---|
| **Global Metadata** | 流派/子流派、BPM、调性音阶（只在确实想要时写）、情绪弧线、场景意象、混音气质 |
| **Vocal Details** | **人声性别与音色（必写！不写会漂成纯音乐）**、演唱方式（逐段）、和声/伴唱、人声 FX |
| **Arrangement** | 当成**时间线**写：什么乐器从头到尾锚定、什么中途进/退，逐段的律动与强度变化 |

对唱写法：

```
Global Metadata: bpm 72, key G major, 新中式古风 / 国风抒情。情绪从清雅铺陈渐入温润相合，尾声归于宁静。
场景：宣纸画室、砚池墨光、山水长卷。混音温暖，人声居中靠前，古琴与弦乐铺底。
Vocal Details: 男女对唱（duet），Singer A（男声）低沉温润的中低音；Singer B（女声）清亮柔和的女声。
演唱分工固定：所有 [Verse] 段落由男声 Singer A 独唱；所有 [Chorus] 段落由女声 Singer B 独唱；
[Bridge] 段落两人逐句交替应答（call-and-response），男声一句、女声一句轮换。两种音色对比鲜明。
人声干净贴麦，中度混响，延迟只打在句尾。
Arrangement: 主奏古琴走圆润散音与琵琶清亮轮指贯穿全曲；洞箫与竹笛在主歌轻声穿插；
大提琴与弦乐长音铺底。前奏只有古琴与竹笛；主歌加入琵琶；副歌进入弦乐与轻鼓；尾奏回到古琴独奏。
```

### 2.2 Lyrics = 英文段落标签 + 歌词（**标签独占一行**）

可用标签（14 种）：

```
[Intro] [Verse] [Pre-Chorus] [Chorus] [Post-Chorus] [Hook]
[Bridge] [Build Up] [Instrumental] [Solo] [Break] [Interlude] [Drop] [Outro]
```

三条硬规则：

1. **标签必须单独占一行**——写成 `[Verse] 你画远山` 会**静默丢弃同一行的歌词**。
2. **标签是唯一的结构指令**；歌词正文只负责内容与情绪，别在歌词里写「（男）」「（女）」——会被**当歌词唱出来**。
3. **想让副歌重复就再写一遍歌词**，只重复标签不重复歌词无效。

## 三、对唱怎么分工（**关键**）

官方**没有**「逐句演唱者」标签。对唱靠两条腿：

1. **Caption 的 Vocal Details 里显式声明歌手**：给两个歌手起名（`Singer A（男声）` / `Singer B（女声）`），并写清**演唱分工**。
2. **用段落标签承载分工**（最可靠）：`[Verse]` 归男声、`[Chorus]` 归女声、`[Bridge]` 两人逐句交替——然后在 Vocal Details 里把这条规则写明。

> ⚠️ **仍是「引导式」，不是硬保证。** 生成后**必须**用基频（f0）验证实际人声，**以实际音频为准**回填 `- sing:`（方法见 `07-双人对唱.md` 第四、五节）。
> 若某一版对唱比例不理想，可换 `seed` 重抽（M3 非确定性），或把段落级分工写得更死。

### 与分镜模板的关系

- 对唱歌 → 分镜用【对唱模板】（`n_chars>1`），带 `- sing:` 字段。
- 演唱者分工写在两处：`歌曲.md` 的 `## 对唱分工`（给人看）+ `分镜剧本.md` 的 `- sing:`（给机器用）。
- `act` 只写非演唱的肢体与互动；演唱由 `sing` 统一驱动。

## 四、时长

`制作.json` 的 `music_duration`（秒，上限 300）→ 占位符 `fxai_music_duration` → 节点 54 的 `max_duration`。

- 它是**上限不是目标**：歌词唱完模型会自己收尾，写大了不会硬撑。
- 写小会截断歌曲，**宁大勿小**（默认 300）。

## 五、工作流与占位符（维护用）

节点构成（API 格式）：

| 节点 | class_type | 关键参数 |
|---|---|---|
| 44 | `UNETLoader` | `minimax_music3_dit_fp32.safetensors`（备选 fp16 更省显存） |
| 45 | `CLIPLoader` | `minimax_music3_text_encoder_pruned_int8_convrot.safetensors`，**`type` 必须为 `minimax`** |
| 46 | `VAELoader` | `minimax_music3_dav.safetensors` |
| 54 | `MiniMaxMusic3TextEncode` | `caption` / `lyrics` / `seed` / `max_duration` / `cfg_scale` 1.7 / `top_k` 50 |
| 48 | `EmptyMiniMaxMusic3LatentAudio` | `seconds` **必须链到 54 的输出 1**（写死会断链） |
| 47 | `ConditioningZeroOut` | 接 54 作 negative |
| 49 | `KSampler` | steps 30 / cfg 1.7 / `euler` / `simple` / denoise 1 |
| 52 | `VAEDecodeAudioTiled` | `tile_size` 1536 / `overlap` 64（长曲低显存用） |
| 56 | `FxAiAudioManager` | `目录` 固定 `音乐生成` |
| 51 / 58 | `SeedNode` / `FxAiCleanGpu` | 种子 / 收尾清显存 |

模型文件放位（Comfy-Org 重打包版）：

```
ComfyUI/models/diffusion_models/minimax_music3_dit_fp32.safetensors   （或 _fp16）
ComfyUI/models/text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors
ComfyUI/models/vae/minimax_music3_dav.safetensors
```

占位符：

| 占位符 | 落在哪 | 类型 |
|---|---|---|
| `fxai_prompt` | 54 `caption` | string |
| `fxai_lyrics` | 54 `lyrics` | string |
| `fxai_seed` | 51 `seed` | 数字（<1 自动随机） |
| `fxai_music_duration` | 54 `max_duration` | **非字符串**（裸数字） |

`fxai_music_duration` 已在 `nodeconfig.json` 的 `placeholders`（integer，默认 300）与 `non_string_placeholders` 里登记——**新增占位符两处都要登记**，否则要么报「缺必填」要么被插成带引号的字符串（`max_duration` 是 FLOAT，**必须裸插**）。

注册：`WORKFLOWS["music"]["minimax3"] = "music-minimax3-api.json"`；校验走 `WF_SPECS["music"]["any_of"]` 的第 2 个分支。

## 六、调用

```python
from mv_pipeline import gen_music
dst, rel = gen_music(caption, lyrics, duration=300, model="minimax3",
                     tag="mm3_xxx", timeout=5400)
```

`model=None` 时按人数默认：多人 → minimax3；单人 → yue2。

## 七、常见故障

| 现象 | 原因 / 处置 |
|---|---|
| 提交被拒 `bad_linked_input` | `max_duration` 被插成带引号字符串 → 把 `fxai_music_duration` 加进 `non_string_placeholders` |
| 生成出来是纯音乐（没人声） | Caption 的 **Vocal Details 没写人声性别**——官方明确这是头号原因 |
| 人声漂移 / 对唱没出来 | 换 `seed` 重抽；把段落级分工在 Vocal Details 里写得更死；以 f0 为准回填 `- sing:` |
| 显存不足 / OOM | 44 节点换 `minimax_music3_dit_fp16.safetensors`；52 节点保持 `tiled` 分块解码 |
| 时长比 `music_duration` 短 | 正常——它是**上限**，歌词唱完模型会自行收尾 |
