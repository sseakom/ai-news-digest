# AGENTS.md

## 项目概述

每日 AI 新闻聚合推送服务。从国外英文科技媒体、AI 社区、论文/模型平台采集 RSS + JSON API → 按重要程度评分排序 → (可选) DeepSeek 翻译为中文标题+摘要简报 → 推送到手机。全程跑在 GitHub Actions 上，零第三方依赖，零服务器成本。

## 技术架构

**单文件架构**: 整个采集、评分、格式化、推送逻辑都在 `collect.py` 一个文件里，仅使用 Python 标准库 (`urllib`, `xml.etree`, `email.utils`, `json`, `re`, `html`)，无 `requirements.txt`。

**数据流**:
```
RSS 源 (FEEDS) → fetch_entries() → parse_feed() ─┐
                                                   ├→ _process_entries() → 关键词过滤 + 去重 + 时间窗口
JSON API 源 (JSON_SOURCES) → fetch_xxx() ─────────┘
  → score_item() 评分 → 排序 → 取 top N
  → llm_digest() (有 DeepSeek key) 或 plain_list() (回退)
  → build_text() 拼装 → push() 推送
```

## 信息源

7 个信息源，涵盖科技媒体、论文/模型平台和代码仓库，RSS 与 JSON API 混合:

| 来源 | 地址 | 类型 | 权重 | 过滤策略 |
|------|---------|------|------|---------|
| VentureBeat AI | `venturebeat.com/category/ai/feed/` | RSS | 10 | 全部视为相关 (纯 AI 频道) |
| Ars Technica | `feeds.arstechnica.com/arstechnica/index` | RSS | 9 | 关键词过滤 |
| The Verge | `theverge.com/rss/index.xml` | RSS | 8 | 关键词过滤 |
| Hacker News | `hnrss.org/frontpage` | RSS | 7 | 关键词过滤 |
| HuggingFace Papers | `huggingface.co/api/daily_papers` | JSON API | 8 | 全部视为相关 (纯 AI 论文) |
| HuggingFace Models | `huggingface.co/api/models?sort=trending` | JSON API | 7 | 全部视为相关 (趋势模型) |
| GitHub Trending | `api.github.com/search/repositories` | JSON API | 6 | 全部视为相关 (AI 仓库搜索) |

## 关键代码位置

| 功能 | 函数/变量 |
|------|----------|
| 信息源配置 (RSS) | `FEEDS` |
| 非 RSS 源配置 | `JSON_SOURCES` |
| 全相关来源集合 | `ALWAYS_RELEVANT` |
| AI 关键词 | `AI_KEYWORDS` |
| 回溯窗口 | `HOURS` |
| 最大条数 | `MAX_ITEMS` |
| 重要度门槛 | `MIN_SCORE` |
| 来源权重 | `SOURCE_WEIGHT` |
| 热门话题 (加分项) | `HOT_TOPICS` |
| 来源中文名 | `SOURCE_CN` |
| 星级转换 | `_score_stars()` |
| 重要程度评分 | `score_item()` |
| 日期解析 | `parse_date()` |
| RSS 采集 | `fetch_entries()` |
| JSON API 采集 | `fetch_json()` |
| HuggingFace 论文 | `fetch_hf_papers()` |
| HuggingFace 模型 | `fetch_hf_models()` |
| GitHub 趋势仓库 | `fetch_github_trending()` |
| 条目处理 (去重/过滤) | `_process_entries()` |
| 采集主逻辑 | `collect()` |
| DeepSeek 摘要 | `llm_digest()` |
| 纯列表格式化 | `plain_list()` |
| 拼装最终文本 | `build_text()` |
| Server酱推送 | `push_serverchan()` |
| ntfy 推送 | `push_ntfy()` |
| PushDeer 推送 | `push_pushdeer()` |
| 推送入口 (同时) | `push()` |

## 评分机制

`score_item()` 综合四个因素:

1. **来源基础分** (`SOURCE_WEIGHT`): VentureBeat AI 10 分最高，HuggingFace Papers 8 分，纯 AI 来源天然更重要
2. **话题加分** (`HOT_TOPICS`): 命中 `agent`/`embodied`/`agi`/`openai`/`anthropic`/`gpt-5`/`reasoning`/`local llm`/`open source`/`huggingface` 各 +5 分
3. **关键词密度**: 标题命中 `AI_KEYWORDS` 的数量，最多加 5 分
4. **时效加分**: 6 小时内 +3，12 小时内 +2，24 小时内 +1

星级映射: ≥20→★★★★★, 15-19→★★★★☆, 12-14→★★★☆☆, 10-11→★★☆☆☆, <10→★☆☆☆☆

推送门槛: 仅 `score >= MIN_SCORE`(默认 12, 即 ★★★☆☆ 及以上) 的新闻才入选, 按分数降序取最多 `MAX_ITEMS`(默认 10) 条; 当天达标新闻少则少推, 不凑数。

