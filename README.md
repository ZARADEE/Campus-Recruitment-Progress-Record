# 求职进度台

一款基于 Tauri 2、Rust 和 SQLite 的 Windows 桌面应用，用于整理校招岗位、跟踪投递进度、记录面试问答和管理岗位简历。个人岗位、面试和简历数据默认保存在本机。

**当前版本：** [v0.1.0](https://github.com/ZARADEE/Campus-Recruitment-Progress-Record/tree/v0.1.0)

## 功能

- 从 Markdown、Word `.docx` 和 CSV 导入岗位；CSV 可在保存前逐条检查和编辑。
- 根据公开招聘网址抓取 JD 候选信息，确认后再保存。
- 将未投递岗位放在「看看机会」，已投递岗位放在「正在推进」，跟踪招聘阶段和最近活动。
- 按公司、岗位和面试轮次整理一面、二面、三面及 HR 面的问题、回答和复盘。
- 导入并编辑岗位简历，导出 Word，或通过系统打印窗口保存为 PDF。
- 按公司连续排列岗位；查看保存的 JD，或打开来源招聘网页。
- 删除岗位前二次确认，并清理关联的阶段事件和面试记录。

## 运行

从仓库克隆后，在 Windows 上安装依赖并构建桌面应用：

```powershell
npm install
npm run desktop:test
npm run desktop:build
```

构建需要 Node.js、Rust 稳定版 MSVC 工具链、Microsoft C++ Build Tools、Windows SDK 和 WebView2 Runtime。构建完成后，运行 `src-tauri/target/release/campus-job-tracker.exe`。在本机项目目录运行 `启动求职进度台.cmd` 时，应用数据和 WebView 缓存会保存到项目的 `data/` 目录。

开发预览使用：

```powershell
npm run desktop:dev
```

浏览器预览命令 `npm run dev` 使用样例数据；文件导入、网页抓取、SQLite 保存和简历导出需要桌面版。

## 岗位 CSV

首行为列名，至少包含公司和岗位名称。完整模板见 [examples/岗位导入模板.csv](examples/岗位导入模板.csv)。支持 UTF-8（含 BOM）和 GB18030；日期格式为 `YYYY-MM-DD`，没有明确发布时间时留空。单次最多 1000 行，文件不超过 20 MiB。

| 字段 | 支持的列名示例 |
| --- | --- |
| 公司 | 公司、公司名称、企业、`company`、`company_name` |
| 岗位 | 岗位、岗位名称、职位、`title`、`role`、`job_title` |
| 城市 | 城市、地点、工作地点、`city`、`location` |
| 发布时间 | 发布时间、发布日期、`published_at`、`datePosted` |
| 岗位描述 | JD、岗位描述、职位描述、`description`、`jd_text` |
| 网址 | 网址、招聘网址、链接、`url`、`source_url` |

CSV 内容会先进入可编辑预览；当前桌面导入流程不会自动合并重复岗位。

## 数据和隐私

岗位数据库、简历原件及导入文件保存在本机 `data/` 目录，或 Tauri 的标准用户数据目录。根目录 `.gitignore` 会排除 `data/`、SQLite 文件、工具链、缓存和构建产物；这些内容不会随源码提交。网页抓取只在用户主动触发时请求公开 HTTP/HTTPS 页面，不绕过登录或验证码。

备份本机数据前请先关闭应用，再复制 `data/` 目录。公开仓库不包含个人岗位记录或简历文件。

## 文档

- [产品范围](PRODUCT.md)
- [技术方案](TECHNICAL_DESIGN.md)
- [实现说明](IMPLEMENTATION.md)
- [界面设计方向](UI_DIRECTION.md)
- [验证记录](VALIDATION.md)

## 版本

首个公开源码版本为 `v0.1.0`。版本号与 `package.json`、`src-tauri/Cargo.toml` 和 `src-tauri/tauri.conf.json` 保持一致。
