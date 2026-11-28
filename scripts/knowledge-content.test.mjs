import assert from 'node:assert/strict';
import test from 'node:test';
import { decodeDocument, encodeDocument, documentText, isLocalImage } from '../src/knowledge-content.js';

test('legacy multiline notes keep their text, blank lines and literal markup', () => {
  const old = '第一段\n\n# 旧笔记\n<img src=x onerror=alert(1)>';
  const doc = decodeDocument(old);
  assert.equal(doc.content.length, 4);
  assert.equal(doc.content[3].content[0].text, '<img src=x onerror=alert(1)>');
  assert.equal(documentText(encodeDocument(doc)), old);
});

test('document text includes headings and image descriptions, without stored image bytes', () => {
  const doc = { type: 'doc', content: [
    { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: '请求流程' }] },
    { type: 'image', attrs: { src: 'data:image/png;base64,aGVsbG8=', alt: '架构图', width: 320 } },
    { type: 'paragraph', content: [{ type: 'text', text: '返回结果', marks: [{ type: 'bold' }] }] },
  ] };
  assert.deepEqual(decodeDocument(encodeDocument(doc)), doc);
  assert.equal(documentText(encodeDocument(doc)), '请求流程\n架构图\n返回结果');
});

test('stored rich content rejects executable and remote images, unsafe links and unknown nodes', () => {
  const content = encodeDocument({ type: 'doc', content: [
    { type: 'image', attrs: { src: 'https://untrusted.example/tracker.png' } },
    { type: 'image', attrs: { src: 'data:image/svg+xml;base64,PHN2Zz4=' } },
    { type: 'script', content: [] },
    { type: 'paragraph', content: [{ type: 'text', text: '保留文字', marks: [{ type: 'link', attrs: { href: 'javascript:alert(1)' } }] }] },
  ] });
  const cleaned = decodeDocument(content);
  assert.equal(cleaned.content.length, 1);
  assert.deepEqual(cleaned.content[0].content[0].marks, []);
  assert.equal(isLocalImage('data:image/webp;base64,AAAA'), true);
});
