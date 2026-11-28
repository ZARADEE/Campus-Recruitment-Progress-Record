"""Finish the 2026-11-25 official-source audit without changing application history."""
import json
import re
import sqlite3
import sys
import uuid
from html import unescape
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlsplit
from refresh_opportunities import OUT, ROOT, fetch, dump, check, classify_feishu_offline

def plain(value):
    return unescape(re.sub(r'<[^>]+>', '\n', str(value or ''))).strip()

def official_text(row):
    texts = []
    for key in ['detail', 'moka_official_job', 'moka_direct_detail', 'hotjob_detail', 'xhs_detail']:
        value = row.get(key)
        values = value if isinstance(value, list) else [value]
        for value in values:
            if isinstance(value, dict):
                texts.extend(plain(value.get(k)) for k in ['description', 'requirement', 'Duty', 'Require', 'workContent', 'serviceCondition', 'duty', 'qualification', 'jobDesc'])
    return '\n'.join(filter(None, texts))

def scope(row):
    title = row['title']
    direct = r'前端|客户端|移动端|移动开发|移动应用|Android|安卓|iOS|鸿蒙|HarmonyOS|Flutter|React Native|RN 开发|App(?:开发|助理|）)|Agent|智能体|Unity|U3D|Cocos|Client Software|Mobile Engineer'
    if re.search(direct, title, re.I):
        return 'target'
    if row['verification']=='unknown' and re.search(r'AI应用|AI全栈|AI与全栈', title, re.I):
        # Related leads still need both scope and recruitment verification.
        return 'target'
    # Use the actual employer's JD, never imported resume-matching commentary.
    body = official_text(row)
    substantive = r'前端|客户端|移动端|Android|安卓|iOS|鸿蒙|Agent|智能体'
    if re.search(substantive, body, re.I):
        return 'target'
    return 'other'

def prepare():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    candidates = json.loads((OUT / 'recent-official-candidates.json').read_text(encoding='utf-8'))
    # Confirm each new job appears in the company's existing official campus portal.
    for candidate in candidates:
        org = candidate['orgId']
        prior = next(r for r in rows if f'/campus-recruitment/{org}/' in r.get('source_url',''))
        base = prior['source_url'].split('#')[0]
        site = urlsplit(base).path.strip('/').split('/')[2]
        api = f'https://api.mokahr.com/api-platform/v1/jobs/{org}?mode=campus&siteId={site}&limit=100&offset='
        listed, offset = [], 0
        while True:
            data = json.loads(fetch(api+str(offset)))
            assert data.get('code') == 0 and isinstance(data.get('jobs'), list)
            listed.extend(data['jobs'])
            offset += len(data['jobs'])
            if offset >= int(data['total']): break
            assert data['jobs'], 'Incomplete official listing'
        match = next(j for j in listed if j['id'] == candidate['id'])
        assert match['status'] == 'open' and match['hireMode'] == 2
        source = base+'#/job/'+match['id']
        if any(r.get('source_url') == source for r in rows): continue
        stamp = datetime.now().astimezone().isoformat(timespec='seconds')
        row = dict(id=str(uuid.uuid5(uuid.NAMESPACE_URL, source)), company=prior['company'], company_id=prior['company_id'], title=match['title'],
                   source_url=source, published_at=match['publishedAt'][:10], city='、'.join(dict.fromkeys(x.get('city') or x.get('province') or '' for x in match.get('locations',[]))),
                   verification='active', official_status='open', checked_at=stamp, evidence_url=api+'0', reason='官方校招列表当前开放，近期新发布', moka_official_job=match,
                   source_kind='2027届校招 · 近期新增' + (' · 实习转正' if org=='wedobest' else ''), new_candidate=True)
        rows.append(row)
    original_hq = next(r for r in rows if r['company']=='华勤技术' and '/job/' in r['source_url'])
    duplicate_hq = next((r for r in rows if r['company']=='华勤技术' and r['source_url']=='https://jobs.huaqin.com/'), None)
    if duplicate_hq:
        rows.remove(duplicate_hq)
        dump('merged-duplicates.json', [{'removed_id':duplicate_hq['id'], 'retained_id':original_hq['id'], 'company':original_hq['company'], 'title':original_hq['title']}])
    for row in rows:
        row['scope'] = scope(row)
    dump('audit.json', rows)
    print('PREPARED', dict(Counter((r['verification'],r['scope']) for r in rows)))
    for row in rows:
        if row['verification']=='active' and row['scope']=='other':
            print('OTHER', row['company'], row['title'])

