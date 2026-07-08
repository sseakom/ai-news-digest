# AGENTS.md

## 项目概述

每日 AI 新闻聚合推送服务。从国内权威科技媒体采集 RSS → 按重要程度评分排序 → (可选) DeepSeek 生成中文简报 → 推送到手机。全程跑在 GitHub Actions 上，零第三方依赖，零服务器成本。

## 技术架构

**单文件架构**: 整个采集、评分、格式化、推送逻辑都在 `collect.py` 一个文件里，仅使用 Python 标准库 (`urllib`, `xml.etree`, `email.utils`, `json`, `re`, `html`)，无 `requirements.txt`。

**数据流**:
```
RSS 源 → fetch_entries() → parse_feed() → 关键词过滤 + 去重 + 时间窗口
  → score_item() 评分 → 排序 → 取 top N
  → llm_digest() (有 DeepSeek key) 或 plain_list() (回退)
  → build_text() 拼装 → push() 推送
```

## 信息源

5 个国内权威科技媒体，均使用原生 RSS（不依赖 RSSHub 中转）:

| 来源 | RSS 地址 | 权重 | 过滤策略 |
|------|---------|------|---------|
| 量子位 | `https://www.qbitai.com/feed` | 10 | 全部视为相关 (纯 AI 媒体) |
| Solidot | `https://www.solidot.org/index.rss` | 9 | 关键词过滤 |
| 36氪 | `https://36kr.com/feed` | 8 | 关键词过滤 |
| IT之家 | `https://www.ithome.com/rss/` | 7 | 关键词过滤 |
| 钛媒体 | `https://www.tmtpost.com/rss.xml` | 7 | 关键词过滤 |

机器之心 (`jiqizhixin.com`) 已关闭免费 RSS（`/rss` 端点重定向到数据服务页），且所有公共 RSSHub 实例均不可用，暂无法接入。

## 关键代码位置

| 功能 | 函数/变量 | 约在行 |
|------|----------|-------|
| 信息源配置 | `FEEDS` | ~36 |
| AI 关键词 | `AI_KEYWORDS` | ~51 |
| 回溯窗口 | `HOURS` | ~60 |
| 最大条数 | `MAX_ITEMS` | ~61 |
| 来源权重 | `SOURCE_WEIGHT` | ~64 |
| 热门话题 (加分项) | `HOT_TOPICS` | ~72 |
| 来源中文名 | `SOURCE_CN` | ~86 |
| 星级转换 | `_score_stars()` | ~108 |
| 重要程度评分 | `score_item()` | ~120 |
| 日期解析 (含 36氪特殊格式) | `parse_date()` | ~143 |
| RSS/Atom 解析 | `parse_feed()` | ~180 |
| 采集主逻辑 | `collect()` | ~240 |
| DeepSeek 摘要 | `llm_digest()` | ~284 |
| 纯列表格式化 | `plain_list()` | ~331 |
| 拼装最终文本 | `build_text()` | ~343 |
| Server酱推送 | `push_serverchan()` | ~352 |
| ntfy 推送 | `push_ntfy()` | ~372 |
| PushDeer 推送 | `push_pushdeer()` | ~390 |
| 推送入口 (按优先级) | `push()` | ~410 |

## 评分机制

`score_item()` 综合四个因素:

1. **来源基础分** (`SOURCE_WEIGHT`): 量子位 10 分最高，纯 AI 媒体的报道天然更重要
2. **话题加分** (`HOT_TOPICS`): 命中 `agent`/`智能体`/`embodied`/`具身`/`具身智能` 各 +5 分
3. **关键词密度**: 标题命中 `AI_KEYWORDS` 的数量，最多加 5 分
4. **时效加分**: 6 小时内 +3，12 小时内 +2，24 小时内 +1

星级映射: ≥20→★★★★★, 15-19→★★★★☆, 12-14→★★★☆☆, 10-11→★★☆☆☆, <10→★☆☆☆☆

## 环境变量

| 变量 | 用途 | 默认值 | 必填 |
|------|------|--------|------|
| `SERVERCHAN_KEY` | Server酱 SendKey (微信推送, 主通道) | — | 推荐 |
| `NTFY_TOPIC` | ntfy 主题名 | `ai-news-7f3k9x` | 备选 |
| `NTFY_SERVER` | ntfy 服务器 | `https://ntfy.sh` | 否 |
| `PUSHDEER_KEY` | PushDeer key (已停更, 兜底) | — | 否 |
| `DEEPSEEK_API_KEY` | DeepSeek API key (可选, 生成中文摘要) | — | 否 |
| `MAX_ITEMS` | 摘要最多条数 | `6` | 否 |
| `HOURS` | 回溯窗口小时数 | `30` | 否 |

推送优先级: Server酱 → ntfy → PushDeer，第一个成功即停止。

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

网络请求需要能访问 `qbitai.com`、`solidot.org`、`36kr.com`、`ithome.com`、`tmtpost.com`。

## GitHub Actions

- 工作流文件: `.github/workflows/daily-digest.yml`
- 定时: 每天 01:00 UTC (09:00 北京时间)，GitHub 可能有数分钟延迟
- 手动触发: Actions 页 → Run workflow
- Python 版本: 3.11
- Actions 版本: `checkout@v5`, `setup-python@v6` (Node.js 24)
- Secrets 在仓库 Settings → Secrets and variables → Actions 配置
- 计划任务仅在默认分支生效；仓库 60 天无提交会被自动暂停

## 输出格式

Markdown 格式，适配 Server酱 (微信) 和 ntfy 的 markdown 渲染:

```
# AI 日报 2026-07-08

今日采集 35 条 · 精选 6 条 · 按重要程度排序

---

## 1. 中文标题
★★★★★ 权重 23 · 量子位

> 一句话摘要

🔗 [阅读原文](https://...)

---
```

标题用 `##` (加粗放大)，摘要用 `>` 引用 (缩进区分)，权重用星级 + 数字，条目间 `---` 分隔。

## 常见修改

**加信息源**: 在 `FEEDS` 字典加一行 `"名称": "RSS_URL"`，同时在 `SOURCE_WEIGHT` 和 `SOURCE_CN` 加对应条目。如果新源是纯 AI 媒体，在 `collect()` 的 `always_relevant` 判断里加上。

**改关键词**: `AI_KEYWORDS` 列表和 `HOT_TOPICS` 列表直接增删。中文关键词加在中文段，英文加在英文段。

**改推送时间**: 编辑 `daily-digest.yml` 的 `cron` 字段 (UTC 时间)。

**改条数/时间窗口**: 工作流里改 `MAX_ITEMS` 环境变量，或代码里改 `MAX_ITEMS`/`HOURS` 默认值。

**换推送通道**: 调整 `push()` 函数里的调用顺序，或增删 `push_*()` 函数。

## 注意事项

- `os.getenv("X") or "默认值"` 模式: GitHub Actions 空 secret 会变成空字符串，不能用 `os.getenv("X", "默认值")` (后者对空字符串不回退)
- 36氪的 RSS 日期格式非标准 (`2026-07-08 11:03:34  +0800`)，`parse_date()` 里有 regex 回退处理
- 量子位 RSS 的 `<description>` 常为空，摘要会缺失，标题本身信息量足够
- `collect.py` 修改后本地跑一次验证: `python -c "import collect; print(collect.build_text(collect.collect()))"`
- README.md 内容较旧 (仍引用 arXiv/HN/Reddit 等国际源)，以 `collect.py` 代码和本文件为准
