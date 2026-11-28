import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Code from '@tiptap/extension-code';
import Image from '@tiptap/extension-image';
import { TableKit } from '@tiptap/extension-table';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import Highlight from '@tiptap/extension-highlight';
import Placeholder from '@tiptap/extension-placeholder';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { decodeDocument, encodeDocument, documentText, plainTextDocument, isLocalImage, isSafeLink, MAX_DOCUMENT_BYTES } from './knowledge-content.js';
import './knowledge-editor.css';

let active = null;
const esc = value => String(value).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const button = (action, label, symbol = label, extra = '') => `<button type="button" class="editor-tool" data-editor-command="${action}" title="${label}" aria-label="${label}" ${extra}>${symbol}</button>`;

const LocalImage = Image.extend({
  parseHTML() {
    return [{ tag: 'img[src]', getAttrs: element => isLocalImage(element.getAttribute('src')) ? {} : false }];
  },
  addInputRules() { return []; },
}).configure({ allowBase64: true, resize: { enabled: true, directions: ['bottom-left', 'bottom-right'], minWidth: 64, minHeight: 32, alwaysPreserveAspectRatio: true } });

function toolbar() {
  return `<div class="editor-toolbar" role="toolbar" aria-label="文档格式工具">
    <div class="editor-tool-group">
      ${button('undo', '撤销 · Ctrl+Z', '↶')}${button('redo', '重做 · Ctrl+Shift+Z', '↷')}
      <label class="sr-only" for="knowledge-block-type">段落样式</label><select id="knowledge-block-type" title="正文与标题层级"><option value="paragraph">正文</option><option value="h1">一级标题</option><option value="h2">二级标题</option><option value="h3">三级标题</option><option value="h4">四级标题</option></select>
    </div>
    <div class="editor-tool-group">
      ${button('bold', '加粗 · Ctrl+B', '<b>B</b>')}${button('italic', '斜体 · Ctrl+I', '<i>I</i>')}${button('underline', '下划线 · Ctrl+U', '<u>U</u>')}${button('strike', '删除线', '<s>S</s>')}${button('highlight', '高亮', '<mark>荧</mark>')}${button('code', '行内代码', '&lt;/&gt;')}
    </div>
    <div class="editor-tool-group">
      ${button('bulletList', '无序列表', '• 列表')}${button('orderedList', '有序列表', '1. 列表')}${button('taskList', '待办清单', '☑ 待办')}${button('indent', '增加列表层级 · Tab', '⇥')}${button('outdent', '减少列表层级 · Shift+Tab', '⇤')}
    </div>
    <div class="editor-tool-group">
      ${button('blockquote', '引用', '❝ 引用')}${button('codeBlock', '代码块', '{ } 代码')}${button('link', '插入或编辑链接 · Ctrl+K', '链接')}${button('image', '插入图片，也可粘贴或拖入', '▧ 图片')}${button('table', '插入表格', '▦ 表格')}${button('horizontalRule', '分隔线', '—')}${button('clear', '清除格式', '清除格式')}
    </div>
  </div>
  <div class="editor-context-bar" id="knowledge-table-tools" role="toolbar" aria-label="表格编辑" hidden><span>表格</span>${button('addRowAfter', '在下方插入行', '+ 行')}${button('addColumnAfter', '在右侧插入列', '+ 列')}${button('deleteRow', '删除当前行', '删行')}${button('deleteColumn', '删除当前列', '删列')}${button('mergeOrSplit', '合并或拆分单元格', '合并 / 拆分')}${button('deleteTable', '删除表格', '删除表格')}</div>
  <div class="editor-context-bar" id="knowledge-image-tools" role="toolbar" aria-label="图片编辑" hidden><span>图片</span>${button('imageHalf', '图片半宽', '半宽')}${button('imageFull', '图片适应正文宽度', '适应宽度')}${button('imageAlt', '编辑图片说明', '图片说明')}${button('deleteImage', '删除所选图片', '删除图片')}<span class="editor-context-hint">拖动图片下角可调整大小</span></div>`;
}

