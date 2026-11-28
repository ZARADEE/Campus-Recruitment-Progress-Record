import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { verifyRichEditor } from './verify-rich-editor.mjs';

const native = process.argv.includes('--native');
const captureScreenshots = process.argv.includes('--screenshots');
const captureArtifacts = process.argv.includes('--artifacts');
assert(!captureScreenshots || native, 'README screenshots must be captured from the Tauri desktop window');
const port = native ? 9346 : 9345;
let browser;
if (!native) {
  const profile = path.resolve('.toolchain/chrome-smoke');
  await mkdir(profile, { recursive: true });
  browser = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
    '--headless=new', '--disable-gpu', '--disable-extensions', '--no-sandbox', '--no-first-run', '--no-default-browser-check',
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
    if (message.method === 'Runtime.exceptionThrown') {
      const detail = message.params.exceptionDetails;
      errors.push(detail.exception?.description || detail.text);
    }
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
  const capture = async filename => {
    if (!captureScreenshots && !captureArtifacts) return;
    const directory = path.resolve(captureScreenshots ? 'docs/screenshots' : '.toolchain/ui-verification');
    await mkdir(directory, { recursive: true });
    const result = await cdp('Page.captureScreenshot', { format: 'png', fromSurface: true });
    await writeFile(path.join(directory, filename), Buffer.from(result.data, 'base64'));
  };
  const click = selector => evaluate(`(() => { const target = document.querySelector(${JSON.stringify(selector)}); if (!target) throw new Error("Missing click target: " + ${JSON.stringify(selector)}); target.click(); })()`);
  const fill = (selector, value) => evaluate(`(() => { if (${JSON.stringify(selector)} === '#knowledge-content') { KnowledgeEditor.editor.commands.setContent(KnowledgeEditor.plainTextDocument(${JSON.stringify(value)})); return; } const field = document.querySelector(${JSON.stringify(selector)}); field.value = ${JSON.stringify(value)}; field.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  const until = async expression => {
    for (let attempt = 0; attempt < 80; attempt++) {
      if (await evaluate(expression)) return;
      await delay(100);
    }
    throw new Error(`Timed out: ${expression}; page=${await evaluate('location.href + " " + document.body?.innerText.slice(0,500)')}; errors=${JSON.stringify(errors)}`);
  };
  await cdp('Runtime.enable');
  await cdp('Page.enable');
  if (!native) await cdp('Page.navigate', { url:'http://127.0.0.1:1420/' });
  await until('!!document.querySelector("[data-action=open-add]")');
  if (!native) {
    await evaluate('localStorage.clear()');
    await cdp('Page.reload');
    await until('typeof knowledgeDocuments !== "undefined" && knowledgeDocuments.length === 0');
  }
  if (native) await until('isDesktop && jobs.length === 0 && opportunities.length === 0');
  assert.deepEqual(await evaluate('Array.from(document.querySelectorAll(".nav-link")).map(button => button.dataset.view).slice(-3)'), ['archive', 'knowledge', 'resumes'], 'Knowledge navigation sits between interviews and resumes');
  await click('[data-view=opportunities]');
  if (!native) {
    const visibility = await evaluate(`(() => {
      const original = opportunities.slice();
      try {
        const common = {company:'核验测试',title:'前端开发',city:'上海',mark:'验',tone:'blue',source:'官网',created:'2026-11-25',posted:null};
        opportunities.splice(0,opportunities.length,
          {...common,id:'verified-fixture',tags:['__vacancy:active','__scope:target','__verified:2026-11-25']},
          {...common,id:'pending-fixture',tags:['__vacancy:unknown','__scope:target']},
          {...common,id:'other-fixture',tags:['__vacancy:active','__scope:other']});
        const shown = () => Array.from(document.querySelectorAll('.opportunity-row')).map(row => row.dataset.jobId);
        opportunityFilter='active'; render();
        const active=shown(), badge=document.querySelector('.source-chip').textContent;
        const select=document.querySelector('#opportunity-filter'); select.value='pending'; select.dispatchEvent(new Event('change',{bubbles:true}));
        const pending=shown();
        opportunityFilter='other'; render();
        return {active,pending,other:shown(),badge};
      } finally { opportunities.splice(0,opportunities.length,...original); opportunityFilter='active'; render(); }
    })()`);
    assert.deepEqual(visibility.active,['verified-fixture'],'Only verified matching vacancies appear by default');
    assert.deepEqual(visibility.pending,['pending-fixture'],'Unverified vacancies remain accessible separately');
    assert.deepEqual(visibility.other,['other-fixture'],'Other directions do not enter the default list');
    assert(visibility.badge.includes('2026-11-25'),'The current verification date is visible');
    const reminderSelection = await evaluate(`(() => {
      const original=jobs.slice();
      const date=new Date(), today=[date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('-');
      const make=(id,day,time,result='待安排',status='applied')=>({id,status,sessions:{first:{date:day,time,result}}});
      try {
        jobs.splice(0,jobs.length,make('later','2099-02-01','09:00'),make('nearest','2099-01-01','09:00'),make('past','2000-01-01','09:00'),make('passed','2098-01-01','09:00','通过'),make('feedback','2098-01-01','09:00','待反馈'),make('closed','2098-01-01','09:00','待安排','closed'));
        const nearest=getUpcomingInterview().job.id;
        jobs.push(make('today-unknown',today,''));
        return {nearest,today:getUpcomingInterview().job.id};
      } finally { jobs.splice(0,jobs.length,...original); }
    })()`);
    assert.deepEqual(reminderSelection,{nearest:'nearest',today:'today-unknown'},'Reminder selects the nearest unfinished interview, including today without an exact time');
  }
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
  assert.equal(await evaluate('opportunityFilter'),'pending','Newly entered jobs remain visible in the unverified queue');
  const opportunityId = await evaluate('opportunities.find(job => job.company === "Smoke Co").id');
  await click(`[data-action=apply-job][data-job-id="${opportunityId}"]`);
  await until('!!document.querySelector(".job-title") && document.querySelector(".job-title").textContent === "Smoke Engineer"');
  assert.equal(await evaluate('document.querySelectorAll(".round-button").length'), 4);
  await click('[data-action=set-stage][data-stage="5"]');
  await until('getJob(currentJobId).stageIndex === 5');
  await capture('pipeline.png');
  await click('[data-action=open-interview][data-round=hr]');
  await fill('#question-text', 'Smoke question');
  await fill('#answer-text', 'Smoke answer');
  await fill('#session-summary', 'Smoke summary');
  await fill('#interview-date', '2099-01-02');
  await fill('#interview-time', '09:30');
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
  await capture('interview-records.png');
  await click('[data-view=pipeline]');
  await until('!!document.querySelector(".interview-reminder")');
  assert(await evaluate('document.querySelector(".interview-reminder-title").textContent.includes("Smoke Co")'));
  assert(await evaluate('document.querySelector(".interview-reminder-time").textContent.includes("09:30")'));
  await click('.interview-reminder [data-action=open-interview]');
  assert.equal(await evaluate('activeView'), 'interview', 'The reminder opens the scheduled round');
  await click('[data-view=archive]');
  if (native) {
    await cdp('Page.reload');
    await until('typeof jobs !== "undefined" && jobs.some(job => job.company === "Smoke Co")');
    await click('[data-view=archive]');
    assert(await evaluate('document.querySelector(".archive-qa").textContent.includes("Smoke answer")'), 'Saved answer survives page reload');
    const stored = await evaluate('window.__TAURI__.core.invoke("list_jobs")');
    assert.equal(stored[0].posted, null, 'Missing publication date stays null');
    assert(stored[0].created && stored[0].updated && stored[0].appliedAt);
    assert.equal(stored[0].sessions.hr.summary, 'Smoke summary');
    assert.equal(stored[0].sessions.hr.time, '09:30', 'The scheduled interview time survives SQLite reload');
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
  await click('[data-view=knowledge]');
  await until('!!window.KnowledgeEditor');
  assert.equal(await evaluate('document.querySelector("#global-search").placeholder'), '搜索知识标题、分类或正文');
  await click('[data-action=create-knowledge]');
  await until('!!document.querySelector("#knowledge-title")');
  await fill('#knowledge-title', '并发请求整理');
  await fill('#knowledge-category', '前端工程');
  await fill('#knowledge-content', '记录请求竞态、取消信号与过期响应处理。');
  await delay(1100);
  await until('typeof knowledgeDocuments !== "undefined" && knowledgeDocuments.some(document => document.title === "并发请求整理")');
  await cdp('Page.reload');
  await until('typeof knowledgeDocuments !== "undefined" && knowledgeDocuments.some(document => document.title === "并发请求整理")');
  await click('[data-view=knowledge]');
  await until('!!window.KnowledgeEditor?.editor');
  assert.equal(await evaluate('KnowledgeEditor.getText()'), '记录请求竞态、取消信号与过期响应处理。', 'Knowledge autosave survives reload');
  const knowledgeId = await evaluate('selectedKnowledgeId');
  const readInterviewRecords = () => evaluate('(async () => { const records = isDesktop ? await desktopInvoke("list_jobs") : jobs; return JSON.stringify(records.map(job => ({id:job.id, sessions:job.sessions}))); })()');
  const interviewSnapshot = await readInterviewRecords();
  await fill('#knowledge-title', '');
  await click('[data-view=archive]');
  assert.equal(await evaluate('activeView'), 'knowledge', 'An invalid title keeps unsaved content in the editor');
  await fill('#knowledge-title', '并发请求整理');
  if (!native) {
    await evaluate(`window.smokeOriginalSetItem = Storage.prototype.setItem; Storage.prototype.setItem = function(key, value) { if (key === 'campus-knowledge-documents') throw new Error('Simulated storage failure'); return window.smokeOriginalSetItem.call(this, key, value); }`);
    await fill('#knowledge-content', '保存失败后重试也应保留正文。');
    assert.equal(await evaluate('persistKnowledgeDocument(false)'), false);
    assert.equal(await evaluate('KnowledgeEditor.documentText(knowledgeDocuments.find(item => item.id === selectedKnowledgeId).content)'), '记录请求竞态、取消信号与过期响应处理。', 'Failed saves do not mark edits as persisted');
    await click('[data-view=archive]');
    assert.equal(await evaluate('activeView'), 'knowledge');
    await evaluate('Storage.prototype.setItem = window.smokeOriginalSetItem');
    assert.equal(await evaluate('persistKnowledgeDocument(false)'), true, 'The same edit can be retried after a storage failure');
  }
  await fill('#knowledge-content', '记录请求竞态、取消信号与过期响应处理。');
  await fill('#global-search', '不存在的知识关键词');
  await until('document.querySelector(".knowledge-empty")?.textContent.includes("没有匹配的知识文档")');
  await click('[data-action=create-knowledge]');
  await until('!!document.querySelector("#knowledge-title")');
  assert.equal(await evaluate('document.querySelector("#global-search").value'), '', 'A new document is visible after searching');
  await fill('#knowledge-title', '临时学习笔记');
  await fill('#knowledge-content', '切换文档前立即保存。');
  await click(`[data-action=select-knowledge][data-document-id="${knowledgeId}"]`);
  await until(`selectedKnowledgeId === ${JSON.stringify(knowledgeId)}`);
  assert(await evaluate('knowledgeDocuments.some(item => item.title === "临时学习笔记" && KnowledgeEditor.documentText(item.content) === "切换文档前立即保存。")'), 'Switching documents saves recent edits');
  const temporaryId = await evaluate('knowledgeDocuments.find(item => item.title === "临时学习笔记").id');
  await click(`[data-action=select-knowledge][data-document-id="${temporaryId}"]`);
  await until(`selectedKnowledgeId === ${JSON.stringify(temporaryId)}`);
  await evaluate('window.confirm = () => true');
  await click('[data-action=delete-knowledge]');
  await until(`knowledgeDocuments.length === 1 && selectedKnowledgeId === ${JSON.stringify(knowledgeId)}`);
  assert.equal(await readInterviewRecords(), interviewSnapshot, 'Knowledge editing and deletion leave interview records intact');
  if (!native) assert(await evaluate('document.documentElement.scrollWidth <= 390'), 'Mobile knowledge editor has no overflow');
  await verifyRichEditor({ evaluate, click, fill, until, cdp, capture, native });
  await capture('knowledge-notes.png');
  await click('[data-view=pipeline]');
  await until('!!document.querySelector("[data-action=terminate-job]")');
  const terminatingId = await evaluate('currentJobId');
  const sessionsBeforeTermination = await evaluate('JSON.stringify(getJob(currentJobId).sessions)');
  const activeCount = await evaluate('Number(document.querySelector("#pipeline-count").textContent)');
  assert(await evaluate('document.querySelector(".detail-tools [data-action=terminate-job]").nextElementSibling.dataset.action === "delete-job"'), 'Termination is directly left of the trash button');
  await evaluate('window.confirm = () => false');
  await click('[data-action=terminate-job]');
  assert.equal(await evaluate('getJob(currentJobId).status'), 'applied', 'Canceling termination keeps the job active');
  await evaluate('window.confirm = () => true');
  await click('[data-action=terminate-job]');
  await until('getJob(currentJobId).status === "closed"');
  assert.equal(await evaluate('document.querySelector(".job-inline-meta .badge").textContent'), '已拒绝');
  assert(await evaluate('document.querySelector(".termination-note").textContent.includes("已拒绝")'));
  assert.equal(await evaluate('JSON.stringify(getJob(currentJobId).sessions)'), sessionsBeforeTermination, 'Termination preserves interview notes');
  assert.equal(await evaluate('Number(document.querySelector("#pipeline-count").textContent)'), activeCount - 1);
  assert(await evaluate('Array.from(document.querySelectorAll(".stage-node")).every(button => button.disabled)'), 'Rejected jobs cannot advance');
  assert.equal(await evaluate('document.querySelector("[data-action=terminate-job]")'), null);
  await capture('terminated-job.png');
  if (native) {
    const closed = await evaluate('window.__TAURI__.core.invoke("list_jobs").then(records => records.find(job => job.company === "Smoke Co"))');
    assert.equal(closed.status, 'closed');
    assert.equal(closed.sessions.hr.summary, 'Smoke summary');
    await cdp('Page.reload');
    await until(`typeof jobs !== 'undefined' && jobs.some(job => job.id === ${JSON.stringify(terminatingId)} && job.status === "closed")`);
    assert.equal(await evaluate('document.querySelector(".job-inline-meta .badge").textContent'), '已拒绝', 'Rejection survives reload');
  }
  assert.deepEqual(errors, []);
  if (native && process.argv.includes('--close-save')) {
    await click('[data-view=knowledge]');
    await until('!!window.KnowledgeEditor?.editor');
    await evaluate('KnowledgeEditor.editor.commands.insertContentAt(KnowledgeEditor.editor.state.doc.content.size, {type:"paragraph",content:[{type:"text",text:"关闭窗口前最后输入的内容"}]}); clearTimeout(knowledgeSaveTimer);');
  }
  console.log(JSON.stringify({ result: 'PASS', mode: native ? 'native IPC and SQLite' : 'browser UI', checks: ['manual import', 'URL dialog', 'apply transition', 'stage update', 'four rounds', 'interview date and time reminder', 'reminder opens the scheduled round', 'Q&A archive', 'separate knowledge documents', 'terminate without data loss', ...(native ? ['draft recovery', 'reload persistence', 'CSV batch preview and save', 'invalid batch rollback', ...(captureScreenshots ? ['desktop screenshots'] : [])] : ['390px layout'])] }));
} finally {
  ws?.close();
  browser?.kill();
}
