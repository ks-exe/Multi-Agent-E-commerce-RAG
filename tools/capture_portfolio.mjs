/** Capture the real local evidence page with Chromium; Node 22+, no npm packages. */
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdir, writeFile} from 'node:fs/promises';
import {fileURLToPath, pathToFileURL} from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const browser = process.env.BROWSER_PATH || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(existsSync);
if (!browser) throw new Error('Set BROWSER_PATH to an installed Chrome or Edge executable.');
const out = path.join(root, 'docs', 'images');
const profile = path.join(root, '.build', 'portfolio-browser-' + Date.now());
await mkdir(out, {recursive: true});
await mkdir(profile, {recursive: true});
const child = spawn(browser, [
  '--headless=new', '--remote-debugging-port=0', '--disable-background-networking',
  '--no-first-run', '--no-default-browser-check', '--disable-extensions',
  '--user-data-dir=' + profile, 'about:blank',
], {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});
let socket;
try {
  const endpoint = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Browser startup timed out.')), 20000);
    child.once('error', error => {clearTimeout(timer); reject(error);});
    child.once('exit', code => {clearTimeout(timer); reject(new Error('Browser exited: ' + code));});
    let stderr = '';
    child.stderr.on('data', chunk => {
      stderr += chunk;
      const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {clearTimeout(timer); resolve(match[1]);}
    });
  });
  socket = new WebSocket(endpoint);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, {once: true});
    socket.addEventListener('error', reject, {once: true});
  });
  let nextId = 0;
  const pending = new Map();
  const errors = [];
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
    if (!pending.has(message.id)) return;
    const {resolve, reject, timer} = pending.get(message.id);
    clearTimeout(timer); pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  });
  function call(method, params = {}, sessionId) {
    return new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => {pending.delete(id); reject(new Error(method + ' timed out.'));}, 15000);
      pending.set(id, {resolve, reject, timer});
      socket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
    });
  }
  const {targetId} = await call('Target.createTarget', {url: 'about:blank'});
  const {sessionId} = await call('Target.attachToTarget', {targetId, flatten: true});
  const page = (method, params = {}) => call(method, params, sessionId);
  const evaluate = async expression => {
    const value = await page('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (value.exceptionDetails) throw new Error(JSON.stringify(value.exceptionDetails));
    return value.result.value;
  };
  await page('Page.enable');
  await page('Runtime.enable');
  await page('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1.5, mobile: false});
  await page('Page.navigate', {url: pathToFileURL(path.join(root, 'docs', 'index.html')).href});
  for (let i = 0; i < 60; i++) {
    if (await evaluate('window.portfolioReady === true')) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.equal(await evaluate('window.portfolioReady'), true);
  assert.equal(await evaluate('document.querySelectorAll("#routes .route-row").length'), 5);
  assert.equal(await evaluate('document.querySelectorAll("#timeline .retriever").length'), 3);
  for (const tab of ['overview', 'trace', 'conversations']) {
    await evaluate('document.querySelector("[data-tab=' + tab + ']").click()');
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    assert.equal(await evaluate('document.querySelector("#' + tab + '").classList.contains("hidden")'), false);
    const height = await evaluate('Math.ceil(document.querySelector("footer").getBoundingClientRect().bottom + 28)');
    const result = await page('Page.captureScreenshot', {
      format: 'png', captureBeyondViewport: true,
      clip: {x: 0, y: 0, width: 1440, height, scale: 1},
    });
    await writeFile(path.join(out, tab + '.png'), Buffer.from(result.data, 'base64'));
    console.log('Captured docs/images/' + tab + '.png');
  }
  for (let index = 0; index < 5; index++) {
    await evaluate('document.querySelectorAll("#scenario-buttons button")[' + index + '].click()');
    assert.equal(await evaluate('document.querySelector("#response").textContent'),
      await evaluate('v.scenarios[' + index + '].response'));
  }
  await page('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  for (const tab of ['overview', 'trace', 'conversations']) {
    await evaluate('tab("' + tab + '")');
    assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true, 'Mobile overflow: ' + tab);
  }
  assert.deepEqual(errors, []);
  console.log('Verified all tabs, five captured responses, mobile layout, and absence of JavaScript exceptions.');
  await call('Browser.close');
} finally {
  socket?.close();
  if (child.exitCode === null) child.kill();
}
