# 每日 AI 新闻推送

每天自动采集 AI 新闻 → (可选) DeepSeek 生成中文简报 → 推送到安卓手机。
全程跑在 GitHub Actions 上, 不依赖本机开机, 免费。

## 通道选择 (针对中国大陆 + 安卓)

| 渠道 | 说明 | 推荐度 |
| --- | --- | --- |
| **ntfy** | 免费、不依赖 Google 服务。手机装 ntfy app, 订阅同名 topic 即可。公共服务器 ntfy.sh 国内一般可达; 不稳可自托管。 | 首选 |
| **PushDeer** | 走微信推送, 国内最稳, 不用额外常驻 app。注册 pushdeer.com 拿 key。 | 备选/兜底 |

摘要模型用 **DeepSeek** (国内直连、OpenAI 兼容、便宜), 不用翻墙。

## 信息源

arXiv (cs.AI/CL/LG)、Hacker News、Reddit (MachineLearning / LocalLLaMA)、
OpenAI / Anthropic / Google DeepMind / Meta AI / Hugging Face 博客,
以及机器之心、量子位 (经 RSSHub)。
源列表在 [collect.py](collect.py) 顶部的 `FEEDS` 里, 直接改即可增删。

## 上手步骤

1. **新建 GitHub 仓库** (public 或 private 均可; public 不占 Actions 免费分钟数)。
2. 把本目录所有文件推进去, 保持路径:
   - `collect.py`
   - `.github/workflows/daily-digest.yml`
3. **配 Secrets** (仓库 Settings → Secrets and variables → Actions):
   - `NTFY_TOPIC`: 自己起一个复杂主题名, 例如 `ai-news-7f3k9x` (当密钥用, 别用常见词)
   - `DEEPSEEK_API_KEY`: 在 https://platform.deepseek.com 注册拿 key (可选, 不配则只推标题列表)
   - 若用 PushDeer 兜底, 加 `PUSHDEER_KEY`
4. **手机端 (ntfy)**: 装 ntfy app (F-Droid / GitHub releases / Play 商店),
   订阅与 `NTFY_TOPIC` 完全相同的 topic。
5. **测试**: 仓库 Actions 页 → "AI News Daily Digest" → Run workflow 手动跑一次,
   等手机收到推送即成功。之后每天 09:00 (北京) 自动跑。

## 注意事项

- GitHub 计划任务只在**默认分支**生效, 且仓库**60 天无提交会被自动暂停**计划任务,
  偶尔推一次代码即可保活。
- 计划任务有几分钟到几十分钟延迟属正常; 要更准时换成 VPS cron。
- RSSHub 公共实例 (`rsshub.app`) 偶发不稳定, 想更稳可自托管 RSSHub 后改 `FEEDS` 里的地址。
- Reddit / 部分博客可能对 GitHub IP 限流, 某源偶尔采空属正常, 其它源会兜住。
- ntfy topic 是公开的, 务必起复杂名; 想完全私密可自建 ntfy 服务端并设 `NTFY_SERVER`。
- 本地测试: `python collect.py` (零依赖)
  (需先 `export NTFY_TOPIC=...` 等; 无推送配置时会把摘要直接打到终端)。

## 自定义

- 改时间: 编辑 `daily-digest.yml` 的 `cron` (UTC)。
- 改条数: `MAX_ITEMS` 环境变量。
- 加源 / 改关键词: `collect.py` 的 `FEEDS` 和 `AI_KEYWORDS`。
- 想要纯标题 (不花钱、不调 LLM): 不设 `DEEPSEEK_API_KEY` 即可。
