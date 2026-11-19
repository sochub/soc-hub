#!/usr/bin/env node
// Records the website tour video (tour.mp4 + tour.vtt + tour-poster.jpg) from the demo build.
//
//   scripts/build-demo.sh
//   (cd frontend && npx vite preview --mode demo --outDir dist-demo --port 4180) &
//   node scripts/record-tour.mjs [outDir]   # default: ./tour-out
//
// Then copy outDir/tour.mp4 + tour.vtt to website-public/public/video/ and tour-poster.jpg to
// website-public/public/images/soc-hub/. Needs Node 22+ (global WebSocket), Google Chrome and ffmpeg.
// Captions must describe only what is on screen: the demo data has no AI triage results, for example.
import { spawn, execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const out = resolve(process.argv[2] || 'tour-out');
const B = process.env.DEMO_URL || 'http://localhost:4180/demo/#';
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const W = 1440, H = 712, PORT = 9351;
const work = join(tmpdir(), `sochub-tour-${process.pid}`);
mkdirSync(`${work}/f`, { recursive: true }); mkdirSync(out, { recursive: true });

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${work}/prof`,
  '--hide-scrollbars', `--window-size=${W},${H}`, 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let t; for (let i = 0; i < 40 && !t; i++) { await sleep(250); try { t = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find((x) => x.type === 'page'); } catch {} }
if (!t) { chrome.kill(); throw new Error('Chrome did not start'); }
const ws = new WebSocket(t.webSocketDebuggerUrl); await new Promise((r) => ws.addEventListener('open', r));

let id = 0; const pending = new Map(); const frames = [];
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
ws.addEventListener('message', (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result ?? m.error); pending.delete(m.id); }
  if (m.method === 'Page.screencastFrame') {
    writeFileSync(`${work}/f/${String(frames.length).padStart(5, '0')}.jpg`, Buffer.from(m.params.data, 'base64'));
    frames.push(m.params.metadata.timestamp); send('Page.screencastFrameAck', { sessionId: m.params.sessionId });
  }
});
const js = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result?.value;
const hideToast = () => js("document.getElementById('demo-notice')?.remove()");
const go = async (hash) => { await js(`location.hash = ${JSON.stringify(hash)}`); await sleep(1600); await hideToast(); };
const scroll = (y, ms = 1800) => js(`document.querySelector('main').scrollTo({ top: ${y}, behavior: 'smooth' })`).then(() => sleep(ms));
// Waits up to 3s for the control to render (the page may still be loading its data).
const click = async (txt) => {
    for (let i = 0; i < 30; i++) {
        const ok = await js(`(() => { const e = [...document.querySelectorAll('button,a,[role=tab]')].find(e => e.offsetParent && e.textContent.trim().startsWith(${JSON.stringify(txt)})); e?.click(); return !!e; })()`);
        if (ok) return;
        await sleep(100);
    }
    console.warn(`  ! no control "${txt}"`);
};
const key = async (k, code, vk) => { for (const type of ['keyDown', 'keyUp']) await send('Input.dispatchKeyEvent', { type, key: k, code, windowsVirtualKeyCode: vk }); };
const typeSlow = async (s) => { for (const ch of s) { await send('Input.insertText', { text: ch }); await sleep(110); } };

await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] });
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: `${B}/` }); await sleep(4000); await hideToast();

const cues = [];
const scene = async (caption, fn) => { cues.push({ at: Date.now() / 1000, caption }); await fn(); };

await send('Page.startScreencast', { format: 'jpeg', quality: 88, maxWidth: W, maxHeight: H });
await sleep(300);
await scene('Overview: SLA at risk, trends and severity. Every widget opens the matching cases', async () => { await sleep(1500); await scroll(420, 2200); await scroll(0, 900); });
await scene('Alerts arrive from your SIEM, EDR and email security, paged 10, 40 or 100 at a time', async () => { await go('/alerts'); await sleep(2600); });
await scene('Cases as a board: drag a card to change its status', async () => { await go('/cases?view=board'); await sleep(2800); });
await scene('Press ⌘K to jump to any case, IOC or page', async () => {
  await js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',metaKey:true,ctrlKey:true,bubbles:true}))"); await sleep(600);
  await typeSlow('phish'); await sleep(2400); await key('Escape', 'Escape', 27); await sleep(600);
});
// The case the copilot fixture was recorded for (demo_fixtures.py COPILOT_CASE); ids change with every demo build.
const fixtures = await (await fetch(new URL('demo-fixtures.json', B.replace(/#.*$/, '')))).json();
const heroCase = process.env.TOUR_CASE
    || Object.keys(fixtures.responses).map((k) => k.match(/^\/copilot\/sessions\/(\d+)$/)?.[1]).find(Boolean);
await scene('A case: SLA, owner and the incident at a glance, with one click to ask the AI for triage', async () => { await go(`/cases/${heroCase}`); await sleep(1600); await scroll(330, 2200); });
await scene('The copilot runs on your model and proposes; nothing is written until you confirm', async () => {
  await scroll(0, 500); await js("window.dispatchEvent(new Event('copilot:toggle'))"); await sleep(4200);
  await js("window.dispatchEvent(new Event('copilot:toggle'))"); await sleep(400);
});
await scene('Import MITRE-mapped IR playbooks from the marketplace', async () => { await go('/playbooks'); await click('Marketplace'); await sleep(3000); });
await scene('Build workflows visually: triggers, case actions, Slack approvals', async () => { await go('/workflows/7'); await sleep(3600); });
await scene('The investigation graph links cases through shared indicators', async () => { await go('/graph'); await sleep(5200); });
await send('Page.stopScreencast'); await sleep(300);
ws.close(); chrome.kill();

// Frames arrive only when the page changes, so each one is held until the next (ffmpeg concat durations).
const t0 = frames[0], end = frames.at(-1) + 0.5;
const list = frames.map((ts, i) => `file '${work}/f/${String(i).padStart(5, '0')}.jpg'\nduration ${Math.max((frames[i + 1] ?? end) - ts, 0.001).toFixed(4)}`);
list.push(`file '${work}/f/${String(frames.length - 1).padStart(5, '0')}.jpg'`);
writeFileSync(`${work}/list.txt`, list.join('\n') + '\n');
execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', `${work}/list.txt`,
  '-vf', `fps=30,scale=${W}:${H},format=yuv420p`, '-c:v', 'libx264', '-preset', 'slow', '-crf', '26', '-movflags', '+faststart', '-an', `${out}/tour.mp4`]);
execFileSync('ffmpeg', ['-v', 'error', '-y', '-ss', '0.5', '-i', `${out}/tour.mp4`, '-frames:v', '1', '-q:v', '3', `${out}/tour-poster.jpg`]);

const stamp = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${(s % 60).toFixed(3).padStart(6, '0')}`;
const starts = cues.map((c, i) => (i === 0 ? 0 : Math.max(c.at - t0, 0)));
const vtt = cues.map((c, i) => `${stamp(starts[i])} --> ${stamp(starts[i + 1] ?? end - t0)}\n${c.caption}\n`);
writeFileSync(`${out}/tour.vtt`, `WEBVTT\n\n${vtt.join('\n')}`);
rmSync(work, { recursive: true, force: true });
console.log(`${frames.length} frames, ${(end - t0).toFixed(1)}s -> ${out}`);
