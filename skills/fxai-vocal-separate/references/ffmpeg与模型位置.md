# ffmpeg 与模型位置约定（二次去噪时照查）

## 去噪做什么

分离出人声后再跑一次 **ffmpeg `arnndn`** 滤镜（Recurrent Neural Networks，模型 `model.rnnn`，已在 ffmpeg 同目录）——去掉分离残留的底噪/房间感，让 whisper 更好识别。

## 命令等价形式

```
ffmpeg -y -i 人声.wav -af aresample=48000,arnndn=m=model.rnnn:mix=1.0 人声_去噪.wav
```

## 代码入口与参数

- `denoise_audio(src, dst=None, mix=1.0)`；`separate_vocals(..., denoise=True)` 会在下载人声后自动串一步，产物 `人声_去噪.wav`（返回 `res["denoised"]`）。只对**人声**去噪，伴奏不做。
- `mix`：0~1，去噪输出与原音的混合比（默认 1=全用去噪结果）。人声被削得太狠时降到 0.7~0.9。
- `arnndn` 只吃 48kHz，滤镜链前置 `aresample=48000`（输出即 48k wav，whisper 内部会再降到 16k，无影响）。
- 去噪不改变时长：输出与输入秒数严格一致。

## 路径转义约定

filtergraph 里 `:` 是选项分隔符，绝对路径的盘符需要转义；本实现用 `cwd=模型目录` + 滤镜只写文件名绕开（用户装在哪都行）。

## ffmpeg 与模型位置（只走全局变量）

- `ffmpeg_path()` 读 `fxai_global.json` 的 `ffmpeg`，否则 `<software_install_dir>/ffmpeg/ffmpeg.exe`。
- 模型取 ffmpeg 同目录 `model.rnnn`，可用 `fxai_global.json` 的 `rnnn_model` 覆盖。

两处路径都由全局配置解析，文档与代码都不写死本地路径。