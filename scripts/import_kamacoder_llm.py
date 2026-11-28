"""Import authorized Kamacoder LLM documents into the local knowledge library."""
import argparse
import base64
import hashlib
import io
import json
import re
import sqlite3
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment, NavigableString
from PIL import Image

BASE = 'https://notes.kamacoder.com/interview/llm/'
ARCHIVE = Path('data/imports/kamacoder-llm-2026-11-24')


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def fetch(url):
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=45)
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def page_path(url):
    return ARCHIVE / 'pages' / (hashlib.sha256(url.encode()).hexdigest()[:20] + '.html')


def article(html):
    soup = BeautifulSoup(html, 'html.parser')
    body = soup.select_one('.theme-default-content')
    if body is None or body.find('h1') is None:
        raise ValueError('Missing article body or heading')
    for decoration in body.select('.header-anchor, .line-numbers-wrapper, .icon.outbound, script, style'):
        decoration.decompose()
    return body


def collect():
    (ARCHIVE / 'pages').mkdir(parents=True, exist_ok=True)
    pending, pages = {BASE}, {}

    def download(url):
        path = page_path(url)
        if not path.exists():
            path.write_bytes(fetch(url).content)
        html = path.read_text(encoding='utf-8')
        body = article(html)
        soup = BeautifulSoup(html, 'html.parser')
        group = soup.select_one('.sidebar-heading.open span')
        links = set()
        for anchor in soup.select('.theme-default-content a[href], .sidebar a[href]'):
            target = urldefrag(urljoin(url, anchor['href']))[0]
            if target.startswith(BASE) and not urlparse(target).query and (target.endswith('.html') or target == BASE):
                links.add(target)
        return {'url': url, 'title': body.h1.get_text().strip(),
                'category': group.get_text(strip=True) if group else '大模型面经',
                'htmlFile': str(path), 'htmlSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'tags': dict(Counter(t.name for t in body.find_all(True))),
                'images': [urljoin(url, im['src']) for im in body.select('img[src]')],
                'links': sorted(links)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        while pending:
            results = list(pool.map(download, sorted(pending)))
            pending = set()
            for result in results:
                pages[result['url']] = result
                pending.update(result['links'])
                print('Fetched', result['category'], result['title'], flush=True)
            pending.difference_update(pages)
    ordered = sorted(pages.values(), key=lambda p: (p['category'], p['url']))
    # This new article is linked under AI 编程 on the index, but its sidebar
    # has not yet been assigned to that group by the source site.
    for page in ordered:
        if page['category'] == '大模型面经' and page['url'] != BASE:
            page['category'] = 'AI编程'
    write_json(ARCHIVE / 'manifest.json', {'source': BASE, 'fetchedAt': datetime.now(timezone.utc).isoformat(), 'pages': ordered})
    print(json.dumps({'pages': len(ordered), 'categories': dict(Counter(p['category'] for p in ordered)),
                      'images': len(set(im for p in ordered for im in p['images'])),
                      'tags': dict(sum((Counter(p['tags']) for p in ordered), Counter()))}, ensure_ascii=False))


def compact(text):
    return re.sub(r'\s+', '', text)


def walk(node):
    yield node
    for child in node.get('content', []):
        yield from walk(child)


class Converter:
    """Translate article DOM to the editor's ProseMirror JSON without rewriting."""
    blocks = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li',
              'blockquote', 'pre', 'table', 'div', 'hr'}
    mark_tags = {'strong': 'bold', 'b': 'bold', 'em': 'italic', 'i': 'italic',
                 'u': 'underline', 's': 'strike', 'del': 'strike', 'code': 'code', 'mark': 'highlight'}

    def __init__(self, url, images):
        self.url, self.images = url, images

    def inline(self, element, marks=()):
        if isinstance(element, Comment):
            return []
        if isinstance(element, NavigableString):
            text = re.sub(r'[\t\r\n ]+', ' ', str(element))
            return [{'type': 'text', 'text': text, **({'marks': list(marks)} if marks else {})}] if text else []
        if element.name == 'br':
            return [{'type': 'hardBreak'}]
        if element.name == 'img':
            original = self.images[urljoin(self.url, element['src'])]
            attrs = {'src': original['dataUrl'], 'alt': element.get('alt', '')}
            if element.get('title'):
                attrs['title'] = element['title']
            # Keep original pixels; the editor scales them to its reading width.
            if element.get('width', '').isdigit():
                attrs['width'] = int(element['width'])
            # ProseMirror block images cannot carry inline marks. The original
            # linked-image HTML remains available in the archived source page.
            return [{'type': 'image', 'attrs': attrs}]
        if element.name in self.mark_tags:
            mark = {'type': self.mark_tags[element.name]}
            marks = (*marks, mark) if mark not in marks else marks
        if element.name == 'a' and element.get('href'):
            href = urljoin(self.url, element['href'])
            if re.match(r'^(https?://|mailto:)', href):
                attrs = {'href': href, 'target': '_blank', 'rel': 'noopener noreferrer nofollow'}
                if element.get('title'):
                    attrs['title'] = element['title']
                marks = (*marks, {'type': 'link', 'attrs': attrs})
        return [node for child in element.children for node in self.inline(child, marks)]

    def flow(self, elements):
        result, inline = [], []

        def flush():
            if any(node['type'] != 'text' or node['text'].strip() for node in inline):
                result.append({'type': 'paragraph', 'content': list(inline)})
            inline.clear()

        for element in elements:
            if isinstance(element, Comment):
                continue
            if getattr(element, 'name', None) in self.blocks:
                flush()
                result.extend(self.block(element))
            else:
                for node in self.inline(element):
                    if node['type'] == 'image':
                        flush()
                        result.append(node)
                    else:
                        inline.append(node)
        flush()
        return result

    def block(self, element):
        tag = element.name
        if tag == 'p':
            return self.flow(element.children) or [{'type': 'paragraph'}]
        if re.fullmatch(r'h[1-6]', tag):
            return [{'type': 'heading', 'attrs': {'level': int(tag[1])},
                     'content': [node for child in element.children for node in self.inline(child)]}]
        if tag == 'pre':
            code = element.find('code') or element
            text = code.get_text()
            languages = [c.removeprefix('language-') for c in element.get('class', []) + code.get('class', []) if c.startswith('language-')]
            node = {'type': 'codeBlock', 'attrs': {'language': languages[0] if languages else None}}
            if text:
                node['content'] = [{'type': 'text', 'text': text}]
            return [node]
        if tag in {'ul', 'ol'}:
            items = []
            for item in element.find_all('li', recursive=False):
                content = self.flow(item.children)
                if not content or content[0]['type'] != 'paragraph':
                    content.insert(0, {'type': 'paragraph'})
                items.append({'type': 'listItem', 'content': content})
            node = {'type': 'orderedList' if tag == 'ol' else 'bulletList', 'content': items}
            if tag == 'ol':
                node['attrs'] = {'start': int(element.get('start', 1))}
            return [node]
        if tag == 'blockquote':
            return [{'type': 'blockquote', 'content': self.flow(element.children)}]
        if tag == 'hr':
            return [{'type': 'horizontalRule'}]
        if tag == 'table':
            rows = []
            for tr in element.find_all('tr'):
                cells = []
                for cell in tr.find_all(['th', 'td'], recursive=False):
                    cells.append({'type': 'tableHeader' if cell.name == 'th' else 'tableCell',
                                  'attrs': {'colspan': int(cell.get('colspan', 1)), 'rowspan': int(cell.get('rowspan', 1))},
                                  'content': self.flow(cell.children) or [{'type': 'paragraph'}]})
                rows.append({'type': 'tableRow', 'content': cells})
            return [{'type': 'table', 'content': rows}]
        return self.flow(element.children)


