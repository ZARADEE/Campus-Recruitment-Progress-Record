# 验证记录

Windows x64 桌面构建和端到端检查已完成。验证只使用隔离测试数据库，不读取或提交本机岗位库与简历。

- `npm run desktop:build` 成功生成优化版 `src-tauri/target/release/campus-job-tracker.exe`。
- Rust 单元测试覆盖 Markdown、DOCX、CSV、日期、公开网络地址校验、HTML / JSON-LD JD 解析，以及简历 DOCX 生成和 XML 转义。
- 隔离数据库检查覆盖公司岗位排序、岗位 JD 页面、招聘网址、简历自动保存与 PDF 打印预览，以及岗位删除时关联面试记录的级联清理。
- 浏览器 UI 检查覆盖录入、投递迁移、招聘阶段、四轮面试、问答归档及窄视口。
- 原生 Tauri IPC 检查覆盖岗位录入和投递、HR 面问答、未保存草稿恢复、SQLite 保存、CSV 批量预览及错误回滚。
- SQLite `integrity_check` 返回 `ok`，`foreign_key_check` 无错误。
- 公开静态网页抓取检查验证标题和正文解析；缺少发布时间时保持为空。需要登录、验证码或动态渲染的招聘网页可能需要手动补充信息。

正式构建脚本使用 `--no-bundle` 生成可运行的 Windows exe，不生成安装包。
