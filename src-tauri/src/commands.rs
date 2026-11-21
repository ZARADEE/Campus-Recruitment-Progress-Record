use crate::database::AppState;
use crate::importer;
use crate::models::{InterviewQuestion, InterviewSession, JobDraft, JobRecord};
use crate::scraper_service;
use chrono::{DateTime, Local, Utc};
use rusqlite::{params, OptionalExtension, Transaction};
use serde::Deserialize;
use std::collections::HashMap;
use std::io::Write;
use std::path::Path;
use tauri::{AppHandle, State};
use tauri_plugin_dialog::DialogExt;
use uuid::Uuid;
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipWriter};

const STAGE_KEYS: [&str; 8] = [
    "applied",
    "assessment",
    "written_test",
    "ai_interview",
    "resume_screening",
    "interview",
    "offer",
    "onboarding",
];
const ROUND_KEYS: [&str; 4] = ["first", "second", "third", "hr"];

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InterviewDraft {
    pub job_id: String,
    pub round_type: String,
    #[serde(default)]
    pub date: String,
    #[serde(default)]
    pub result: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub questions: Vec<InterviewQuestion>,
}

#[derive(Debug, Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ResumeRecord {
    id: String,
    name: String,
    target_role: String,
    original_file: String,
    content: String,
    updated_at: String,
}

fn now() -> String {
    Utc::now().to_rfc3339()
}

fn short_date(value: Option<String>) -> Option<String> {
    value.map(|date| date.chars().take(10).collect())
}

fn local_datetime(value: Option<String>) -> String {
    value
        .and_then(|raw| DateTime::parse_from_rfc3339(&raw).ok())
        .map(|date| {
            date.with_timezone(&Local)
                .format("%Y-%m-%d %H:%M")
                .to_string()
        })
        .unwrap_or_default()
}

fn source_label(source: &str) -> String {
    match source {
        "markdown" | "Markdown" => "Markdown".into(),
        "docx" | "Word 文档" => "Word 文档".into(),
        "csv" | "CSV" => "CSV".into(),
        "url" | "招聘网址" => "招聘网址".into(),
        _ => "手动录入".into(),
    }
}

fn source_kind(source: &str) -> &'static str {
    match source {
        "markdown" | "Markdown" => "markdown",
        "docx" | "Word 文档" => "docx",
        "csv" | "CSV" => "csv",
        "url" | "招聘网址" => "url",
        _ => "manual",
    }
}

fn normalize_company(name: &str) -> String {
    name.split_whitespace().collect::<String>().to_lowercase()
}

fn mark_tone(name: &str) -> &'static str {
    let value = name.chars().map(|ch| ch as usize).sum::<usize>() % 3;
    ["coral", "blue", "amber"][value]
}

fn validate_draft(draft: &JobDraft) -> Result<(), String> {
    if draft.company.trim().is_empty() || draft.title.trim().is_empty() {
        return Err("公司和岗位名称不能为空".into());
    }
    if draft.company.chars().count() > 120 || draft.title.chars().count() > 240 {
        return Err("公司或岗位名称超过长度限制".into());
    }
    if draft.jd.len() > 1_000_000 {
        return Err("岗位描述超过 1 MB 限制".into());
    }
    if let Some(date) = draft.published_at.as_deref() {
        if !date.is_empty() && chrono::NaiveDate::parse_from_str(date, "%Y-%m-%d").is_err() {
            return Err("发布时间格式无效".into());
        }
    }
    Ok(())
}

