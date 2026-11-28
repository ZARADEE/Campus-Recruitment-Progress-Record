"""Read public vacancy status; preserve evidence and only remove confirmed closed opportunities."""
import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/imports/2026-11-25-refresh'
OUT.mkdir(parents=True, exist_ok=True)
CACHE = OUT / 'sources'
CACHE.mkdir(exist_ok=True)
sys.stdout.reconfigure(encoding='utf-8')

def dump(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def fetch(url):
    name = hashlib.sha256(url.encode()).hexdigest()[:24] + '.txt'
    path = CACHE / name
    if path.exists():
        return path.read_text(encoding='utf-8')
    result = subprocess.run(['curl.exe', '-L', '--silent', '--show-error', '--connect-timeout', '7', '--max-time', '22', url], capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace')[:200])
    value = result.stdout.decode('utf-8', errors='replace')
    path.write_text(value, encoding='utf-8')
    return value

def fetch_form(url, fields):
    key = hashlib.sha256((url + json.dumps(fields, sort_keys=True)).encode()).hexdigest()[:24]
    path = CACHE / (key + '.txt')
    if path.exists():
        return path.read_text(encoding='utf-8')
    command = ['curl.exe', '-L', '--silent', '--show-error', '--connect-timeout', '7', '--max-time', '22', '-H', 'Content-Type: application/x-www-form-urlencoded']
    for name, value in fields.items():
        command.extend(['--data-urlencode', f'{name}={value}'])
    command.append(url)
    result = subprocess.run(command, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace')[:200])
    value = result.stdout.decode('utf-8', errors='replace')
    path.write_text(value, encoding='utf-8')
    return value

def check(job):
    r = dict(job, verification='unknown', checked_at=datetime.now(timezone.utc).isoformat())
    url = job.get('source_url') or ''
    p = urlsplit(url)
    try:
        match = re.search(r'/position/(\d+)/detail', url)
        if match:
            api = f'https://{p.netloc}/api/v1/job/posts/{match[1]}/'
            raw = json.loads(fetch(api))
            d = (raw.get('data') or {}).get('job_post_detail') or {}
            status = d.get('channel_online_status')
            r.update(evidence_url=api, official_status=status, official_title=d.get('title'), detail=d)
            if d.get('title') and status in [0, 1]:
                r['verification'] = 'active' if status == 1 else 'closed'
                r['reason'] = f'企业公开职位接口 channel_online_status={status}'
        elif parse_qs(p.query).get('jobAdId'):
            api = f'https://{p.netloc}/api/JobAd/GetJobAdInfo?jobAdId={parse_qs(p.query)["jobAdId"][0]}'
            raw = json.loads(fetch(api))
            d = raw.get('Data') or {}
            status = d.get('Status') if isinstance(d, dict) else None
            r.update(evidence_url=api, official_status=status, detail=d)
            if isinstance(d, dict) and d.get('JobAdName') and status in [1, 2]:
                r['official_title'] = d['JobAdName']
                r['verification'] = 'active' if status == 1 else 'closed'
                r['reason'] = f'企业公开职位接口 Status={status}'
                if d.get('EndTimeInt', 0) > 0 and str(d.get('EndTime', ''))[:10] < '2026-11-25':
                    r['verification'] = 'closed'
                    r['reason'] = '官方投递截止时间已过：' + str(d['EndTime'])
        else:
            html = fetch(url) if url else ''
            r['evidence_url'] = url
            # A generic JS shell or a failed fetch is not evidence of closure.
            r['page_length'] = len(html)
            if '4399om.com' in p.netloc:
                api = 'https://hr.4399om.com/main/?r=officialSite/jobList&type=1&applicationType=1'
                listing = json.loads(fetch(api))['data']['list']
                found = next((x for x in listing if x.get('jobName') == job['title']), None)
                r['evidence_url'] = api
                if found:
                    r.update(verification='active', detail=found, reason='官网当前全职校招列表包含此岗位')
                else:
                    r.update(verification='closed', official_status='not_listed', reason=f'企业官网当前全职校招列表（{len(listing)}条）已不包含该岗位')
            if job['title'] in html and re.search('该职位已下线|该岗位已下线|职位已停止招聘|职位已关闭', html):
                r.update(verification='closed', reason='官方职位页面明确显示职位已下线')
    except Exception as error:
        r['error'] = str(error)[:220]
    return r

def database():
    c = sqlite3.connect(f'file:{ROOT / "data/career.sqlite"}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c

def audit():
    with database() as c:
        jobs = [dict(r) for r in c.execute('SELECT j.*, c.name AS company FROM jobs j JOIN companies c ON c.id=j.company_id')]
        dump('before-jobs.json', jobs)
        dump('before-interviews.json', [dict(r) for r in c.execute('SELECT * FROM interview_sessions')])
    opportunities = [j for j in jobs if j['status'] == 'opportunity']
    print('opportunities', len(opportunities), 'companies', len({j['company'] for j in opportunities}), flush=True)
    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(check, opportunities):
            rows.append(r)
            if len(rows) % 40 == 0:
                print('checked', len(rows), dict(Counter(x['verification'] for x in rows)), flush=True)
    dump('audit.json', rows)
    print('result', dict(Counter(x['verification'] for x in rows)), flush=True)
    for r in rows:
        if r['verification'] == 'closed':
            print('CLOSED', r['id'], r['company'], r['title'], r['reason'])

def summary():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    print('status', dict(Counter(x['verification'] for x in rows)))
    print('unknown hosts', Counter(urlsplit(x.get('source_url') or '').netloc for x in rows if x['verification']=='unknown'))
    for r in rows:
        if r['verification']=='closed': print('CLOSED', r['id'], r['company'], r['title'], r.get('reason'))
    print('Feishu statuses', Counter(str(r.get('official_status')) for r in rows if '/position/' in (r.get('source_url') or '')))
    for r in rows:
        if r['verification']=='active' and (r.get('published_at') or '') >= '2026-11-01' and re.search(r'移动端|客户端|前端|agent|智能体|AI应用', r['title'], re.I):
            print('RECENT_MATCH', r['company'], r['title'], r.get('published_at'), r.get('official_title'), r.get('source_url'))
        if r.get('official_status') == 0:
            print('FEISHU_0', r['company'], r['title'], r.get('published_at'), r.get('source_url'))
        if r['company'].startswith('面壁'):
            print('MIANBI', r['verification'], r.get('official_status'), r['title'], r.get('published_at'))
        if r['company']=='4399':
            print('4399', r['title'], r.get('detail'), r.get('error'), r.get('page_length'))
    listing = json.loads(fetch('https://hr.4399om.com/main/?r=officialSite/jobList&type=1&applicationType=1'))
    jobs = (listing.get('data') or {}).get('list') or []
    print('4399_CURRENT_LIST', 'count=', len(jobs), 'meta=', {k:v for k,v in listing.get('data',{}).items() if k != 'list'}, 'titles=', [j.get('jobName') for j in jobs])
    for item in jobs:
        if re.search(r'客户端|Agent', item.get('jobName', ''), re.I):
            print('4399_MATCH', json.dumps(item, ensure_ascii=False))


def add_candidates():
    listing = json.loads(fetch('https://hr.4399om.com/main/?r=officialSite/jobList&type=1&applicationType=1'))
    jobs = (listing.get('data') or {}).get('list') or []
    candidates = []
    for item in jobs:
        if item.get('jobName') not in ['【2027校招】SDK客户端开发工程师', '【2027校招】AI Agent技术开发岗']:
            continue
        candidates.append({
            'company': '4399', 'title': item['jobName'], 'city': item.get('workCity', ''),
            'published_at': None, 'source_url': item.get('jobUrl') or 'https://hr.4399om.com/campus/graduate/',
            'source_kind': '2027届校招（企业官网在招）', 'salary': '', 'tags': ['客户端', '移动端', 'AI Agent'],
            'jd_text': f"官方当前校招职位清单详情\n\n岗位职责：\n{item.get('jobDesc','')}\n\n岗位要求：\n{item.get('requirement','')}\n\n官网岗位编号：{item.get('jobID','')}\n官网清单：https://hr.4399om.com/campus/graduate/"
        })
    candidates.extend([
        {
            'company': '华勤技术', 'title': '应用软件开发工程师（2027届）',
            'city': '无锡、南昌、东莞、西安、上海等', 'published_at': '2026-08-17',
            'source_url': 'https://jobs.huaqin.com/',
            'source_kind': '2027届秋招补录（软件开发；官网复核岗位编号MJ007915）', 'salary': '',
            'tags': ['移动端', 'Android', '客户端'],
            'jd_text': 'Android应用软件开发方向：完善Android Application应用层功能，进行Android应用层和系统层功能设计、编码、调试与问题分析；使用C++或Java。要求本科及以上，计算机、电子、自动化、通信等理工科相关专业。\n\n状态核验：华勤技术2026-11-24补录公告说明软件开发类岗位仍在持续招聘；该具体岗位信息于2026-11-23更新，岗位编号MJ007915。投递前请在华勤官方招聘门户按岗位名称或编号确认当前城市和是否仍接收简历。岗位详情线索：https://offerseek.ai/jobs/43763；官方补录公告：https://f.15386.cn/a/480204.html。'
        },
        {
            'company': '药明康德', 'title': '2027届-DMPK-AI Agent应用开发-南京(J25506)', 'city': '南京',
            'published_at': '2026-11-24',
            'source_url': 'https://wuxiapptec.zhiye.com/campus/detail?activityGuid=9a36f7d2-7735-47f8-a8e5-4870632f9ec1&jobAdId=b8b1ee47-21ed-4fde-930b-3a1bece41ac4',
            'source_kind': '2027届校招（AI Agent）', 'salary': '', 'tags': ['AI Agent', 'LLM', '前端', '全栈'],
            'jd_text': 'AI Agent设计与开发：基于大语言模型和领域知识开发实验设计辅助、数据分析、报告生成等智能体；完成需求分析、系统设计、对话接口、RAG管线、工具调用、前端交互、测试和部署。建设Evaluation Harness和自动化评测流水线，集成ELN及低代码平台。\n\n要求：本科及以上，计算机/软件相关；理解LLM、Prompt、RAG、Agent推理循环；有LangChain、AutoGen、LlamaIndex等项目经验；熟悉Vue.js及C#/.NET、Python/Django等开发技术。\n\n官方岗位详情入口：药明康德校园招聘门户（岗位编号J25506）。职位信息来自企业招聘渠道，页面最近更新于2026-11-25。'
        },
        {
            'company': '广州虹科电子科技有限公司', 'title': 'AI应用开发工程师（2027届秋招补录）', 'city': '上海、广州',
            'published_at': '2026-11-19', 'source_url': 'https://job.hkaco.com/',
            'source_kind': '2027届秋招补录（官方高校就业网公告）', 'salary': '', 'tags': ['AI应用', 'LLM', 'RAG'],
            'jd_text': '2026-11-19发布的2027届秋招补录岗位。职责包括在工业、嵌入式、IT、安全场景部署和优化AI算法，进行LLM API对接、Prompt工程与功能开发，并针对低功耗硬件做模型优化。\n\n要求：硕士及以上，计算机、电子信息、通信、自动化、人工智能等相关专业；掌握Python/C/C++至少一门；了解TensorFlow Lite、LLM、RAG。该JD未明确要求Agent框架，作为AI应用相关岗位收录。\n\n来源：南开大学就业网补录公告 https://career.nankai.edu.cn/correcruit/content/id/120722.html；申请入口为虹科招聘官网。'
        }
    ])
    dump('candidate-jobs.json', candidates)
    return candidates


def apply():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    deletions = [r for r in rows if r['verification'] == 'closed']
    candidates = add_candidates()
    old_report = json.loads((OUT / 'refresh-report.json').read_text(encoding='utf-8')) if (OUT / 'refresh-report.json').exists() else {}
    db_path = ROOT / 'data/career.sqlite'
    backup_path = OUT / 'career-before-refresh.sqlite'
    connection = sqlite3.connect(db_path, timeout=30)
    connection.execute('PRAGMA foreign_keys=ON')
    if not backup_path.exists():
        with sqlite3.connect(backup_path) as backup:
            connection.backup(backup)
    connection.row_factory = sqlite3.Row
    stamp = datetime.now().astimezone().isoformat(timespec='seconds')
    added, skipped, removed = [], [], []
    try:
        connection.execute('BEGIN IMMEDIATE')
        for item in deletions:
            current = connection.execute('SELECT status FROM jobs WHERE id=?', (item['id'],)).fetchone()
            if current and current['status'] == 'opportunity':
                connection.execute('DELETE FROM jobs WHERE id=? AND status=?', (item['id'], 'opportunity'))
                removed.append({'id': item['id'], 'company': item['company'], 'title': item['title'], 'reason': item['reason'], 'evidence_url': item.get('evidence_url')})
        for item in candidates:
            normalized = ''.join(item['company'].split()).lower()
            existing = connection.execute('SELECT id FROM companies WHERE normalized_name=?', (normalized,)).fetchone()
            company_id = existing['id'] if existing else str(uuid.uuid5(uuid.NAMESPACE_URL, 'company:' + normalized))
            if not existing:
                connection.execute('INSERT INTO companies(id,name,normalized_name,created_at) VALUES(?,?,?,?)', (company_id, item['company'], normalized, stamp))
            duplicate = connection.execute('SELECT id FROM jobs WHERE company_id=? AND title=? AND city=?', (company_id, item['title'], item['city'])).fetchone()
            if duplicate:
                skipped.append({'company': item['company'], 'title': item['title'], 'existing_id': duplicate['id']})
                continue
            job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, item['source_url'] + '|' + item['title']))
            connection.execute('''INSERT INTO jobs(id,company_id,title,status,published_at,created_at,current_stage,active_round,source_url,source_kind,city,team,salary,tags_json,jd_text,updated_at)
                VALUES(?,?,?,'opportunity',?,?,0,'first',?,?,?,?,?,?,?,?)''', (
                job_id, company_id, item['title'], item['published_at'], stamp, item['source_url'], item['source_kind'], item['city'], '', item['salary'], json.dumps(item['tags'], ensure_ascii=False), item['jd_text'], stamp
            ))
            added.append({'id': job_id, 'company': item['company'], 'title': item['title'], 'source_url': item['source_url']})
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    def merge_records(previous, current, key):
        combined = {item[key]: item for item in previous if key in item}
        combined.update({item[key]: item for item in current if key in item})
        return list(combined.values())
    report = {
        'updated_at': stamp, 'backup': str(backup_path.relative_to(ROOT)),
        'removed': merge_records(old_report.get('removed', []), removed, 'id'),
        'added': merge_records(old_report.get('added', []), added, 'id'),
        'skipped_duplicates': merge_records(old_report.get('skipped_duplicates', []), skipped, 'existing_id'),
        'audit_status': dict(Counter(r['verification'] for r in rows))
    }
    dump('refresh-report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def verify():
    report = json.loads((OUT / 'refresh-report.json').read_text(encoding='utf-8'))
    with database() as connection:
        counts = dict(connection.execute('SELECT status, COUNT(*) FROM jobs GROUP BY status').fetchall())
        interview_columns = {row['name'] for row in connection.execute("PRAGMA table_info('interview_sessions')")}
        records = []
        for item in report['added'] + report['skipped_duplicates']:
            found = connection.execute('''SELECT j.id,c.name AS company,j.title,j.status,j.city,j.source_url
                FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=?''', (item.get('id') or item.get('existing_id'),)).fetchone()
            records.append(dict(found) if found else {'missing': item})
        removed_present = [item['id'] for item in report['removed'] if connection.execute('SELECT 1 FROM jobs WHERE id=?', (item['id'],)).fetchone()]
    result = {'status_counts': counts, 'refreshed_records': records, 'removed_records_still_present': removed_present, 'interview_time_column': 'interview_time' in interview_columns, 'backup_exists': (ROOT / report['backup']).is_file()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


def migrate():
    connection = sqlite3.connect(ROOT / 'data/career.sqlite', timeout=30)
    columns = {row[1] for row in connection.execute("PRAGMA table_info('interview_sessions')")}
    if 'interview_time' not in columns:
        connection.execute("ALTER TABLE interview_sessions ADD COLUMN interview_time TEXT NOT NULL DEFAULT ''")
        connection.commit()
        print('added interview_time column to existing career database')
    else:
        print('interview_time column already exists')
    connection.close()


def inventory():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    unknown = [r for r in rows if r['verification'] == 'unknown']
    grouped = {}
    for row in unknown:
        host = urlsplit(row.get('source_url') or '').netloc or '(no source URL)'
        grouped.setdefault(host, []).append({k: row.get(k) for k in ['id', 'company', 'title', 'published_at', 'source_url', 'error', 'official_status']})
    dump('unknowns.json', unknown)
    print(json.dumps({host: {'count': len(items), 'examples': items[:2]} for host, items in sorted(grouped.items(), key=lambda pair: -len(pair[1]))}, ensure_ascii=False, indent=2))

def focus():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    pattern = re.compile(r'mobile|android|ios|客户端|移动端|前端|web|agent|智能体|ai应用|应用开发', re.I)
    selected = [r for r in rows if r['verification'] == 'unknown' and pattern.search((r.get('title') or '') + ' ' + (r.get('jd_text') or ''))]
    print('focused_unknowns', len(selected))
    for r in selected:
        print(json.dumps({k: r.get(k) for k in ['id', 'company', 'title', 'source_url', 'error', 'official_status', 'evidence_url']}, ensure_ascii=False))

def inspect_unknown_statuses():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    samples = [r for r in rows if r.get('official_status') in (0, 1)]
    emitted = {0: 0, 1: 0}
    for row in samples:
        if emitted[row['official_status']] < 3:
            detail = row.get('detail') or {}
            nested = {k: sorted(detail.get(k)) if isinstance(detail.get(k), dict) else ([{x: item.get(x) for x in ['id', 'name', 'status', 'channel_online_status'] if x in item} for item in detail.get(k, [])[:3]] if isinstance(detail.get(k), list) else detail.get(k)) for k in ['job_post_info', 'storefront_list', 'storefront_mode', 'recruit_type'] if k in detail}
            print(json.dumps({'id': row['id'], 'company': row['company'], 'title': row['title'], 'source_url': row.get('source_url'), 'official_status': row.get('official_status'), 'detail_keys': sorted(detail), 'nested': nested}, ensure_ascii=False))
            emitted[row['official_status']] += 1
    for row in rows:
        if row.get('verification') == 'unknown' and 'app.mokahr.com' in (row.get('source_url') or ''):
            print('MOKA_UNKNOWN', json.dumps({k: row.get(k) for k in ['id', 'company', 'title', 'source_url', 'evidence_url', 'official_status']}, ensure_ascii=False))

def list_host():
    host = sys.argv[2] if len(sys.argv) > 2 else ''
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    for row in rows:
        if row.get('verification') == 'unknown' and host in urlsplit(row.get('source_url') or '').netloc:
            print(json.dumps({k: row.get(k) for k in ['id', 'company', 'title', 'source_url', 'error', 'official_status', 'evidence_url']}, ensure_ascii=False))

def discover_xhs_api():
    js = fetch('https://fe-static.xhscdn.com/formula-static/ats-website/public/js/main.78840e4.js')
    values = re.findall(r'["\']([^"\'\r\n]{0,180}(?:/api/|/campus/|position|jobList|job/list)[^"\'\r\n]{0,180})["\']', js, re.I)
    seen = set()
    for value in values:
        if value not in seen:
            print(value[:400])
            seen.add(value)
    runtime = fetch('https://fe-static.xhscdn.com/formula-static/ats-website/public/js/runtime-main.358c129.js')
    for token in ['6733', '9787', '3595']:
        match = re.search(re.escape(token), runtime)
        if match:
            print('RUNTIME', token, runtime[max(0, match.start()-160):match.end()+160])
    for chunk_id, hash_value in [('6733', '438b56f'), ('9787', '883f431'), ('3595', '0930141')]:
        chunk = fetch(f'https://fe-static.xhscdn.com/formula-static/ats-website/public/js/{chunk_id}.{hash_value}.chunk.js')
        values = re.findall(r'["\']([^"\'\r\n]{0,160}(?:/api/|/campus/|position|job)[^"\'\r\n]{0,160})["\']', chunk, re.I)
        print('CHUNK', chunk_id, 'size', len(chunk))
        for value in dict.fromkeys(values):
            print('API_STRING', value[:350])
        for token in ['/websiterecruit/position/pageQueryPosition', '/websiterecruit/position/queryPositionDetail']:
            match = re.search(re.escape(token), chunk)
            if match:
                print('API_CONTEXT', chunk_id, chunk[max(0, match.start()-500):match.end()+650])

def verify_moka():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    candidates = [r for r in rows if r['verification'] == 'unknown' and 'app.mokahr.com' in (r.get('source_url') or '')]
    grouped = {}
    for row in candidates:
        p = urlsplit(row['source_url'])
        parts = [part for part in p.path.split('/') if part]
        if len(parts) < 3 or parts[0] not in ('campus-recruitment', 'social-recruitment', 'apply', 'recommendation-apply'):
            row['moka_verification'] = 'unknown'
            continue
        org_id, site_id = parts[1], parts[2]
        mode = 'social' if parts[0] == 'social-recruitment' else 'campus'
        match = re.search(r'/job/([^/?#]+)', p.fragment)
        if not match:
            match = re.search(r'[?&](?:id|jobId)=([^&]+)', p.query)
        row['moka_job_id'] = match[1] if match else None
        row['moka_site'] = (org_id, site_id, mode)
        row['moka_direct_detail'] = parts[0] == 'recommendation-apply'
        grouped.setdefault((org_id, site_id, mode), []).append(row)

    def load_group(key):
        org, site, mode = key
        all_jobs, total, offset, error = [], None, 0, None
        try:
            while total is None or offset < total:
                api = 'https://api.mokahr.com/api-platform/v1/jobs/' + org + '?' + urlencode({'mode': mode, 'siteId': site, 'limit': 100, 'offset': offset})
                payload = json.loads(fetch(api))
                if payload.get('code') != 0 or not isinstance(payload.get('jobs'), list):
                    raise RuntimeError(f"Moka API returned code={payload.get('code')}")
                all_jobs.extend(payload['jobs'])
                total = int(payload.get('total') or len(all_jobs))
                if not payload['jobs']:
                    break
                offset += len(payload['jobs'])
        except Exception as exc:
            error = str(exc)[:220]
        return key, all_jobs, total, error

    reports = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for key, jobs, total, error in pool.map(load_group, grouped):
            reports[key] = (jobs, total, error)

    result_rows = []
    audit_by_id = {r['id']: r for r in rows}
    for row in candidates:
        key = row.get('moka_site')
        job_id = row.get('moka_job_id')
        jobs, total, error = reports.get(key, ([], 0, 'unparseable Moka URL'))
        direct_api = None
        if row.get('moka_direct_detail') and key and job_id:
            direct_api = f'https://api.mokahr.com/api-platform/v1/jobs/{key[0]}/{job_id}?mode={key[2]}'
            try:
                detail = json.loads(fetch(direct_api))
                jobs, total = ([detail], 1) if detail.get('success') is True else ([], 0)
                error = None if detail.get('success') is True else 'Moka detail API did not return success'
            except Exception as exc:
                error = str(exc)[:220]
        found = next((job for job in jobs if str(job.get('id')) == str(job_id)), None) if job_id else None
        status = (found or {}).get('status')
        if error:
            verification = 'unknown'
        elif found and status == 'open':
            verification = 'active'
        elif found and status in ('closed', 'pause'):
            verification = 'closed'
        elif not found and total is not None and len(jobs) >= total:
            verification = 'closed'
            status = 'not_listed'
        else:
            verification = 'unknown'
        api = direct_api or (f'https://api.mokahr.com/api-platform/v1/jobs/{key[0]}?mode={key[2]}&siteId={key[1]}' if key else None)
        row.update(moka_verification=verification, moka_status=status, moka_api=api, moka_total=total, moka_listed_count=len(jobs), moka_error=error, moka_official_job=found)
        base = audit_by_id[row['id']]
        base['verification'] = verification
        base['official_status'] = status
        base['evidence_url'] = api
        base['reason'] = f'Moka官网职位列表 status={status}' if found else ('Moka官网校招列表未收录该jobId' if verification == 'closed' else 'Moka官网接口状态未能确认')
        if found:
            base['official_title'] = found.get('title')
        result_rows.append({k: row.get(k) for k in ['id', 'company', 'title', 'source_url', 'moka_verification', 'moka_status', 'moka_api', 'moka_total', 'moka_listed_count', 'moka_error']})

    dump('moka-audit.json', result_rows)
    dump('audit.json', rows)
    print('Moka sites', len(grouped), 'records', len(candidates), 'verification', dict(Counter(r['moka_verification'] for r in result_rows)))
    for row in result_rows:
        if row['moka_verification'] == 'closed':
            print('MOKA_CLOSED', json.dumps({k: row.get(k) for k in ['id', 'company', 'title', 'moka_status', 'moka_api']}, ensure_ascii=False))

def verify_hotjob():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    candidates = [r for r in rows if r['verification'] == 'unknown' and 'hotjob.cn' in (r.get('source_url') or '')]
    result_rows = []
    for row in candidates:
        p = urlsplit(row.get('source_url') or '')
        parts = [part for part in p.path.split('/') if part]
        site = next((part for part in parts if part.startswith('SU')), None)
        post_id = parse_qs(p.query).get('postId', [''])[0]
        if not post_id and p.fragment:
            post_id = parse_qs(p.fragment).get('postId', [''])[0]
        api = f'https://{p.netloc}/wecruit/positionInfo/listPositionDetail/{site}?isAjax=isAjax&request_locale=zh_CN&t=1' if site and post_id else None
        try:
            if not api:
                raise RuntimeError('无法从官方岗位链接提取站点或postId')
            payload = json.loads(fetch_form(api, {'postId': post_id}))
            detail = payload.get('data') or {}
            state = payload.get('state')
            end = str(detail.get('endDate') or '')[:10]
            if state == '200' and detail.get('postId') == post_id:
                verification = 'closed' if (detail.get('canDelivery') is False or (end and end < '2026-11-25')) else ('active' if detail.get('canDelivery') is True else 'unknown')
                status = 'canDelivery=' + str(detail.get('canDelivery'))
            else:
                verification, status, detail = 'unknown', state or payload.get('type'), None
        except Exception as exc:
            verification, status, detail = 'unknown', None, None
            error = str(exc)[:220]
        else:
            error = None
        row.update(hotjob_verification=verification, hotjob_status=status, hotjob_api=api, hotjob_error=error, hotjob_detail=detail)
        row['verification'] = verification
        row['official_status'] = status
        row['evidence_url'] = api or row.get('source_url')
        row['reason'] = f'Hotjob官方岗位接口 {status}' if verification != 'unknown' else 'Hotjob官方岗位接口状态未能确认'
        result_rows.append({k: row.get(k) for k in ['id', 'company', 'title', 'source_url', 'hotjob_verification', 'hotjob_status', 'hotjob_api', 'hotjob_error']})
    dump('hotjob-audit.json', result_rows)
    dump('audit.json', rows)
    print('Hotjob records', len(candidates), 'verification', dict(Counter(r['hotjob_verification'] for r in result_rows)))
    for row in result_rows:
        if row['hotjob_verification'] == 'closed':
            print('HOTJOB_CLOSED', json.dumps({k: row.get(k) for k in ['id', 'company', 'title', 'hotjob_status', 'hotjob_api']}, ensure_ascii=False))

def verify_xhs():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    candidates = [r for r in rows if r['verification'] == 'unknown' and 'job.xiaohongshu.com' in (r.get('source_url') or '')]
    result_rows = []
    for row in candidates:
        match = re.search(r'/position/(\d+)', row.get('source_url') or '')
        position_id = match[1] if match else None
        api = f'https://job.xiaohongshu.com/websiterecruit/position/queryPositionDetail?positionId={position_id}' if position_id else None
        try:
            if not api:
                raise RuntimeError('missing position ID')
            payload = json.loads(fetch(api))
            detail = payload.get('data') or {}
            status = detail.get('recruitStatus')
            if payload.get('success') is True and int(detail.get('positionId') or 0) == int(position_id) and status:
                verification = 'active' if status == 'in_recruitment' else 'closed'
            else:
                verification = 'unknown'
        except Exception as exc:
            status, detail, verification, error = None, None, 'unknown', str(exc)[:220]
        else:
            error = None
        row.update(xhs_verification=verification, xhs_status=status, xhs_api=api, xhs_error=error, xhs_detail=detail)
        row['verification'] = verification
        row['official_status'] = status
        row['evidence_url'] = api or row.get('source_url')
        row['reason'] = f'小红书官网职位接口 recruitStatus={status}' if verification != 'unknown' else '小红书官网职位状态未能确认'
        result_rows.append({k: row.get(k) for k in ['id', 'company', 'title', 'source_url', 'xhs_verification', 'xhs_status', 'xhs_api', 'xhs_error']})
    dump('xiaohongshu-audit.json', result_rows)
    dump('audit.json', rows)
    print('XHS records', len(candidates), 'verification', dict(Counter(r['xhs_verification'] for r in result_rows)))
    for row in result_rows:
        if row['xhs_verification'] == 'closed':
            print('XHS_CLOSED', json.dumps({k: row.get(k) for k in ['id', 'company', 'title', 'xhs_status', 'xhs_api']}, ensure_ascii=False))

def classify_feishu_offline():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    changed = []
    for row in rows:
        detail = row.get('detail') or {}
        if row.get('official_status') == 0 and detail.get('channel_online_status') == 0:
            row['verification'] = 'closed'
            row['reason'] = '官网岗位状态 channel_online_status=0；官方前端以 isOnline:0!==e.channel_online_status 判定已下线'
            row['status_semantics_evidence_url'] = 'https://lf-package-cn.feishucdn.com/obj/atsx-throne/hire-fe-prod/portal/saas-career/static/js/9084.a2d61706.js'
            changed.append({k: row.get(k) for k in ['id', 'company', 'title', 'source_url', 'evidence_url', 'reason']})
    dump('feishu-offline.json', changed)
    dump('audit.json', rows)
    print('Feishu offline records', len(changed))
    for row in changed:
        print('FEISHU_CLOSED', json.dumps(row, ensure_ascii=False))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['audit', 'summary', 'apply', 'verify', 'migrate', 'inventory', 'focus', 'moka', 'hotjob', 'inspect-unknown', 'feishu-offline', 'host', 'xhs-discover', 'xhs'])
    parser.add_argument('host_filter', nargs='?', default='')
    args = parser.parse_args()
    {'audit': audit, 'summary': summary, 'apply': apply, 'verify': verify, 'migrate': migrate, 'inventory': inventory, 'focus': focus, 'moka': verify_moka, 'hotjob': verify_hotjob, 'inspect-unknown': inspect_unknown_statuses, 'feishu-offline': classify_feishu_offline, 'host': list_host, 'xhs-discover': discover_xhs_api, 'xhs': verify_xhs}[args.action]()
