mod commands;
mod database;
mod importer;
mod models;
mod scraper_service;

use database::AppState;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let data_dir = match std::env::var_os("CAMPUS_DATA_DIR") {
                Some(path) => std::path::PathBuf::from(path),
                None => app.path().app_data_dir()?,
            };
            let database_path = data_dir.join("career.sqlite");
            let state = AppState::open(&database_path).map_err(std::io::Error::other)?;
            app.manage(state);
            tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?
                .data_directory(data_dir.join("webview"))
                .build()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::list_jobs,
            commands::create_job,
            commands::create_jobs,
            commands::mark_job_applied,
            commands::terminate_job,
            commands::update_job_stage,
            commands::save_exam_time,
            commands::save_interview_session,
            commands::delete_job,
            commands::list_knowledge_documents,
            commands::create_knowledge_document,
            commands::save_knowledge_document,
            commands::delete_knowledge_document,
            commands::open_job_url,
            commands::list_resumes,
            commands::import_resume,
            commands::save_resume,
            commands::export_resume_docx,
            commands::parse_job_file,
            commands::preview_job_url
        ])
        .run(tauri::generate_context!())
        .expect("failed to run 求职进度台");
}
