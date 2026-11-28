import csv
import json
import re
import sqlite3
import subprocess
import sys
import hashlib
from html import unescape
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/imports/2026-11-28-refresh'
OUT.mkdir(parents=True, exist_ok=True)
sys.stdout.reconfigure(encoding='utf-8')

def normalize(value):
    return re.sub(r'\s+', '', str(value or '')).lower()

def load_progress_companies():
    with sqlite3.connect(ROOT / 'data/career.sqlite') as conn:
        return {normalize(row[0]) for row in conn.execute(
            "SELECT DISTINCT c.name FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.status IN ('applied','closed')"
        )}

def load_audit():
    paths = sorted((ROOT / 'data/imports').glob('2026-11-25-refresh/audit.json'))
    if not paths:
        raise SystemExit('missing prior official audit')
    return json.loads(paths[-1].read_text(encoding='utf-8'))

def target(row):
    text = row.get('title') or ''
    return bool(re.search(r'agent|智能体|前端|客户端|移动端|移动开发|移动应用|android|安卓|ios|鸿蒙|flutter|react native|ai.*全栈', text, re.I))

def fetch(url):
    result = subprocess.run(['curl.exe', '-L', '--silent', '--show-error', '--connect-timeout', '8', '--max-time', '25', url], capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace')[:300])
    payload = json.loads(result.stdout.decode('utf-8-sig'))
    evidence = OUT / 'sources'
    evidence.mkdir(exist_ok=True)
    (evidence / (hashlib.sha256(url.encode()).hexdigest() + '.json')).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return payload

def verify(row):
    result = dict(row, verification='unknown', checked_at=datetime.now().astimezone().isoformat(timespec='seconds'))
    url = row.get('source_url') or ''
    parsed = urlsplit(url)
    try:
        match = re.search(r'/position/(\d+)/detail', url)
        job_id = parse_qs(parsed.query).get('jobAdId', [''])[0]
        if match:
            api = f'https://{parsed.netloc}/api/v1/job/posts/{match[1]}/'
            payload = fetch(api)
            detail = (payload.get('data') or {}).get('job_post_detail') or {}
            if detail.get('title') and detail.get('channel_online_status') == 1:
                result.update(verification='active', official_title=detail['title'], detail=detail, evidence_url=api, reason='官方职位接口 channel_online_status=1')
        elif job_id and '/campus/' in parsed.path:
            api = f'https://{parsed.netloc}/api/JobAd/GetJobAdInfo?jobAdId={job_id}'
            payload = fetch(api)
            detail = payload.get('Data') or {}
            end = str(detail.get('EndTime') or '')[:10] if detail.get('EndTimeInt', 0) else ''
            if detail.get('JobAdName') and detail.get('Status') == 1 and (not end or end >= date.today().isoformat()):
                result.update(verification='active', official_title=detail['JobAdName'], detail=detail, evidence_url=api, reason='官方职位接口 Status=1' + (f'，截止 {end}' if end else '，未公开截止日期'))
        return result
    except Exception as exc:
        result['error'] = str(exc)[:300]
        return result

def make_rows(rows, excluded):
    selected = []
    seen = set()
    for row in rows:
        if row.get('verification') != 'active' or not target(row):
            continue
        company = row.get('company') or ''
        if normalize(company) in excluded:
            continue
        key = (normalize(company), normalize(row.get('title')), row.get('source_url') or '')
        if key in seen:
            continue
        seen.add(key)
        detail = row.get('detail') or {}
        jd = row.get('jd_text') or ''
        if isinstance(detail, dict) and detail.get('JobAdName'):
            clean = lambda value: unescape(re.sub(r'<[^>]+>', '\n', str(value or ''))).strip()
            jd = '\n\n'.join(filter(None, [clean(detail.get('Duty')), clean(detail.get('Require'))])) or jd
        selected.append({
            '公司': company,
            '岗位': row.get('official_title') or row.get('title') or '',
            '城市': row.get('city') or '',
            '发布时间': (row.get('published_at') or '')[:10],
            '薪资': row.get('salary') or '',
            'JD': jd,
            '网址': row.get('source_url') or '',
            '招聘状态': '官方已核验在招',
            '核验日期': row.get('checked_at', '')[:10],
            '来源状态': row.get('reason') or '官方招聘接口显示开放',
            '核验依据': row.get('evidence_url') or row.get('source_url') or '',
        })
    return selected

def main():
    excluded = load_progress_companies()
    audit = load_audit()
    print('excluded', sorted(excluded), flush=True)
    candidates = [row for row in audit if target(row) and normalize(row.get('company')) not in excluded and '/social/' not in (row.get('source_url') or '')]
    print('checking official endpoints', len(candidates), flush=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        verified = list(pool.map(verify, candidates))
    (OUT / 'verification.json').write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = make_rows(verified, excluded)
    rows.sort(key=lambda row: (normalize(row['公司']), normalize(row['岗位'])))
    csv_path = OUT / 'ai-agent-mobile-frontend-opportunities-2026-11-28.csv'
    with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ['公司','岗位','城市','发布时间','薪资','JD','网址'])
        writer.writeheader()
        writer.writerows(rows)
    (OUT / 'excluded-progress-companies.json').write_text(json.dumps(sorted(excluded), ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'selected.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'excluded_companies': len(excluded), 'rows': len(rows), 'csv': str(csv_path)}, ensure_ascii=False))
    for row in rows:
        print(row['公司'], '|', row['岗位'], '|', row['网址'])

if __name__ == '__main__':
    main()
