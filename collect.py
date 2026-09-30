#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日 AI 新闻聚合 + 推送 (仅用 Python 标准库, 零第三方依赖)。

特性:
  - 事件聚簇: 多个来源报道同一事件时自动合并 (标题 token Jaccard 相似度),
    代表条目携带 cluster_sources/cluster_count/cluster_links 字段
  - 信源分级: primary(一手原始) > media(媒体报道) > community(社区讨论), 评分时分级加分
  - 多源热度加成: 被 N 个独立来源报道的事件额外 +(N-1)*3 分
  - LLM 评分增强: 设置 DEEPSEEK_API_KEY 后, DeepSeek 除翻译摘要外还为每条新闻
    输出 1-10 重要性评分 (llm_score), 直接加到权重分上并重新排序

推送到手机 (按优先级依次尝试):
  Server酱: 设置 SERVERCHAN_KEY 即可走微信推送 (国内推荐)
  ntfy    : 设置 NTFY_TOPIC (主题名当密钥), 默认服务器 https://ntfy.sh
  PushDeer: 设置 PUSHDEER_KEY (项目已停更, 仅作兜底)

可选 AI 翻译摘要: 设置 DEEPSEEK_API_KEY 后, 用 DeepSeek 将英文新闻翻译成中文标题+摘要简报;
不设置则只推送英文原标题列表 + 来源 + 链接。

环境变量:
  SERVERCHAN_KEY    Server酱 SendKey (推荐, 微信推送)
  NTFY_TOPIC        ntfy 主题名
  NTFY_SERVER       ntfy 服务器, 默认 https://ntfy.sh
  PUSHDEER_KEY      PushDeer 推送 key (已停更, 仅作兜底)
  DEEPSEEK_API_KEY  DeepSeek API key (可选, 用于英文翻译+中文摘要)
  MAX_ITEMS         摘要最多包含条数, 默认 10
  HOURS             回溯窗口小时数, 默认 30 (略大于 24 容忍时区/延迟)