def prepare():
    manifest = json.loads((ARCHIVE / 'manifest.json').read_text(encoding='utf-8'))
    urls = sorted({url for page in manifest['pages'] for url in page['images']})
    (ARCHIVE / 'images').mkdir(exist_ok=True)

    def download(url):
        path = ARCHIVE / 'images' / hashlib.sha256(url.encode()).hexdigest()
        if not path.exists():
            path.write_bytes(fetch(url).content)
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            mime = Image.MIME[image.format]
            size = image.size
            image.verify()
        if mime not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp'}:
            raise ValueError(f'Unsupported original image type {mime}: {url}')
        return url, {'path': str(path), 'mime': mime, 'width': size[0], 'height': size[1], 'bytes': len(data),
                     'sha256': hashlib.sha256(data).hexdigest(), 'dataUrl': f'data:{mime};base64,' + base64.b64encode(data).decode()}

    images = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for url, image in pool.map(download, urls):
            images[url] = image
            if len(images) % 20 == 0:
                print(f'Images {len(images)}/{len(urls)}', flush=True)
    write_json(ARCHIVE / 'images.json', {url: {k: v for k, v in image.items() if k != 'dataUrl'} for url, image in images.items()})
    documents, checks = [], []
    for page in manifest['pages']:
        url = page['url']
        body = article(Path(page['htmlFile']).read_text(encoding='utf-8'))
        doc = {'type': 'doc', 'content': Converter(url, images).flow(body.children)}
        nodes = list(walk(doc))
        source_text = compact(body.get_text())
        saved_text = compact(''.join(node.get('text', '') for node in nodes))
        if source_text != saved_text:
            raise ValueError(f'Article text changed during conversion: {url}')
        original_code = [(pre.find('code') or pre).get_text() for pre in body.find_all('pre')]
        saved_code = [''.join(child.get('text', '') for child in node.get('content', [])) for node in nodes if node['type'] == 'codeBlock']
        if original_code != saved_code:
            raise ValueError(f'Code whitespace changed: {url}')
        expected = {'image': len(body.find_all('img')), 'table': len(body.find_all('table')),
                    'heading': len(body.find_all(re.compile(r'^h[1-6]$'))), 'codeBlock': len(original_code)}
        for kind, count in expected.items():
            if sum(n['type'] == kind for n in nodes) != count:
                raise ValueError(f'{kind} count mismatch: {url}')
        category = page['category']
        if category == '大模型面经' and url != BASE:
            category = 'AI编程'
        content = json.dumps({'format': 'campus-knowledge-v1', 'doc': doc}, ensure_ascii=False, separators=(',', ':'))
        if len(content.encode()) > 24 * 1024 * 1024:
            raise ValueError(f'Original article exceeds editor document limit: {url}')
        documents.append({'id': str(uuid.uuid5(uuid.NAMESPACE_URL, url)),
                          'category': f'卡码笔记 / 大模型面经 / {category}', 'title': page['title'],
                          'sourceUrl': url, 'content': content})
        checks.append({'url': url, 'title': page['title'], 'textCharacters': len(source_text),
                       'textSha256': hashlib.sha256(source_text.encode()).hexdigest(),
                       'contentBytes': len(content.encode()), 'contentSha256': hashlib.sha256(content.encode()).hexdigest(),
                       'exactCodeWhitespace': True, **expected})
    write_json(ARCHIVE / 'documents.json', documents)
    write_json(ARCHIVE / 'validation.json', {'documents': len(documents), 'uniqueImages': len(images), 'checks': checks})
    print(json.dumps({'prepared': len(documents), 'uniqueImages': len(images), 'imageBytes': sum(im['bytes'] for im in images.values()),
                      'documentBytes': sum(c['contentBytes'] for c in checks), 'largestDocumentBytes': max(c['contentBytes'] for c in checks),
                      'textCharacters': sum(c['textCharacters'] for c in checks)}, ensure_ascii=False))


