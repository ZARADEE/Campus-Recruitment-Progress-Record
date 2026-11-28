# 大模型面经文档迁移

2026-11-24，按用户确认的原文迁移授权，将 `https://notes.kamacoder.com/interview/llm/` 目录下的 36 篇公开文档导入本机知识积累。

应用入口为「知识积累 → 卡码笔记 → 大模型面经」，包括总览、Transformer 原理、RAG 检索增强、模型微调、Agent 智能体、AI 编程及真实面经。原有笔记保留。

迁移保留正文、标题层级、列表、表格、代码块和原图。图片直接嵌入本地文档，使用原始图片字节，不依赖网站加载；源页面 HTML 另行归档。正文按项目编辑器的样式显示。站内文字链接转换为原站绝对地址；原页中图片外层的点击链接保存在 HTML 归档中，编辑器内显示图片本身。

## 本机归档

`data/imports/kamacoder-llm-2026-11-24/` 不进入 Git，包含以下文件。

- `pages/`、`images/`：原始页面 HTML 与 207 张不同原图。
- `manifest.json`、`images.json`：来源网址、文件路径与 SHA-256 校验信息。
- `documents.json`：36 篇可导入的富文本文档。
- `validation.json`：逐篇正文、标题、代码、表格与图片校验结果。
- `before-import-*.sqlite`：每次执行写入前的 SQLite 一致性备份。
- `import-result-*.json`：数据库写入结果。
- `editor-verification.json`、`desktop-verification.json`：浏览器及桌面编辑器验证结果。

本次内容共有 299,482 个非空白文本字符、70 张表格、78 个代码块与 241 处图片。不同文档使用的重复图片计入 241 处，原图去重后为 207 张。

## 重用脚本

需要 Python 3、`requests`、`beautifulsoup4` 和 `Pillow`。在项目根目录执行。

```powershell
python scripts/import_kamacoder_llm.py collect
python scripts/import_kamacoder_llm.py prepare
node scripts/verify-kamacoder-import.mjs
python scripts/import_kamacoder_llm.py apply --database data/career.sqlite
```

`collect` 仅遍历目标目录内的文档链接，缓存原始页面；`prepare` 下载图片并验证转换结果；`apply` 先备份数据库，再用事务新增文档。文档 ID 根据原文网址固定生成，重复导入跳过已存在 ID，保留用户后续编辑。

源码校验忽略 HTML 排版空白后逐字比较正文，代码块单独比较完整换行和缩进。编辑器验证逐篇检查 ProseMirror 结构、标题层级、正文、表格、代码和图片解码。桌面验证使用独立启动的本项目 WebView 调试端口 9346，命令为 `node scripts/verify-kamacoder-import.mjs --native`。
