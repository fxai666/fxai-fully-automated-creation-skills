# -*- coding: utf-8 -*-
"""Whisper 语音转文本 HTTP 服务（云端版，端口 8185）。

用途：fxai-whisper-transcribe 是离线 CLI、不得常驻被调；本服务在云端常驻，
让技能包通过 URL 直接调用（配置键 whisper.cloud_url）。

与本机 CLI 的差异（都是刻意这样，不是偷懒）：
- 常驻 + 空闲自动卸载模型（--idle-timeout），4090 只有 24G，
  跟 ComfyUI 同时占着不放会 OOM；本机 CLI 是一次性进程不存在这问题。
- 强制对外监听 0.0.0.0，端口 8185（与统一服务端口表一致）。

只用标准库起服务（http.server），与千问 TTS 那套保持一致，不引入 fastapi/uvicorn。
模型：mobiuslabsgmbh/faster-whisper-large-v3-turbo（与本机同款，走 ctranslate2，不吃 torch 显存）。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 与本机 fxai_global.json 同款模型：large-v3-turbo
MODEL_REPO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
DEFAULT_MODEL = "large-v3-turbo"
MAX_UPLOAD = 200 * 1024 * 1024   # 单次上传上限 200MB，够一段几十分钟的录音

# 模型句柄与锁：加载一次、多个请求串行用（GPU 推理本来就串行，并发只会互相拖慢）
_model = None
_model_key = None
_lock = threading.Lock()
_last_use = 0.0


def log(*a):
    print("[whisper]", *a, file=sys.stderr, flush=True)


def resolve_device():
    """有 CUDA 走 cuda（float16），否则 cpu（int8）。ctranslate2 不依赖 torch。"""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def get_model(name):
    """按需加载模型；name 与当前一致且已加载就复用。"""
    global _model, _model_key
    with _lock:
        if _model is not None and _model_key == name:
            return _model
        from faster_whisper import WhisperModel
        dev = resolve_device()
        # GPU 用 float16 省显存，CPU 用 int8 保速度
        ct = "float16" if dev == "cuda" else "int8"
        log(f"加载模型 {name} device={dev} compute_type={ct} …")
        t0 = time.time()
        _model = WhisperModel(name, device=dev, compute_type=ct)
        _model_key = name
        log(f"模型就绪，耗时 {time.time() - t0:.1f}s")
        return _model


def maybe_unload(idle_timeout):
    """空闲超时卸载模型，把显存还给 ComfyUI。"""
    global _model, _model_key
    if idle_timeout <= 0 or _model is None:
        return
    idle = time.time() - _last_use
    if idle > idle_timeout:
        with _lock:
            if _model is not None and (time.time() - _last_use) > idle_timeout:
                log(f"空闲 {idle:.0f}s，卸载模型（显存还给 ComfyUI）")
                _model = None
                _model_key = None
                try:
                    import ctranslate2
                    ctranslate2.unset_cuda_allocator()
                except Exception:
                    pass


def do_transcribe(path, language="zh", beam=5, vad=False, word_ts=False):
    """真正转写。返回 {text, segments, duration, device}。"""
    global _last_use
    name = DEFAULT_MODEL
    model = get_model(name)
    _last_use = time.time()
    segments, info = model.transcribe(
        path,
        language=(None if language in ("auto", "", None) else language),
        beam_size=beam,
        vad_filter=bool(vad),
        word_timestamps=bool(word_ts),
        # condition_on_previous_text：歌里关掉可减少幻觉复读，与本机 CLI 默认一致
        condition_on_previous_text=True,
    )
    segs = []
    for s in segments:
        item = {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text}
        if word_ts and getattr(s, "words", None):
            item["words"] = [
                {"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word}
                for w in s.words
            ]
        segs.append(item)
    text = "".join(s["text"] for s in segs).strip()
    return {
        "text": text,
        "segments": segs,
        "duration": round(float(getattr(info, "duration", 0) or 0), 2),
        "language": getattr(info, "language", language),
        "device": resolve_device(),
        "model": name,
    }


class Server(ThreadingHTTPServer):
    """多线程 HTTP 服务。客户端断开属于正常 TCP 行为，不刷 traceback（详见 Handler）。"""
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        # 远程主机强迫关闭连接（WinError 10054）一族：客户端关页面 / 取消上传 /
        # 浏览器回收空闲 keep-alive 连接——不是服务故障，静默丢弃即可。
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (10054, 10053, 10052, 10058, 995):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    idle_timeout = 1800
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # 静音默认访问日志
        pass

    def handle_one_request(self):
        # 客户端提前断开时 readline / wfile.write 抛 ConnectionReset 一族——正常现象，
        # 静默关掉这条连接，不刷 traceback。
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            self.close_connection = True
        except OSError as e:
            if getattr(e, "winerror", None) in (10054, 10053, 10052, 10058, 995):
                self.close_connection = True   # WSAECONNRESET 等：客户端侧断开
            else:
                raise

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code=400):
        self._send({"ok": False, "error": msg}, code)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send({"status": "ok", "service": "whisper",
                        "model": _model_key or DEFAULT_MODEL,
                        "model_loaded": _model is not None,
                        "device": resolve_device()})
        else:
            self._err("未知路径：" + self.path, 404)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return self._err("空请求体")
        if n > MAX_UPLOAD:
            return self._err(f"上传过大（{n/1048576:.0f}MB > {MAX_UPLOAD/1048576:.0f}MB）", 413)

        raw = self.rfile.read(n)
        # 两种传法：multipart/form-data（带文件名）或裸 body（Content-Type 给 audio/*）
        fname, data, lang, beam, vad, wts = "audio.wav", raw, "zh", 5, False, False
        ct = self.headers.get("Content-Type", "")
        if "multipart/form-data" in ct and b'name="file"' in raw[:2048]:
            try:
                import cgi  # py3.10-3.12 仍可用，3.13 已移除 → 失败时回退裸 body
                env = {"REQUEST_METHOD": "POST", "CONTENT_TYPE": ct, "CONTENT_LENGTH": str(n)}
                fs = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=env,
                                      keep_blank_values=True)
                if "file" in fs:
                    item = fs["file"]
                    fname = os.path.basename(getattr(item, "filename", "") or "audio.wav")
                    data = item.value
                for k, conv in (("language", str), ("beam", int), ("vad", bool), ("word_ts", bool)):
                    if k in fs:
                        v = fs[k].value
                        if conv is bool:
                            lang_v = v.lower() in ("1", "true", "yes", "on")
                            if k == "vad":
                                vad = lang_v
                            else:
                                wts = lang_v
                        elif conv is int:
                            beam = v
                        else:
                            lang = v
                del fs
            except Exception:
                fname, data = "audio.wav", raw
        elif "application/json" in ct:
            return self._err("这个服务收音频二进制，不收 JSON")

        if not data:
            return self._err("没拿到音频数据")
        suffix = os.path.splitext(fname)[1] or ".wav"
        tmp = os.path.join(tempfile.gettempdir(), f"whisper_{int(time.time()*1000)}{suffix}")
        with open(tmp, "wb") as f:
            f.write(data)
        try:
            t0 = time.time()
            res = do_transcribe(tmp, language=lang, beam=beam, vad=vad, word_ts=wts)
            res["ok"] = True
            res["elapsed"] = round(time.time() - t0, 2)
            res["bytes"] = len(data)
            log(f"转写完成 {res['elapsed']}s / 音频 {res['duration']}s / {len(data)/1048576:.1f}MB")
            self._send(res)
        except Exception as e:
            log("转写失败：", e)
            log(traceback.format_exc())
            self._err(f"转写失败：{e}", 500)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
            maybe_unload(self.idle_timeout)


def main():
    global DEFAULT_MODEL   # 必须在读DEFAULT_MODEL 之前声明，否则 compile 报
                           # "name 'DEFAULT_MODEL' is used prior to global declaration"
    ap = argparse.ArgumentParser(description="Whisper 语音转文本服务")
    ap.add_argument("--host", default="0.0.0.0",
                    help="监听地址。公网映射进来必须 0.0.0.0，默认就是")
    ap.add_argument("--port", type=int, default=8185, help="统一端口 8185")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"模型，默认 {DEFAULT_MODEL}")
    ap.add_argument("--idle-timeout", type=int, default=1800,
                    help="空闲多少秒卸载模型（把显存还给 ComfyUI），0=不卸")
    ap.add_argument("--preload", action="store_true", help="启动即加载模型")
    a = ap.parse_args()
    DEFAULT_MODEL = a.model
    Handler.idle_timeout = a.idle_timeout

    # 模型走 hf-mirror 拉（远端直连 huggingface.co 不通），拉完就离线
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    if a.preload:
        try:
            get_model(a.model)
        except Exception as e:
            log("预加载失败（不阻断启动，首次请求会重试）：", e)

    # 空闲卸载的守护线程
    if a.idle_timeout > 0:
        def _tick():
            while True:
                time.sleep(60)
                try:
                    maybe_unload(a.idle_timeout)
                except Exception:
                    pass
        threading.Thread(target=_tick, daemon=True).start()

    log(f"启动：{a.host}:{a.port} model={a.model} idle_timeout={a.idle_timeout}s")
    Server((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
