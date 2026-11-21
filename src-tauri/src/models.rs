use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JobDraft {
    pub company: String,
    pub title: String,
    #[serde(default)]
    pub city: String,
    #[serde(default)]
    pub team: String,
    #[serde(default)]
    pub published_at: Option<String>,
    #[serde(default)]
    pub source_url: Option<String>,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub salary: String,
    #[serde(default)]
    pub jd: String,
    #[serde(default)]
    pub tags: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JobRecord {
    pub id: String,
    pub company: String,
    pub mark: String,
    pub mark_tone: String,
    pub title: String,
    pub city: String,
    pub team: String,
    pub status: String,
    pub stage_index: usize,
    pub active_round: String,
    pub posted: Option<String>,
    pub created: String,
    pub applied_at: Option<String>,
    pub updated: String,
    pub source: String,
    pub source_url: Option<String>,
    pub salary: String,
    pub tags: Vec<String>,
    pub jd: String,
    pub activity: Vec<String>,
    pub sessions: std::collections::HashMap<String, InterviewSession>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InterviewSession {
    pub date: String,
    pub result: String,
    pub summary: String,
    pub questions: Vec<InterviewQuestion>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InterviewQuestion {
    pub id: String,
    pub question: String,
    pub answer: String,
}
