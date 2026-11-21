use crate::models::JobDraft;
use encoding_rs::{Encoding, UTF_8};
use reqwest::blocking::{Client, Response};
use reqwest::header::{CONTENT_TYPE, LOCATION};
use reqwest::redirect::Policy;
use scraper::{Html, Selector};
use std::io::Read;
use std::net::{IpAddr, Ipv4Addr, SocketAddr, ToSocketAddrs};
use std::time::Duration;
use url::Url;

const MAX_RESPONSE_BYTES: u64 = 5 * 1024 * 1024;
const MAX_REDIRECTS: usize = 5;

fn public_ipv4(ip: Ipv4Addr) -> bool {
    let [a, b, c, _] = ip.octets();
    !ip.is_private()
        && !ip.is_loopback()
        && !ip.is_link_local()
        && !ip.is_broadcast()
        && !ip.is_unspecified()
        && !ip.is_multicast()
        && !(a == 0
            || a >= 240
            || (a == 100 && (64..=127).contains(&b))
            || (a == 192 && b == 0 && (c == 0 || c == 2))
            || (a == 198 && (b == 18 || b == 19 || (b == 51 && c == 100)))
            || (a == 203 && b == 0 && c == 113))
}

fn public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => public_ipv4(ip),
        IpAddr::V6(ip) => {
            if let Some(mapped) = ip.to_ipv4_mapped() {
                return public_ipv4(mapped);
            }
            let segments = ip.segments();
            !ip.is_loopback()
                && !ip.is_unspecified()
                && !ip.is_unique_local()
                && !ip.is_unicast_link_local()
                && !ip.is_multicast()
                && !(segments[0] == 0x2001 && (segments[1] == 0x0db8 || segments[1] == 0x0002))
        }
    }
}

fn resolve_public(host: &str, port: u16) -> Result<SocketAddr, String> {
    let addresses = if let Ok(ip) = host.parse::<IpAddr>() {
        vec![SocketAddr::new(ip, port)]
    } else {
        (host, port)
            .to_socket_addrs()
            .map_err(|_| "招聘网址域名无法解析".to_string())?
            .collect::<Vec<_>>()
    };
    if addresses.is_empty() || addresses.iter().any(|address| !public_ip(address.ip())) {
        return Err("招聘网址解析到非公开网络地址，已阻止访问".into());
    }
    addresses
        .into_iter()
        .next()
        .ok_or_else(|| "招聘网址域名无法解析".into())
}

fn checked_host(url: &Url) -> Result<(String, u16, SocketAddr), String> {
    if !matches!(url.scheme(), "http" | "https") {
        return Err("只支持 HTTP 或 HTTPS 招聘网址".into());
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err("网址不能包含用户名或密码".into());
    }
    let host = url
        .host_str()
        .ok_or_else(|| "招聘网址缺少域名".to_string())?
        .to_ascii_lowercase();
    let port = url
        .port_or_known_default()
        .ok_or_else(|| "招聘网址端口无效".to_string())?;
    let address = resolve_public(&host, port)?;
    Ok((host, port, address))
}

fn fetch_html(input: &str) -> Result<(Url, String, String), String> {
    let mut current = Url::parse(input).map_err(|_| "招聘网址格式无效".to_string())?;
    for redirect_count in 0..=MAX_REDIRECTS {
        let (host, _port, address) = checked_host(&current)?;
        let client = Client::builder()
            .no_proxy()
            .resolve(&host, address)
            .redirect(Policy::none())
            .connect_timeout(Duration::from_secs(6))
            .timeout(Duration::from_secs(15))
            .user_agent("CampusJobTracker/0.1 (public job description preview)")
            .build()
            .map_err(|_| "无法初始化网页请求".to_string())?;
        let response = client
            .get(current.clone())
            .send()
            .map_err(|_| "网页请求失败，请检查网络或稍后重试".to_string())?;
        if response.status().is_redirection() {
            if redirect_count == MAX_REDIRECTS {
                return Err("网页跳转次数过多".into());
            }
            let location = response
                .headers()
                .get(LOCATION)
                .and_then(|value| value.to_str().ok())
                .ok_or_else(|| "网页返回无效跳转地址".to_string())?;
            current = current
                .join(location)
                .map_err(|_| "网页返回无效跳转地址".to_string())?;
            continue;
        }
        if !response.status().is_success() {
            return Err(format!("网页返回状态码 {}", response.status().as_u16()));
        }
        return read_html_response(response)
            .map(|(content_type, html)| (current, content_type, html));
    }
    Err("无法读取招聘网页".into())
}

