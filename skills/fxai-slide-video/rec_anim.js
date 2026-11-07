#!/usr/bin/env node
'use strict';
/*
 * rec_anim.js —— 用 Chrome DevTools Protocol 的 screencast「真实录制」网页动画 / 操作
 *
 * 与 slides.py:render_anim 的「逐帧定格截图」不同：
 *   本工具只启动一次 Chrome，让页面动画**按真实时间播放**，同时用 CDP 的
 *   Page.startScreencast 持续抓屏，拿到的是「实时录屏帧」（带真实时间戳）。
 *   最后写出 frames.txt（concat 清单，含每帧真实时长），交给 ffmpeg 归一化。
 *
 * 零依赖：Node 22 自带 fetch + WebSocket。
 *
 * 用法：
 *   node rec_anim.js --html <file.html> --out <dir> --duration 1.7
 *                    [--fps 30]           归一化目标帧率（默认 30）
 *                    [--wait 150]         页面 load 后、开录前的等待(ms)，避开白屏/未渲染
 *                    [--preroll 80]       起录后抓「动画起点」的预抓时长(ms)
 *                    [--idle 450]         画面静止多久算「播完」自动收工(ms)
 *                    [--js <file.js>]     开录后执行的脚本（可做点击/输入等交互，await 完成）
 *                    [--gpu|--no-gpu]     是否用显卡渲染（默认用）
 *                    [--format png|jpeg] [--quality 92] [--every 1]
 *                    [--w 768] [--h 1280] [--chrome <exe>]
 *                    [--scroll auto|wheel|none]   内容超出视口时是否自动上滚（默认 auto）
 *                    [--scroll-dur 0]     滚动时长(ms)，0=自动
 *                    [--scroll-min 800] [--scroll-max 6000] [--scroll-speed 500]
 *                    自动时长 = clamp(超出px / 速度 × 1000, min, max)
 *                    [--scroll-hold 350]  滚完停留(ms)再收工
 *                    [--hold 0]        静态页面（不出帧）把最后一帧补齐到这么长(ms)
 *
 * 自动滚动（★ 录制端独立能力，页面无需写任何滚动代码）：
 *   画面静止、即将收工时，录制端自己量页面内容高度：
 *     ① 先看原生可滚动量 `documentElement.scrollHeight - innerHeight`（普通网页/别人的页面走这条）；
 *     ② 若为 0（如我们自己的页面 html/body 是 overflow:hidden），解锁后再量内容最大底边。
 *   超出阈值(>120px)就模拟上滚。
 *     · mode=auto ：注入一段 setInterval 逐步 window.scrollTo（精确、平滑，easeInOutQuad）
 *     · mode=wheel：用 CDP Input.dispatchMouseEvent(type=mouseWheel) 发真实滚轮事件
 *                   （对监听 wheel 的第三方页面更"像人在滚"）
 *   滚完再静默一次才收工。position:fixed 的装饰（品牌条/满屏底图）保持视口锚定不动。
 *
 * 产物：
 *   <out>/f_0000.png …        实时录制帧
 *   <out>/frames.txt          concat 清单（每帧真实时长，用于 VFR→CFR 归一化）
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const { pathToFileURL } = require('url');

function arg(n, d) { const i = process.argv.indexOf('--' + n); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d; }
function flag(n) { return process.argv.includes('--' + n); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const HTML = arg('html');
const URL_ARG = arg('url', '');        // 直接录一个真实网页（别人的页面也能录）
const NAV_TIMEOUT = parseInt(arg('nav-timeout', URL_ARG ? '30000' : '10000'), 10);
const OUT = arg('out');
const DURATION = parseFloat(arg('duration', '1.7'));
const FPS = parseInt(arg('fps', '24'), 10);
const WAIT = parseInt(arg('wait', '150'), 10);
const PREROLL = parseInt(arg('preroll', '80'), 10);
const IDLE = parseInt(arg('idle', '450'), 10);
const JSFILE = arg('js', '');
const FORMAT = arg('format', 'png');
const QUALITY = parseInt(arg('quality', '92'), 10);
const EVERY = parseInt(arg('every', '1'), 10);
const W = parseInt(arg('w', '768'), 10);
const H = parseInt(arg('h', '1280'), 10);
const USE_GPU = !flag('no-gpu');            // 默认用显卡渲染
const PROBE = flag('probe-gpu');            // 只探测显卡能力，不录制

// 浏览器路径按环境解析：--chrome > CHROME_PATH > Program Files 环境变量下探测
function defaultChrome() {
  const dirs = [process.env.ProgramFiles, process.env['ProgramFiles(x86)'],
                process.env.ProgramW6432].filter(Boolean);
  const rels = [['Google', 'Chrome', 'Application', 'chrome.exe'],
                ['Microsoft', 'Edge', 'Application', 'msedge.exe']];
  for (const d of dirs) {
    for (const r of rels) {
      const p = path.join(d, ...r);
      if (fs.existsSync(p)) return p;
    }
  }
  return '';
}
const CHROME = arg('chrome', process.env.CHROME_PATH || defaultChrome());
if (!CHROME) {
  console.error('[rec_anim] 找不到 Chrome/Edge：传 --chrome <exe>，或设 CHROME_PATH，或把浏览器加入 PATH');
  process.exit(2);
}
// —— 自动滚动（录制端独立能力） ——
const SCROLL = arg('scroll', 'auto');       // auto | wheel | none
const SCROLL_DUR = parseInt(arg('scroll-dur', '0'), 10);    // 0 = 自动
const SCROLL_MIN = parseInt(arg('scroll-min', '800'), 10);
const SCROLL_MAX = parseInt(arg('scroll-max', '6000'), 10);
const SCROLL_SPEED = parseFloat(arg('scroll-speed', '500'));  // px/s：正常滚动速度
const AUDIO_DUR = parseInt(arg('audio-dur', '0'), 10);        // 本段音频时长(ms)：以时间为准算能滚多远
const HOLD = parseInt(arg('hold', '0'), 10);                  // 静态页面补到至少这么长(ms)，免得导出空片段
const SCROLL_HOLD = parseInt(arg('scroll-hold', '350'), 10);

if (!PROBE && ((!HTML && !URL_ARG) || !OUT)) {
  console.error('用法: node rec_anim.js --html <file.html> --out <dir> --duration 1.7 [--fps 30] [--wait 150] [--js act.js]');
  console.error('      node rec_anim.js --url <https://…> --out <dir> --duration 6   # 直接录真实网页');
  console.error('      node rec_anim.js --probe-gpu     # 探测 Chrome 是否真的在用显卡');
  process.exit(2);
}
// 录真实网页时要联网，超时放宽；本地文件保持短一些
const tmpHtml = path.join(OUT || os.tmpdir(), '_rec_src.html');
if (!PROBE) {
  fs.mkdirSync(OUT, { recursive: true });
  if (URL_ARG) {
    fs.writeFileSync(tmpHtml, URL_ARG, 'utf8');   // 只是占位，后面用 URL 导航
  } else {
    // 把动画定格到 0 时刻（--T:0s）且保持 paused；等录制开始后再放开播放，
    // 这样第一帧就是「动画起点」，不会先闪一下终态。
    let html = fs.readFileSync(HTML, 'utf8');
    html = html.replace(':root{--T:3s}', ':root{--T:0s}');
    fs.writeFileSync(tmpHtml, html, 'utf8');
  }
}

const userDir = fs.mkdtempSync(path.join(os.tmpdir(), 'cdprec-'));
const chromeArgs = [
  '--headless=new', '--no-sandbox', '--hide-scrollbars',
  '--force-device-scale-factor=1', `--window-size=${W},${H}`,
  '--remote-debugging-port=0', `--user-data-dir=${userDir}`,
  '--allow-file-access-from-files',
];
if (USE_GPU) {
  // 优先显卡渲染：ANGLE/D3D11 后端 + GPU 光栅化 + 零拷贝
  chromeArgs.push('--use-gl=angle', '--use-angle=d3d11',
                  '--enable-gpu', '--ignore-gpu-blocklist',
                  '--enable-gpu-rasterization', '--enable-zero-copy');
} else {
  chromeArgs.push('--disable-gpu');
}
chromeArgs.push('about:blank');
const proc = spawn(CHROME, chromeArgs, { stdio: 'ignore' });

async function waitPort() {
  const f = path.join(userDir, 'DevToolsActivePort');
  const t0 = Date.now();
  while (Date.now() - t0 < 20000) {
    if (fs.existsSync(f)) {
      const line = fs.readFileSync(f, 'utf8').split('\n')[0].trim();
      if (line) return parseInt(line, 10);
    }
    await sleep(120);
  }
  throw new Error('Chrome CDP 端口未就绪（检查 chrome 路径）');
}

// ---------- 自动滚动：录制端自己量高度、自己模拟滚动（页面不需要写任何滚动代码） ----------
// ① 先量原生可滚动量（普通网页 / 别人的页面）；
// ② 若为 0（我们自己的页面 html/body 是 overflow:hidden），解锁后再量内容最大底边。
const OVERFLOW_JS = `(function(){
  var H = window.innerHeight || ${H};
  var native = Math.max(0, (document.documentElement.scrollHeight || 0) - H);
  if (native > 120) return JSON.stringify({ over: Math.ceil(native), how: 'native', H: H });
  var st = document.getElementById('__fx_scroll_style');
  if (!st) {
    st = document.createElement('style'); st.id = '__fx_scroll_style';
    st.textContent = 'html{height:auto!important;overflow:auto!important}body{overflow:visible!important}';
    document.head.appendChild(st);
  }
  void document.body.offsetHeight;   // 强制重排后再量
  var max = 0, els = document.body.querySelectorAll('*');
  for (var i = 0; i < els.length; i++) {
    var el = els[i], cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed') continue;
    var b = el.getBoundingClientRect();
    if (!b.width && !b.height) continue;
    if (b.bottom > max) max = b.bottom;
  }
  return JSON.stringify({ over: Math.ceil(max - H), how: 'unlock', H: H });
})()`;

// 逐步 scrollTo：由录制端注入一段定时器（不是页面自己写的逻辑），easeInOutQuad
function scrollByJs(send, over, dur) {
  return send('Runtime.evaluate', {
    expression: `(function(){
      var over=${over}, dur=${dur}, t0=null;
      var iv=setInterval(function(){
        var now=performance.now();
        if(t0===null) t0=now;
        var p=Math.min(1,(now-t0)/dur);
        var e=p<.5?2*p*p:1-Math.pow(-2*p+2,2)/2;
        window.scrollTo(0, Math.round(over*e));
        if(p>=1) clearInterval(iv);
      },16);
      return 1;
    })()`,
    returnByValue: true,
  });
}

// 真实滚动手势：CDP Input.synthesizeScrollGesture（平滑、连续，对监听滚动的第三方页面最"像人在滚"）
// 滚动手势用 Input.synthesizeScrollGesture：连续位移，帧率平稳。
async function scrollByWheel(send, over, dur) {
  const speed = Math.max(120, Math.round((over / dur) * 1000));   // px/s
  try {
    await send('Input.synthesizeScrollGesture', {
      x: Math.round(W / 2), y: Math.round(H / 2),
      xDistance: 0, yDistance: -over, speed: speed,
      gestureSourceType: 'mouse', preventFling: true,
    });
  } catch (e) {
    // 兜底：老版本没有该命令时，退回逐步 scrollTo
    await scrollByJs(send, over, dur);
  }
}

// 需要滚吗？需要就滚，返回 true（继续录）；不需要返回 false（可以收工）
//
// ★ 核心：**以「音频时长」为准，而不是以「内容高度」为准**。
//   给了 --audio-dur → 先按正常速度算出「这段时间最多能滚多远」，再和实际超出量取小：
//     · 页面比时间能滚的还长 → 只滚到时间允许的位置（滚不完就滚不完，无所谓）
//     · 页面比时间能滚的短   → 用满整个时长慢慢滚完（速度自动变慢）
//   没给 --audio-dur → 退回按「超出px / 速度」折算时长，夹到 [min,max]。
async function tryScroll(send) {
  if (SCROLL === 'none') return false;
  let info = null;
  try {
    const r = await send('Runtime.evaluate', { expression: OVERFLOW_JS, returnByValue: true });
    info = JSON.parse(r.result.value);
  } catch (e) { return false; }
  if (!info || !(info.over > 120)) return false;
  const spd = SCROLL_SPEED > 0 ? SCROLL_SPEED : 500;
  let dist = info.over, dur;
  if (AUDIO_DUR > 0) {
    const able = Math.round((AUDIO_DUR / 1000) * spd);   // 这段时间按正常速度能滚多远
    dist = Math.min(info.over, able);
    dur = AUDIO_DUR;                                     // 用满音频时长
  } else if (SCROLL_DUR > 0) {
    dur = SCROLL_DUR;
  } else {
    dur = Math.min(SCROLL_MAX, Math.max(SCROLL_MIN, Math.round((info.over / spd) * 1000)));
  }
  if (dist <= 16) return false;
  if (SCROLL === 'wheel') await scrollByWheel(send, dist, dur);
  else await scrollByJs(send, dist, dur);
  console.log(`SCROLL 超出 ${info.over}px（${info.how}） 实际滚 ${dist}px / ${dur}ms`
    + `  均速 ${Math.round(dist / (dur / 1000))}px/s  mode=${SCROLL}`);
  return true;
}

async function recordRun() {
  let ws = null;
  const frames = [];
  try {
    const port = await waitPort();
    const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const page = list.find((t) => t.type === 'page') || list[0];
    if (!page) throw new Error('未找到可录制页面');

    ws = new WebSocket(page.webSocketDebuggerUrl);
    let id = 0;
    const pending = new Map();
    const events = [];
    let frameIdx = 0;
    let lastFrameAt = 0;
    let preCount = 0;   // 预抓帧数，稍后裁掉（只留最后一张当动画起点）

    function send(method, params = {}) {
      return new Promise((res, rej) => {
        const i = ++id;
        pending.set(i, (m) => (m.error ? rej(new Error(method + ': ' + JSON.stringify(m.error))) : res(m.result)));
        ws.send(JSON.stringify({ id: i, method, params }));
      });
    }

    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
      if (msg.method === 'Page.screencastFrame') {
        const p = msg.params;
        const ext = FORMAT === 'jpeg' ? 'jpg' : 'png';
        const name = `f_${String(frameIdx).padStart(4, '0')}.${ext}`;
        fs.writeFileSync(path.join(OUT, name), Buffer.from(p.data, 'base64'));
        frames.push({ name, ts: p.metadata && p.metadata.timestamp ? p.metadata.timestamp : null });
        frameIdx++;
        lastFrameAt = Date.now();
        send('Page.screencastFrameAck', { sessionId: p.sessionId }).catch(() => {});
      } else if (msg.method) {
        events.push(msg.method);
      }
    });
    await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });

    await send('Page.enable');
    await send('Runtime.enable');
    // 🔴 用 CDP 强制把视口固定成 W×H，保证录制帧 1:1 不变形。
    await send('Emulation.setDeviceMetricsOverride', {
      width: W, height: H, deviceScaleFactor: 1, mobile: false,
    });
    const navUrl = URL_ARG || pathToFileURL(tmpHtml).href;
    await send('Page.navigate', { url: navUrl });
    const t0 = Date.now();
    while (Date.now() - t0 < NAV_TIMEOUT) { if (events.includes('Page.loadEventFired')) break; await sleep(60); }
    // 开录前的等待：让字体/图片/布局彻底就位，避开白屏或半渲染（联网页面建议 --wait 大一些）
    await sleep(Math.max(0, WAIT));

    await send('Page.startScreencast', { format: FORMAT, quality: QUALITY, everyNthFrame: EVERY, maxWidth: W, maxHeight: H });
    await sleep(PREROLL); // 先抓几帧「起点」画面
    preCount = frames.length;
    // 放开动画，开始按真实时间播放
    await send('Runtime.evaluate', { expression: "(function(){var s=document.createElement('style');s.textContent='*{animation-play-state:running !important}';document.head.appendChild(s);return 1;})()" });

    // 可选：执行交互脚本（点击/输入/跳转…），全程都在录
    if (JSFILE) {
      const code = fs.readFileSync(JSFILE, 'utf8');
      const wrapped = `(async()=>{${code}\n})()`;
      const r = await send('Runtime.evaluate', { expression: wrapped, awaitPromise: true, returnByValue: true });
      if (r && r.exceptionDetails) console.error('JS 脚本报错:', JSON.stringify(r.exceptionDetails).slice(0, 300));
    }

    // 录到「画面停住」（idle 无新帧）或到达时长上限为止。screencast 只在画面变化时出帧，
    // 动画/操作结束即静默，收工判据就是「连续静默」，不按整段时长计时。
    // 静默时由录制端判断「内容是否超出视口」：超出就模拟上滚，滚完再静默一次才收工。
    const tStart = Date.now();
    let scrollDone = SCROLL === 'none', busy = false;
    await new Promise((res) => {
      const timer = setInterval(() => {
        if (busy) return;
        const now = Date.now();
        if (now - tStart > DURATION * 1000) { clearInterval(timer); res(); return; }
        if (!(lastFrameAt && now - lastFrameAt > IDLE && now - tStart > PREROLL + 120)) return;
        if (scrollDone) { clearInterval(timer); res(); return; }
        busy = true;
        tryScroll(send).then((did) => {
          busy = false;
          scrollDone = true;
          if (did) { lastFrameAt = Date.now(); return; }   // 已滚动 → 继续录（滚完静默再收工）
          clearInterval(timer); res();
        }).catch(() => { busy = false; scrollDone = true; clearInterval(timer); res(); });
      }, 40);
    });
    if (SCROLL_HOLD > 0) await sleep(SCROLL_HOLD);
    await send('Page.stopScreencast');
    await sleep(150);

    // 丢掉预抓的帧，只留最后一张当动画起点
    // 注意：只裁数组，不改磁盘文件名——concat 清单按 name 引用，多出的文件无害
    if (preCount > 1) frames.splice(0, preCount - 1);

    // concat 清单：每帧按真实时间戳给时长
    const lines = [];
    for (let i = 0; i < frames.length; i++) {
      let d;
      if (i < frames.length - 1 && frames[i].ts && frames[i + 1].ts) d = Math.max(0.001, frames[i + 1].ts - frames[i].ts);
      else if (i > 0 && frames[i].ts && frames[i - 1].ts) d = Math.max(0.001, frames[i].ts - frames[i - 1].ts);
      else d = 1 / FPS;
      // 首帧是「放开动画前」的暂停态：它到第二帧的间隔含预抓等待，但那段时间动画并未推进 ——
      // 片段开头用相邻帧的常规间隔。
      if (i === 0) {
        if (frames.length > 2 && frames[1].ts && frames[2].ts) d = Math.max(0.001, frames[2].ts - frames[1].ts);
        else d = 1 / FPS;
      }
      lines.push(`file '${frames[i].name}'`);
      lines.push(`duration ${d.toFixed(5)}`);
    }
    if (frames.length) lines.push(`file '${frames[frames.length - 1].name}'`);

    const span = frames.length > 1 && frames[0].ts && frames[frames.length - 1].ts
      ? frames[frames.length - 1].ts - frames[0].ts : 0;
    // 静态页面几乎不出帧（不滚动、没动画时可能只有 1 帧）：把最后一帧拉长补齐到 --hold，
    // 免得导出一个 0.03s 的空片段。
    if (HOLD > 0 && frames.length && span < HOLD / 1000) {
      const add = HOLD / 1000 - span;
      for (let i = lines.length - 1; i >= 0; i--) {
        if (lines[i].startsWith('duration ')) {
          lines[i] = 'duration ' + (parseFloat(lines[i].slice(9)) + add).toFixed(5);
          break;
        }
      }
    }
    fs.writeFileSync(path.join(OUT, 'frames.txt'), lines.join('\n') + '\n', 'utf8');

    console.log(`REC ${frames.length} 帧  时长≈${span.toFixed(3)}s  [原生抓帧 ${span > 0 ? (frames.length / span).toFixed(1) : '?'}fps → 输出归一化 ${FPS}fps]  gpu=${USE_GPU ? 'on' : 'off'}  -> ${OUT}`);
  } catch (e) {
    console.error('REC FAIL', e.message);
    process.exitCode = 1;
  } finally {
    try { if (ws) ws.close(); } catch (e) {}
    try { proc.kill(); } catch (e) {}
    setTimeout(() => { try { fs.rmSync(userDir, { recursive: true, force: true }); } catch (e) {} process.exit(process.exitCode || 0); }, 300);
  }
}

// ---------- 显卡探测：CDP SystemInfo.getInfo 真查一次 GL 渲染器 ----------
async function probeGpu() {
  const out = { ok: false, renderer: '', vendor: '', software: false };
  let ws = null;
  try {
    const port = await waitPort();
    const ver = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
    ws = new WebSocket(ver.webSocketDebuggerUrl);
    let id = 0;
    const pending = new Map();
    function send(method, params = {}) {
      return new Promise((res, rej) => {
        const i = ++id;
        pending.set(i, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)));
        ws.send(JSON.stringify({ id: i, method, params }));
      });
    }
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    });
    await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });
    const info = await send('SystemInfo.getInfo');
    const aux = (info && info.gpu && info.gpu.auxAttributes) || {};
    out.renderer = aux.glRenderer || '';
    out.vendor = aux.glVendor || '';
    out.ok = !!out.renderer;
    out.software = /swiftshader|software|llvmpipe|basic render/i.test(out.renderer + ' ' + out.vendor);
  } catch (e) {
    out.error = String((e && e.message) || e).slice(0, 160);
  } finally {
    try { if (ws) ws.close(); } catch (e) {}
    try { proc.kill(); } catch (e) {}
    // 清理临时 profile 交给**分离的子进程**去删：本进程必须立刻同步退出。
    // 原因：Chrome 刚被 kill，临时目录里的句柄还没释放，fs.rmSync 会阻塞事件循环 ——
    // 一旦阻塞，连 setTimeout 兜底都来不及触发，进程就永远退不出；调用方 subprocess
    // 只能等到超时，把「显卡可用」误判成「不支持显卡」，整条管线退回软件渲染。
    try {
      spawn(process.execPath,
            ['-e', `require('fs').rmSync(${JSON.stringify(userDir)},{recursive:true,force:true})`],
            { detached: true, stdio: 'ignore' }).unref();
    } catch (e) {}
  }
  return out;
}

if (PROBE) {
  // 打印完结果**立刻同步退出**：探测路径不做任何延时收尾（见 probeGpu 里的说明）。
  probeGpu().then((r) => {
    console.log(JSON.stringify(r));
    process.exit(0);
  }).catch((e) => {
    console.log(JSON.stringify({ ok: false, renderer: '', vendor: '', software: false, error: String(e).slice(0, 160) }));
    process.exit(0);
  });
} else {
  recordRun();
}