## 环境变量

| 变量 | 用途 | 默认值 | 必填 |
|------|------|--------|------|
| `SERVERCHAN_KEY` | Server酱 SendKey (微信推送, 主通道) | — | 推荐 |
| `NTFY_TOPIC` | ntfy 主题名 | `ai-news-7f3k9x` | 备选 |
| `NTFY_SERVER` | ntfy 服务器 | `https://ntfy.sh` | 否 |
| `PUSHDEER_KEY` | PushDeer key (已停更, 兜底) | — | 否 |
| `DEEPSEEK_API_KEY` | DeepSeek API key (可选, 翻译+摘要) | — | 否 |
| `GITHUB_TOKEN` | GitHub Token (可选, 提升 Search API 速率限制) | — | 否 |
| `MAX_ITEMS` | 摘要最多条数 | `10` | 否 |
| `HOURS` | 回溯窗口小时数 | `30` | 否 |

推送方式: Server酱与 ntfy 同时推送; 两者均失败时 PushDeer 兜底。

## 本地运行

```bash
# 零依赖, 直接跑
python collect.py

# 无推送配置时, 摘要直接输出到终端
# 测试采集和格式化 (不推送)
python -c "import collect; items=collect.collect(); print(collect.build_text(items))"

# 只看采集结果和评分
python -c "import collect; items=collect.collect(); [print(f'{i[\"score\"]:>3} {i[\"source\"]} | {i[\"title\"][:50]}') for i in items]"
```

网络请求需要能访问 `venturebeat.com`、`feeds.arstechnica.com`、`theverge.com`、`hnrss.org` (RSS 源), `huggingface.co`、`api.github.com` (JSON API 源) 及 `api.deepseek.com` (可选翻译摘要)。

## GitHub Actions

- 工作流文件: `.github/workflows/daily-digest.yml`
- 定时: 每天 23:30 UTC (07:30 北京时间)，GitHub 可能有数分钟延迟
- 手动触发: Actions 页 → Run workflow
- Python 版本: 3.11
- Actions 版本: `checkout@v5`, `setup-python@v6` (Node.js 24)
- Secrets 在仓库 Settings → Secrets and variables → Actions 配置
- 计划任务仅在默认分支生效；仓库 60 天无提交会被自动暂停

## 输出格式

Markdown 格式，适配 Server酱 (微信) 和 ntfy 的 markdown 渲染:

```
# AI 日报 2026-07-08

📋 今日采集 35 条，精选 6 条，按重要程度排序。

---

## 1. 中文标题

**★★★★★** · 权重 23 · VentureBeat

> 一句话摘要

🔗 [阅读原文](https://...)

---
```

新闻标题用 `###`，摘要用 `>` 引用，权重用加粗星级 + 数字、来源独立成行，条目间 `---` 分隔。

## 常见修改

**加 RSS 信息源**: 在 `FEEDS` 字典加一行 `"名称": "RSS_URL"`，同时在 `SOURCE_WEIGHT` 和 `SOURCE_CN` 加对应条目。如果新源是纯 AI 媒体，在 `ALWAYS_RELEVANT` 集合里加上。

**加 JSON API 信息源**: 写一个 `fetch_xxx()` 函数返回 `[{title, link, summary, date}]` 格式，在 `JSON_SOURCES` 字典加一行 `"名称": fetch_xxx`，同时在 `SOURCE_WEIGHT`、`SOURCE_CN` 加对应条目，按需加入 `ALWAYS_RELEVANT`。

**改关键词**: `AI_KEYWORDS` 列表和 `HOT_TOPICS` 列表直接增删。

**改推送时间**: 编辑 `daily-digest.yml` 的 `cron` 字段 (UTC 时间)。

**改条数/时间窗口**: 工作流里改 `MAX_ITEMS` 环境变量，或代码里改 `MAX_ITEMS`/`MIN_SCORE`/`HOURS` 默认值（`MIN_SCORE` 控制重要度门槛，默认12即★★★☆☆以上）。

**换推送通道**: 调整 `push()` 函数里的调用顺序，或增删 `push_*()` 函数。

## 注意事项

- `os.getenv("X") or "默认值"` 模式: GitHub Actions 空 secret 会变成空字符串，不能用 `os.getenv("X", "默认值")` (后者对空字符串不回退)
- Hacker News RSS 的 description 常为 'Comments', `_clean_abstract` 会过滤
- HuggingFace API 不需要认证，但本地网络可能无法直连 (GitHub Actions 无此问题)
- GitHub Search API 未认证时限 10 次/分钟，单次调用足够; 设置 `GITHUB_TOKEN` 可提升至 30 次/分钟
- `collect.py` 修改后本地跑一次验证: `python -c "import collect; print(collect.build_text(collect.collect()))"`
- README.md 内容较旧 (仍引用 arXiv/HN 等国际源)，以 `collect.py` 代码和本文件为准
