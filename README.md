# GitHub Trending Analyzer · GitHub 热门项目分析

抓取 GitHub 趋势榜，按四维评分（S/A/B/C）输出项目清单与可视化报告。仓库包含两套实现：

| 目录 | 形态 | 适合场景 |
|---|---|---|
| [`github-trending-analyzer/`](github-trending-analyzer/) | Agent Skill（提示词资产） | **深读**：真的打开仓库页读 README、判断项目价值、写个性化点评 |
| [`app/`](app/) | 本地可视化应用（Flask + pywebview） | **快筛**：选周期即出榜，可排序筛选、可复算、可导出 HTML/CSV |

两者共用同一套评分口径，输出可以互相衔接：应用导出的 CSV 丢回对话，让 skill 在候选清单上做深读。

## 效果预览

| Skill 输出的 HTML 报告 | 应用的评分看板 | 应用的官方趋势页视图 |
|---|---|---|
| ![报告预览](docs/demo-report.png) | ![评分看板](docs/app-preview.png) | ![官方趋势页视图](docs/official-preview.png) |

## 快速开始

### 用 Skill（无需依赖）

把 `github-trending-analyzer/` 整个文件夹放入 Agent 的技能目录即可：

| 平台 | 安装位置 |
|---|---|
| 豆包工作 | `workspace/.user_skills/` |
| Claude Code | `~/.claude/skills/` |
| WorkBuddy | `~/.workbuddy/skills/` |

然后对 Agent 说：「分析今日 GitHub 热门项目」「本周 Python 热榜有哪些好项目」。

### 用可视化应用

```bash
cd app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py --window        # 原生窗口；去掉 --window 则用浏览器打开
```

详见 [app/README.md](app/README.md)。

## 评分细则

| 维度 | 分值 | 依据 |
|---|---|---|
| 受欢迎度 | 25 | 周期新增 Star、总 Star |
| 维护活跃度 | 25 | 最近提交、Release、Issue 规模 |
| 实用可应用性 | 30 | 用途明确度、文档与示例、可落地性 |
| 技术栈质量 | 20 | 技术主流性、许可证、CI、工程规范 |

评级：S ≥ 90 强烈推荐 / A 75–89 推荐 / B 60–74 值得关注 / C < 60 一般。
完整打分参考见 [`github-trending-analyzer/references/scoring.md`](github-trending-analyzer/references/scoring.md)。

## 输出

**Skill**：对话内清单（总分、四维得分、评级、亮点、使用方式、点评）+ 独立 HTML 报告 `github-trending-YYYY-MM-DD-{today|weekly|monthly}.html`。

**应用**提供两个视图：

| 视图 | 内容 |
|---|---|
| 评分看板 | 卡片按总分排序，含分类标签、中文简介、四维评分与逐条评分依据；支持评级筛选、排序、搜索、分类板块栏 |
| 官方趋势页 | `github.com/trending` 原始榜单顺序，含真实的「周期新增 Star」；同样叠加四维评分、分类与点评，可一键打开原页、单独手动刷新 |

导出 HTML 报告与 CSV；CSV 含分类、中英简介、四维分项与点评。

示例报告：[docs/index.html](docs/index.html)（2026-11-01 今日榜实跑输出）。

## 目录结构

```
.
├── README.md
├── LICENSE
├── docs/
│   ├── demo-report.png       # Skill 输出报告示例
│   ├── index.html            # 示例报告（可打开）
│   ├── app-preview.png       # 评分看板界面
│   └── official-preview.png  # 官方趋势页视图界面
├── github-trending-analyzer/ # Agent Skill
│   ├── SKILL.md              # 工作流、输入参数、输出规范
│   ├── references/scoring.md # 评分细则
│   └── assets/template.html  # 报告模板
└── app/                      # 可视化应用
    ├── app.py                # Flask 服务与导出
    ├── core.py               # 抓取、分类、打分
    ├── translate.py          # 简介翻译（含缓存）
    ├── templates/            # 看板与报告模板
    └── assets/
```

## 数据来源与边界

- 榜单优先取 `github.com/trending` 官方页；网页不可达时应用会降级为 GitHub API 推算（按「创建时间 + Star 增速」估算），并在界面标注来源，**不把推算说成官方数据**；
- 官方页在部分网络下间歇性可达：实时抓取失败时展示本地快照，界面用黄色提示条写明快照时间，**不把缓存冒充实时数据**；连续失败后熔断 5 分钟，「手动刷新」可强制重试；
- 官方视图与评分看板共用同一套评分与分类函数，同一项目两处结果一致；手动刷新只更新官方榜单，不重跑评分；
- Star 数、评分、描述均来自实际抓取，缺失字段标注「未获取到」，禁止编造；配额不足导致元数据缺失时界面会明确提示「评级仅供参考」；
- Star 数、评分、描述均来自实际抓取，缺失字段标注「未获取到」，禁止编造；
- 应用的打分是规则计算，可复算、每次一致，但不等于人工判断；Skill 的点评由 LLM 生成，代价是每次结果可能有差异。

## License

MIT，详见 [LICENSE](LICENSE)。
