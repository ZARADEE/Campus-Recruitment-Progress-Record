import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import import_kamacoder_llm as importer


class MigrationTests(unittest.TestCase):
    def test_nested_content_code_and_images_keep_the_source_order(self):
        html = '''<div class="theme-default-content"><h1><a class="header-anchor">#</a> 标题</h1>
        <p>前<strong>加粗<a href="next.html#part">链接</a></strong><img src="/a.png" alt="图">后</p>
        <h4>第四级</h4><ul><li>一级<ul><li>二级</li></ul></li></ul>
        <div class="language-python"><pre><code class="language-python">if True:\n    print('中文')\n</code></pre>
        <div class="line-numbers-wrapper">1 2</div></div>
        <table><tr><th>表头</th></tr><tr><td>数据</td></tr></table></div>'''
        body = importer.article(html)
        images = {'https://notes.kamacoder.com/a.png': {'dataUrl': 'data:image/png;base64,AAAA'}}
        doc = {'type': 'doc', 'content': importer.Converter(importer.BASE, images).flow(body.children)}
        nodes = list(importer.walk(doc))
        self.assertEqual(importer.compact(body.get_text()), importer.compact(''.join(n.get('text', '') for n in nodes)))
        self.assertEqual([n['attrs']['level'] for n in nodes if n['type'] == 'heading'], [1, 4])
        code = next(n for n in nodes if n['type'] == 'codeBlock')
        self.assertEqual(code['content'][0]['text'], "if True:\n    print('中文')\n")
        self.assertEqual(code['attrs']['language'], 'python')
        self.assertEqual([n['type'] for n in doc['content']][:5], ['heading', 'paragraph', 'image', 'paragraph', 'heading'])
        link = next(n for n in nodes if n.get('text') == '链接')
        self.assertEqual(link['marks'][1]['attrs']['href'], importer.BASE + 'next.html#part')

    def test_import_is_additive_and_repeat_preserves_user_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'archive'
            archive.mkdir()
            database = root / 'career.sqlite'
            with contextlib.closing(sqlite3.connect(database)) as db, db:
                db.executescript('CREATE TABLE knowledge_documents (id TEXT PRIMARY KEY, category TEXT, title TEXT, content TEXT, created_at TEXT, updated_at TEXT); CREATE TABLE jobs (id TEXT PRIMARY KEY, title TEXT);')
                db.execute('INSERT INTO knowledge_documents VALUES (?,?,?,?,?,?)', ('existing', '个人', '笔记', '原有内容', 'old', 'old'))
                db.execute('INSERT INTO jobs VALUES (?,?)', ('job', '后端'))
            docs = [{'id': 'new', 'category': '导入', 'title': '新文档', 'content': '正文'}]
            importer.write_json(archive / 'documents.json', docs)
            with patch.object(importer, 'ARCHIVE', archive), contextlib.redirect_stdout(io.StringIO()):
                importer.apply(database)
                with contextlib.closing(sqlite3.connect(database)) as db, db:
                    db.execute('UPDATE knowledge_documents SET content=? WHERE id=?', ('用户后续编辑', 'new'))
                importer.apply(database)
            with contextlib.closing(sqlite3.connect(database)) as db:
                self.assertEqual(dict(db.execute('SELECT id,content FROM knowledge_documents')), {'existing': '原有内容', 'new': '用户后续编辑'})
                self.assertEqual(db.execute('SELECT * FROM jobs').fetchall(), [('job', '后端')])
            backups = list(archive.glob('before-import-*.sqlite'))
            self.assertTrue(backups)


if __name__ == '__main__':
    unittest.main()
