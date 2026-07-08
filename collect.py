#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日 AI 新闻聚合 + 推送 (仅用 Python 标准库, 零第三方依赖)。

推送到手机 (二选一):
  ntfy    : 设置 NTFY_TOPIC (主题名当密钥, 起复杂点), 默认服务器 https://ntfy.sh
  PushDeer: 设置 PUSHDEER_KEY 即可走微信推送 (国内最稳)

可选 AI 摘要: 设置 DEEPSEEK_API_KEY 后, 用 DeepSeek 把当天新闻汇总成中文简报;
不设置则只推送标题列表 + 来源 + 链接。

环境变量:
  NTFY_TOPIC        ntfy 主题名
  NTFY_SERVER       ntfy 服务器, 默认 https://ntfy.sh
  PUSHDEER_KEY      PushDeer 推送 key (与 ntfy 二选一)
  DEEPSEEK_API_KEY  DeepSeek API key (可选, 用于中文摘要)
  MAX_ITEMS         摘要最多包含条数, 默认 15
  HOURS             回溯窗口小时数, 默认 30 (略大于 24 容忍时区/延迟)
"""
import os
import re
import sys
import html
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import email.utils
from datetime import datetime, timezone, timedelta

# ---------- 配置: 信息源 ----------
# arXiv / 机器之心 / 量子位 默认视为相关; HN / Reddit / 博客按关键词过滤。
FEEDS = {
    "arXiv cs.AI": "https://export.arxiv.org/rss/cs.AI",
    "arXiv cs.CL": "https://export.arxiv.org/rss/cs.CL",
    "arXiv cs.LG": "https://export.arxiv.org/rss/cs.LG",
    "Hacker News": "https://news.ycombinator.com/rss",
    "r/MachineLearning": "https://www.reddit.com/r/MachineLearning/.rss",
    "r/LocalLLaMA": "https://www.reddit.com/r/LocalLLaMA/.rss",
    "OpenAI": "https://openai.com/news/rss.xml",
    "Anthropic": "https://www.anthropic.com/news/rss.xml",
    "Google DeepMind": "https://deepmind.google/blog/rss.xml",
    "Meta AI": "https://ai.meta.com/blog/rss/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "机器之心": "https://rsshub.app/jiqizhixin",
    "量子位": "https://rsshub.app/qbitai/news",
}

AI_KEYWORDS = [
    # 英文
    "ai", "a.i.", "artificial intelligence", "llm", "gpt", "claude",
    "gemini", "llama", "qwen", "deepseek", "diffusion", "transformer",
    "machine learning", "deep learning", "neural", "agi", "rag",
    "fine-tun", "finetun", "multimodal", "reinforcement learning",
    "rlhf", "vision-language", "vision language", "mcp",
    "reasoning", "scaling law", "generative", "text-to-image", "agent",
    # 中文
    "人工智能", "大模型", "大语言模型", "智能体", "多模态", "深度学习",
]

HOURS = int(os.getenv("HOURS", "30"))
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "15"))
ABSTRACT_CAP = 400  # 单条送入 LLM 的摘要字符上限
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


def fetch_entries(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return parse_feed(r.read())


def is_ai_relevant(title):
    low = title.lower()
    return any(k in low for k in AI_KEYWORDS)


def collect():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS)
    items, seen = [], set()
    for name, url in FEEDS.items():
        try:
            entries = fetch_entries(url)
        except Exception as e:
            log(f"  ! 获取失败: {name} ({e})")
            continue
        count = 0
        always_relevant = name.startswith("arXiv") or name in ("机器之心", "量子位")
        for e in entries:
            title = strip_html(e.get("title", ""))
            if not title:
                continue
            link = (e.get("link") or "").strip()
            date = e.get("date")
            if not (always_relevant or is_ai_relevant(title)):
                continue
            if date and date < cutoff:
                continue
            key = re.sub(r"\W+", "", title.lower())[:60] or link
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "source": name,
                "title": title,
                "link": link,
                "date": date.isoformat() if date else "",
                "abstract": strip_html(e.get("summary", ""))[:ABSTRACT_CAP],
            })
            count += 1
        log(f"  - {name}: {count} 条")
    items.sort(key=lambda x: x["date"] or "", reverse=True)
    return items


def llm_digest(items):
    """用 DeepSeek 生成中文简报。失败/无 key 则返回 None, 由调用方回退。"""
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return None
    top = items[:MAX_ITEMS]
    context = "\n\n".join(
        f"[{i+1}] 源: {it['source']}\n标题: {it['title']}\n摘要: {it['abstract']}\n链接: {it['link']}"
        for i, it in enumerate(top)
    )
    prompt = (
        "你是 AI 新闻编辑。下面是今天采集到的 AI 相关新闻条目。"
        "请用中文生成一份简洁日报:先一句话概述今日趋势,然后用编号列表,每条"
        "给出「标题 + 一句话中文摘要 + 来源 + 链接」。保持客观、信息密度高,不要寒暄。"
        f"最多 {len(top)} 条。\n\n{context}"
    )
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是专业 AI 新闻编辑, 擅长把英文技术新闻压缩成高密度中文简报。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log(f"  ! DeepSeek 调用失败, 回退纯列表: {e}")
        return None


def plain_list(items):
    top = items[:MAX_ITEMS]
    lines = [f"今日共采集 {len(items)} 条 AI 新闻, 精选 {len(top)} 条:\n"]
    for i, it in enumerate(top, 1):
        lines.append(f"{i}. {it['title']}")
        lines.append(f"   来源: {it['source']}")
        if it["abstract"]:
            lines.append(f"   {it['abstract'][:120]}")
        lines.append(f"   {it['link']}\n")
    return "\n".join(lines)


def build_text(items):
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    header = f"# AI 日报 {today}\n"
    if not items:
        return header + "\n今天没有采集到新的 AI 新闻。"
    body = llm_digest(items) or plain_list(items)
    return header + "\n" + body


def push_ntfy(text):
    server = (os.getenv("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    topic = (os.getenv("NTFY_TOPIC") or "ai-news-7f3k9x").strip()
    if not topic:
        return False
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    req = urllib.request.Request(
        f"{server}/{urllib.parse.quote(topic)}",
        data=text.encode("utf-8"),
        headers={
            "Title": f"AI 日报 {today}",
            "Tags": "robot, newspaper",
            "Markdown": "yes",
        },
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
    if push_ntfy(text):
        log("已通过 ntfy 推送")
    elif push_pushdeer(text):
        log("已通过 PushDeer 推送")
    else:
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
