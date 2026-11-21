use rusqlite::Connection;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Mutex;

pub struct AppState {
    pub db: Mutex<Connection>,
    pub data_dir: PathBuf,
}

impl AppState {
    pub fn open(path: &Path) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let connection = Connection::open(path).map_err(|error| error.to_string())?;
        connection
            .pragma_update(None, "foreign_keys", "ON")
            .map_err(|error| error.to_string())?;
        connection
            .execute_batch(
                "
                CREATE TABLE IF NOT EXISTS companies (
                    id TEXT PRIMARY KEY NOT NULL,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY NOT NULL,
                    company_id TEXT NOT NULL REFERENCES companies(id),
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('opportunity', 'applied', 'closed')),
                    published_at TEXT,
                    created_at TEXT NOT NULL,
                    applied_at TEXT,
                    last_activity_at TEXT,
                    current_stage INTEGER NOT NULL DEFAULT 0 CHECK(current_stage BETWEEN 0 AND 7),
                    active_round TEXT NOT NULL DEFAULT 'first',
                    source_url TEXT,
                    source_kind TEXT NOT NULL,
                    city TEXT NOT NULL DEFAULT '',
                    team TEXT NOT NULL DEFAULT '',
                    salary TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    jd_text TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS job_events (
                    id TEXT PRIMARY KEY NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    from_stage INTEGER,
                    to_stage INTEGER,
                    occurred_at TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS interview_sessions (
                    id TEXT PRIMARY KEY NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    round_type TEXT NOT NULL CHECK(round_type IN ('first', 'second', 'third', 'hr')),
                    interview_date TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '待安排',
                    summary TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, round_type)
                );

                CREATE TABLE IF NOT EXISTS interview_questions (
                    id TEXT PRIMARY KEY NOT NULL,
                    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
                    sort_order INTEGER NOT NULL,
                    question_text TEXT NOT NULL DEFAULT '',
                    answer_text TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS resumes (
                    id TEXT PRIMARY KEY NOT NULL,
                    name TEXT NOT NULL,
                    target_role TEXT NOT NULL DEFAULT '',
                    original_file TEXT NOT NULL,
                    stored_file TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id TEXT PRIMARY KEY NOT NULL,
                    category TEXT NOT NULL DEFAULT '未分类',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status_activity ON jobs(status, last_activity_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id, title);
                CREATE INDEX IF NOT EXISTS idx_events_job_time ON job_events(job_id, occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_job ON interview_sessions(job_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_knowledge_documents_category ON knowledge_documents(category, title);
                ",
            )
            .map_err(|error| error.to_string())?;
        Ok(Self {
            db: Mutex::new(connection),
            data_dir: path.parent().unwrap_or(Path::new(".")).to_path_buf(),
        })
    }
}