function mount({ element, content, onChange, onSave, onError, onBusy, onOpenLink }) {
  destroy();
  element.innerHTML = `<div class="editor-toolbar-area">${toolbar()}</div><div class="editor-page"><div id="knowledge-content"></div></div>
    <input type="file" id="knowledge-image-file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp" multiple hidden>
    <dialog class="editor-dialog" id="knowledge-link-dialog"><form method="dialog"><h2>插入链接</h2><label>链接地址<input id="knowledge-link-url" type="text" placeholder="https://example.com" autocomplete="off"></label><label>显示文字<input id="knowledge-link-label" type="text" placeholder="选中文字或填写链接名称"></label><p class="editor-dialog-error" role="alert"></p><div class="editor-dialog-actions"><button class="button small" type="button" data-editor-command="unlink">移除链接</button><span></span><button class="button small" value="cancel">取消</button><button class="button primary small" type="submit" value="save">确定</button></div></form></dialog>
    <dialog class="editor-dialog" id="knowledge-image-dialog"><form method="dialog"><h2>图片说明</h2><label>说明文字<input id="knowledge-image-alt" maxlength="240" placeholder="描述图片内容，方便后续检索"></label><div class="editor-dialog-actions"><span></span><button class="button small" value="cancel">取消</button><button class="button primary small" type="submit" value="save">保存说明</button></div></form></dialog>`;
  const state = { element, editor: null, uploading: 0, onError, onBusy, selection: null };
  active = state;
  const editor = new Editor({
    element: element.querySelector('#knowledge-content'),
    extensions: [StarterKit.configure({ code: false, heading: { levels: [1, 2, 3, 4] }, link: { openOnClick: false, autolink: true, defaultProtocol: 'https', isAllowedUri: isSafeLink } }), Code.extend({ excludes: '' }), LocalImage, TableKit.configure({ table: { resizable: true } }), TaskList, TaskItem.configure({ nested: true }), Highlight, Placeholder.configure({ placeholder: '开始记录你的想法……输入 # 加空格创建标题，或粘贴、拖入图片。' })],
    content: decodeDocument(content),
    editorProps: {
      attributes: { class: 'knowledge-prose', role: 'textbox', 'aria-label': '文档正文', 'aria-multiline': 'true', spellcheck: 'false' },
      handlePaste(view, event) {
        const files = [...(event.clipboardData?.files || [])].filter(file => file.type.startsWith('image/'));
        if (!files.length) return false;
        event.preventDefault(); insertImages(state, files); return true;
      },
      handleDrop(view, event, _slice, moved) {
        const files = [...(event.dataTransfer?.files || [])];
        if (moved || !files.length) return false;
        event.preventDefault();
        const position = view.posAtCoords({ left: event.clientX, top: event.clientY });
        if (position) editor.commands.setTextSelection(position.pos);
        insertImages(state, files); return true;
      },
      handleKeyDown(view, event) {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); onSave(); return true; }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openLink(state); return true; }
        if (event.key === 'Tab' && (editor.isActive('listItem') || editor.isActive('taskItem'))) {
          event.preventDefault(); const type = editor.isActive('taskItem') ? 'taskItem' : 'listItem';
          return event.shiftKey ? editor.commands.liftListItem(type) : editor.commands.sinkListItem(type);
        }
        return false;
      },
      handleClick(view, pos, event) {
        const link = event.target.closest('a');
        if (link && (event.ctrlKey || event.metaKey)) { event.preventDefault(); if (isSafeLink(link.href)) onOpenLink(link.href); return true; }
        return false;
      },
    },
    onUpdate() { updateChrome(state); onChange(); },
    onSelectionUpdate() { updateToolbar(state); },
  });
  state.editor = editor;
  element.addEventListener('mousedown', event => { if (event.target.closest('[data-editor-command]')) event.preventDefault(); });
  element.addEventListener('click', event => {
    const control = event.target.closest('[data-editor-command]');
    if (!control) return;
    event.preventDefault(); command(state, control.dataset.editorCommand);
  });
  element.querySelector('#knowledge-block-type').addEventListener('change', event => {
    const style = event.target.value;
    if (style === 'paragraph') editor.chain().focus().setParagraph().run();
    else editor.chain().focus().setHeading({ level: Number(style.slice(1)) }).run();
  });
  element.querySelector('#knowledge-image-file').addEventListener('change', event => {
    const files = [...event.target.files]; event.target.value = ''; insertImages(state, files);
  });
  element.querySelector('#knowledge-link-dialog form').addEventListener('submit', event => {
    if (event.submitter?.value !== 'save') return;
    event.preventDefault();
    let href = element.querySelector('#knowledge-link-url').value.trim();
    if (href && !/^[a-z][a-z\d+.-]*:/i.test(href)) href = `https://${href}`;
    if (!isSafeLink(href)) { element.querySelector('.editor-dialog-error').textContent = '请输入有效的 https、http 或 mailto 链接。'; return; }
    const label = element.querySelector('#knowledge-link-label').value.trim() || href;
    element.querySelector('#knowledge-link-dialog').close();
    const chain = editor.chain().focus().setTextSelection(state.selection);
    if (label === state.linkLabel && state.selection.from !== state.selection.to) chain.setLink({ href }).run();
    else chain.insertContent({ type: 'text', text: label, marks: [{ type: 'link', attrs: { href } }] }).run();
  });
  element.querySelector('#knowledge-image-dialog form').addEventListener('submit', event => {
    if (event.submitter?.value !== 'save') return;
    event.preventDefault();
    const alt = element.querySelector('#knowledge-image-alt').value.trim();
    element.querySelector('#knowledge-image-dialog').close();
    editor.chain().focus().setNodeSelection(state.selection.from).updateAttributes('image', { alt, title: alt }).run();
  });
  updateChrome(state);
  return editor;
}