fn ensure_company(tx: &Transaction<'_>, name: &str, created_at: &str) -> Result<String, String> {
    let normalized = normalize_company(name);
    let company_id: Option<String> = tx
        .query_row(
            "SELECT id FROM companies WHERE normalized_name = ?1",
            [&normalized],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    if let Some(id) = company_id {
        return Ok(id);
    }
    let id = Uuid::new_v4().to_string();
    tx.execute(
        "INSERT INTO companies(id, name, normalized_name, created_at) VALUES (?1, ?2, ?3, ?4)",
        params![id, name.trim(), normalized, created_at],
    )
    .map_err(|error| error.to_string())?;
    Ok(id)
}

fn insert_event(
    tx: &Transaction<'_>,
    job_id: &str,
    event_type: &str,
    from_stage: Option<usize>,
    to_stage: Option<usize>,
    occurred_at: &str,
    note: &str,
) -> Result<(), String> {
    tx.execute(
        "INSERT INTO job_events(id, job_id, event_type, from_stage, to_stage, occurred_at, note) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![
            Uuid::new_v4().to_string(),
            job_id,
            event_type,
            from_stage.map(|stage| stage as i64),
            to_stage.map(|stage| stage as i64),
            occurred_at,
            note
        ],
    )
    .map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn list_jobs(state: State<'_, AppState>) -> Result<Vec<JobRecord>, String> {
    let connection = state.db.lock().map_err(|_| "数据库锁定失败")?;
    let mut statement = connection
        .prepare(
            "SELECT j.id, c.name, j.title, j.status, j.published_at, j.created_at, j.applied_at,
                    j.last_activity_at, j.current_stage, j.active_round, j.source_url, j.source_kind,
                    j.city, j.team, j.salary, j.tags_json, j.jd_text
             FROM jobs j JOIN companies c ON c.id = j.company_id
             ORDER BY CASE j.status WHEN 'applied' THEN 0 WHEN 'opportunity' THEN 1 ELSE 2 END,
                      COALESCE(j.last_activity_at, j.created_at) DESC",
        )
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, Option<String>>(6)?,
                row.get::<_, Option<String>>(7)?,
                row.get::<_, i64>(8)?,
                row.get::<_, String>(9)?,
                row.get::<_, Option<String>>(10)?,
                row.get::<_, String>(11)?,
                row.get::<_, String>(12)?,
                row.get::<_, String>(13)?,
                row.get::<_, String>(14)?,
                row.get::<_, String>(15)?,
                row.get::<_, String>(16)?,
            ))
        })
        .map_err(|error| error.to_string())?;
    let raw_jobs = rows
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    drop(statement);

    raw_jobs
        .into_iter()
        .map(|raw| {
            let (
                id,
                company,
                title,
                status,
                posted,
                created_at,
                applied_at,
                last_activity_at,
                stage_index,
                active_round,
                source_url,
                source_kind_value,
                city,
                team,
                salary,
                tags_json,
                jd,
            ) = raw;
            let created = created_at.chars().take(10).collect();
            let updated = local_datetime(last_activity_at.clone());
            let tags = serde_json::from_str(&tags_json).unwrap_or_default();
            let activity = load_activity(&connection, &id)?;
            let sessions = load_sessions(&connection, &id)?;
            Ok(JobRecord {
                mark: company.chars().next().unwrap_or('岗').to_string(),
                mark_tone: mark_tone(&company).into(),
                company,
                title,
                status,
                posted: short_date(posted),
                created,
                applied_at: short_date(applied_at),
                updated,
                stage_index: stage_index.clamp(0, 7) as usize,
                active_round,
                source: source_label(&source_kind_value),
                source_url,
                city,
                team,
                salary,
                tags,
                jd,
                activity,
                sessions,
                id,
            })
        })
        .collect()
}

