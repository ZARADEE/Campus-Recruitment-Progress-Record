"""Deduplicate the six recruiting research CSV lists and import into Career Desk.

Run without --apply first. Excluded rows stay in the report unless
--include-excluded is passed. Only Python's standard library is required.
"""
import argparse
import collections
from contextlib import closing
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import parse_qs, urlsplit
import uuid

PREFIXES = ('01_', '02_', '03_', '04_', '05_', '07_')
NAMESPACE = uuid.UUID('a9eedcca-bc1d-416c-8803-18b17ba48074')


def normalized(value):
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


def key_for(row):
    return tuple(normalized(row[field]) for field in ('公司', '岗位', '工作地点'))


def official_identity(url, is_detail=False):
    """Keep IDs inside hash routes; never use a shared portal as a job ID."""
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    for query in (parts.query, parts.fragment.partition('?')[2]):
        values = {k.lower(): v for k, v in parse_qs(query).items()}
        for name in ('postid', 'positionid', 'jobadid', 'jobid', 'requisitionid'):
            if values.get(name):
                return (host, name, values[name][0])
        if values.get('id') and ('detail' in parts.path.lower() or 'detail' in parts.fragment.lower()):
            return (host, 'id', values['id'][0])
    for route in (parts.path, parts.fragment.partition('?')[0]):
        match = re.search(r'/(?:job|position|zpdetail)/([\w-]+)(?:/|$)', route)
        if match and match[1] not in ('detail', 'list'):
            return (host, 'route', match[1])
    return (host, 'url', url.rstrip('/')) if is_detail else None


def identity_for(row):
    return official_identity(row['投递链接'], '详情' in row.get('链接类型', ''))


