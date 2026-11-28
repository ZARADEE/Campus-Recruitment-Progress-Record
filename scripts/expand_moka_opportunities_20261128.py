import csv
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/imports/2026-11-28-refresh'
CSV_PATH = OUT / 'ai-agent-mobile-frontend-opportunities-2026-11-28-expanded.csv'

def norm(value):
    return re.sub(r'\s+', '', str(value or '')).casefold()

def target(row):
    return bool(re.search(r'agent|智能体|前端|客户端|移动端|移动开发|移动应用|android|安卓|ios|鸿蒙|flutter|react native|ai.*全栈', row.get('title', ''), re.I))

def main():
    base = json.loads((OUT / 'selected.json').read_text(encoding='utf-8'))
    audit = json.loads((OUT.parent / '2026-11-25-refresh' / 'audit.json').read_text(encoding='utf-8'))
    moka = json.loads((OUT.parent / '2026-11-25-refresh' / 'moka-audit.json').read_text(encoding='utf-8'))
    with sqlite3.connect(ROOT / 'data/career.sqlite') as conn:
        excluded = {norm(row[0]) for row in conn.execute("SELECT DISTINCT c.name FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.status='applied'")}
    rows = []
    seen = set()
    for row in base:
        key = (norm(row['公司']), norm(row['岗位']), row['网址'])
        if norm(row['公司']) not in excluded and key not in seen:
            rows.append(row)
            seen.add(key)
    audit_by_url = {row.get('source_url'): row for row in audit}
    added = 0
    for checked in moka:
        if checked.get('moka_verification') != 'active' or norm(checked.get('company')) in excluded:
            continue
        source = checked.get('source_url') or ''
        original = audit_by_url.get(source)
        if not original or not target(original):
            continue
        key = (norm(original.get('company')), norm(original.get('title')), source)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            '公司': original.get('company', ''),
            '岗位': original.get('title', ''),
            '城市': original.get('city', ''),
            '发布时间': (original.get('published_at') or '')[:10],
            '薪资': original.get('salary') or '',
            'JD': original.get('jd_text') or '',
            '网址': source,
            '招聘状态': '官方已核验在招',
            '核验日期': '2026-11-28',
            '来源状态': 'Moka 官方 ATS 职位列表 status=open',
            '核验依据': checked.get('moka_api') or source,
        })
        added += 1
    rows.sort(key=lambda row: (norm(row['公司']), norm(row['岗位'])))
    with CSV_PATH.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['公司','岗位','城市','发布时间','薪资','JD','网址','招聘状态','核验日期','来源状态','核验依据'])
        writer.writeheader()
        writer.writerows(rows)
    (OUT / 'expanded-selected.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'base_rows': len(base), 'moka_added': added, 'expanded_rows': len(rows), 'csv': str(CSV_PATH)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