def finalize():
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    assert all('scope' in r for r in rows), 'Run prepare first'
    report = json.loads((OUT / 'refresh-report.json').read_text(encoding='utf-8'))
    duplicates = json.loads((OUT / 'merged-duplicates.json').read_text(encoding='utf-8'))
    duplicate_ids = {r['removed_id'] for r in duplicates}
    report['added'] = [r for r in report['added'] if r['id'] not in duplicate_ids]
    stamp = datetime.now().astimezone().isoformat(timespec='seconds')
    with sqlite3.connect(ROOT / 'data/career.sqlite', timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        with sqlite3.connect(OUT / 'career-before-final-refresh.sqlite') as backup:
            conn.backup(backup)
        conn.execute('BEGIN IMMEDIATE')
        untouched = [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE status!='opportunity' ORDER BY id")]
        sessions = [dict(r) for r in conn.execute('SELECT * FROM interview_sessions ORDER BY id')]
        for duplicate in duplicates:
            conn.execute("DELETE FROM jobs WHERE id=? AND status='opportunity'", (duplicate['removed_id'],))
        for row in rows:
            if row['verification']=='closed':
                assert row.get('reason') and row.get('evidence_url')
                conn.execute("DELETE FROM jobs WHERE id=? AND status='opportunity'", (row['id'],))
                if not any(r['id']==row['id'] for r in report['removed']):
                    report['removed'].append({k:row.get(k) for k in ['id','company','title','reason','evidence_url']})
                continue
            if row.get('new_candidate'):
                conn.execute('''INSERT OR IGNORE INTO jobs(id,company_id,title,status,published_at,created_at,current_stage,active_round,source_url,source_kind,city,team,salary,tags_json,jd_text,updated_at)
                    VALUES(?,?,?,'opportunity',?,?,0,'first',?,?,?,?,?,?,?,?)''',
                    (row['id'],row['company_id'],row['title'],row['published_at'],stamp,row['source_url'],row['source_kind'],row['city'],'','', '[]',official_text(row),stamp))
                if not any(r['id']==row['id'] for r in report['added']):
                    report['added'].append({k:row.get(k) for k in ['id','company','title','source_url','published_at']})
            current = conn.execute("SELECT * FROM jobs WHERE id=? AND status='opportunity'", (row['id'],)).fetchone()
            if current is None: continue
            tags = [t for t in json.loads(current['tags_json']) if not t.startswith(('__vacancy:','__verified:','__scope:'))]
            tags.extend(['__vacancy:'+row['verification'], '__scope:'+row['scope'], '__verified:2026-11-25'])
            body = official_text(row)
            # Replace the newly imported summaries with the verbatim public employer JD.
            jd = body if row.get('new_candidate') or row['company']=='药明康德' else current['jd_text']
            published = (row.get('detail') or {}).get('PostDate') if row['company']=='药明康德' else None
            published = str(published)[:10] if published else current['published_at']
            conn.execute("UPDATE jobs SET tags_json=?,jd_text=?,published_at=?,updated_at=? WHERE id=? AND status='opportunity'", (json.dumps(tags,ensure_ascii=False),jd,published,stamp,row['id']))
        assert untouched == [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE status!='opportunity' ORDER BY id")]
        assert sessions == [dict(r) for r in conn.execute('SELECT * FROM interview_sessions ORDER BY id')]
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        actual = [dict(r) for r in conn.execute("SELECT j.*,c.name AS company FROM jobs j JOIN companies c ON c.id=j.company_id WHERE status='opportunity'")]
        by_id = {r['id']:r for r in rows}
        counts = Counter('other' if by_id[r['id']]['scope']=='other' else 'active' if by_id[r['id']]['verification']=='active' else 'pending' for r in actual)
        report.update(updated_at=stamp, merged_duplicates=duplicates, counts=dict(counts), total_opportunities=len(actual), audit_status=dict(Counter(r['verification'] for r in rows)), application_history_unchanged=True, interview_history_unchanged=True)
    dump('refresh-report.json', report)
    lines = ['# 2026-11-25 岗位刷新记录', '', f"官网确认下架、暂停或关闭：{len(report['removed'])} 个；新增记录：{len(report['added'])} 个；合并本次重复录入：{len(duplicates)} 个。", '', f"默认列表：{counts['active']} 个已核验在招且方向匹配的岗位；待核验：{counts['pending']} 个；其他方向：{counts['other']} 个。", '', '投递进度和面试历史保持不变。核验状态是本次查询时的快照，日期展示在界面。近期新增不等同于官方补录。', '', '## 新增', '']
    for row in report['added']:
        audited = by_id[row['id']]
        lines.append(f"- [{row['company']} · {row['title']}]({row['source_url']}) — {'官网在招' if audited['verification']=='active' else '待核验'}")
    lines.extend(['', '## 已移除', ''])
    for row in report['removed']:
        lines.append(f"- {row['company']} · {row['title']} — {row['reason']}；[官方核验依据]({row['evidence_url']})")
    lines.extend(['', '## 备份和证据', '', '- career-before-refresh.sqlite：刷新前备份', '- audit.json：各岗位状态、范围、理由及官方接口证据', '- sources/：本次公开招聘页面与接口响应缓存'])
    (OUT / 'refresh-report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['counts','total_opportunities','audit_status','application_history_unchanged','interview_history_unchanged']},ensure_ascii=False))
    print('REMOVED',len(report['removed']),'ADDED',len(report['added']))

def validate():
    before = json.loads((OUT / 'before-jobs.json').read_text(encoding='utf-8'))
    interviews = json.loads((OUT / 'before-interviews.json').read_text(encoding='utf-8'))
    report = json.loads((OUT / 'refresh-report.json').read_text(encoding='utf-8'))
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    audit = {r['id']:r for r in rows}
    with sqlite3.connect(ROOT / 'data/career.sqlite') as conn:
        conn.row_factory=sqlite3.Row
        current = {r['id']:dict(r) for r in conn.execute('SELECT * FROM jobs')}
        for row in before:
            if row['status']!='opportunity':
                assert all(current[row['id']][k]==v for k,v in row.items() if k!='company'), row['id']
        sessions = {r['id']:dict(r) for r in conn.execute('SELECT * FROM interview_sessions')}
        assert set(sessions)=={r['id'] for r in interviews}
        for row in interviews:
            assert all(sessions[row['id']][k]==v for k,v in row.items()), row['id']
        assert not {r['id'] for r in report['removed']} & current.keys()
        assert all(r['id'] in current for r in report['added'])
        for row in current.values():
            if row['status']!='opportunity': continue
            tags=json.loads(row['tags_json'])
            assert '__vacancy:'+audit[row['id']]['verification'] in tags
            assert '__scope:'+audit[row['id']]['scope'] in tags
            assert audit[row['id']]['verification']!='closed'
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    print('PASS: original application records and interview history unchanged; all removed jobs absent; new jobs present; verification tags consistent; SQLite integrity valid.')

def targeted():
    classify_feishu_offline()
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    # Generic Moka /apply portals can contain social and campus jobs. Confirm
    # absence against both modes before treating an old job link as withdrawn.
    for row in rows:
        if row.get('moka_status') != 'not_listed':
            continue
        p = urlsplit(row['source_url'])
        parts = p.path.strip('/').split('/')
        org, site = parts[1:3]
        job_id = re.search(r'/job/([^/?#]+)', p.fragment)[1]
        api = f'https://api.mokahr.com/api-platform/v1/jobs/{org}/{job_id}'
        try:
            detail = json.loads(fetch(api))
            row['moka_direct_detail'] = detail
            if detail.get('status') in ('closed', 'pause') and detail.get('id') == job_id:
                row.update(verification='closed', official_status=detail['status'], evidence_url=api, reason='官方岗位详情明确标为 ' + detail['status'])
            elif detail.get('status') == 'open':
                alternate = f'https://api.mokahr.com/api-platform/v1/jobs/{org}?mode=social&siteId={site}&limit=100&offset=0'
                data = json.loads(fetch(alternate))
                # An open job may have moved to another official portal; retain
                # as unknown until exact-site listing is confirmed.
                found = next((j for j in data.get('jobs', []) if j.get('id') == job_id), None)
                row.update(verification='active' if found else 'unknown', official_status='open', evidence_url=api, reason='官方详情开放；' + ('社招入口包含该职位' if found else '原校招入口未收录，投递渠道待核验'))
        except Exception as error:
            row['direct_error'] = str(error)
            row['verification'] = 'unknown'
        print('RECHECK', row['company'], row['title'], row['verification'], row.get('official_status'))
    # The DJI branded portal uses Moka as well.
    api = 'https://api.mokahr.com/api-platform/v1/jobs/dji?mode=campus&siteId=143359&limit=100&offset='
    jobs, offset, total = [], 0, None
    try:
        while total is None or offset < total:
            data = json.loads(fetch(api + str(offset)))
            assert data.get('code') == 0 and isinstance(data.get('jobs'), list)
            jobs.extend(data['jobs'])
            total = int(data['total'])
            if not data['jobs']:
                break
            offset += len(data['jobs'])
        for row in rows:
            if 'apply.careers.dji.com' not in (row.get('source_url') or ''):
                continue
            normalize = lambda s: re.sub(r'\s+', '', s).replace('（深圳）', '')
            found = [j for j in jobs if normalize(j['title']) == normalize(row['title'])]
            if found and any(j.get('status') == 'open' for j in found):
                row.update(verification='active', official_status='open', evidence_url=api+'0', reason='大疆官方校招职位列表包含开放岗位', detail=found)
            print('DJI', row['title'], row['verification'])
    except Exception as error:
        print('DJI_ERROR', str(error))
    # Include the newly inserted jobs in this audit too.
    with sqlite3.connect(ROOT / 'data/career.sqlite') as connection:
        connection.row_factory = sqlite3.Row
        all_jobs = [dict(r) for r in connection.execute('SELECT j.*, c.name AS company FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.status=?', ('opportunity',))]
    ids = {r['id'] for r in rows}
    for job in all_jobs:
        if job['id'] not in ids:
            row = check(job)
            rows.append(row)
            print('NEW_AUDIT', job['company'], job['title'], row['verification'])
    dump('audit.json', rows)

def catalog():
    from collections import Counter
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    print('AUDIT', dict(Counter(r['verification'] for r in rows)))
    api = 'https://api.mokahr.com/api-platform/v1/jobs/hq?mode=campus&siteId=44757&limit=100&offset=0'
    data = json.loads(fetch(api))
    print('HUAQIN', data.get('code'), data.get('total'))
    for job in data.get('jobs', []):
        if re.search(r'应用软件|前端|Agent|客户端|移动端', job.get('title', ''), re.I):
            print('MATCH', json.dumps(job, ensure_ascii=False))
    for row in rows:
        if row['company'] in ['药明康德', '广州虹科电子科技有限公司', '华勤技术']:
            print('NEW', row['company'], row['title'], row['verification'], 'detail_keys', list((row.get('detail') or {}).keys()) if isinstance(row.get('detail'), dict) else '')
    print('ACTIVE_TITLES', json.dumps([r['company']+' / '+r['title'] for r in rows if r['verification']=='active'], ensure_ascii=False))

def recent():
    from refresh_opportunities import CACHE
    seen = set()
    rows = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    known = {re.search(r'/job/([^/?#]+)', r.get('source_url') or '')[1] for r in rows if re.search(r'/job/([^/?#]+)', r.get('source_url') or '')}
    candidates = []
    for path in CACHE.glob('*.txt'):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError):
            continue
        if not isinstance(data, dict) or data.get('code') != 0 or not isinstance(data.get('jobs'), list):
            continue
        for job in data['jobs']:
            if job.get('id') in seen or job.get('id') in known or job.get('status') != 'open' or job.get('hireMode') != 2:
                continue
            seen.add(job['id'])
            title = job.get('title', '')
            if re.search(r'前端|客户端|移动端|Android|iOS|Agent|智能体', title, re.I) and (str(job.get('publishedAt') or '') >= '2026-11-01' or '补录' in title):
                candidates.append(job)
    candidates.sort(key=lambda j: j.get('publishedAt') or '', reverse=True)
    dump('recent-official-candidates.json', candidates)
    for j in candidates[:35]:
        print(json.dumps({k:j.get(k) for k in ['id','orgId','title','publishedAt','updatedAt','mjCode']}, ensure_ascii=False))
    for r in rows:
        if r['company']=='华勤技术' or (r['verification']=='active' and r.get('detail') and isinstance(r['detail'], dict) and 'hotjob.cn' in r.get('source_url','')):
            print('DETAIL', r['id'], r['company'], r['source_url'], list((r.get('detail') or {}).keys()) if isinstance(r.get('detail'),dict) else '')
    for host in ['hotjob.cn', 'xiaohongshu.com', 'zhiye.com', 'feishu.cn', 'mokahr.com']:
        r = next((r for r in rows if host in r.get('source_url','') and r['verification']=='active'), None)
        if r:
            print('KEYS', host, list(r.keys()))
            print('EVIDENCE_KEYS', {k:list(v.keys()) for k,v in r.items() if isinstance(v,dict)})
    for j in candidates:
        print('CANDIDATE', j['orgId'], j['title'], j.get('description'), j.get('locations'))


if __name__ == '__main__':
    if 'catalog' in sys.argv: catalog()
    elif 'recent' in sys.argv: recent()
    elif 'prepare' in sys.argv: prepare()
    elif 'finalize' in sys.argv: finalize()
    elif 'validate' in sys.argv: validate()
    else: targeted()