"""

import os
import re
import sys
import html
import json
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import email.utils
from datetime import datetime, timezone, timedelta

# ---------- 配置: 信息源 ----------
# VentureBeat AI (纯 AI 频道) 默认全部视为相关; 其余来源按关键词过滤。
FEEDS = {
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/index",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "Hacker News": "https://hnrss.org/frontpage",
}

AI_KEYWORDS = [
    "ai",
    "a.i.",
    "artificial intelligence",
    "llm",
    "gpt",
    "claude",
    "gemini",
    "llama",
    "qwen",
    "deepseek",
    "diffusion",
    "transformer",
    "machine learning",
    "deep learning",
    "neural",
    "agi",
    "rag",
    "fine-tun",
    "finetun",
    "multimodal",
    "reinforcement learning",
    "rlhf",
    "vision-language",
    "vision language",
    "mcp",
    "reasoning",
    "scaling law",
    "generative",
    "text-to-image",
    "agent",
    "embodied",
    "embodied intelligence",
    "chatbot",
    "copilot",
    "openai",
    "anthropic",
    "midjourney",
    "stable diffusion",
    "sora",
    "grok",
    "mistral",
    "inference",
    "training",
    "benchmark",
    "alignment",
    "safety",
    "huggingface",
    "hugging face",
    "local llm",
    "open source",
    "open-source",
    "fine-tuned",
    "vllm",
    "ollama",
    "langchain",
    "diffusion model",
    "language model",
    "foundation model",
    "moe",
    "mixture of experts",
]

HOURS = int(os.getenv("HOURS") or "30")
MAX_ITEMS = int(os.getenv("MAX_ITEMS") or "10")
MIN_SCORE = 12  # 重要程度门槛: 仅推送 ★★★☆☆(score>=12) 及以上, 最多 MAX_ITEMS 条

# ---------- 重要程度评分 ----------
SOURCE_WEIGHT = {
    "VentureBeat AI": 10,
    "Ars Technica": 9,
    "The Verge": 8,
    "Hacker News": 7,
    "HuggingFace Papers": 8,
    "HuggingFace Models": 7,
    "GitHub Trending": 6,
}
# 信源分级: primary(一手原始) > media(媒体报道) > community(社区讨论)
SOURCE_TIER = {
    "HuggingFace Papers": "primary",
    "GitHub Trending": "primary",
    "VentureBeat AI": "media",
    "Ars Technica": "media",
    "The Verge": "media",
    "Hacker News": "community",
    "HuggingFace Models": "primary",
}
# 信源分级加分: 一手信息源更重要
TIER_BONUS = {"primary": 2, "media": 1, "community": 0}
# 用户重点关注的话题, 命中加分
HOT_TOPICS = [
    "agent",
    "embodied",
    "agi",
    "openai",
    "anthropic",
    "gpt-5",
    "reasoning",
    "local llm",
    "open source",
    "huggingface",
]
ABSTRACT_CAP = 400  # 单条送入 LLM 的摘要字符上限
# 全部视为 AI 相关的来源 (不做关键词过滤)
ALWAYS_RELEVANT = {
    "VentureBeat AI",
    "HuggingFace Papers",
    "HuggingFace Models",
    "GitHub Trending",
}
# 跳过时间窗口过滤的来源 (自带时间限定或非时效性)
SKIP_TIME_FILTER = {
    "HuggingFace Papers",
    "HuggingFace Models",
    "GitHub Trending",
}
BEIJING = timezone(timedelta(hours=8))
UA = "ai-news-digest/1.0 (+https://github.com)"


def log(msg):
    print(msg, flush=True)


def strip_html(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


SOURCE_CN = {
    "VentureBeat AI": "VentureBeat",
    "Ars Technica": "Ars Technica",
    "The Verge": "The Verge",
    "Hacker News": "Hacker News",
    "HuggingFace Papers": "HF Papers",
    "HuggingFace Models": "HF Models",
    "GitHub Trending": "GitHub Trending",
}


def _source_cn(name):
    return SOURCE_CN.get(name, name)


def _clean_abstract(s):
    """过滤无意义的摘要内容 (如 HN 的 'Comments')。"""
    if not s:
        return ""
    s = s.strip()
    if s.lower() in ("comments", "comment", "[removed]"):
        return ""
    return s


def _score_stars(score):
    """根据权重分值返回星级, 用于视觉化重要程度。"""
    if score >= 20:
        return "★★★★★"
    if score >= 15:
        return "★★★★☆"
    if score >= 12:
        return "★★★☆☆"
    if score >= 10:
        return "★★☆☆☆"
    return "★☆☆☆☆"


# 标题归一化时剔除的常见停用词 (不影响事件语义)
_TITLE_STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "on",
    "in",
    "for",
    "to",
    "with",
    "and",
    "or",
    "is",
    "are",
    "will",
    "be",
    "new",
    "says",
    "reports",
    "at",
    "by",
    "from",
    "as",
    "it",
    "its",
    "this",
    "that",
    "after",
    "over",
    "how",
    "why",
    "what",
}


def _normalize_title(title):
    """标题归一化: 转小写、去标点、去停用词, 返回 token 集合。"""
    low = title.lower()
    tokens = re.findall(r"[a-z0-9]+", low)
    return {t for t in tokens if t not in _TITLE_STOPWORDS}


def _jaccard(set_a, set_b):
    """计算两个 token 集合的 Jaccard 相似度。"""
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    if not inter:
        return 0.0
    return inter / len(set_a | set_b)


def _cluster_events(items, threshold=0.5):
    """事件聚簇: 多个来源报道同一事件时合并为一个代表条目。

    贪心法: 逐个 item 与已有 cluster 的代表比较 Jaccard 相似度,
    > threshold 且来源不同则归入该 cluster, 否则新建。
    每个 cluster 保留先到的 item 作为代表 (此时未评分, 用原始顺序),
    代表上追加 cluster_sources/cluster_count/cluster_links 字段;
    单来源 cluster 保持原样。"""
    clusters = []  # [{rep, tokens, members}]
    for item in items:
        tokens = _normalize_title(item["title"])
        target = None
        for c in clusters:
            # 同来源的精确去重已由 _process_entries 处理, 聚簇只在跨来源间进行
            if c["rep"]["source"] == item["source"]:
                continue
            if _jaccard(tokens, c["tokens"]) > threshold:
                target = c
                break
        if target is None:
            clusters.append({"rep": item, "tokens": tokens, "members": [item]})
        else:
            target["members"].append(item)
    result = []
    for c in clusters:
        rep = c["rep"]
        # 按来源去重收集 (source, link) 对, 保持两个列表对齐
        seen_src = {}
        for m in c["members"]:
            seen_src.setdefault(m["source"], m.get("link", ""))
        sources = list(seen_src)
        links = [seen_src[s] for s in sources]
        if len(sources) > 1:
            rep["cluster_sources"] = sources
            rep["cluster_count"] = len(sources)
            rep["cluster_links"] = links
        result.append(rep)
    return result


def score_item(item):
    """计算新闻重要程度权重 (越高越重要)。"""
    score = SOURCE_WEIGHT.get(item["source"], 5)
    # 信源分级加分: primary(一手原始) +2, media(媒体报道) +1, community +0
    score += TIER_BONUS.get(SOURCE_TIER.get(item["source"], "community"), 0)
    low = item["title"].lower()
    for topic in HOT_TOPICS:
        if topic in low:
            score += 5
    score += min(sum(1 for k in AI_KEYWORDS if k in low), 5)
    date = item.get("date")
    if date:
        try:
            dt = datetime.fromisoformat(date)
            hours_ago = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            if hours_ago <= 6:
                score += 3
            elif hours_ago <= 12:
                score += 2
            elif hours_ago <= 24:
                score += 1
        except Exception:
            pass
    # 多源覆盖热度加成: 被 N 个独立来源报道的事件 +(N-1)*3 分
    sources = item.get("cluster_sources")
    if sources and len(sources) > 1:
        score += (len(sources) - 1) * 3
    return score


def _local(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _alltext(el):
    if el is None:
        return ""
    return "".join(el.itertext())


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    # 部分源使用非标准日期格式
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*([+-]\d{4})", s)
    if m:
        dt_str, tz_str = m.groups()
        tz_str = tz_str[:3] + ":" + tz_str[3:]
        try:
            return datetime.fromisoformat(f"{dt_str}{tz_str}")
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_rss_item(it):
    title = link = desc = date = ""
    for c in it:
        n = _local(c.tag)
        if n == "title":
            title = _alltext(c)
        elif n == "link":
            link = (c.text or "").strip() or (c.attrib.get("href") or "").strip()
        elif n in ("description", "summary"):
            desc = _alltext(c)
        elif n in ("date", "pubDate", "published"):
            date = c.text or ""
    return {"title": title, "link": link, "summary": desc, "date": parse_date(date)}


def _parse_atom_entry(it):
    title = link = summ = date = ""
    for c in it:
        n = _local(c.tag)
        if n == "title":
            title = _alltext(c)
        elif n == "link":
            href = (c.attrib.get("href") or "").strip()
            if href and not link:
                link = href
        elif n in ("summary", "content"):
            if not summ:
                summ = _alltext(c)
        elif n in ("updated", "published"):
            if not date:
                date = c.text or ""
    return {"title": title, "link": link, "summary": summ, "date": parse_date(date)}


def parse_feed(raw):
    """解析 RSS 2.0 / Atom / RSS 1.0 (RDF), 返回 [{title,link,summary,date}]。"""
    root = ET.fromstring(raw)
    t = _local(root.tag)
    items = []
    if t == "rss":
        for ch in root:
            if _local(ch.tag) == "channel":
                for it in ch:
                    if _local(it.tag) == "item":
                        items.append(_parse_rss_item(it))
    elif t == "feed":  # Atom
        for it in root:
            if _local(it.tag) == "entry":
                items.append(_parse_atom_entry(it))
    elif t == "RDF":  # RSS 1.0
        for it in root:
            if _local(it.tag) == "item":
                items.append(_parse_rss_item(it))
    else:  # 兜底: 任意位置找 item/entry
        for it in root.iter():
            if _local(it.tag) == "item":
                items.append(_parse_rss_item(it))
            elif _local(it.tag) == "entry":
                items.append(_parse_atom_entry(it))
    return items


def fetch_entries(url, retries=2):
    """获取 RSS/Atom feed, 429 限流时自动重试。"""
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept-Encoding": "identity"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return parse_feed(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(3 * (attempt + 1))  # 3s, 6s 退避
                continue
            raise


def fetch_json(url, headers=None):
    """获取 JSON API 并返回解析后的 Python 对象。"""
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_hf_papers():
    """HuggingFace 每日热门论文 (JSON API, 非 RSS)。"""
    data = fetch_json("https://huggingface.co/api/daily_papers")
    # API 可能返回数组或 {results: [...]} 分页包装
    if isinstance(data, dict):
        data = data.get("results", [])
    items = []
    for entry in data:
        paper = entry.get("paper", entry) if isinstance(entry, dict) else {}
        pid = paper.get("id", "") or entry.get("id", "")
        title = entry.get("title", "") or paper.get("title", "")
        summary = paper.get("summary", "") or entry.get("summary", "")
        upvotes = paper.get("upvotes", 0) or entry.get("upvotes", 0)
        # 日期在条目顶层: publishedAt (非 paper.published)
        date_str = entry.get("publishedAt", "") or paper.get("published", "")
        if upvotes:
            summary = f"👍 {upvotes} · {summary}" if summary else f"👍 {upvotes}"
        items.append(
            {
                "title": title,
                "link": f"https://huggingface.co/papers/{pid}" if pid else "",
                "summary": summary,
                "date": parse_date(date_str),
            }
        )
    return items


def fetch_hf_models():
    """HuggingFace 趋势模型 (JSON API, 非 RSS)。"""
    # sort=trending 可能在部分 API 版本返回 400, 回退 sort=likes
    try:
        data = fetch_json("https://huggingface.co/api/models?sort=trending&limit=10")
    except Exception:
        data = fetch_json(
            "https://huggingface.co/api/models?sort=likes&direction=-1&limit=10"
        )
    if isinstance(data, dict):
        data = data.get("models", data.get("results", []))
    items = []
    for m in data:
        mid = m.get("id", "")
        tags = m.get("tags", [])
        pipeline = m.get("pipeline_tag", "")
        downloads = m.get("downloads", 0)
        likes = m.get("likes", 0)
        parts = []
        if pipeline:
            parts.append(f"pipeline: {pipeline}")
        if tags:
            parts.append(f"tags: {', '.join(tags[:5])}")
        if downloads or likes:
            parts.append(f"⬇ {downloads} ❤ {likes}")
        summary = " · ".join(parts) if parts else mid
        items.append(
            {
                "title": f"Trending model: {mid}" if mid else "",
                "link": f"https://huggingface.co/{mid}" if mid else "",
                "summary": summary,
                "date": parse_date(m.get("createdAt", "")),
            }
        )
    return items


def fetch_github_trending():
    """GitHub 近 7 天创建、按 star 降序的 AI 仓库 (Search API)。"""
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    query = urllib.parse.quote(f"ai OR llm OR gpt OR transformer created:>{since}")
    url = (
        f"https://api.github.com/search/repositories"
        f"?q={query}&sort=stars&order=desc&per_page=10"
    )
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = fetch_json(url, headers=headers)
    items = []
    for repo in data.get("items", []):
        name = repo.get("full_name", "")
        desc = repo.get("description", "") or ""
        stars = repo.get("stargazers_count", 0)
        lang = (repo.get("language") or "").strip()
        items.append(
            {
                "title": f"{name}: {desc}" if desc else name,
                "link": repo.get("html_url", ""),
                "summary": f"⭐ {stars} · {desc}"[:ABSTRACT_CAP],
                "date": parse_date(repo.get("created_at", "")),
                "stars": stars,
                "repo_name": name,
                "repo_desc": desc,
                "lang": lang,
            }
        )
    return items


# 非 RSS 源: 名称 → 获取函数
JSON_SOURCES = {
    "HuggingFace Papers": fetch_hf_papers,
    "HuggingFace Models": fetch_hf_models,
    "GitHub Trending": fetch_github_trending,
}


def is_ai_relevant(title):
    low = title.lower()
    return any(k in low for k in AI_KEYWORDS)


def _process_entries(source, entries, items, seen, cutoff):
    """处理单个来源的条目: 去重、过滤、收集。"""
    count = 0
    always = source in ALWAYS_RELEVANT
    skip_time = source in SKIP_TIME_FILTER
    for e in entries:
        title = strip_html(e.get("title", ""))
        if not title:
            continue
        link = (e.get("link") or "").strip()
        date = e.get("date")
        if not (always or is_ai_relevant(title)):
            continue
        if date and not skip_time and date < cutoff:
            continue
        key = re.sub(r"\W+", "", title.lower())[:60] or link
        if key in seen:
            continue
        seen.add(key)
        item = {
            "source": source,
            "title": title,
            "link": link,
            "date": date.isoformat() if date else "",
            "abstract": strip_html(e.get("summary", ""))[:ABSTRACT_CAP],
        }
        # 透传 GitHub Trending 额外字段 (用于独立表格)
        for k in ("stars", "repo_name", "repo_desc", "lang"):
            if k in e:
                item[k] = e[k]
        items.append(item)
        count += 1
    log(f"  - {source}: {count} 条")


def collect():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS)
    items, seen = [], set()
    # RSS 源
    for name, url in FEEDS.items():
        try:
            entries = fetch_entries(url)
        except Exception as e:
            log(f"  ! 获取失败: {name} ({e})")
            continue
        _process_entries(name, entries, items, seen, cutoff)
    # JSON API 源
    for name, fn in JSON_SOURCES.items():
        try:
            entries = fn()
        except Exception as e:
            log(f"  ! 获取失败: {name} ({e})")
            continue
        _process_entries(name, entries, items, seen, cutoff)
    # 事件聚簇: 多源报道同一事件时合并 (评分前进行, 评分时利用 cluster 字段)
    items = _cluster_events(items)
    for item in items:
        item["score"] = score_item(item)
    items.sort(key=lambda x: (x["score"], x["date"] or ""), reverse=True)
    return items


def llm_digest(items):
    """用 DeepSeek 为 top 条目生成中文标题 + 一句话摘要, 原地写回 items。
    成功返回 True; 无 key / 调用失败 / 解析异常返回 False, 由调用方回退纯列表。"""
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return False
    top = [it for it in items if it["score"] >= MIN_SCORE][:MAX_ITEMS]
    context = "\n\n".join(
        f"[{i + 1}]\n标题: {it['title']}\n摘要: {_clean_abstract(it['abstract'])}"
        for i, it in enumerate(top)
    )
    prompt = (
        "你是 AI 新闻编辑。所有新闻来源均为英文媒体。为每条新闻生成:\n"
        "1. 中文标题: 将英文标题翻译成中文(技术术语可保留英文缩写如 AI/GPT/LLM)\n"
        "2. 一句话摘要: 约60字中文, 点出核心事实与关键影响或细节\n"
        "3. 重要性评分(1-10): 10=重大突破/里程碑(如新模型发布、重大融资), "
        "7-8=有意义的技术进展, 5-6=常规更新, 3-4=边缘相关, 1-2=噪声\n"
        "只输出 JSON 数组, 顺序与输入一致, 不要解释或前后缀:\n"
        '[{"t":"中文标题","s":"一句话摘要","r":8}]\n\n'
        f"{context}"
    )
    body = json.dumps(
        {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "system",
                    "content": "你是专业 AI 新闻编辑, 擅长将英文技术新闻翻译并浓缩成高密度中文简报。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log(f"  ! DeepSeek 调用失败, 回退纯列表: {e}")
        return False
    # 容错解析 JSON 数组 (模型可能套 ```json``` 围栏或带前后缀)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            log("  ! DeepSeek 返回非 JSON, 回退纯列表")
            return False
        try:
            result = json.loads(m.group(0))
        except json.JSONDecodeError:
            log("  ! DeepSeek JSON 解析失败, 回退纯列表")
            return False
    if not isinstance(result, list) or len(result) != len(top):
        log(
            f"  ! DeepSeek 返回条数不符 ({len(result) if isinstance(result, list) else 0}/{len(top)}), 回退纯列表"
        )
        return False
    for i, r in enumerate(result):
        if not isinstance(r, dict):
            continue
        t = (r.get("t") or "").strip()
        s = (r.get("s") or "").strip()
        if t:
            top[i]["title"] = t
        if s:
            top[i]["abstract"] = s
        # LLM 重要性评分写回 (1-10 整数, 异常值忽略)
        rv = r.get("r")
        if isinstance(rv, (int, float)) and not isinstance(rv, bool) and 1 <= rv <= 10:
            top[i]["llm_score"] = int(rv)
    return True


def plain_list(items):
    top = [it for it in items if it["score"] >= MIN_SCORE][:MAX_ITEMS]
    lines = [
        f"📋 今日采集 {len(items)} 条，精选 {len(top)} 条，按重要程度排序。",
        "",
        "---",
        "",
    ]
    for i, it in enumerate(top, 1):
        lines.append(f"### {i}. {it['title']}")
        lines.append("")
        # 来源行: 聚簇多源时显示"N 源报道"
        sources = it.get("cluster_sources")
        if sources and len(sources) > 1:
            source_str = f"📰 {len(sources)} 源报道: " + ", ".join(
                _source_cn(s) for s in sources
            )
        else:
            source_str = _source_cn(it["source"])
        score_line = f"**{_score_stars(it['score'])}** · 权重 {it['score']}"
        if "llm_score" in it:
            score_line += f" · LLM {it['llm_score']}/10"
        score_line += f" · {source_str}"
        lines.append(score_line)
        lines.append("")
        abstract = _clean_abstract(it.get("abstract", ""))
        if abstract:
            lines.append(f"> {abstract[:160]}")
            lines.append("")
        lines.append(f"🔗 [阅读原文]({it['link']})")
        lines.append("")
        # 聚簇多链接时追加"更多来源"
        links = it.get("cluster_links")
        if sources and links and len(sources) > 1 and len(links) > 1:
            extra = [
                f"[{_source_cn(s)}]({l})"
                for s, l in zip(sources, links)
                if l and l != it["link"]
            ]
            if extra:
                lines.append(f"📎 更多来源: {' · '.join(extra[:3])}")
                lines.append("")
        # 条目间加分隔线，最后一条后不加
        if i < len(top):
            lines.append("---")
            lines.append("")
    return "\n".join(lines)


def _trending_table(trending):
    """GitHub 趋势仓库独立表格 (Top 5, 按星数降序)。"""
    top = sorted(trending, key=lambda x: x.get("stars", 0), reverse=True)[:5]
    if not top:
        return ""
    lines = [
        "## 🔥 GitHub 趋势仓库 (近 7 天 Top 5)",
        "",
        "| 仓库 | ⭐ | 用途 |",
        "|------|----|------|",
    ]
    for it in top:
        name = it.get("repo_name", it["title"])
        stars = it.get("stars", 0)
        desc = it.get("repo_desc", "").strip()
        if len(desc) > 60:
            desc = desc[:57] + "..."
        link = it.get("link", "")
        lines.append(f"| [{name}]({link}) | {stars} | {desc} |")
    return "\n".join(lines)


def build_text(items):
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    # GitHub Trending 单独成表，不混入主新闻列表
    trending = [it for it in items if it["source"] == "GitHub Trending"]
    rest = [it for it in items if it["source"] != "GitHub Trending"]
    parts = [f"# AI 日报 {today}"]
    if rest:
        if llm_digest(rest):  # 原地润色标题/摘要; 失败则静默, plain_list 用原始数据
            # LLM 评分修正: 将 llm_score 折算为额外加分并重新排序
            for it in rest:
                if "llm_score" in it:
                    it["score"] += it["llm_score"]
            rest.sort(key=lambda x: (x["score"], x["date"] or ""), reverse=True)
        parts.append(plain_list(rest))
    else:
        parts.append("今天没有采集到新的 AI 新闻。")
    if trending:
        parts.append(_trending_table(trending))
    return "\n\n".join(part.strip("\n") for part in parts).rstrip() + "\n"


def push_serverchan(text):
    key = (os.getenv("SERVERCHAN_KEY") or "").strip()
    if not key:
        return False
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    # 标题单独传, 正文去掉 # 行避免重复
    parts = text.split("\n", 1)
    desp = (
        parts[1].lstrip("\n") if len(parts) > 1 and parts[0].startswith("# ") else text
    )
    data = urllib.parse.urlencode(
        {
            "title": f"AI 日报 {today}",
            "desp": desp,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://sctapi.ftqq.com/{key}.send",
        data=data,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("code") == 0:
            log("  Server酱 响应: 成功")
            return True
        log(f"  ! Server酱 推送失败: {result.get('message', result)}")
        return False
    except Exception as e:
        log(f"  ! Server酱 推送失败: {e}")
        return False


def push_ntfy(text):
    server = (os.getenv("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    topic = (os.getenv("NTFY_TOPIC") or "ai-news-7f3k9x").strip()
    if not topic:
        return False
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    params = urllib.parse.urlencode(
        {
            "title": f"AI 日报 {today}",
            "tags": "robot, newspaper",
            "markdown": "1",
        }
    )
    req = urllib.request.Request(
        f"{server}/{urllib.parse.quote(topic)}?{params}",
        data=text.encode("utf-8"),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log(f"  ntfy 响应: {resp.status}")
        return True
    except Exception as e:
        log(f"  ! ntfy 推送失败: {e}")
        return False


def push_pushdeer(text):
    key = os.getenv("PUSHDEER_KEY", "").strip()
    if not key:
        return False
    # 微信文本消息有长度限制, 超长截断
    if len(text) > 3800:
        text = text[:3800] + "\n...(已截断)"
    data = urllib.parse.urlencode(
        {"pushkey": key, "type": "text", "text": text}
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api2.pushdeer.com/message/push", data=data, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log(f"  PushDeer 响应: {resp.status}")
        return True
    except Exception as e:
        log(f"  ! PushDeer 推送失败: {e}")
        return False


def push(text):
    """Server酱与ntfy同时推送; 两者均失败时回退PushDeer兜底; 全部失败则打印摘要。"""
    ok = False
    if push_serverchan(text):
        ok = True
        log("已通过 Server酱 推送")
    if push_ntfy(text):
        ok = True
        log("已通过 ntfy 推送")
    if not ok and push_pushdeer(text):
        ok = True
        log("已通过 PushDeer 推送")
    if not ok:
        log("!! 未配置推送渠道或推送失败, 摘要如下:")
        log("--- 摘要内容 ---")
        print(text)


def main():
    log("== 开始采集 ==")
    items = collect()
    log(f"== 共 {len(items)} 条 ==")
    text = build_text(items)
    log("== 推送 ==")
    push(text)
    log("== 完成 ==")


if __name__ == "__main__":
    main()
