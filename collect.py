#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日 AI 新闻聚合 + 推送 (仅用 Python 标准库, 零第三方依赖)。

推送到手机 (按优先级依次尝试):
  Server酱: 设置 SERVERCHAN_KEY 即可走微信推送 (国内推荐)
  ntfy    : 设置 NTFY_TOPIC (主题名当密钥), 默认服务器 https://ntfy.sh
  PushDeer: 设置 PUSHDEER_KEY (项目已停更, 仅作兜底)

可选 AI 摘要: 设置 DEEPSEEK_API_KEY 后, 用 DeepSeek 把当天新闻汇总成中文简报;
不设置则只推送标题列表 + 来源 + 链接。

AI 模型能力排行榜: 每日附在新闻后, 分两个维度各取前 10:
  Coding 能力 - Arena AI / LMArena 代码投票榜 (ELO 评分, 每日更新, 含最新模型)
  Agent 能力  - SWE-bench Verified (真实 GitHub Issue 解决率, agent 系统能力)
两个源均为 GitHub raw 数据文件, 零依赖抓取; 单个源失败优雅降级不影响整体。

环境变量:
  SERVERCHAN_KEY    Server酱 SendKey (推荐, 微信推送)
  NTFY_TOPIC        ntfy 主题名
  NTFY_SERVER       ntfy 服务器, 默认 https://ntfy.sh
  PUSHDEER_KEY      PushDeer 推送 key (已停更, 仅作兜底)
  DEEPSEEK_API_KEY  DeepSeek API key (可选, 用于中文摘要)
  MAX_ITEMS         摘要最多包含条数, 默认 10
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
# 量子位 (纯 AI 媒体) 默认全部视为相关; 其余来源按关键词过滤。
# 注: 机器之心 (jiqizhixin.com) 已关闭免费 RSS, 暂不可用
FEEDS = {
    "量子位": "https://www.qbitai.com/feed",
    "Solidot": "https://www.solidot.org/index.rss",
    "36氪": "https://36kr.com/feed",
    "IT之家": "https://www.ithome.com/rss/",
    "钛媒体": "https://www.tmtpost.com/rss.xml",
}

AI_KEYWORDS = [
    # 英文
    "ai", "a.i.", "artificial intelligence", "llm", "gpt", "claude",
    "gemini", "llama", "qwen", "deepseek", "diffusion", "transformer",
    "machine learning", "deep learning", "neural", "agi", "rag",
    "fine-tun", "finetun", "multimodal", "reinforcement learning",
    "rlhf", "vision-language", "vision language", "mcp",
    "reasoning", "scaling law", "generative", "text-to-image", "agent",
    "embodied", "embodied intelligence",
    # 中文
    "人工智能", "大模型", "大语言模型", "智能体", "多模态", "深度学习",
    "具身智能", "具身", "机器人", "算力", "自动驾驶", "AIGC",
    "数字人", "智驾", "AI芯片", "计算机视觉", "强化学习",
    "提示词", "微调", "开源模型", "幻觉",
]

HOURS = int(os.getenv("HOURS") or "30")
MAX_ITEMS = int(os.getenv("MAX_ITEMS") or "10")
MIN_SCORE = 12  # 重要程度门槛: 仅推送 ★★★☆☆(score>=12) 及以上, 最多 MAX_ITEMS 条

# ---------- 重要程度评分 ----------
SOURCE_WEIGHT = {
    "量子位": 10, "Solidot": 9, "36氪": 8, "IT之家": 7, "钛媒体": 7,
}
# 用户重点关注的话题, 命中加分
HOT_TOPICS = ["agent", "智能体", "embodied", "具身", "具身智能"]
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


SOURCE_CN = {
    "量子位": "量子位",
    "Solidot": "Solidot·奇客",
    "36氪": "36氪",
    "IT之家": "IT之家",
    "钛媒体": "钛媒体",
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


def _rank_medal(rank):
    """前三名返回奖牌 emoji, 其余返回数字排名。"""
    if rank == 1:
        return "🥇"
    if rank == 2:
        return "🥈"
    if rank == 3:
        return "🥉"
    return str(rank)


def score_item(item):
    """计算新闻重要程度权重 (越高越重要)。"""
    score = SOURCE_WEIGHT.get(item["source"], 5)
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
    # 36氪等使用 "2026-07-08 11:03:34  +0800" 格式
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
        always_relevant = name == "量子位"
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
        f"[{i+1}]\n标题: {it['title']}\n摘要: {_clean_abstract(it['abstract'])}"
        for i, it in enumerate(top)
    )
    prompt = (
        "你是 AI 新闻编辑。为每条新闻生成: 中文标题(英文译成中文, 已是中文则保持)和1-2句话摘要(约60字, 点出核心事实与关键影响或细节)。\n"
        "只输出 JSON 数组, 顺序与输入一致, 不要解释或前后缀:\n"
        '[{"t":"中文标题","s":"一句话摘要"}]\n\n'
        f"{context}"
    )
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是专业 AI 新闻编辑, 擅长把技术新闻压缩成高密度中文简报。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
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
        log(f"  ! DeepSeek 返回条数不符 ({len(result) if isinstance(result, list) else 0}/{len(top)}), 回退纯列表")
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
        lines.append(
            f"**{_score_stars(it['score'])}** · 权重 {it['score']} · {_source_cn(it['source'])}"
        )
        lines.append("")
        abstract = _clean_abstract(it.get("abstract", ""))
        if abstract:
            lines.append(f"> {abstract[:160]}")
            lines.append("")
        lines.append(f"🔗 [阅读原文]({it['link']})")
        lines.append("")
        # 条目间加分隔线，最后一条后不加，避免与排行榜分隔线重复
        if i < len(top):
            lines.append("---")
            lines.append("")
    return "\n".join(lines)


