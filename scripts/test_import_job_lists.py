from contextlib import closing
import csv
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('import_job_lists', Path(__file__).with_name('import_job_lists.py'))
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def record(number, title='前端工程师', city='深圳', url='https://example.test/campus', link_type='招聘项目入口'):
    return {'row': {'编号': number, '公司': '示例公司', '岗位': title, '工作地点': city,
                    '投递链接': url, '链接类型': link_type, '分组': '待核验线索',
                    '官方发布日期': '', '官方更新日期': '2026-11-20', '投递进度': '未投递'},
            'sources': [{'file': '02_测试.csv', 'line': 2}]}


class ImportTests(unittest.TestCase):
    def test_shared_portal_is_not_job_identity(self):
        self.assertEqual(len(importer.group_records([record('1'), record('2', title='客户端工程师')])), 2)

    def test_distinct_official_ids_and_cities_are_preserved(self):
        rows = [record('1', url='https://jobs.test/position/111/detail', link_type='官方岗位详情'),
                record('2', url='https://jobs.test/position/222/detail', link_type='官方岗位详情'),
                record('3', city='北京', url='https://jobs.test/position/111/detail', link_type='官方岗位详情')]
        self.assertEqual(len(importer.group_records(rows)), 3)

    def test_mobile_desktop_post_ids_merge(self):
        rows = [record('1', url='https://jobs.test/pc/detail?postId=abc', link_type='官方岗位详情'),
                record('2', url='https://jobs.test/mobile/detail?postId=abc', link_type='官方岗位详情')]
        self.assertEqual(len(importer.group_records(rows)), 1)

    def test_hash_routes_remain_distinct(self):
        left = importer.official_identity('https://jobs.test/campus#/job/abc')
        right = importer.official_identity('https://jobs.test/campus#/job/xyz')
        self.assertIsNotNone(left)
        self.assertNotEqual(left, right)

    def test_metadata_dates_and_link_selection(self):
        listing = record('1', title='AI DevOps 工程师')
        detail = record('2', title='AI DevOps工程师', url='https://jobs.test/campus#/job/abc', link_type='官方岗位详情')
        detail['sources'].append({'file': '07_优先投递.csv', 'line': 2})
        groups = importer.group_records([listing, detail])
        self.assertEqual(len(groups), 1)
        job = importer.prepare_job(groups[0], 'sample')
        self.assertEqual(job['source_url'], detail['row']['投递链接'])
        self.assertIsNone(job['published_at'])
        self.assertIn('官方更新日期：2026-11-20', job['jd_text'])
        self.assertIn('优先投递', job['tags'])
        self.assertEqual(job['source_ids'], ['1', '2'])

    def test_repeat_import_preserves_application_and_interview(self):
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=workspace / '.toolchain') as folder:
            root = Path(folder)
            source = root / 'sources'
            source.mkdir()
            primary = record('1')['row']
            excluded = record('2', title='不匹配岗位')['row']
            for prefix in importer.PREFIXES:
                rows = [excluded if prefix == '05_' else primary]
                importer.write_csv(source / (prefix + '测试.csv'), rows, list(primary))
            database = root / 'career.sqlite'
            with closing(sqlite3.connect((workspace / 'data/career.sqlite').as_uri() + '?mode=ro', uri=True)) as original:
                schema = [row[0] for row in original.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC")]
            with closing(sqlite3.connect(database)) as db, db:
                for sql in schema:
                    db.execute(sql)
            first = importer.import_lists(source, database, root / 'first', apply=True)
            self.assertEqual(first['counts']['new_jobs'], 1)
            self.assertEqual(first['counts']['excluded_not_imported'], 1)
            with closing(sqlite3.connect(first['backup'])) as backup:
                self.assertEqual(backup.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
            with closing(sqlite3.connect(database)) as db, db:
                job_id = db.execute('SELECT id FROM jobs').fetchone()[0]
                db.execute("UPDATE jobs SET status='applied',current_stage=5,applied_at='2026-11-21',last_activity_at='2026-11-21'")
                db.execute("INSERT INTO interview_sessions(id,job_id,round_type,summary,updated_at) VALUES('session',?,'first','保留复盘','2026-11-21')", (job_id,))
                db.execute("INSERT INTO interview_questions(id,session_id,sort_order,question_text,answer_text,updated_at) VALUES('question','session',0,'保留问题','保留回答','2026-11-21')")
            second = importer.import_lists(source, database, root / 'second', apply=True)
            self.assertEqual(second['counts']['new_jobs'], 0)
            self.assertEqual(second['counts']['existing_jobs_skipped'], 1)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute('SELECT status,current_stage FROM jobs').fetchall(), [('applied', 5)])
                self.assertEqual(db.execute('SELECT answer_text FROM interview_questions').fetchall(), [('保留回答',)])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM job_events').fetchone()[0], 1)
            third = importer.import_lists(source, database, root / 'third', apply=True, include_excluded=True)
            self.assertEqual(third['counts']['new_jobs'], 1)

    def test_excluded_company_is_removed_from_csv_results_but_keeps_manual_jobs(self):
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=workspace / '.toolchain') as folder:
            root = Path(folder)
            source = root / 'sources'
            source.mkdir()
            csv_record = record('1', title='Seed 安全研发')['row']
            csv_record['公司'] = '字节跳动'
            for prefix in importer.PREFIXES:
                rows = [csv_record] if prefix == '02_' else []
                importer.write_csv(source / (prefix + '测试.csv'), rows, list(csv_record))
            database = root / 'career.sqlite'
            with closing(sqlite3.connect(workspace / 'data/career.sqlite')) as existing:
                schema = [row[0] for row in existing.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC")]
            with closing(sqlite3.connect(database)) as db:
                for sql in schema:
                    db.execute(sql)
                db.execute("INSERT INTO companies(id,name,normalized_name,created_at) VALUES('company','字节跳动','字节跳动','now')")
                db.execute("INSERT INTO jobs(id,company_id,title,status,created_at,source_kind,city,updated_at) VALUES('csv-job','company','CSV 岗位','applied','now','csv','北京','now')")
                db.execute("INSERT INTO jobs(id,company_id,title,status,created_at,source_kind,city,updated_at) VALUES('manual-job','company','手动岗位','opportunity','now','manual','北京','now')")
                db.execute("INSERT INTO job_events(id,job_id,event_type,occurred_at) VALUES('csv-event','csv-job','applied','now')")
                db.execute("INSERT INTO interview_sessions(id,job_id,round_type,updated_at) VALUES('csv-session','csv-job','first','now')")
                db.commit()
            result = importer.import_lists(source, database, root / 'report', apply=True,
                                          exclude_companies=['字节跳动'], remove_excluded_from_db=True)
            self.assertEqual(result['counts']['excluded_company_source_records'], 1)
            self.assertEqual(result['counts']['removed_existing_jobs'], 1)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute('SELECT id FROM jobs').fetchall(), [('manual-job',)])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM interview_sessions').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM job_events').fetchone()[0], 0)
            with (root / 'report' / '去重后岗位.csv').open(encoding='utf-8-sig', newline='') as stream:
                self.assertEqual(list(csv.DictReader(stream)), [])


if __name__ == '__main__':
    unittest.main()
