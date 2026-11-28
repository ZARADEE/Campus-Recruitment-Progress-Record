import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { createServer } from 'vite';

const archive = path.resolve('data/imports/kamacoder-llm-2026-11-24');
const documents = JSON.parse(await readFile(path.join(archive, 'documents.json'), 'utf8'));
const checks = JSON.parse(await readFile(path.join(archive, 'validation.json'), 'utf8')).checks;
const native = process.argv.includes('--native');
const debugPort = native ? 9346 : 9347;
let server, browser, ws;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const errors = [];
try {
  if (!native) {
    server = await createServer({ server: { host: '127.0.0.1', port: 1431, strictPort: true } });
    await server.listen();
    const profile = path.resolve('.toolchain/kamacoder-verification');
    await mkdir(profile, { recursive: true });
    browser = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
      '--headless=new', '--disable-gpu', '--disable-extensions', '--no-first-run', '--no-default-browser-check',
      `--user-data-dir=${profile}`, `--remote-debugging-port=${debugPort}`, '--window-size=1440,1000', 'http://127.0.0.1:1431/',
    ], { windowsHide: true, stdio: 'ignore' });
  }
  let target;
  for (let attempt = 0; attempt < 80; attempt++) {
    try {
      target = (await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json()).find(t => t.type === 'page' && !t.url.startsWith('devtools:'));
      if (target) break;
    } catch {}
    await delay(250);
  }
  assert(target, 'Verification browser must be reachable');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let nextId = 0;
  const pending = new Map();
  ws.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
    if (!message.id) return;
    const task = pending.get(message.id);
    pending.delete(message.id);
    message.error ? task.reject(new Error(JSON.stringify(message.error))) : task.resolve(message.result);
  };
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const response = await cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  };
  await cdp('Runtime.enable');
  for (let attempt = 0; attempt < 100 && !await evaluate('!!window.KnowledgeEditor'); attempt++) await delay(100);
  assert(await evaluate('!!window.KnowledgeEditor'));
  if (native) {
    await evaluate('refreshNativeData()');
    const importedIds = documents.map(document => document.id);
    assert.equal(await evaluate(`knowledgeDocuments.filter(doc => ${JSON.stringify(importedIds)}.includes(doc.id)).length`), documents.length, 'All imported documents are available through native IPC');
    await evaluate('setView("knowledge")');
  } else {
    await evaluate(`(() => {
      const host = document.createElement('section'); host.id = 'migration-test'; host.style.cssText = 'padding:24px;max-width:1000px;margin:0 auto;background:white';
      document.body.replaceChildren(host);
      KnowledgeEditor.mount({element:host,content:'',onChange(){},onSave(){},onError(e){throw new Error(e)},onBusy(){},onOpenLink(){}});
    })()`);
  }
  const results = [];
  for (let index = 0; index < documents.length; index++) {
    const document = documents[index];
    if (native) {
      await evaluate(`document.querySelector('[data-document-id="${document.id}"]').click()`);
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await evaluate(`selectedKnowledgeId === ${JSON.stringify(document.id)} && document.querySelector('#knowledge-title')?.value === ${JSON.stringify(document.title)}`)) break;
        await delay(100);
      }
    } else {
      const validity = await evaluate(`(() => {try {KnowledgeEditor.editor.schema.nodeFromJSON(KnowledgeEditor.decodeDocument(${JSON.stringify(document.content)})).check();return null;}catch(error){return error.message;}})()`);
      assert.equal(validity, null, document.title + ': schema validation');
      await evaluate(`KnowledgeEditor.editor.commands.setContent(KnowledgeEditor.decodeDocument(${JSON.stringify(document.content)}),{emitUpdate:false,errorOnInvalidContent:true})`);
    }
    const result = await evaluate(`(async () => {
      const editor = KnowledgeEditor.editor;
      editor.state.doc.check();
      const counts = {}; const code = []; let text = '';
      editor.state.doc.descendants(node => { counts[node.type.name] = (counts[node.type.name] || 0) + 1; if(node.isText) text += node.text; if(node.type.name === 'codeBlock') code.push(node.textContent); });
      const images = [...editor.view.dom.querySelectorAll('img')];
      await Promise.all(images.map(image => image.decode()));
      if(images.some(image => !image.naturalWidth)) throw new Error('Broken image');
      const levels = [...editor.view.dom.querySelectorAll('h1,h2,h3,h4')].map(node => Number(node.tagName.slice(1)));
      return {counts, code, text:text.replace(/\\s/g,''), levels, renderedImages:images.length, renderedTables:editor.view.dom.querySelectorAll('table').length};
    })()`);
    const expected = checks.find(check => check.url === document.sourceUrl);
    const source = JSON.parse(document.content).doc;
    const flatten = node => [node, ...(node.content || []).flatMap(flatten)];
    const nodes = flatten(source);
    assert.equal(result.text, nodes.map(node => node.text || '').join('').replace(/\s/g, ''), document.title + ': exact text');
    assert.deepEqual(result.code, nodes.filter(node => node.type === 'codeBlock').map(node => (node.content || []).map(child => child.text || '').join('')), document.title + ': exact code');
    assert.deepEqual(result.levels, nodes.filter(node => node.type === 'heading').map(node => node.attrs.level), document.title + ': heading hierarchy');
    assert.equal(result.renderedImages, expected.image);
    assert.equal(result.renderedTables, expected.table);
    results.push({ title: document.title, images: result.renderedImages, tables: result.renderedTables, textCharacters: result.text.length });
    if (document.sourceUrl.endsWith('/transformer_interview.html')) {
      await evaluate(`KnowledgeEditor.editor.view.dom.querySelector('h2')?.scrollIntoView({block:'start'})`);
      const screenshot = await cdp('Page.captureScreenshot', { format: 'png', fromSurface: true });
      await writeFile(path.join(archive, native ? 'desktop-verification.png' : 'editor-verification.png'), Buffer.from(screenshot.data, 'base64'));
    }
    console.log(`Verified ${index + 1}/${documents.length}: ${document.title}`);
  }
  assert.deepEqual(errors, []);
  if (native) {
    const overview = documents.find(doc => doc.sourceUrl.endsWith('/interview/llm/'));
    await evaluate(`document.querySelector('[data-document-id="${overview.id}"]').click()`);
    for (let attempt = 0; attempt < 100 && !await evaluate(`selectedKnowledgeId === ${JSON.stringify(overview.id)}`); attempt++) await delay(100);
    await evaluate('window.scrollTo(0,0)');
  }
  await writeFile(path.join(archive, native ? 'desktop-verification.json' : 'editor-verification.json'), JSON.stringify({ result: 'PASS', documents: results, errors }, null, 2));
  console.log(JSON.stringify({ result: 'PASS', documents: results.length, images: results.reduce((sum, doc) => sum + doc.images, 0) }));
} finally {
  ws?.close();
  browser?.kill();
  await server?.close();
}
