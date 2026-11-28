import json
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with sqlite3.connect(root / 'data/career.sqlite') as conn:
    conn.row_factory = sqlite3.Row
    counts = dict(conn.execute('SELECT status, COUNT(*) FROM jobs GROUP BY status').fetchall())
    active_tags = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='opportunity' AND instr(tags_json, '__vacancy:active') > 0").fetchone()[0]
    applied = [dict(r) for r in conn.execute("SELECT c.name,j.title FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.status='applied' ORDER BY c.name,j.title")]
    columns = [r[1] for r in conn.execute("PRAGMA table_info('jobs')")]
print(json.dumps({'status_counts': counts, 'active_tagged_opportunities': active_tags, 'applied_count': len(applied), 'exam_at_column': 'exam_at' in columns}, ensure_ascii=False, indent=2))
