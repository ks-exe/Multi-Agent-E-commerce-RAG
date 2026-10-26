/** Screenshot complete captured command output. No results are synthesized. */
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const browser = process.env.BROWSER_PATH || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].find(existsSync);
if (!browser) throw new Error('Set BROWSER_PATH to Chrome or Edge.');
const profile = path.join(root, '.build', 'execution-capture-' + Date.now());
const output = path.join(root, 'docs/submission/images');
await mkdir(profile, {recursive: true});
await mkdir(output, {recursive: true});
const child = spawn(browser, ['--headless=new', '--remote-debugging-port=0',
  '--no-first-run', '--no-default-browser-check', '--disable-extensions',
  '--disable-background-networking', '--user-data-dir=' + profile, 'about:blank'],
  {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});
let socket;
try {
  const endpoint = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Browser startup timed out')), 20000);
    child.once('error', reject);
    let stderr = '';
    child.stderr.on('data', chunk => {
      stderr += chunk;
      const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {clearTimeout(timer); resolve(match[1]);}
    });
  });
  socket = new WebSocket(endpoint);
  await new Promise(resolve => socket.addEventListener('open', resolve, {once: true}));
  let id = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const m = JSON.parse(event.data), p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id); clearTimeout(p.timer);
    if (m.error) p.reject(new Error(m.error.message)); else p.resolve(m.result);
  });
  function call(method, params = {}, sessionId) {
    return new Promise((resolve, reject) => {
      const key = ++id;
      const timer = setTimeout(() => reject(new Error(method + ' timed out')), 15000);
      pending.set(key, {resolve, reject, timer});
      socket.send(JSON.stringify({id:key, method, params, ...(sessionId ? {sessionId} : {})}));
    });
  }
  const {targetId} = await call('Target.createTarget', {url:'about:blank'});
  const {sessionId} = await call('Target.attachToTarget', {targetId, flatten:true});
  const page = (method, params) => call(method, params, sessionId);
  const evaluate = async expression => (await page('Runtime.evaluate',
    {expression, returnByValue:true, awaitPromise:true})).result.value;
  await page('Page.enable');
  await page('Emulation.setDeviceMetricsOverride',
    {width:1500, height:1000, deviceScaleFactor:1, mobile:false});
  const escape = text => text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
  const cases = process.argv.slice(2);
  for (const name of cases.length ? cases : ['task2','task3','task4','task5','task6','all','parallel','live']) {
    if (!/^(task[2-6]|all|parallel|live|deploy|infrastructure)$/.test(name)) throw new Error('Unknown capture: ' + name);
    const bytes = await readFile(path.join(root, 'docs/submission/logs', name + '.txt'));
    const meta = JSON.parse(await readFile(path.join(root, 'docs/submission/logs', name + '.json'), 'utf8'));
    if (createHash('sha256').update(bytes).digest('hex') !== meta.transcript_sha256)
      throw new Error('Transcript checksum mismatch: ' + name);
    const html = `<!doctype html><meta charset="utf-8"><style>
      *{box-sizing:border-box}body{margin:0;background:#101822;color:#eef4fb;padding:32px 44px;font:18px/1.5 Consolas,monospace}
      header{border-bottom:1px solid #435267;padding-bottom:20px}h1{font:600 26px system-ui;margin:0 0 8px}
      .muted{color:#b8c5d6;font-size:15px}.command{color:#8dddc8;font-weight:700;margin:24px 0 0;white-space:pre-wrap}
      pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0 20px;font:inherit}
      footer{border-top:1px solid #435267;padding-top:18px;color:#b8c5d6;font-size:15px}
    </style><header><h1>NovaMart / ${escape(name)} / execution log</h1>
      <div class="muted">UTC ${escape(meta.started_utc)} &nbsp; | &nbsp; Python ${escape(meta.python_version)}</div>
      <div class="muted">Screenshot of recorded stdout and stderr; original transcript accompanies this image.</div></header>
      <div class="command">$ ${escape(meta.command)}</div><pre>${escape(bytes.toString('utf8'))}</pre>
      <footer>Process exit: ${meta.exit_code} &nbsp; | &nbsp; Finished UTC ${escape(meta.finished_utc)}<br>
      SHA-256: ${meta.transcript_sha256}</footer>`;
    await page('Page.setDocumentContent', {frameId:(await page('Page.getFrameTree')).frameTree.frame.id, html});
    await evaluate('document.fonts.ready');
    const height = await evaluate('document.documentElement.scrollHeight');
    const result = await page('Page.captureScreenshot', {format:'png', captureBeyondViewport:true,
      clip:{x:0,y:0,width:1500,height,scale:1}});
    await writeFile(path.join(output,name + '.png'),Buffer.from(result.data,'base64'));
    console.log(`Captured ${name}.png (${height}px); transcript checksum verified.`);
    if(name === 'parallel') {
      const summary=bytes.toString('utf8').split('\n').filter(line=>line.startsWith('PASS ')).join('\n');
      await evaluate(`document.querySelector('pre').textContent=${JSON.stringify(summary)};
        document.querySelector('h1').textContent='NovaMart / parallel retrieval / PASS lines excerpt';`);
      const shot=await page('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,
        clip:{x:0,y:0,width:1500,height:await evaluate('document.documentElement.scrollHeight'),scale:1}});
      await writeFile(path.join(output,'parallel-summary.png'),Buffer.from(shot.data,'base64'));
      console.log('Captured parallel-summary.png; explicitly labeled excerpt.');
    }
  }
  await call('Browser.close');
} finally {
  socket?.close();
  if(child.exitCode === null) child.kill();
}