fn read_html_response(response: Response) -> Result<(String, String), String> {
    let content_type = response
        .headers()
        .get(CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .to_string();
    if !content_type.is_empty()
        && !content_type.to_ascii_lowercase().contains("text/html")
        && !content_type
            .to_ascii_lowercase()
            .contains("application/xhtml+xml")
    {
        return Err("该网址返回的不是网页 HTML".into());
    }
    if response
        .content_length()
        .is_some_and(|size| size > MAX_RESPONSE_BYTES)
    {
        return Err("招聘网页超过 5 MiB 大小限制".into());
    }
    let mut limited = response.take(MAX_RESPONSE_BYTES + 1);
    let mut bytes = Vec::new();
    limited
        .read_to_end(&mut bytes)
        .map_err(|_| "无法读取网页正文".to_string())?;
    if bytes.len() as u64 > MAX_RESPONSE_BYTES {
        return Err("招聘网页超过 5 MiB 大小限制".into());
    }
    let charset = content_type
        .split(';')
        .filter_map(|part| part.trim().split_once('='))
        .find(|(key, _)| key.trim().eq_ignore_ascii_case("charset"))
        .map(|(_, value)| value.trim().trim_matches(['"', '\'']));
    let encoding = charset
        .and_then(|label| Encoding::for_label(label.as_bytes()))
        .unwrap_or(UTF_8);
    let (decoded, _, _) = encoding.decode(&bytes);
    Ok((content_type, decoded.into_owned()))
}

fn meta_content(document: &Html, selector: &Selector) -> Option<String> {
    document
        .select(selector)
        .find_map(|element| element.value().attr("content").map(str::to_string))
}

fn preview_date(document: &Html) -> Option<String> {
    let selectors = [
        "meta[property='article:published_time']",
        "meta[property='og:published_time']",
        "meta[name='datePublished']",
        "meta[name='publishdate']",
        "meta[name='publish-date']",
        "time[datetime]",
    ];
    for selector in selectors {
        if let Ok(selector) = Selector::parse(selector) {
            if let Some(value) = document.select(&selector).find_map(|element| {
                element
                    .value()
                    .attr("content")
                    .or_else(|| element.value().attr("datetime"))
                    .or_else(|| element.value().attr("content"))
            }) {
                let date = value.chars().take(10).collect::<String>();
                if chrono::NaiveDate::parse_from_str(&date, "%Y-%m-%d").is_ok() {
                    return Some(date);
                }
            }
        }
    }
    None
}

fn job_posting(value: &serde_json::Value) -> Option<&serde_json::Value> {
    if value.get("@type").is_some_and(|kind| {
        kind.as_str() == Some("JobPosting")
            || kind
                .as_array()
                .is_some_and(|items| items.iter().any(|item| item.as_str() == Some("JobPosting")))
    }) {
        return Some(value);
    }
    match value {
        serde_json::Value::Array(items) => items.iter().find_map(job_posting),
        serde_json::Value::Object(object) => object.get("@graph").and_then(job_posting),
        _ => None,
    }
}

fn readable_text(root: scraper::ElementRef<'_>) -> String {
    root.descendants()
        .filter_map(|node| {
            let text = node.value().as_text()?;
            if node.ancestors().any(|parent| {
                parent.value().as_element().is_some_and(|element| {
                    matches!(
                        element.name(),
                        "script" | "style" | "noscript" | "nav" | "footer"
                    )
                })
            }) {
                return None;
            }
            let text = text.trim();
            (!text.is_empty()).then_some(text)
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn parse_page(source: &str, final_url: &Url) -> Result<JobDraft, String> {
    let document = Html::parse_document(source);
    let structured = document
        .select(&Selector::parse("script[type='application/ld+json']").unwrap())
        .filter_map(|element| {
            serde_json::from_str::<serde_json::Value>(&element.text().collect::<String>()).ok()
        })
        .find_map(|value| job_posting(&value).cloned());
    if let Some(job) = structured {
        let title = job
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        let description = job
            .get("description")
            .and_then(|v| v.as_str())
            .unwrap_or_default();
        if !title.is_empty() && !description.is_empty() {
            let published_at = job
                .get("datePosted")
                .and_then(|v| v.as_str())
                .and_then(|value| {
                    let date: String = value.chars().take(10).collect();
                    chrono::NaiveDate::parse_from_str(&date, "%Y-%m-%d")
                        .ok()
                        .map(|_| date)
                });
            let location = job.get("jobLocation").and_then(|value| {
                value
                    .as_array()
                    .and_then(|items| items.first())
                    .or(Some(value))
            });
            let city = location
                .and_then(|v| v.get("address"))
                .and_then(|v| v.get("addressLocality"))
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string();
            return Ok(JobDraft {
                company: job
                    .get("hiringOrganization")
                    .and_then(|v| v.get("name"))
                    .and_then(|v| v.as_str())
                    .unwrap_or_default()
                    .to_string(),
                title,
                city,
                published_at,
                jd: readable_text(Html::parse_fragment(description).root_element())
                    .chars()
                    .take(200_000)
                    .collect(),
                source_url: Some(final_url.to_string()),
                source: "url".into(),
                team: String::new(),
                salary: String::new(),
                tags: Vec::new(),
            });
        }
    }

    let title_selector = Selector::parse("h1").map_err(|_| "无法读取岗位标题")?;
    let page_title_selector = Selector::parse("title").map_err(|_| "无法读取网页标题")?;
    let description_selector =
        Selector::parse("meta[name='description'], meta[property='og:description']")
            .map_err(|_| "无法读取网页描述")?;
    let company_selector =
        Selector::parse("meta[property='og:site_name']").map_err(|_| "无法读取网页公司名称")?;
    let title = document
        .select(&title_selector)
        .next()
        .map(|element| element.text().collect::<String>().trim().to_string())
        .filter(|value| !value.is_empty())
        .or_else(|| {
            document
                .select(&page_title_selector)
                .next()
                .map(|element| element.text().collect::<String>().trim().to_string())
                .filter(|value| !value.is_empty())
        })
        .unwrap_or_default();
    let description = meta_content(&document, &description_selector).unwrap_or_default();
    let company = meta_content(&document, &company_selector).unwrap_or_default();
    let content_selector =
        Selector::parse("main, article, [class*='job-description'], [id*='job-description']")
            .map_err(|_| "无法读取岗位正文")?;
    let content_root = document.select(&content_selector).next();
    let page_root = document.root_element();
    let root = content_root.unwrap_or(page_root);
    let jd = readable_text(root);
    let jd = if jd.is_empty() { description } else { jd };
    if title.is_empty() && jd.is_empty() {
        return Err("页面没有可识别的岗位标题或正文，请改用手动录入".into());
    }
    Ok(JobDraft {
        company,
        title,
        city: String::new(),
        team: String::new(),
        published_at: preview_date(&document),
        source_url: Some(final_url.to_string()),
        source: "url".into(),
        salary: String::new(),
        jd: jd.chars().take(200_000).collect(),
        tags: Vec::new(),
    })
}

pub fn preview_url(input: &str) -> Result<JobDraft, String> {
    let (final_url, _, source) = fetch_html(input)?;
    parse_page(&source, &final_url)
}

#[cfg(test)]
mod tests {
    use super::{parse_page, public_ip, public_ipv4};
    use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

    #[test]
    fn private_and_reserved_ipv4_addresses_are_rejected() {
        for ip in [
            Ipv4Addr::new(127, 0, 0, 1),
            Ipv4Addr::new(10, 0, 0, 2),
            Ipv4Addr::new(172, 16, 0, 1),
            Ipv4Addr::new(192, 168, 0, 1),
            Ipv4Addr::new(169, 254, 169, 254),
            Ipv4Addr::new(203, 0, 113, 10),
        ] {
            assert!(!public_ipv4(ip));
        }
    }

    #[test]
    fn private_ipv6_and_ipv4_mapped_private_addresses_are_rejected() {
        assert!(!public_ip(IpAddr::V6(
            "fd00::1".parse::<Ipv6Addr>().unwrap()
        )));
        assert!(!public_ip(IpAddr::V6(
            "::ffff:127.0.0.1".parse::<Ipv6Addr>().unwrap()
        )));
    }

    #[test]
    fn structured_job_posting_reads_company_date_and_description() {
        let source = r#"<script type="application/ld+json">{"@graph":[{"@type":"JobPosting","title":"前端工程师","hiringOrganization":{"name":"示例公司"},"datePosted":"2026-11-20","description":"<p>负责产品研发</p>","jobLocation":{"address":{"addressLocality":"上海"}}}]}</script>"#;
        let draft =
            parse_page(source, &url::Url::parse("https://example.com/job").unwrap()).unwrap();
        assert_eq!(draft.company, "示例公司");
        assert_eq!(draft.city, "上海");
        assert_eq!(draft.jd, "负责产品研发");
        assert_eq!(draft.published_at.as_deref(), Some("2026-11-20"));
    }

    #[test]
    fn generic_html_excludes_scripts_and_does_not_invent_publish_date() {
        let draft = parse_page(
            "<h1>Engineer</h1><main><script>privateConfig=123</script><p>Build apps</p></main>",
            &url::Url::parse("https://example.com/job").unwrap(),
        )
        .unwrap();
        assert_eq!(draft.jd, "Build apps");
        assert_eq!(draft.published_at, None);
    }
}
