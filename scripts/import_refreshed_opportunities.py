import csv
import json
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / 'data/imports/2026-11-28-refresh/ai-agent-mobile-frontend-opportunities-2026-11-28.csv'
OUT = CSV_PATH.parent
DB = ROOT / 'data/career.sqlite'

def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else CSV_PATH
    if not csv_path.is_absolute():
        csv_path = ROOT / csv_path
    rows = list(csv.DictReader(csv_path.open(encoding='utf-8-sig', newline='')))
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    backup = OUT / 'career-before-opportunity-reset-expanded.sqlite'
    shutil.copy2(DB, backup)
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    columns = {r[1] for r in conn.execute("PRAGMA table_info('jobs')")}
    if 'exam_at' not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN exam_at TEXT NOT NULL DEFAULT ''")
    before_pipeline = [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE status!='opportunity' ORDER BY id")]
    before_interviews = [dict(r) for r in conn.execute('SELECT * FROM interview_sessions ORDER BY id')]
    conn.execute('BEGIN IMMEDIATE')
    old_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='opportunity'").fetchone()[0]
    conn.execute("DELETE FROM jobs WHERE status='opportunity'")
    inserted = []
    for row in rows:
        company = row['公司'].strip()
        normalized = ''.join(company.split()).lower()
        company_row = conn.execute('SELECT id FROM companies WHERE normalized_name=?', (normalized,)).fetchone()
        company_id = company_row['id'] if company_row else str(uuid.uuid5(uuid.NAMESPACE_URL, 'company:' + normalized))
        if not company_row:
            conn.execute('INSERT INTO companies(id,name,normalized_name,created_at) VALUES(?,?,?,?)', (company_id, company, normalized, stamp))
        source_url = row['网址'].strip()
        title = row['岗位'].strip()
        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, source_url + '|' + title))
        tags = ['AI Agent/前端/客户端', '__vacancy:active', '__scope:target', '__verified:2026-11-28', '__source_level:official']
        jd = row['JD'].strip() + f"\n\n官方状态：{row['招聘状态']}\n核验日期：{row['核验日期']}\n核验依据：{row['核验依据']}"
        conn.execute('''INSERT INTO jobs(
            id, company_id, title, status, published_at, created_at, current_stage, active_round,
            source_url, source_kind, city, team, salary, tags_json, jd_text, updated_at, exam_at
        ) VALUES(?,?,?,?,?, ?,0,'first',?,?,?,?,?,?,?, ?, '')''', (
            job_id, company_id, title, 'opportunity', row['发布时间'] or None, stamp,
            source_url, 'csv', row['城市'].strip(), '', row['薪资'].strip(),
            json.dumps(tags, ensure_ascii=False), jd, stamp,
        ))
        conn.execute('INSERT INTO job_events(id,job_id,event_type,occurred_at,note) VALUES(?,?,?,?,?)', (str(uuid.uuid4()), job_id, 'created', stamp, '2026-11-28 官方核验 CSV 导入'))
        inserted.append(job_id)
    after_pipeline = [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE status!='opportunity' ORDER BY id")]
    after_interviews = [dict(r) for r in conn.execute('SELECT * FROM interview_sessions ORDER BY id')]
    if before_pipeline != after_pipeline or before_interviews != after_interviews:
        conn.rollback()
        raise RuntimeError('已投递/已拒绝岗位或面试历史发生意外变化，已回滚')
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
        conn.rollback()
        raise RuntimeError('SQLite integrity check failed')
    conn.commit()
    result = {
        'reset_old_opportunities': old_count,
        'inserted_opportunities': len(inserted),
        'backup': str(backup.relative_to(ROOT)),
        'pipeline_unchanged': True,
        'interview_history_unchanged': True,
        'status_counts': dict(conn.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status').fetchall()),
    }
    (OUT / 'import-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