function openLink(state) {
  const { editor, element } = state;
  if (editor.isActive('link')) editor.commands.extendMarkRange('link');
  state.selection = { from: editor.state.selection.from, to: editor.state.selection.to };
  element.querySelector('#knowledge-link-url').value = editor.getAttributes('link').href || '';
  state.linkLabel = editor.state.doc.textBetween(state.selection.from, state.selection.to, ' ');
  element.querySelector('#knowledge-link-label').value = state.linkLabel;
  element.querySelector('.editor-dialog-error').textContent = '';
  element.querySelector('#knowledge-link-dialog').showModal();
  element.querySelector('#knowledge-link-url').focus();
}

function command(state, name) {
  const { editor, element } = state;
  const chain = editor.chain().focus();
  const toggles = { bold: 'toggleBold', italic: 'toggleItalic', underline: 'toggleUnderline', strike: 'toggleStrike', highlight: 'toggleHighlight', code: 'toggleCode', bulletList: 'toggleBulletList', orderedList: 'toggleOrderedList', taskList: 'toggleTaskList', blockquote: 'toggleBlockquote', codeBlock: 'toggleCodeBlock' };
  if (toggles[name]) { chain[toggles[name]]().run(); return; }
  if (['undo', 'redo', 'addRowAfter', 'addColumnAfter', 'deleteRow', 'deleteColumn', 'mergeOrSplit', 'deleteTable'].includes(name)) { chain[name]().run(); return; }
  if (name === 'image') { element.querySelector('#knowledge-image-file').click(); return; }
  if (name === 'table') { chain.insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(); return; }
  if (name === 'horizontalRule') { chain.setHorizontalRule().run(); return; }
  if (name === 'clear') { chain.unsetAllMarks().clearNodes().run(); return; }
  if (name === 'indent' || name === 'outdent') { chain[name === 'indent' ? 'sinkListItem' : 'liftListItem'](editor.isActive('taskItem') ? 'taskItem' : 'listItem').run(); return; }
  if (name === 'link') { openLink(state); return; }
  if (name === 'unlink') { element.querySelector('#knowledge-link-dialog').close(); chain.setTextSelection(state.selection).extendMarkRange('link').unsetLink().run(); return; }
  if (name === 'deleteImage') { chain.deleteSelection().run(); return; }
  if (name === 'imageAlt') {
    state.selection = { from: editor.state.selection.from, to: editor.state.selection.to };
    element.querySelector('#knowledge-image-alt').value = editor.getAttributes('image').alt || '';
    element.querySelector('#knowledge-image-dialog').showModal(); return;
  }
  if (name === 'imageHalf' || name === 'imageFull') {
    const width = Math.floor(editor.view.dom.clientWidth * (name === 'imageHalf' ? 0.5 : 1));
    chain.updateAttributes('image', { width, height: null }).run();
  }
}

function updateToolbar(state) {
  if (!state.editor || state.editor.isDestroyed) return;
  const { editor, element } = state;
  element.querySelectorAll('[data-editor-command]').forEach(button => {
    const name = button.dataset.editorCommand;
    const pressed = editor.isActive(name);
    button.classList.toggle('active', pressed);
    if (['bold', 'italic', 'underline', 'strike', 'highlight', 'code', 'bulletList', 'orderedList', 'taskList', 'blockquote', 'codeBlock', 'link'].includes(name)) button.setAttribute('aria-pressed', String(pressed));
    if (name === 'undo' || name === 'redo') button.disabled = !editor.can()[name]();
    if (name === 'indent' || name === 'outdent') button.disabled = !editor.can()[name === 'indent' ? 'sinkListItem' : 'liftListItem'](editor.isActive('taskItem') ? 'taskItem' : 'listItem');
  });
  element.querySelector('#knowledge-block-type').value = editor.isActive('heading') ? `h${editor.getAttributes('heading').level}` : 'paragraph';
  element.querySelector('#knowledge-table-tools').hidden = !editor.isActive('table');
  element.querySelector('#knowledge-image-tools').hidden = !editor.isActive('image');
}