fn load_activity(connection: &rusqlite::Connection, job_id: &str) -> Result<Vec<String>, String> {
    let mut statement = connection
        .prepare("SELECT event_type, to_stage, note, occurred_at FROM job_events WHERE job_id = ?1 ORDER BY occurred_at DESC LIMIT 3")
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map([job_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<i64>>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .map_err(|error| error.to_string())?;
    let mut activity = Vec::new();
    for row in rows {
        let (event, stage, note, occurred_at) = row.map_err(|error| error.to_string())?;
        let label = match event.as_str() {
            "created" => "录入岗位信息".to_string(),
            "applied" => "已投递岗位".to_string(),
            "stage" => stage
                .and_then(|index| {
                    [
                        "投递",
                        "测评",
                        "笔试",
                        "AI 面试",
                        "简历筛选",
                        "面试",
                        "Offer",
                        "入职",
                    ]
                    .get(index as usize)
                })
                .map(|name| format!("进度更新至{name}"))
                .unwrap_or_else(|| "招聘进度已更新".into()),
            "interview_saved" => "面试复盘已保存".to_string(),
            _ => note,
        };
        activity.push(format!("{} · {}", label, local_datetime(Some(occurred_at))));
    }
    Ok(activity)
}

fn load_sessions(
    connection: &rusqlite::Connection,
    job_id: &str,
) -> Result<HashMap<String, InterviewSession>, String> {
    let mut statement = connection
        .prepare("SELECT id, round_type, interview_date, result, summary FROM interview_sessions WHERE job_id = ?1")
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map([job_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
            ))
        })
        .map_err(|error| error.to_string())?;
    let mut sessions = HashMap::new();
    for row in rows {
        let (session_id, round, date, result, summary) = row.map_err(|error| error.to_string())?;
        let mut question_statement = connection
            .prepare("SELECT id, question_text, answer_text FROM interview_questions WHERE session_id = ?1 ORDER BY sort_order")
            .map_err(|error| error.to_string())?;
        let question_rows = question_statement
            .query_map([&session_id], |question_row| {
                Ok(InterviewQuestion {
                    id: question_row.get(0)?,
                    question: question_row.get(1)?,
                    answer: question_row.get(2)?,
                })
            })
            .map_err(|error| error.to_string())?;
        let questions = question_rows
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| error.to_string())?;
        sessions.insert(
            round,
            InterviewSession {
                date,
                result,
                summary,
                questions,
            },
        );
    }
    Ok(sessions)
}

#[tauri::command]
pub fn create_job(state: State<'_, AppState>, draft: JobDraft) -> Result<String, String> {
    create_jobs(state, vec![draft]).map(|mut ids| ids.remove(0))
}

#[tauri::command]
pub fn create_jobs(
    state: State<'_, AppState>,
    drafts: Vec<JobDraft>,
) -> Result<Vec<String>, String> {
    if drafts.is_empty() || drafts.len() > 1000 {
        return Err("单次导入需要 1 至 1000 个岗位".into());
    }
    for (index, draft) in drafts.iter().enumerate() {
        validate_draft(draft).map_err(|error| format!("第 {} 个岗位：{error}", index + 1))?;
    }
    let connection = state.db.lock().map_err(|_| "数据库锁定失败")?;
    let tx = connection
        .unchecked_transaction()
        .map_err(|error| error.to_string())?;
    let timestamp = now();
    let ids = drafts
        .iter()
        .map(|draft| insert_job(&tx, draft, &timestamp))
        .collect::<Result<Vec<_>, _>>()?;
    tx.commit().map_err(|error| error.to_string())?;
    Ok(ids)
}

fn insert_job(tx: &Transaction<'_>, draft: &JobDraft, timestamp: &str) -> Result<String, String> {
    let company_id = ensure_company(&tx, &draft.company, &timestamp)?;
    let id = Uuid::new_v4().to_string();
    let published_at = draft
        .published_at
        .as_deref()
        .filter(|value| !value.is_empty());
    let source = source_kind(&draft.source);
    let tags = serde_json::to_string(&draft.tags).map_err(|error| error.to_string())?;
    tx.execute(
        "INSERT INTO jobs(id, company_id, title, status, published_at, created_at, current_stage,
                          source_url, source_kind, city, team, salary, tags_json, jd_text, updated_at)
         VALUES (?1, ?2, ?3, 'opportunity', ?4, ?5, 0, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?5)",
        params![
            id,
            company_id,
            draft.title.trim(),
            published_at,
            timestamp,
            draft.source_url.as_deref().filter(|value| !value.is_empty()),
            source,
            draft.city.trim(),
            draft.team.trim(),
            draft.salary.trim(),
            tags,
            draft.jd.trim()
        ],
    )
    .map_err(|error| error.to_string())?;
    insert_event(&tx, &id, "created", None, None, &timestamp, "")?;
    Ok(id)
}

