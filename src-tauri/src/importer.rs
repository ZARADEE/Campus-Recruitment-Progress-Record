use crate::models::JobDraft;
use pulldown_cmark::{Event, Parser};
use roxmltree::Document;
use std::io::{Cursor, Read};
use std::path::Path;
use zip::ZipArchive;

const MAX_FILE_BYTES: u64 = 20 * 1024 * 1024;
const MAX_DOCX_XML_BYTES: u64 = 100 * 1024 * 1024;
const MAX_DOCX_ENTRIES: usize = 2_000;

fn labeled_value<'a>(text: &'a str, labels: &[&str]) -> Option<&'a str> {
    text.lines().find_map(|line| {
        let (key, value) = line.split_once('：').or_else(|| line.split_once(':'))?;
        let key = key.trim().trim_start_matches(['#', '*', '-', ' ']);
        if labels.iter().any(|label| key.eq_ignore_ascii_case(label)) {
            Some(value.trim().trim_matches(['"', '\'', '`']))
        } else {
            None
        }
    })
}

fn metadata_value<'a>(metadata: &'a str, labels: &[&str]) -> Option<&'a str> {
    labeled_value(metadata, labels)
}

fn first_heading(text: &str) -> Option<&str> {
    text.lines().find_map(|line| {
        let line = line.trim();
        line.strip_prefix('#')
            .map(str::trim)
            .filter(|heading| !heading.is_empty())
    })
}

fn first_nonempty_line(text: &str) -> Option<&str> {
    text.lines().map(str::trim).find(|line| !line.is_empty())
}

