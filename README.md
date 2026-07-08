# 每日 AI 新闻推送

每天自动采集国内权威科技媒体的 AI 新闻 → 按重要程度评分排序 → (可选) DeepSeek 生成中文简报 → 推送到手机。
全程跑在 GitHub Actions 上，零第三方依赖，零服务器成本。

## 推送渠道

| 渠道 | 说明 | 推荐度 |
| --- | --- | --- |
| **Server酱** | 走微信推送，国内最稳，不用额外装 app。注册 [sct.ftqq.com](https://sct.ftqq.com) 拿 SendKey。 | 首选 |
| **ntfy** | 免费、不依赖 Google 服务。手机装 ntfy app，订阅同名 topic 即可。公共服务器 ntfy.sh 国内一般可达；不稳可自托管。 | 备选 |
| **PushDeer** | 走微信推送，项目已停更，仅作兜底。 | 兜底 |

推送优先级：Server酱 → ntfy → PushDeer，第一个成功即停止。
摘要模型用 **DeepSeek**（国内直连、OpenAI 兼容、便宜），不用翻墙。

## 信息源

5 个国内权威科技媒体，均使用原生 RSS（不依赖 RSSHub 中转）：

| 来源 | RSS | 权重 | 说明 |
| --- | --- | --- | --- |
| 量子位 | `qbitai.com/feed` | 10 | 纯 AI 媒体，全部视为相关 |
| Solidot | `solidot.org/index.rss` | 9 | 科技新闻，关键词过滤 |
| 36氪 | `36kr.com/feed` | 8 | 科技商业，关键词过滤 |
| IT之家 | `ithome.com/rss/` | 7 | 科技资讯，关键词过滤 |
| 钛媒体 | `tmtpost.com/rss.xml` | 7 | 财经科技，关键词过滤 |

机器之心已关闭免费 RSS，暂不可用。源列表在 [collect.py](collect.py) 的 `FEEDS` 里，直接改即可增删。

## 推送效果

```markdown
# AI 日报 2026-07-08

今日采集 35 条 · 精选 6 条 · 按重要程度排序

---

## 1. 从共识到非共识：科技有「联想」沙龙首场活动直击具身智能产业化"三大困惑"
★★★★★ 权重 23 · 量子位

> "共识与非共识"的深度思辨

🔗 [阅读原文](https://www.qbitai.com/...)

---
```

标题加粗放大（`##`），摘要用引用缩进区分（`>`），权重用星级可视化，条目间 `---` 分隔。

## 上手步骤

1. **Fork 或新建 GitHub 仓库**（public 不占 Actions 免费分钟数）。
2. 把本目录所有文件推进去，保持路径：
   - `collect.py`
   - `.github/workflows/daily-digest.yml`
3. **配 Secrets**（仓库 Settings → Secrets and variables → Actions）：
   - `SERVERCHAN_KEY`：在 [sct.ftqq.com](https://sct.ftqq.com) 注册拿 SendKey（推荐，微信推送）
   - `DEEPSEEK_API_KEY`：在 [platform.deepseek.com](https://platform.deepseek.com) 注册拿 key（可选，不配则只推标题列表）
   - `NTFY_TOPIC`：自己起一个复杂主题名，例如 `ai-news-7f3k9x`（备选，需手机装 ntfy app 订阅同名 topic）
   - 若用 PushDeer 兜底，加 `PUSHDEER_KEY`
4. **测试**：仓库 Actions 页 → "AI News Daily Digest" → Run workflow 手动跑一次，等手机收到推送即成功。之后每天 09:00（北京）自动跑。

## 环境变量

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `SERVERCHAN_KEY` | Server酱 SendKey（微信推送，主通道） | — |
| `NTFY_TOPIC` | ntfy 主题名 | `ai-news-7f3k9x` |
| `NTFY_SERVER` | ntfy 服务器 | `https://ntfy.sh` |
| `PUSHDEER_KEY` | PushDeer key（已停更，兜底） | — |
| `DEEPSEEK_API_KEY` | DeepSeek API key（可选，生成中文摘要） | — |
| `MAX_ITEMS` | 摘要最多条数 | `6` |
| `HOURS` | 回溯窗口小时数 | `30` |

## 注意事项

- GitHub 计划任务只在**默认分支**生效，且仓库 **60 天无提交会被自动暂停**，偶尔推一次代码即可保活。
- 计划任务有几分钟到几十分钟延迟属正常；要更准时换成 VPS cron。
- 某源偶尔采空属正常（RSS 波动、限流），其它源会兜住。
- ntfy topic 是公开的，务必起复杂名；想完全私密可自建 ntfy 服务端并设 `NTFY_SERVER`。
- 本地测试：`python collect.py`（零依赖，无推送配置时摘要直接输出到终端）。

## 自定义

- 改时间：编辑 `daily-digest.yml` 的 `cron`（UTC）。
- 改条数：`MAX_ITEMS` 环境变量。
- 加源 / 改关键词：`collect.py` 的 `FEEDS` 和 `AI_KEYWORDS`。
- 改评分权重 / 热门话题：`collect.py` 的 `SOURCE_WEIGHT` 和 `HOT_TOPICS`。
- 想要纯标题（不花钱、不调 LLM）：不设 `DEEPSEEK_API_KEY` 即可。

更多细节见 [AGENTS.md](AGENTS.md)。