# ---------- AI 模型能力排行榜 ----------
# Coding 能力: Arena AI / LMArena 代码投票榜 (ELO 评分, 每日镜像更新)
# Agent 能力: SWE-bench Verified (500 个真实 GitHub Issue, agent 系统解决率)
ARENA_BASE = (
    "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/"
    "main/data"
)
ARENA_LATEST_URL = ARENA_BASE + "/latest.json"
SWEBENCH_URL = (
    "https://raw.githubusercontent.com/swe-bench/swe-bench.github.io/"
    "master/data/leaderboards.json"
)
LB_TOP = 10  # 每个榜单展示条数


def fetch_arena_code_leaderboard():
    """Arena AI (LMArena) 代码投票榜 (ELO 评分)。先抓 latest 得最新日期 path,
    再抓该日 code.json。成功返回 [(model, vendor, score, ci, votes)], 失败 None。"""
    try:
        req = urllib.request.Request(ARENA_LATEST_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            latest = json.loads(r.read().decode("utf-8"))
        path = latest.get("path") or latest.get("date")
        if not path:
            return None
        code_url = f"{ARENA_BASE}/{path}/code.json"
        req = urllib.request.Request(code_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log(f"  ! Arena AI 代码榜获取失败: {e}")
        return None
    models = data.get("models", [])[:LB_TOP]
    return [
        (m.get("model", ""), m.get("vendor", ""), m.get("score") or 0,
         m.get("ci") or 0, m.get("votes") or 0)
        for m in models
    ]


def fetch_swebench_leaderboard():
    """SWE-bench Verified 榜 (agent/SWE 能力)。取 Verified 子榜按解决率降序, 返回前 LB_TOP。
    成功返回 [(name, resolved, date)], 失败返回 None。"""
    try:
        req = urllib.request.Request(SWEBENCH_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log(f"  ! SWE-bench 榜获取失败: {e}")
        return None
    for lb in data.get("leaderboards", []):
        if lb.get("name") == "Verified":
            results = lb.get("results", [])
            results = sorted(
                results, key=lambda x: x.get("resolved") or 0, reverse=True
            )[:LB_TOP]
            return [
                (r.get("name", ""), r.get("resolved") or 0, r.get("date", ""))
                for r in results
            ]
    return []


def build_leaderboard():
    """构建 AI 模型能力排行榜 Markdown 段落, 单个源失败优雅降级。"""
    lines = ["", "## 🏆 AI 模型能力排行榜", ""]

    # --- Coding 能力 (Arena AI 代码投票 ELO 榜) ---
    lines.append("**Coding 能力** · Arena AI (LMArena 代码投票 ELO 评分)")
    lines.append("")
    arena = fetch_arena_code_leaderboard()
    if arena is None:
        lines.append("> 数据获取失败, 稍后重试")
    elif not arena:
        lines.append("> 暂无数据")
    else:
        lines.append("| 排名 | 模型 | 厂商 | ELO |")
        lines.append("|:----:|------|------|----:|")
        for i, (model, vendor, score, ci, votes) in enumerate(arena, 1):
            lines.append(f"| {_rank_medal(i)} | {model} | {vendor} | {score} |")
    lines.append("")

    # --- Agent 能力 (SWE-bench Verified) ---
    lines.append("**Agent 能力** · SWE-bench Verified (真实 Issue 解决率)")
    lines.append("")
    swe = fetch_swebench_leaderboard()
    if swe is None:
        lines.append("> 数据获取失败, 稍后重试")
    elif not swe:
        lines.append("> 暂无数据")
    else:
        lines.append("| 排名 | Agent 系统 | 解决率 |")
        lines.append("|:----:|------------|--------:|")
        for i, (name, resolved, date) in enumerate(swe, 1):
            lines.append(f"| {_rank_medal(i)} | {name} | {resolved}% |")
    lines.append("")

    # 数据来源说明，增加可信度
    lines.append("> 📌 数据来源：Arena AI (LMArena) 代码投票榜 与 SWE-bench Verified 公开榜单。")
    lines.append("")

    return "\n".join(lines)


def build_text(items):
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    parts = [f"# AI 日报 {today}"]
    if items:
        llm_digest(items)  # 原地润色标题/摘要; 失败则静默, plain_list 用原始数据
        parts.append(plain_list(items))
    else:
        parts.append("今天没有采集到新的 AI 新闻。")
    # 用一条分隔线隔开新闻列表与排行榜，避免与列表内部分隔线重复
    parts.append("---")
    parts.append(build_leaderboard())
    return "\n\n".join(part.strip("\n") for part in parts).rstrip() + "\n"


def push_serverchan(text):
    key = (os.getenv("SERVERCHAN_KEY") or "").strip()
    if not key:
        return False
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    # 标题单独传, 正文去掉 # 行避免重复
    parts = text.split("\n", 1)
    desp = parts[1].lstrip("\n") if len(parts) > 1 and parts[0].startswith("# ") else text
    data = urllib.parse.urlencode({
        "title": f"AI 日报 {today}",
        "desp": desp,
    }).encode("utf-8")
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
    params = urllib.parse.urlencode({
        "title": f"AI 日报 {today}",
        "tags": "robot, newspaper",
        "markdown": "1",
    })
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
    if push_serverchan(text):
        log("已通过 Server酱 推送")
    elif push_ntfy(text):
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
