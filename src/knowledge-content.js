export const DOCUMENT_FORMAT = 'campus-knowledge-v1';
export const MAX_DOCUMENT_BYTES = 24 * 1024 * 1024;
export const isLocalImage = value => /^data:image\/(png|jpeg|webp|gif|bmp);base64,[a-z\d+/=\s]+$/i.test(value || '');
export const isSafeLink = value => /^(https?:\/\/|mailto:)/i.test(value || '');

const nodeTypes = new Set(['doc', 'paragraph', 'text', 'heading', 'bulletList', 'orderedList', 'listItem', 'taskList', 'taskItem', 'blockquote', 'codeBlock', 'hardBreak', 'horizontalRule', 'image', 'table', 'tableRow', 'tableCell', 'tableHeader']);
const markTypes = new Set(['bold', 'italic', 'underline', 'strike', 'code', 'highlight', 'link']);

function cleanNode(node, depth = 0) {
  if (!node || !nodeTypes.has(node.type) || depth > 40) return null;
  if (node.type === 'image' && !isLocalImage(node.attrs?.src)) return null;
  const result = { type: node.type };
  if (node.type === 'text') {
    if (typeof node.text !== 'string' || !node.text) return null;
    result.text = node.text;
  }
  if (node.attrs) {
    const attributes = {
      heading: ['level'], orderedList: ['start'], taskItem: ['checked'], codeBlock: ['language'],
      image: ['src', 'alt', 'title', 'width', 'height'], tableCell: ['colspan', 'rowspan', 'colwidth'], tableHeader: ['colspan', 'rowspan', 'colwidth'],
    }[node.type] || [];
    result.attrs = Object.fromEntries(attributes.filter(key => node.attrs[key] != null).map(key => [key, node.attrs[key]]));
    if (node.type === 'heading') result.attrs.level = Math.max(1, Math.min(4, Number(result.attrs.level) || 1));
  }
  if (Array.isArray(node.content)) result.content = node.content.map(child => cleanNode(child, depth + 1)).filter(Boolean);
  if (Array.isArray(node.marks)) {
    result.marks = node.marks.filter(mark => markTypes.has(mark.type) && (mark.type !== 'link' || isSafeLink(mark.attrs?.href)))
      .map(mark => mark.type === 'link' ? { type: 'link', attrs: { href: mark.attrs.href, target: '_blank', rel: 'noopener noreferrer nofollow', ...(typeof mark.attrs.title === 'string' ? { title: mark.attrs.title } : {}) } } : { type: mark.type });
  }
  return result;
}

export function plainTextDocument(text = '') {
  return { type: 'doc', content: String(text).split(/\r?\n/).map(line => ({ type: 'paragraph', ...(line ? { content: [{ type: 'text', text: line }] } : {}) })) };
}

export function decodeDocument(content = '') {
  try {
    const saved = JSON.parse(content);
    if (saved.format === DOCUMENT_FORMAT && saved.doc?.type === 'doc') {
      const doc = cleanNode(saved.doc);
      if (doc.content?.length) return doc;
      return plainTextDocument();
    }
  } catch { /* Existing notes are plain text, including literal HTML and Markdown. */ }
  return plainTextDocument(content);
}

export function encodeDocument(doc) {
  return JSON.stringify({ format: DOCUMENT_FORMAT, doc });
}

export function documentText(content) {
  const walk = node => node.type === 'text' ? node.text : node.type === 'image' ? node.attrs?.alt || '' : (node.content || []).map(walk).join(['paragraph', 'heading', 'codeBlock'].includes(node.type) ? '' : '\n');
  return walk(decodeDocument(content));
}
