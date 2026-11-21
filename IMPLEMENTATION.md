# 桌面版实现说明

## 代码组织

`index.html` 是正式界面，检测 Tauri 后通过命令读取真实数据。浏览器预览使用岗位样例数据。Rust 的 `database.rs` 管理 SQLite，`commands.rs` 负责岗位事务、面试问答、知识文档和简历管理，`importer.rs` 解析 MD / DOCX / CSV，`scraper_service.rs` 抓取公开 JD。

## 已落地的数据规则

岗位初始状态为 `opportunity`，录入时间由后端生成，发布时间只有明确有效日期时才保存。投递迁移在事务中更新 `applied_at`、`last_activity_at` 并写入事件；修改阶段或保存面试也更新活动时间，查看与搜索不更新。

“终止流程”将已投递岗位的状态改为 `closed`，刷新最近活动时间并写入事件；岗位阶段和面试数据保留，已终止岗位继续显示在“正在推进”的已终止分组中。

公司使用规范化名称去重；岗位有独立 ID。面试表通过岗位 ID 与 `first` / `second` / `third` / `hr` 唯一组合存档，问答按 `sort_order` 保存。此次实现每轮一份可编辑记录，补面多场次尚未开放。草稿在 WebView 本地存储暂存，正式保存使用 SQLite 事务。

机会及推进列表按公司名称排序，使同一公司的岗位相邻。机会和已投递岗位的详情显示本机 JD，来源网址通过 Rust 命令交由 Windows 打开。岗位可经二次确认后删除，数据库外键级联移除阶段事件与面试记录。

简历以 `resumes` 表保存编辑文本、文件名和目标岗位，导入的原始 `.docx` 副本存放在 `data/resumes/`。简历文本修改后自动保存；Word 导出由 Rust 生成 `.docx`，PDF 导出使用 WebView 的系统打印窗口。PDF 打印样式将简历排成 A4 页面。

知识积累写入独立的 `knowledge_documents` 表，以分类、文档标题和纯文本正文组织。桌面端自动保存到 SQLite；浏览器预览单独保存在浏览器本机存储，不与面试记录混用。

## CSV 导入

文件对话框调用 `parse_job_file()`，返回岗位候选数组；Markdown 和 DOCX 返回单项，CSV 可返回多项。CSV 支持 UTF-8 BOM、GB18030、引号内的逗号与换行，以及中英文表头。日期不允许猜测或替换为录入时间，单次限制 1000 条、20 MiB。

`create_jobs(drafts)` 先验证整批字段，再在同一事务中写入公司、岗位与录入事件。有错误时整批不落库。预览支持取消选中或逐条修正。模板在 `examples/岗位导入模板.csv`。

## 网页导入

优先解析 JSON-LD `JobPosting`，无结构化数据时读取网页标题与主要正文。外部网页仅作为纯文本展示，过滤脚本、样式、导航和页脚。只允许公开 HTTP/HTTPS 地址，每次跳转都检查目标并固定已校验 DNS 地址，限制响应大小与超时。不执行招聘网站 JavaScript，也不处理登录或验证码。

## Windows 开发环境

开发脚本 `scripts/toolchain-env.ps1` 在项目包含本地 `.toolchain` 时使用隔离的 Rustup、Cargo、MSVC 与 SDK；没有本地工具链时使用已安装的系统 Rust 工具链和 MSVC 环境。项目工具链和构建缓存均列入 `.gitignore`。

根目录 `启动求职进度台.cmd` 指向正式 exe，并设置 `CAMPUS_DATA_DIR` 为项目 `data/`。这个环境变量由启动进程配置，前端没有任意写入路径接口。未设置它时使用标准 Tauri 用户数据目录。

## 设计方案与当前范围

`TECHNICAL_DESIGN.md` 保留架构与安全约束。岗位命令还包括 `delete_job`、`terminate_job` 和 `open_job_url`；知识文档命令支持列表、新建、保存和删除。简历命令包括 `list_resumes`、`import_resume`、`save_resume`、`export_resume_docx`。搜索与目录树由前端对读取的数据计算；JSON 备份导入导出、补面多场次和问答拖动排序尚未提供。