#[tauri::command]
pub fn mark_job_applied(state: State<'_, AppState>, job_id: String) -> Result<(), String> {
    let connection = state.db.lock().map_err(|_| "数据库锁定失败")?;
    let tx = connection
        .unchecked_transaction()
        .map_err(|error| error.to_string())?;
    let timestamp = now();
    let changed = tx
        .execute(
            "UPDATE jobs SET status='applied', applied_at=?2, last_activity_at=?2, current_stage=0, updated_at=?2 WHERE id=?1 AND status='opportunity'",
            params![job_id, timestamp],
        )
        .map_err(|error| error.to_string())?;
    if changed == 0 {
        return Err("岗位不存在或已投递".into());
    }
    insert_event(&tx, &job_id, "applied", None, Some(0), &timestamp, "")?;
    tx.commit().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn update_job_stage(
    state: State<'_, AppState>,
    job_id: String,
    stage_index: usize,
) -> Result<(), String> {
    if stage_index >= STAGE_KEYS.len() {
        return Err("招聘阶段无效".into());
    }
    let connection = state.db.lock().map_err(|_| "数据库锁定失败")?;
    let tx = connection
        .unchecked_transaction()
        .map_err(|error| error.to_string())?;
    let previous: Option<i64> = tx
        .query_row(
            "SELECT current_stage FROM jobs WHERE id=?1 AND status='applied'",
            [&job_id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    let previous = previous.ok_or_else(|| "正在推进的岗位不存在".to_string())?;
    let timestamp = now();
    tx.execute(
        "UPDATE jobs SET current_stage=?2, last_activity_at=?3, updated_at=?3 WHERE id=?1",
        params![job_id, stage_index as i64, timestamp],
    )
    .map_err(|error| error.to_string())?;
    insert_event(
        &tx,
        &job_id,
        "stage",
        Some(previous.clamp(0, 7) as usize),
        Some(stage_index),
        &timestamp,
        STAGE_KEYS[stage_index],
    )?;
    tx.commit().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn save_interview_session(
    state: State<'_, AppState>,
    session: InterviewDraft,
) -> Result<(), String> {
    if !ROUND_KEYS.contains(&session.round_type.as_str()) {
        return Err("面试轮次无效".into());
    }
    if !session.date.is_empty()
        && chrono::NaiveDate::parse_from_str(&session.date, "%Y-%m-%d").is_err()
    {
        return Err("面试日期无效".into());
    }
    if session.summary.len() > 500_000 {
        return Err("面试总结过长".into());
    }
    if !["进行中", "待安排", "通过", "待反馈", "未通过"].contains(&session.result.as_str())
    {
        return Err("面试结果无效".into());
    }
    if session.questions.len() > 500 {
        return Err("单轮问题数量超过限制".into());
    }
    if session
        .questions
        .iter()
        .any(|question| question.question.len() > 100_000 || question.answer.len() > 500_000)
    {
        return Err("题目或回答内容过长".into());
    }
    let connection = state.db.lock().map_err(|_| "数据库锁定失败")?;
    let tx = connection
        .unchecked_transaction()
        .map_err(|error| error.to_string())?;
    let timestamp = now();
    let exists: bool = tx
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM jobs WHERE id=?1 AND status='applied')",
            [&session.job_id],
            |row| row.get(0),
        )
        .map_err(|error| error.to_string())?;
    if !exists {
        return Err("对应的已投递岗位不存在".into());
    }
    let session_id: Option<String> = tx
        .query_row(
            "SELECT id FROM interview_sessions WHERE job_id=?1 AND round_type=?2",
            params![session.job_id, session.round_type],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    let session_id = session_id.unwrap_or_else(|| Uuid::new_v4().to_string());
    tx.execute(
        "INSERT INTO interview_sessions(id, job_id, round_type, interview_date, result, summary, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
         ON CONFLICT(job_id, round_type) DO UPDATE SET interview_date=excluded.interview_date,
             result=excluded.result, summary=excluded.summary, updated_at=excluded.updated_at",
        params![session_id, session.job_id, session.round_type, session.date, session.result, session.summary, timestamp],
    )
    .map_err(|error| error.to_string())?;
    let mut existing_ids = Vec::new();
    {
        let mut statement = tx
            .prepare("SELECT id FROM interview_questions WHERE session_id=?1")
            .map_err(|error| error.to_string())?;
        let rows = statement
            .query_map([&session_id], |row| row.get::<_, String>(0))
            .map_err(|error| error.to_string())?;
        for row in rows {
            existing_ids.push(row.map_err(|error| error.to_string())?);
        }
    }
    for id in existing_ids {
        tx.execute("DELETE FROM interview_questions WHERE id=?1", [id])
            .map_err(|error| error.to_string())?;
    }
    for (order, question) in session.questions.iter().enumerate() {
        tx.execute(
            "INSERT INTO interview_questions(id, session_id, sort_order, question_text, answer_text, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                if question.id.is_empty() { Uuid::new_v4().to_string() } else { question.id.clone() },
                session_id,
                order as i64,
                question.question,
                question.answer,
                timestamp
            ],
        )
        .map_err(|error| error.to_string())?;
    }
    tx.execute(
        "UPDATE jobs SET last_activity_at=?2, updated_at=?2, active_round=?3 WHERE id=?1",
        params![session.job_id, timestamp, session.round_type],
    )
    .map_err(|error| error.to_string())?;
    insert_event(
        &tx,
        &session.job_id,
        "interview_saved",
        None,
        None,
        &timestamp,
        &session.round_type,
    )?;
    tx.commit().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn delete_job(state: State<'_, AppState>, job_id: String) -> Result<(), String> {
    let mut connection = state.db.lock().map_err(|_| "数据库锁定失败")?;
    let tx = connection
        .transaction()
        .map_err(|error| error.to_string())?;
    let changed = tx
        .execute("DELETE FROM jobs WHERE id = ?1", [&job_id])
        .map_err(|error| error.to_string())?;
    if changed == 0 {
        return Err("岗位已不存在".into());
    }
    tx.commit().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn open_job_url(url: String) -> Result<(), String> {
    let parsed = url::Url::parse(&url).map_err(|_| "岗位网址格式无效".to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err("只允许打开 HTTP 或 HTTPS 招聘网址".into());
    }
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer.exe")
            .arg(parsed.as_str())
            .spawn()
            .map(|_| ())
            .map_err(|error| format!("无法打开招聘网址：{error}"))
    }
    #[cfg(not(target_os = "windows"))]
    {
        Err("当前平台暂不支持打开外部网址".into())
    }
}

#[tauri::command]
pub fn list_resumes(state: State<'_, AppState>) -> Result<Vec<ResumeRecord>, String> {
    let connection = state.db.lock().map_err(|_| "数据库锁定失败")?;
    let mut statement = connection
        .prepare("SELECT id,name,target_role,original_file,content,updated_at FROM resumes ORDER BY created_at,id")
        .map_err(|error| error.to_string())?;
    let resumes = statement
        .query_map([], |row| {
            Ok(ResumeRecord {
                id: row.get(0)?,
                name: row.get(1)?,
                target_role: row.get(2)?,
                original_file: row.get(3)?,
                content: row.get(4)?,
                updated_at: row.get(5)?,
            })
        })
        .map_err(|error| error.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string());
    resumes
}

#[tauri::command]
pub fn import_resume(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<Option<ResumeRecord>, String> {
    let selected = app
        .dialog()
        .file()
        .add_filter("Word 简历", &["docx"])
        .blocking_pick_file();
    let Some(selected) = selected else {
        return Ok(None);
    };
    let source = selected
        .into_path()
        .map_err(|_| "无法读取所选简历路径".to_string())?;
    import_resume_path(&state, &source).map(Some)
}

fn import_resume_path(state: &AppState, source: &Path) -> Result<ResumeRecord, String> {
    if source
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        != "docx"
    {
        return Err("仅支持 .docx 格式的简历".into());
    }
    let metadata = std::fs::metadata(source).map_err(|error| error.to_string())?;
    if metadata.len() > 20 * 1024 * 1024 {
        return Err("简历文件超过 20 MiB 限制".into());
    }
    let content = importer::docx_text(&std::fs::read(source).map_err(|error| error.to_string())?)?;
    if content.trim().is_empty() {
        return Err("简历正文为空，无法导入".into());
    }
    let id = Uuid::new_v4().to_string();
    let original_name = source
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("resume.docx");
    let filename = original_name.to_lowercase();
    let target_role = if filename.contains("agent") {
        "AI Agent 开发工程师"
    } else if filename.contains("前端") {
        "前端开发工程师"
    } else {
        ""
    };
    let role_tag = if target_role.is_empty() {
        "其他方向"
    } else {
        target_role
    };
    let name = format!("简历 · {role_tag}");
    let relative_path = format!("resumes/{id}.docx");
    let full_path = state.data_dir.join(&relative_path);
    std::fs::create_dir_all(full_path.parent().ok_or("简历存储路径无效")?)
        .map_err(|error| error.to_string())?;
    std::fs::copy(source, &full_path).map_err(|error| error.to_string())?;
    let timestamp = now();
    let inserted = state.db.lock().map_err(|_| "数据库锁定失败")?.execute(
        "INSERT INTO resumes(id,name,target_role,original_file,stored_file,content,created_at,updated_at) VALUES(?1,?2,?3,?4,?5,?6,?7,?7)",
        params![id, name, target_role, original_name, relative_path, content, timestamp],
    );
    if let Err(error) = inserted {
        let _ = std::fs::remove_file(full_path);
        return Err(error.to_string());
    }
    Ok(ResumeRecord {
        id,
        name,
        target_role: target_role.into(),
        original_file: original_name.into(),
        content,
        updated_at: timestamp,
    })
}

#[tauri::command]
pub fn save_resume(
    state: State<'_, AppState>,
    resume_id: String,
    name: String,
    target_role: String,
    content: String,
) -> Result<(), String> {
    if name.trim().is_empty() || target_role.trim().is_empty() {
        return Err("简历名称和目标岗位不能为空".into());
    }
    if content.len() > 500_000 {
        return Err("简历内容超过 500 KB 限制".into());
    }
    let changed = state
        .db
        .lock()
        .map_err(|_| "数据库锁定失败")?
        .execute(
            "UPDATE resumes SET name=?2,target_role=?3,content=?4,updated_at=?5 WHERE id=?1",
            params![resume_id, name.trim(), target_role.trim(), content, now()],
        )
        .map_err(|error| error.to_string())?;
    if changed == 0 {
        return Err("简历已不存在".into());
    }
    Ok(())
}

fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn resume_docx(content: &str) -> Result<Vec<u8>, String> {
    if content.trim().is_empty() {
        return Err("简历内容为空，无法导出".into());
    }
    let mut bytes = std::io::Cursor::new(Vec::new());
    let mut archive = ZipWriter::new(&mut bytes);
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    let sections = [
        "教育背景",
        "核心技能",
        "技能清单",
        "实习与项目经历",
        "项目经历",
        "工作经历",
        "实习经历",
        "荣誉奖项",
        "个人总结",
    ];
    let mut body = String::new();
    for (index, line) in content.lines().enumerate() {
        let trimmed = line.trim();
        let style = if index == 0 {
            "Title"
        } else if sections.contains(&trimmed) {
            "Heading1"
        } else {
            "Normal"
        };
        body.push_str(&format!(
            "<w:p><w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr><w:r><w:t xml:space=\"preserve\">{}</w:t></w:r></w:p>",
            xml_escape(line)
        ));
    }
    if body.is_empty() {
        return Err("简历内容为空，无法导出".into());
    }
    let document = format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body>{body}<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"850\" w:right=\"1000\" w:bottom=\"850\" w:left=\"1000\" w:header=\"420\" w:footer=\"420\" w:gutter=\"0\"/></w:sectPr></w:body></w:document>"
    );
    let content_types = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/><Override PartName=\"/word/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml\"/></Types>";
    let relationships = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/></Relationships>";
    let styles = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><w:styles xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:style w:type=\"paragraph\" w:default=\"1\" w:styleId=\"Normal\"><w:name w:val=\"Normal\"/><w:rPr><w:rFonts w:ascii=\"Aptos\" w:eastAsia=\"等线\"/><w:sz w:val=\"20\"/></w:rPr><w:pPr><w:spacing w:after=\"90\" w:line=\"300\"/></w:pPr></w:style><w:style w:type=\"paragraph\" w:styleId=\"Title\"><w:name w:val=\"Title\"/><w:basedOn w:val=\"Normal\"/><w:rPr><w:b/><w:color w:val=\"175F48\"/><w:sz w:val=\"34\"/></w:rPr><w:pPr><w:spacing w:after=\"150\"/></w:pPr></w:style><w:style w:type=\"paragraph\" w:styleId=\"Heading1\"><w:name w:val=\"heading 1\"/><w:basedOn w:val=\"Normal\"/><w:rPr><w:b/><w:color w:val=\"175F48\"/><w:sz w:val=\"23\"/></w:rPr><w:pPr><w:spacing w:before=\"160\" w:after=\"80\"/></w:pPr></w:style></w:styles>";
    for (path, data) in [
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", relationships),
        ("word/document.xml", &document),
        ("word/styles.xml", styles),
    ] {
        archive
            .start_file(path, options)
            .map_err(|error| error.to_string())?;
        archive
            .write_all(data.as_bytes())
            .map_err(|error| error.to_string())?;
    }
    archive.finish().map_err(|error| error.to_string())?;
    Ok(bytes.into_inner())
}

#[tauri::command]
pub fn export_resume_docx(
    app: AppHandle,
    state: State<'_, AppState>,
    resume_id: String,
) -> Result<Option<String>, String> {
    let resume = {
        let connection = state.db.lock().map_err(|_| "数据库锁定失败")?;
        connection
            .query_row(
                "SELECT name,content FROM resumes WHERE id=?1",
                [&resume_id],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
            )
            .optional()
            .map_err(|error| error.to_string())?
    };
    let Some((name, content)) = resume else {
        return Err("简历已不存在".into());
    };
    let safe_name = name
        .chars()
        .map(|character| {
            if "/\\:*?\"<>|".contains(character) {
                '_'
            } else {
                character
            }
        })
        .collect::<String>();
    let filename = format!("{safe_name}.docx");
    let selected = app
        .dialog()
        .file()
        .add_filter("Word 文档", &["docx"])
        .set_file_name(&filename)
        .blocking_save_file();
    let Some(selected) = selected else {
        return Ok(None);
    };
    let path = selected
        .into_path()
        .map_err(|_| "无法读取保存路径".to_string())?;
    let bytes = resume_docx(&content)?;
    std::fs::write(&path, bytes).map_err(|error| error.to_string())?;
    Ok(Some(path.to_string_lossy().into_owned()))
}

#[tauri::command]
pub async fn parse_job_file(app: AppHandle) -> Result<Option<Vec<JobDraft>>, String> {
    let selected = app
        .dialog()
        .file()
        .add_filter("岗位文件", &["md", "docx", "csv"])
        .blocking_pick_file();
    let Some(selected) = selected else {
        return Ok(None);
    };
    let path = selected
        .into_path()
        .map_err(|_| "无法读取所选文件路径".to_string())?;
    tauri::async_runtime::spawn_blocking(move || importer::parse_file_batch(&path))
        .await
        .map_err(|error| error.to_string())?
        .map(Some)
}

#[tauri::command]
pub async fn preview_job_url(url: String) -> Result<JobDraft, String> {
    if url.len() > 2048 {
        return Err("网址超过长度限制".into());
    }
    tauri::async_runtime::spawn_blocking(move || scraper_service::preview_url(&url))
        .await
        .map_err(|error| error.to_string())?
}

#[cfg(test)]
mod resume_tests {
    use super::resume_docx;
    use std::io::Read;
    use zip::ZipArchive;

    #[test]
    fn exported_word_is_valid_and_escapes_resume_text() {
        let bytes = resume_docx("测试求职者\n教育背景\nC++ <Rust> & Agent").unwrap();
        let mut archive = ZipArchive::new(std::io::Cursor::new(bytes)).unwrap();
        let mut document = String::new();
        archive
            .by_name("word/document.xml")
            .unwrap()
            .read_to_string(&mut document)
            .unwrap();
        assert!(document.contains("测试求职者"));
        assert!(document.contains("C++ &lt;Rust&gt; &amp; Agent"));
        assert!(document.contains("w:val=\"Heading1\""));
    }

    #[test]
    fn empty_resume_cannot_be_exported() {
        assert!(resume_docx("\n  \n").is_err());
    }
}