fn markdown_plain_text(markdown: &str) -> String {
    let mut output = String::with_capacity(markdown.len());
    for event in Parser::new(markdown) {
        match event {
            Event::Text(text) | Event::Code(text) => output.push_str(&text),
            Event::SoftBreak | Event::HardBreak => output.push('\n'),
            Event::End(_) => output.push('\n'),
            _ => {}
        }
    }
    output.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn parse_markdown(input: &str) -> JobDraft {
    let (metadata, body) = if let Some(rest) = input.strip_prefix("---") {
        if let Some(end) = rest.find("\n---") {
            (&rest[..end], rest[end + 4..].trim_start())
        } else {
            ("", input)
        }
    } else {
        ("", input)
    };
    let plain = markdown_plain_text(body);
    let company = metadata_value(metadata, &["company", "公司", "公司名称"])
        .or_else(|| labeled_value(body, &["公司", "公司名称", "Company"]))
        .unwrap_or("")
        .to_string();
    let title = metadata_value(metadata, &["title", "role", "岗位", "职位", "职位名称"])
        .or_else(|| {
            labeled_value(
                body,
                &["岗位", "职位", "岗位名称", "职位名称", "Title", "Role"],
            )
        })
        .or_else(|| first_heading(body))
        .unwrap_or("")
        .to_string();
    let city = metadata_value(metadata, &["city", "location", "城市", "地点"])
        .or_else(|| labeled_value(body, &["城市", "地点", "工作地点", "Location"]))
        .unwrap_or("")
        .to_string();
    let published_at = metadata_value(
        metadata,
        &["published_at", "published", "发布日期", "发布时间"],
    )
    .or_else(|| labeled_value(body, &["发布日期", "发布时间", "发布于"]))
    .and_then(normalize_date);
    let source_url = metadata_value(metadata, &["url", "source_url", "链接", "网址"])
        .or_else(|| labeled_value(body, &["链接", "网址", "招聘网址"]))
        .map(str::to_string);
    JobDraft {
        company,
        title,
        city,
        team: String::new(),
        published_at,
        source_url,
        source: "markdown".into(),
        salary: String::new(),
        jd: plain.chars().take(200_000).collect(),
        tags: Vec::new(),
    }
}

fn normalize_date(value: &str) -> Option<String> {
    let candidate = value.trim().chars().take(10).collect::<String>();
    chrono::NaiveDate::parse_from_str(&candidate, "%Y-%m-%d")
        .ok()
        .map(|date| date.format("%Y-%m-%d").to_string())
}

pub fn docx_text(bytes: &[u8]) -> Result<String, String> {
    let mut archive =
        ZipArchive::new(Cursor::new(bytes)).map_err(|_| "Word 文档结构无效".to_string())?;
    if archive.len() > MAX_DOCX_ENTRIES {
        return Err("Word 文档包含过多压缩条目".into());
    }
    let xml_file = archive
        .by_name("word/document.xml")
        .map_err(|_| "Word 文档缺少正文".to_string())?;
    if xml_file.size() > MAX_DOCX_XML_BYTES {
        return Err("Word 文档正文超过解压限制".into());
    }
    let mut xml = String::with_capacity(xml_file.size() as usize);
    xml_file
        .take(MAX_DOCX_XML_BYTES + 1)
        .read_to_string(&mut xml)
        .map_err(|_| "Word 文档正文编码无效".to_string())?;
    if xml.len() as u64 > MAX_DOCX_XML_BYTES {
        return Err("Word 文档正文超过解压限制".into());
    }
    let document = Document::parse(&xml).map_err(|_| "Word 文档 XML 无法解析".to_string())?;
    let paragraphs = document
        .descendants()
        .filter(|node| node.is_element() && node.tag_name().name() == "p")
        .map(|paragraph| {
            paragraph
                .descendants()
                .filter(|node| node.is_element() && node.tag_name().name() == "t")
                .filter_map(|node| node.text())
                .collect::<String>()
        })
        .filter(|line| !line.trim().is_empty())
        .collect::<Vec<_>>();
    Ok(paragraphs.join("\n"))
}

fn parse_docx(input: &str) -> JobDraft {
    let company = labeled_value(input, &["公司", "公司名称", "Company"])
        .unwrap_or("")
        .to_string();
    let title = labeled_value(
        input,
        &["岗位", "职位", "岗位名称", "职位名称", "Title", "Role"],
    )
    .or_else(|| first_heading(input))
    .or_else(|| first_nonempty_line(input))
    .unwrap_or("")
    .to_string();
    let city = labeled_value(input, &["城市", "地点", "工作地点", "Location"])
        .unwrap_or("")
        .to_string();
    let published_at =
        labeled_value(input, &["发布日期", "发布时间", "发布于"]).and_then(normalize_date);
    JobDraft {
        company,
        title,
        city,
        team: String::new(),
        published_at,
        source_url: labeled_value(input, &["链接", "网址", "招聘网址"]).map(str::to_string),
        source: "docx".into(),
        salary: String::new(),
        jd: input.chars().take(200_000).collect(),
        tags: Vec::new(),
    }
}

pub fn parse_file(path: &Path) -> Result<JobDraft, String> {
    let extension = path
        .extension()
        .and_then(|extension| extension.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if extension != "md" && extension != "docx" {
        return Err("仅支持 Markdown (.md) 和 Word (.docx) 文件".into());
    }
    let metadata = std::fs::metadata(path).map_err(|_| "无法读取所选文件".to_string())?;
    if metadata.len() > MAX_FILE_BYTES {
        return Err("文件超过 20 MiB 限制".into());
    }
    let bytes = std::fs::read(path).map_err(|_| "无法读取所选文件".to_string())?;
    if extension == "md" {
        let text =
            String::from_utf8(bytes).map_err(|_| "Markdown 文件必须使用 UTF-8 编码".to_string())?;
        Ok(parse_markdown(&text))
    } else {
        let text = docx_text(&bytes)?;
        Ok(parse_docx(&text))
    }
}

fn parse_csv(bytes: &[u8]) -> Result<Vec<JobDraft>, String> {
    let text = match std::str::from_utf8(bytes) {
        Ok(text) => text.trim_start_matches('\u{feff}').to_string(),
        Err(_) => {
            let (text, _, errors) = encoding_rs::GB18030.decode(bytes);
            if errors {
                return Err("CSV 编码无法识别，请另存为 UTF-8 CSV".into());
            }
            text.into_owned()
        }
    };
    let mut reader = csv::ReaderBuilder::new()
        .trim(csv::Trim::All)
        .from_reader(text.as_bytes());
    let headers = reader
        .headers()
        .map_err(|error| format!("CSV 表头无法读取：{error}"))?
        .clone();
    let position = |names: &[&str]| {
        headers
            .iter()
            .position(|header| names.iter().any(|name| header.eq_ignore_ascii_case(name)))
    };
    let company = position(&["公司", "公司名称", "企业", "company", "company_name"])
        .ok_or("CSV 缺少公司列，请使用“公司”或 company 表头")?;
    let title = position(&[
        "岗位",
        "岗位名称",
        "职位",
        "职位名称",
        "title",
        "role",
        "job_title",
    ])
    .ok_or("CSV 缺少岗位列，请使用“岗位”或 title 表头")?;
    let city = position(&["城市", "地点", "工作地点", "city", "location"]);
    let date = position(&[
        "发布时间",
        "发布日期",
        "published_at",
        "datePosted",
        "published",
    ]);
    let jd = position(&["JD", "岗位描述", "职位描述", "description", "jd_text"]);
    let url = position(&["网址", "招聘网址", "链接", "url", "source_url"]);
    let salary = position(&["薪资", "salary"]);
    let team = position(&["部门", "团队", "team", "department"]);
    let mut drafts = Vec::new();
    for (index, record) in reader.records().enumerate() {
        let row =
            record.map_err(|error| format!("CSV 第 {} 条记录格式错误：{error}", index + 1))?;
        if row.iter().all(|value| value.trim().is_empty()) {
            continue;
        }
        if drafts.len() >= 1000 {
            return Err("单次 CSV 导入最多支持 1000 个岗位".into());
        }
        let value = |column: Option<usize>| {
            column
                .and_then(|column| row.get(column))
                .unwrap_or_default()
                .trim()
                .to_string()
        };
        let raw_date = value(date);
        let published_at = if raw_date.is_empty() {
            None
        } else {
            Some(normalize_date(&raw_date).ok_or_else(|| {
                format!(
                    "CSV 第 {} 条记录的发布时间无效，请使用 YYYY-MM-DD",
                    index + 1
                )
            })?)
        };
        drafts.push(JobDraft {
            company: value(Some(company)),
            title: value(Some(title)),
            city: value(city),
            team: value(team),
            salary: value(salary),
            jd: value(jd),
            published_at,
            source_url: url.map(|_| value(url)).filter(|value| !value.is_empty()),
            source: "csv".into(),
            tags: Vec::new(),
        });
    }
    if drafts.is_empty() {
        return Err("CSV 没有可导入的岗位记录".into());
    }
    Ok(drafts)
}

pub fn parse_file_batch(path: &Path) -> Result<Vec<JobDraft>, String> {
    if path
        .extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| extension.eq_ignore_ascii_case("csv"))
    {
        let metadata = std::fs::metadata(path).map_err(|_| "无法读取所选 CSV 文件")?;
        if metadata.len() > MAX_FILE_BYTES {
            return Err("CSV 文件超过 20 MiB 限制".into());
        }
        let bytes = std::fs::read(path).map_err(|_| "无法读取所选 CSV 文件")?;
        parse_csv(&bytes)
    } else {
        parse_file(path).map(|draft| vec![draft])
    }
}

#[cfg(test)]
mod tests {
    use super::{docx_text, normalize_date, parse_csv, parse_docx, parse_markdown};
    use std::io::{Cursor, Write};

    #[test]
    fn markdown_keeps_missing_publish_date_empty() {
        let draft = parse_markdown("# 前端工程师\n\n公司：示例科技\n岗位描述：负责 Web 产品");
        assert_eq!(draft.title, "前端工程师");
        assert_eq!(draft.company, "示例科技");
        assert_eq!(draft.published_at, None);
    }

    #[test]
    fn markdown_reads_explicit_frontmatter_date() {
        let draft = parse_markdown(
            "---\ncompany: 示例科技\ntitle: 前端工程师\npublished_at: 2026-11-20\n---\nJD 正文",
        );
        assert_eq!(draft.published_at.as_deref(), Some("2026-11-20"));
    }

    #[test]
    fn date_requires_an_iso_calendar_date() {
        assert_eq!(normalize_date("2026-02-30"), None);
        assert_eq!(
            normalize_date("2026-02-28 08:30"),
            Some("2026-02-28".into())
        );
    }

    #[test]
    fn csv_imports_multiple_rows_quotes_bom_and_missing_date() {
        let data = "\u{feff}公司,岗位,发布时间,JD\r\n示例科技,前端工程师,2026-11-20,\"React, TypeScript\n跨行描述\"\r\n另一家公司,后端工程师,,Rust\r\n";
        let drafts = parse_csv(data.as_bytes()).unwrap();
        assert_eq!(drafts.len(), 2);
        assert!(drafts[0].jd.contains("TypeScript\n跨行描述"));
        assert_eq!(drafts[1].published_at, None);
        assert!(parse_csv(b"name,description\nfoo,bar").is_err());
        assert!(parse_csv(b"company,title,published_at\nFoo,Bar,2026-02-30").is_err());
    }

    #[test]
    fn docx_extracts_split_runs_and_keeps_paragraph_boundaries() {
        let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
        zip.start_file(
            "word/document.xml",
            zip::write::SimpleFileOptions::default(),
        )
        .unwrap();
        zip.write_all(r#"<w:document xmlns:w="urn:word"><w:body><w:p><w:r><w:t>公司：</w:t></w:r><w:r><w:t>示例科技</w:t></w:r></w:p><w:p><w:r><w:t>岗位：前端工程师</w:t></w:r></w:p><w:p><w:r><w:t>发布时间：2026-11-20</w:t></w:r></w:p><w:p><w:r><w:t>负责 Web &amp; 桌面应用</w:t></w:r></w:p></w:body></w:document>"#.as_bytes()).unwrap();
        let bytes = zip.finish().unwrap().into_inner();
        let text = docx_text(&bytes).unwrap();
        let draft = parse_docx(&text);
        assert_eq!(draft.company, "示例科技");
        assert_eq!(draft.title, "前端工程师");
        assert_eq!(draft.published_at.as_deref(), Some("2026-11-20"));
        assert!(draft.jd.contains("负责 Web & 桌面应用"));
        assert!(docx_text(b"broken document").is_err());
    }
}