def load_sources(directory):
    records = {}
    files = []
    for prefix in PREFIXES:
        matches = sorted(directory.glob(prefix + '*.csv'))
        if len(matches) != 1:
            raise ValueError(f'{prefix}: expected one CSV, found {len(matches)}')
        path = matches[0]
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        files.append({'file': path.name, 'rows': len(rows), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        for line, row in enumerate(rows, 2):
            if not all(row.get(field, '').strip() for field in ('编号', '公司', '岗位')):
                raise ValueError(f'{path.name}:{line}: missing ID/company/title')
            if row.get('投递进度', '') not in ('', '未投递') or row.get('投递日期'):
                raise ValueError(f'{path.name}:{line}: application history needs separate mapping')
            source_id = row['编号']
            if source_id in records and records[source_id]['row'] != row:
                raise ValueError(f'{source_id}: conflicting rows require review')
            item = records.setdefault(source_id, {'row': row, 'sources': []})
            item['sources'].append({'file': path.name, 'line': line})
    return list(records.values()), files


def is_excluded(item):
    return any(source['file'].startswith('05_') for source in item['sources'])


def rank(item):
    row = item['row']
    return (row.get('分组', '').startswith('已核验在招'),
            '详情' in row.get('链接类型', ''), bool(row.get('官方发布日期')),
            sum(bool(value) for value in row.values()))


def group_records(records):
    buckets = collections.defaultdict(list)
    for item in records:
        buckets[key_for(item['row'])].append(item)
    groups = []
    for items in buckets.values():
        identities = {identity_for(item['row']) for item in items} - {None}
        if len(identities) <= 1:
            groups.append(items)
        else:
            # Distinct official IDs remain separate, even when names match.
            by_identity = collections.defaultdict(list)
            for item in items:
                identity = identity_for(item['row']) or ('ambiguous', item['row']['编号'])
                by_identity[identity].append(item)
            groups.extend(by_identity.values())
    return [sorted(group, key=rank, reverse=True) for group in groups]


def prepare_job(group, dataset):
    row = group[0]['row']
    source_ids = sorted(item['row']['编号'] for item in group)
    sources = sorted({s['file'] for item in group for s in item['sources']})
    published = next((item['row']['官方发布日期'] for item in group if item['row'].get('官方发布日期')), None)
    if published:
        dt.date.fromisoformat(published)
    tags = [row.get('分组', ''), row.get('方向', ''), row.get('投递建议', '')]
    if any(name.startswith('07_') for name in sources):
        tags.insert(0, '优先投递')
    if any(name.startswith('04_') for name in sources):
        tags.insert(0, '近期更新/补录')
    tags = list(dict.fromkeys(tag for tag in tags if tag))
    note = ['# 岗位检索资料', '', '以下内容来自导入的检索清单。', '',
            '原始编号：' + '、'.join(source_ids), '来源文件：' + '；'.join(sources), '',
            '## 岗位与核验信息', '']
    for field in row:
        values = list(dict.fromkeys(item['row'][field] for item in group if item['row'].get(field)))
        if values:
            note.append(f'{field}：' + '\n补充记录：'.join(values))
            note.append('')
    return {
        'id': str(uuid.uuid5(NAMESPACE, dataset + ':' + source_ids[0])),
        'company': row['公司'].strip(), 'title': row['岗位'].strip(),
        'city': row['工作地点'].strip(), 'published_at': published,
        'source_url': row['投递链接'], 'tags': tags, 'jd_text': '\n'.join(note),
        'source_ids': source_ids, 'sources': sources, 'group': row['分组'],
        'members': group,
    }


def existing_match(job, existing):
    target_key = tuple(normalized(job[k]) for k in ('company', 'title', 'city'))
    target_identity = identity_for(job['members'][0]['row'])
    for saved in existing:
        if saved['id'] == job['id']:
            return saved['id']
        if tuple(normalized(saved[k]) for k in ('company', 'title', 'city')) != target_key:
            continue
        saved_identity = official_identity(saved['source_url'] or '')
        if saved_identity and target_identity and saved_identity != target_identity:
            continue
        return saved['id']
    return None


def write_csv(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def import_lists(source_dir, database, report_dir, apply=False, include_excluded=False,
                 exclude_companies=None, remove_excluded_from_db=False, backup_name=None):
    if remove_excluded_from_db and not apply:
        raise ValueError('Removing already-imported jobs requires --apply')
    exclude_companies = exclude_companies or []
    excluded_company_names = {normalized(name) for name in exclude_companies}
    records, files = load_sources(source_dir)
    excluded = [item for item in records if is_excluded(item)]
    selected = records if include_excluded else [item for item in records if not is_excluded(item)]
    excluded_companies = [item for item in selected if normalized(item['row']['公司']) in excluded_company_names]
    selected = [item for item in selected if normalized(item['row']['公司']) not in excluded_company_names]
    groups = group_records(selected)
    prepared = [prepare_job(group, source_dir.name) for group in groups]
    report_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime('%Y%m%dT%H%M%S%fZ')
    connection = sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    backup = None
    try:
        if apply:
            # This acquires the writer lock before capturing the backup and plan.
            connection.execute('BEGIN IMMEDIATE')
            label = backup_name or 'career-before-import'
            backup = report_dir / f'{label}-{stamp}.sqlite'
            with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as reader:
                with closing(sqlite3.connect(backup)) as destination:
                    reader.backup(destination)
        existing = [dict(row) for row in connection.execute(
            'SELECT j.id,c.name AS company,j.title,j.city,j.source_url,j.status '
            'FROM jobs j JOIN companies c ON c.id=j.company_id')]
        new_jobs, skipped = [], []
        for job in prepared:
            match = existing_match(job, existing)
            if match:
                job['existing_id'] = match
                skipped.append(job)
            else:
                new_jobs.append(job)
        removed_jobs = []
        removed_interviews = 0
        removed_events = 0
        if apply:
            timestamp = now.isoformat(timespec='seconds')
            for job in new_jobs:
                company_name = ''.join(job['company'].split()).lower()
                saved_company = connection.execute('SELECT id FROM companies WHERE normalized_name=?', (company_name,)).fetchone()
                company_id = saved_company[0] if saved_company else str(uuid.uuid4())
                if not saved_company:
                    connection.execute('INSERT INTO companies(id,name,normalized_name,created_at) VALUES(?,?,?,?)',
                                       (company_id, job['company'], company_name, timestamp))
                connection.execute(
                    "INSERT INTO jobs(id,company_id,title,status,published_at,created_at,source_url,source_kind,city,tags_json,jd_text,updated_at) "
                    "VALUES(?,?,?,'opportunity',?,?,?,'csv',?,?,?,?)",
                    (job['id'], company_id, job['title'], job['published_at'], timestamp, job['source_url'],
                     job['city'], json.dumps(job['tags'], ensure_ascii=False), job['jd_text'], timestamp))
                connection.execute(
                    'INSERT INTO job_events(id,job_id,event_type,occurred_at,note) VALUES(?,?,?,?,?)',
                    (str(uuid.uuid4()), job['id'], 'created', timestamp,
                     'CSV去重导入：' + source_dir.name + ' / ' + '、'.join(job['source_ids'])))
            if remove_excluded_from_db and excluded_company_names:
                current = connection.execute(
                    'SELECT j.id,j.source_kind,c.name AS company FROM jobs j JOIN companies c ON c.id=j.company_id'
                ).fetchall()
                removable = [row['id'] for row in current if row['source_kind'] == 'csv' and
                             normalized(row['company']) in excluded_company_names]
                for job_id in removable:
                    removed_interviews += connection.execute('SELECT COUNT(*) FROM interview_sessions WHERE job_id=?', (job_id,)).fetchone()[0]
                    removed_events += connection.execute('SELECT COUNT(*) FROM job_events WHERE job_id=?', (job_id,)).fetchone()[0]
                    removed_jobs.append(job_id)
                    connection.execute('DELETE FROM jobs WHERE id=?', (job_id,))
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Database foreign key check failed')
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Database integrity check failed')
            connection.commit()
        counts = {
            'input_rows': sum(file['rows'] for file in files), 'unique_source_ids': len(records),
            'repeated_source_rows': sum(file['rows'] for file in files) - len(records),
            'excluded_not_imported': 0 if include_excluded else len(excluded),
            'excluded_company_source_records': len(excluded_companies),
            'cross_id_duplicates': len(selected) - len(prepared), 'deduplicated_jobs': len(prepared),
            'new_jobs': len(new_jobs), 'existing_jobs_skipped': len(skipped),
            'removed_existing_jobs': len(removed_jobs), 'removed_interview_sessions': removed_interviews,
            'removed_job_events': removed_events,
            'companies_in_batch': len({normalized(job['company']) for job in prepared}),
            'priority_jobs': sum('优先投递' in job['tags'] for job in prepared),
            'without_official_publish_date': sum(job['published_at'] is None for job in prepared),
        }
        summary = {'applied': apply, 'created_at': now.isoformat(), 'source_directory': str(source_dir),
                   'database': str(database.resolve()), 'backup': str(backup.resolve()) if backup else None,
                   'excluded_companies': exclude_companies,
                   'counts': counts, 'source_files': files,
                   'verification_groups': dict(collections.Counter(job['group'] for job in prepared))}
        (report_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        (report_dir / 'provenance.json').write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding='utf-8')
        write_csv(report_dir / '去重后岗位.csv', [
            {'公司': j['company'], '岗位': j['title'], '城市': j['city'], '发布时间': j['published_at'] or '',
             '网址': j['source_url'], '标签': '；'.join(j['tags']), '原始编号': '、'.join(j['source_ids']),
             'JD': j['jd_text']} for j in prepared
        ], ['公司', '岗位', '城市', '发布时间', '网址', '标签', '原始编号', 'JD'])
        write_csv(report_dir / '跨编号合并记录.csv', [
            {'原始编号': '、'.join(j['source_ids']), '公司': j['company'], '岗位': j['title'], '城市': j['city'],
             '保留链接': j['source_url'], '保留分组': j['group']} for j in prepared if len(j['source_ids']) > 1
        ], ['原始编号', '公司', '岗位', '城市', '保留链接', '保留分组'])
        if excluded:
            write_csv(report_dir / '排除记录留档.csv', [item['row'] for item in excluded], list(excluded[0]['row']))
        report = [
            '# CSV 去重导入报告', '', f'执行时间：{now.isoformat()}',
            f'执行结果：{"已写入数据库" if apply else "预览，未写入数据库"}', '',
            f'- 原始 {counts["input_rows"]} 行；同编号重复 {counts["repeated_source_rows"]} 行。',
            f'- 排除清单暂不导入 {counts["excluded_not_imported"]} 条；跨编号重复合并 {counts["cross_id_duplicates"]} 条。',
            f'- 从本次导入结果排除公司岗位 {counts["excluded_company_source_records"]} 条；源 CSV 文件未修改。',
            f'- 去重后 {len(prepared)} 个岗位，涉及 {counts["companies_in_batch"]} 家公司。',
            f'- {"新增" if apply else "待新增"} {len(new_jobs)} 个岗位；已有 {len(skipped)} 个岗位跳过。',
            f'- 从当前数据库删除 CSV 导入岗位 {counts["removed_existing_jobs"]} 条，关联面试轮次 {removed_interviews} 轮。',
            f'- 优先投递 {counts["priority_jobs"]} 个；无官方发布日期 {counts["without_official_publish_date"]} 个。', '',
            '岗位均按源文件的“未投递”状态进入“看看机会”。录入时间使用实际导入时间。',
            '发布时间仅取官方发布日期；官方更新、补录及线索日期保存在岗位详情中。',
            '以公司、岗位名、地点及官方岗位 ID 判断重复；保留不同城市或不同官方岗位 ID 的岗位。',
            '详情链接优先于列表入口，核验结果优先于待核验线索；完整原始记录保存在 provenance.json。',
            '再次执行时跳过已有岗位，保留现有投递进度及面试记录。', '', '## 原始核验分组', '',
        ]
        report.extend(f'- {name}：{count}' for name, count in summary['verification_groups'].items())
        if backup:
            report.extend(['', f'导入前备份：{backup.name}'])
        (report_dir / '导入报告.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
        return summary
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--database', type=Path, default=Path('data/career.sqlite'))
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--include-excluded', action='store_true')
    parser.add_argument('--exclude-company', action='append', default=[])
    parser.add_argument('--remove-excluded-from-db', action='store_true')
    parser.add_argument('--backup-name')
    args = parser.parse_args()
    print(json.dumps(import_lists(args.source_dir, args.database, args.report_dir, args.apply, args.include_excluded,
                                 args.exclude_company, args.remove_excluded_from_db, args.backup_name), ensure_ascii=False, indent=2))
