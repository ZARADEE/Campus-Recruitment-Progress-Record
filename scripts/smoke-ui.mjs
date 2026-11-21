import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const native = process.argv.includes('--native');
const port = native ? 9346 : 9345;
let browser;
if (!native) {
  const profile = path.resolve('.toolchain/chrome-smoke');
  await mkdir(profile, { recursive: true });
  browser = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run', '--no-default-browser-check',
    `--user-data-dir=${profile}`, `--remote-debugging-port=${port}`, 'http://127.0.0.1:1420/',
  ], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
  browser.stderr.on('data', data => { if (data.toString().includes('ERROR')) process.stderr.write(data); });
}
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let ws;
try {
  let target;
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = targets.find(item => item.type === 'page' && !item.url.startsWith('devtools:'));
      if (target) break;
    } catch {}
    await delay(250);
  }
  assert(target, 'Browser debugging endpoint must be available');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let nextId = 0;
  const pending = new Map();
  const errors = [];
  ws.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
    if (!message.id) return;
    const promise = pending.get(message.id);
    if (!promise) return;
    pending.delete(message.id);
    message.error ? promise.reject(message.error) : promise.resolve(message.result);
  };
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const fill = (selector, value) => evaluate(`(() => { const field = document.querySelector(${JSON.stringify(selector)}); field.value = ${JSON.stringify(value)}; field.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  const until = async expression => {
    for (let attempt = 0; attempt < 80; attempt++) {
      if (await evaluate(expression)) return;
      await delay(100);
    }
    throw new Error(`Timed out: ${expression}; page=${await evaluate('location.href + " " + document.body?.innerText.slice(0,500)')}; errors=${JSON.stringify(errors)}`);
  };
  await cdp('Runtime.enable');
  if (!native) await cdp('Page.navigate', { url:'http://127.0.0.1:1420/' });
  await until('!!document.querySelector("[data-action=open-add]")');
  if (native) await until('isDesktop && jobs.length === 0 && opportunities.length === 0');
  await click('[data-view=opportunities]');
  await click('[data-action=open-add][data-mode=url]');
  assert(await evaluate('document.querySelector("#add-dialog").open'), 'URL entry opens the dialog');
  await click('[data-mode=manual]');
  await fill('#manual-company', 'Smoke Co');
  await fill('#manual-title', 'Smoke Engineer');
  await fill('#manual-city', 'Test City');
  await fill('#manual-jd', 'Smoke job description');
  await click('[data-action=save-job]');
  await until('!document.querySelector("#add-dialog").open');
  assert(await evaluate('document.querySelector(".opportunity-table").textContent.includes("Smoke Co")'));
  const id = await evaluate('opportunities.find(job => job.company === "Smoke Co").id');
  await click(`[data-action=apply-job][data-job-id="${id}"]`);
  await until('!!document.querySelector(".job-title") && document.querySelector(".job-title").textContent === "Smoke Engineer"');
  assert.equal(await evaluate('document.querySelectorAll(".round-button").length'), 4);
  await click('[data-action=set-stage][data-stage="5"]');
  await until('getJob(currentJobId).stageIndex === 5');
  await click('[data-action=open-interview][data-round=hr]');
  await fill('#question-text', 'Smoke question');
  await fill('#answer-text', 'Smoke answer');
  await fill('#session-summary', 'Smoke summary');
  if (native) {
    await cdp('Page.reload');
    await until('typeof jobs !== "undefined" && jobs.some(job => job.company === "Smoke Co")');
    await click('[data-action=open-interview][data-round=hr]');
    assert.equal(await evaluate('document.querySelector("#answer-text").value'), 'Smoke answer', 'Unsaved draft survives reload');
  }
  await click('[data-action=save-interview]');
  if (native) await until('getJob(currentJobId).activeRound === "hr"');
  await click('[data-action=open-archive]');
  assert(await evaluate('document.querySelector(".archive-qa").textContent.includes("Smoke answer")'));
  assert(await evaluate('document.querySelector(".archive-title").textContent.includes("HR")'));
  if (native) {
    await cdp('Page.reload');
    await until('typeof jobs !== "undefined" && jobs.some(job => job.company === "Smoke Co")');
    await click('[data-view=archive]');
    assert(await evaluate('document.querySelector(".archive-qa").textContent.includes("Smoke answer")'), 'Saved answer survives page reload');
    const stored = await evaluate('window.__TAURI__.core.invoke("list_jobs")');
    assert.equal(stored[0].posted, null, 'Missing publication date stays null');
    assert(stored[0].created && stored[0].updated && stored[0].appliedAt);
    assert.equal(stored[0].sessions.hr.summary, 'Smoke summary');
    await click('[data-action=open-add]');
    await evaluate(`previewImport([{company:'CSV Co',title:'Role A',city:'A',publishedAt:null,source:'csv',jd:'First job',tags:[]},{company:'CSV Co',title:'Role B',city:'B',publishedAt:'2026-11-20',source:'csv',jd:'Second job',tags:[]}])`);
    assert.equal(await evaluate('getComputedStyle(document.querySelector("#file-fields")).display'), 'none');
    await fill('#import-1-title', 'Edited Role B');
    await click('[data-action=save-job]');
    await until('!document.querySelector("#add-dialog").open');
    assert.equal(await evaluate('opportunities.filter(job => job.company === "CSV Co").length'), 2);
    assert(await evaluate('opportunities.some(job => job.title === "Edited Role B")'));
    const rejected = await evaluate(`(async () => { try { await window.__TAURI__.core.invoke('create_jobs', {drafts:[{company:'Rollback Co',title:'Valid',source:'csv'},{company:'',title:'Invalid',source:'csv'}]}); return false; } catch { return true; } })()`);
    assert(rejected);
    assert(await evaluate(`window.__TAURI__.core.invoke('list_jobs').then(jobs => !jobs.some(job => job.company === 'Rollback Co'))`), 'Invalid batch does not save a partial import');
  } else {
    await cdp('Emulation.setDeviceMetricsOverride', { width:390, height:844, deviceScaleFactor:1, mobile:true });
    assert(await evaluate('document.documentElement.scrollWidth <= 390'), 'Mobile archive has no overflow');
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: 'PASS', mode: native ? 'native IPC and SQLite' : 'browser UI', checks: ['manual import', 'URL dialog', 'apply transition', 'stage update', 'four rounds', 'Q&A archive', ...(native ? ['draft recovery', 'reload persistence', 'CSV batch preview and save', 'invalid batch rollback'] : ['390px layout'])] }));
} finally {
  ws?.close();
  browser?.kill();
}