def apply(database):
    documents = json.loads((ARCHIVE / 'documents.json').read_text(encoding='utf-8'))
    timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True, timeout=30)
    try:
        backup = ARCHIVE / ('before-import-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite')
        with closing(sqlite3.connect(backup)) as destination:
            db.backup(destination)
        before = dict(db.execute('SELECT id, content FROM knowledge_documents'))
        unrelated = {name: db.execute(f'SELECT * FROM "{name}"').fetchall() for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'knowledge_documents'")}
        inserted, skipped = 0, 0
        with db:
            for document in documents:
                if document['id'] in before:
                    skipped += 1
                    continue
                db.execute('INSERT INTO knowledge_documents(id,category,title,content,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                           (document['id'], document['category'], document['title'], document['content'], timestamp, timestamp))
                inserted += 1
            for document in documents:
                row = db.execute('SELECT content FROM knowledge_documents WHERE id=?', (document['id'],)).fetchone()
                expected = before.get(document['id'], document['content'])
                if not row or row[0] != expected:
                    raise ValueError('Database readback mismatch')
            for ident, content in before.items():
                if db.execute('SELECT content FROM knowledge_documents WHERE id=?', (ident,)).fetchone()[0] != content:
                    raise ValueError('An existing document changed')
            for table, rows in unrelated.items():
                if db.execute(f'SELECT * FROM "{table}"').fetchall() != rows:
                    raise ValueError(f'Unexpected changes to {table}')
        result = {'inserted': inserted, 'skipped': skipped, 'existingPreserved': len(before),
                  'totalDocuments': db.execute('SELECT COUNT(*) FROM knowledge_documents').fetchone()[0],
                  'database': str(database.resolve()), 'backup': str(backup.resolve()), 'completedAt': timestamp,
                  'integrityCheck': db.execute('PRAGMA integrity_check').fetchone()[0]}
        write_json(ARCHIVE / ('import-result-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json'), result)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        db.close()

def inspect():
    manifest_path = ARCHIVE / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        images = sorted({url for page in manifest['pages'] for url in page['images']})
        print('IMAGE_SAMPLE', images[:5])
        cached = list((ARCHIVE / 'images').glob('*'))
        print('CACHED_IMAGES', len(cached), 'BYTES', sum(p.stat().st_size for p in cached))
    with closing(sqlite3.connect('file:data/career.sqlite?mode=ro', uri=True)) as db:
        print('EXISTING', db.execute('SELECT id, category, title, length(content) FROM knowledge_documents').fetchall())


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'collect', 'prepare', 'apply'])
    parser.add_argument('--database', type=Path, default=Path('data/career.sqlite'))
    args = parser.parse_args()
    if args.action == 'apply':
        apply(args.database)
    else:
        {'inspect': inspect, 'collect': collect, 'prepare': prepare}[args.action]()
