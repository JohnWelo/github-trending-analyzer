---
name: github-trending-analyzer
description: 分析 GitHub Trending 热门项目并输出可视化评分报告。当用户要求查看、分析、整理 GitHub 趋势榜/热榜、今日/本周/本月热门开源项目，或希望获得一份带多维度评分、项目清单与推荐结论的可视化报告时使用。工作流：浏览 GitHub Trending 抓取项目列表 → 打开仓库页读取 README 与元数据 → 按受欢迎度/维护活跃度/实用可应用性/技术栈质量四维度评分 → 输出结构化项目清单 → 生成带评分柱状图的项目卡片网页。
---

# GitHub Trending 热门项目分析

将 GitHub 趋势榜中的项目自动整理为「评分 + 清单 + 可视化网页」的分析报告，让用户一眼判断项目价值，免去逐个翻仓库的重复劳动。

## 工作流总览

1. 确定分析范围（时间范围 / 语言过滤 / 数量上限）
2. 浏览 GitHub Trending 页面，抓取项目列表
3. 逐个打开仓库页，读取 README 与仓库元数据
4. 按评分细则打分（先读 `references/scoring.md`）
5. 生成可视化网页报告（模板见 `assets/template.html`）

## 输入参数

- **时间范围**（默认 today）：`today`（今日）/ `weekly`（本周）/ `monthly`（本月）
- **语言过滤**（可选）：如 Python、TypeScript，不填则全语言
- **数量上限**（默认 25）：输出项目数，超过时按总分取前 N

## 执行步骤

### 1. 访问 GitHub Trending

用浏览器打开对应页面：

- 今日：`https://github.com/trending`
- 本周：`https://github.com/trending?since=weekly`
- 本月：`https://github.com/trending?since=monthly`

指定语言时在路径中加语言名，如 `https://github.com/trending/python?since=weekly`。

### 2. 抓取项目列表

从页面中提取每个仓库的以下字段，缺失的字段记为空并在报告中标注「未获取到」：

- 仓库全名（owner/repo）
- 一句话描述
- 主要语言
- 总 Star 数
- 周期内新增 Star 数（如 `1,234 stars today`）

### 3. 读取仓库关键信息

打开各仓库主页 `https://github.com/<owner>/<repo>`，读取：

- README 首屏内容：项目定位、解决的问题、核心能力（长 README 读开头 2-3 段和功能列表即可）
- 最近 commit 时间、最近 release（判断维护活跃度）
- Star / Fork 数
- 许可证（LICENSE）
- 是否展示 used-by / 引用案例

若某仓库页无法打开或信息缺失，用 GitHub 搜索页 `https://github.com/search?q=<repo>` 补齐，不编造数据。

### 4. 评分

打开 `references/scoring.md`，按四个维度（受欢迎度 25 / 维护活跃度 25 / 实用可应用性 30 / 技术栈质量 20）逐项打分，计算总分（0-100）与评级（S/A/B/C），并为每个项目写 1-2 句点评（价值点 + 风险点）。

### 5. 生成可视化网页报告

打开 `assets/template.html` 阅读模板结构与占位符说明，将真实数据填入，输出独立 HTML 文件保存到用户项目目录，用 present_files 交付。

## 输出规范

### 对话内文本清单

按总分从高到低列出每个项目：

- **项目名**（语言 · 总分/评级）
- 简介：一句话
- 亮点：2-3 条
- 使用方式：安装/集成一句话
- 点评：价值点 / 风险点

### 网页报告

- 卡片按总分降序排列（模板内置按 data-score 排序的脚本，生成时按总分顺序输出）
- 每张卡片：仓库名（链接）、语言、Star 与周期增量、描述、总分与评级、四个维度小分条形图、亮点列表、使用方式、点评
- 保留模板既有样式，不更换主题、不删减模板结构
- 文件命名：`github-trending-<yyyy-mm-dd>-<today|weekly|monthly>.html`

## 注意事项

- 数据必须来自实际抓取，禁止编造 star 数、评分或描述；不确定的数值标注「≈」并注明依据
- 页面结构变化导致抓取失败时，改用 GitHub 搜索页或仓库页兜底，不终止任务
- 周期性自动运行（如每日分析）时使用默认参数：today、全语言、25 条
- 语言过滤时忽略 Unknown 语言（README/文档类仓库）

## 配套可视化应用（快筛用）

同一套评分口径另有一份本地可视化实现（本仓库 `app/` 目录），用户如需「秒级出榜 + 交互筛选 + 导出」而非深读报告，可引导其使用：

- 启动：`cd app && python app.py --window`（依赖 flask / requests / pywebview）
- 差别：应用用规则打分（可复算、每次一致），本 skill 用 LLM 判断（能读 README、给个性化点评）。需要深入判断时，让应用导出 CSV 后再由本 skill 深读。