function updateChrome(state) {
  if (!state.editor || state.editor.isDestroyed) return;
  updateToolbar(state);
  const { editor } = state;
  const outline = document.querySelector('#knowledge-outline');
  const entries = [];
  let images = 0;
  editor.state.doc.descendants((node, pos) => {
    if (node.type.name === 'heading') entries.push({ level: node.attrs.level, text: node.textContent || '未命名标题', pos });
    if (node.type.name === 'image') images++;
  });
  if (outline) {
    outline.innerHTML = entries.length ? entries.map(entry => `<button type="button" class="outline-entry level-${entry.level}" data-outline-position="${entry.pos}" title="${esc(entry.text)}">${esc(entry.text)}</button>`).join('') : '<p class="outline-empty">添加标题后，这里会自动生成大纲。</p>';
    outline.querySelectorAll('button').forEach(button => button.addEventListener('click', () => {
      const pos = Number(button.dataset.outlinePosition);
      editor.chain().focus().setTextSelection(pos + 1).run();
      editor.view.nodeDOM(pos)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }));
  }
  const count = document.querySelector('#knowledge-word-count');
  if (count) count.textContent = `${editor.getText().replace(/\s/g, '').length} 字${images ? ` · ${images} 张图片` : ''}`;
}

async function readImage(file) {
  if (!/^image\/(png|jpeg|webp|gif|bmp)$/.test(file.type)) throw new Error('请选择 PNG、JPG、WebP、GIF 或 BMP 图片。');
  if (file.size > 20 * 1024 * 1024) throw new Error('单张图片不能超过 20 MB。');
  const dataUrl = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('图片读取失败')); reader.readAsDataURL(file); });
  const image = new window.Image(); image.src = dataUrl;
  await image.decode();
  if (image.naturalWidth * image.naturalHeight > 50_000_000) throw new Error('图片尺寸过大，请缩小后插入。');
  let src = dataUrl;
  if (file.type !== 'image/gif' && (image.naturalWidth > 1800 || image.naturalHeight > 1800 || file.size > 700_000)) {
    const ratio = Math.min(1, 1800 / Math.max(image.naturalWidth, image.naturalHeight));
    const canvas = document.createElement('canvas'); canvas.width = Math.round(image.naturalWidth * ratio); canvas.height = Math.round(image.naturalHeight * ratio);
    canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
    src = canvas.toDataURL('image/webp', 0.86);
  }
  if (src.length > 3 * 1024 * 1024) throw new Error('图片压缩后仍然过大，请先缩小图片。');
  return { type: 'image', attrs: { src, alt: file.name.replace(/\.[^.]+$/, ''), width: Math.min(720, image.naturalWidth), height: null } };
}

async function insertImages(state, files) {
  if (!files.length || state !== active) return;
  const editor = state.editor;
  state.uploading++;
  state.onBusy(true);
  try {
    const images = [];
    for (const file of files) images.push(await readImage(file));
    if (state !== active || editor.isDestroyed) return;
    const size = new TextEncoder().encode(encodeDocument(editor.getJSON())).length + JSON.stringify(images).length;
    if (size > MAX_DOCUMENT_BYTES) throw new Error('文档最多可保存 24 MB 内容，请减少图片数量或缩小图片。');
    editor.chain().focus().insertContent([...images, { type: 'paragraph' }]).run();
  } catch (error) { state.onError(error.message || '图片插入失败'); }
  finally { state.uploading--; if (state === active) state.onBusy(state.uploading > 0); }
}

function destroy() {
  if (active) active.editor?.destroy();
  active = null;
}

window.KnowledgeEditor = {
  mount, destroy, documentText, decodeDocument, plainTextDocument, MAX_DOCUMENT_BYTES,
  getContent: () => active ? encodeDocument(active.editor.getJSON()) : null,
  getText: () => active?.editor.getText() || '',
  isBusy: () => !!active?.uploading,
  get editor() { return active?.editor || null; },
  async guardClose(save) {
    let closing = false;
    const appWindow = getCurrentWindow();
    await appWindow.onCloseRequested(async event => {
      event.preventDefault();
      if (closing) return;
      closing = true;
      try { if (await save()) await appWindow.destroy(); }
      finally { closing = false; }
    });
  },
};
document.dispatchEvent(new Event('knowledge-editor-ready'));
