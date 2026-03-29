#!/usr/bin/env python3
"""
AI 日报 v3 —— 每日 AI 信息过滤器（增强版）

信息源（6个）：
  1. GitHub Search API — 热门 Agent/Skill/AI 项目
  2. Hacker News      — AI 热议话题 + 高质量评论精华
  3. arXiv            — 高影响力 AI 新论文
  4. Reddit           — r/MachineLearning 等 AI 社区热帖
  5. TechCrunch RSS   — AI 产品发布 / 融资新闻
  6. Product Hunt RSS — 每日热门 AI 产品

输出：
  1. GitHub Pages 精美网页（完整日报，卡片式，手机友好）
  2. 企业微信群机器人推送（摘要 + 网页链接）
  3. QQ邮箱推送（备份渠道）
"""

import os
import json
import sys
import smtplib
import logging
import re
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from pathlib import Path
from html import escape as html_escape

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

KEYWORDS = [
    "agent", "skill", "mcp", "mcp-server", "claude-code", "claude-skill",
    "ai-agent", "multi-agent", "langchain", "langgraph", "crewai",
    "autogen", "agentic", "rag", "llm", "gpt", "transformer",
    "prompt-engineering", "function-calling", "tool-use",
    "openclaw", "superagent", "workflow-automation", "copilot",
    "deepseek", "openai", "anthropic", "gemini", "diffusion",
]

SEARCH_TOPICS = [
    "ai-agent", "agent-skill", "mcp-server", "claude-code",
    "langchain", "multi-agent", "large-language-model",
]

MAX_GITHUB_PROJECTS = 5
MAX_HN_STORIES = 8
MAX_ARXIV_PAPERS = 5
MAX_REDDIT_POSTS = 8
CN_TZ = timezone(timedelta(hours=8))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 通用 HTTP
# ---------------------------------------------------------------------------

def http_get(url, headers=None, timeout=30):
    hdrs = {"User-Agent": "AIDailyReport/3.0"}
    if headers:
        hdrs.update(headers)
    try:
        req = Request(url, headers=hdrs)
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (HTTPError, URLError, TimeoutError) as e:
        log.warning(f"HTTP 失败: {url} -> {e}")
        return None


def http_get_json(url, headers=None):
    data = http_get(url, headers)
    if data:
        try:
            return json.loads(data.decode())
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# 数据源 1：GitHub
# ---------------------------------------------------------------------------

def fetch_github(token=None):
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repos = {}
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    for topic in SEARCH_TOPICS:
        url = f"https://api.github.com/search/repositories?q=topic:{quote(topic)}+pushed:>{week_ago}&sort=stars&order=desc&per_page=8"
        data = http_get_json(url, headers)
        if data and "items" in data:
            for item in data["items"]:
                repos[item["full_name"]] = item

    for q in ["AI agent framework 2026", "MCP server tool", "LLM agent skill"]:
        url = f"https://api.github.com/search/repositories?q={quote(q)}+pushed:>{week_ago}&sort=stars&order=desc&per_page=5"
        data = http_get_json(url, headers)
        if data and "items" in data:
            for item in data["items"]:
                repos[item["full_name"]] = item

    filtered = []
    for repo in sorted(repos.values(), key=lambda r: r.get("stargazers_count", 0), reverse=True):
        combined = (repo.get("full_name", "").lower() + " " +
                    (repo.get("description") or "").lower() + " " +
                    " ".join(t.lower() for t in repo.get("topics", [])))
        if any(kw in combined for kw in KEYWORDS) and repo.get("stargazers_count", 0) >= 50:
            created = repo.get("created_at", "")
            is_new = False
            if created:
                try:
                    ct = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    is_new = (datetime.now(timezone.utc) - ct).days <= 7
                except ValueError:
                    pass
            filtered.append({
                "name": repo["full_name"],
                "url": repo.get("html_url", ""),
                "description": repo.get("description") or "暂无描述",
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "language": repo.get("language") or "未知",
                "topics": repo.get("topics", [])[:6],
                "is_new": is_new,
            })

    return filtered[:MAX_GITHUB_PROJECTS]


# ---------------------------------------------------------------------------
# 数据源 2：Hacker News（增强版，含评论精华）
# ---------------------------------------------------------------------------

def fetch_hn():
    top_ids = http_get_json("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not top_ids:
        return []

    ai_kw = [
        "ai", "gpt", "llm", "claude", "openai", "anthropic", "deepseek",
        "gemini", "agent", "model", "transformer", "machine learning",
        "neural", "diffusion", "reasoning", "rag", "mcp", "skill",
        "copilot", "cursor", "coding assistant", "fine-tun", "embedding",
        "artificial intelligence", "chatbot", "generative", "foundation model",
        "multimodal", "vision language", "benchmark", "alignment",
    ]

    stories = []
    for sid in top_ids[:200]:
        item = http_get_json(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
        if not item or item.get("type") != "story":
            continue
        title_lower = (item.get("title") or "").lower()
        if any(kw in title_lower for kw in ai_kw):
            story = {
                "title": item.get("title", ""),
                "url": item.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                "hn_url": f"https://news.ycombinator.com/item?id={sid}",
                "score": item.get("score", 0),
                "comments_count": item.get("descendants", 0),
                "top_comments": [],
            }

            # 抓取前5条高分评论
            kid_ids = item.get("kids", [])[:10]
            comments = []
            for kid_id in kid_ids:
                c = http_get_json(f"https://hacker-news.firebaseio.com/v0/item/{kid_id}.json")
                if c and c.get("type") == "comment" and c.get("text") and not c.get("deleted"):
                    # 清理 HTML 标签
                    text = re.sub(r'<[^>]+>', ' ', c.get("text", ""))
                    text = re.sub(r'\s+', ' ', text).strip()
                    if len(text) > 20:
                        comments.append({"text": text[:500], "by": c.get("by", "匿名")})
                if len(comments) >= 5:
                    break

            story["top_comments"] = comments
            stories.append(story)

        if len(stories) >= MAX_HN_STORIES:
            break

    return sorted(stories, key=lambda s: s["score"], reverse=True)[:MAX_HN_STORIES]


# ---------------------------------------------------------------------------
# 数据源 3：arXiv
# ---------------------------------------------------------------------------

def fetch_arxiv():
    categories = ["cs.AI", "cs.CL", "cs.LG", "cs.MA"]
    query = "+OR+".join(f"cat:{c}" for c in categories)
    url = f"http://export.arxiv.org/api/query?search_query={query}&sortBy=submittedDate&sortOrder=descending&start=0&max_results=30"
    data = http_get(url)
    if not data:
        return []

    text = data.decode("utf-8")
    papers = []
    entries = text.split("<entry>")[1:]

    ai_terms = [
        "agent", "llm", "language model", "transformer", "reasoning",
        "rag", "retrieval", "tool use", "multi-agent", "benchmark",
        "fine-tun", "prompt", "instruct", "reward", "rlhf", "alignment",
        "diffusion", "generation", "multimodal", "vision-language",
        "skill", "planning", "chain-of-thought", "mcp", "scaling",
    ]

    for entry in entries:
        title_s = entry.find("<title>") + 7
        title_e = entry.find("</title>")
        title = entry[title_s:title_e].strip().replace("\n", " ") if title_s > 6 else ""

        summary_s = entry.find("<summary>") + 9
        summary_e = entry.find("</summary>")
        summary = entry[summary_s:summary_e].strip().replace("\n", " ") if summary_s > 8 else ""

        link = ""
        link_s = entry.find("<id>")
        if link_s >= 0:
            link_e = entry.find("</id>")
            link = entry[link_s + 4:link_e].strip()

        authors = []
        rem = entry
        while "<name>" in rem and len(authors) < 4:
            ns = rem.find("<name>") + 6
            ne = rem.find("</name>")
            if ns > 5 and ne > ns:
                authors.append(rem[ns:ne].strip())
            rem = rem[ne + 7:]

        combined = (title + " " + summary).lower()
        if any(t in combined for t in ai_terms):
            papers.append({
                "title": title,
                "summary": summary[:800],
                "url": link,
                "authors": authors,
            })

        if len(papers) >= MAX_ARXIV_PAPERS:
            break

    return papers


# ---------------------------------------------------------------------------
# 数据源 4：Reddit AI 社区
# ---------------------------------------------------------------------------

def fetch_reddit():
    subreddits = ["MachineLearning", "artificial", "LocalLLaMA"]
    posts = []

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit=15"
        data = http_get_json(url, {"User-Agent": "AIDailyReport/3.0"})
        if not data or "data" not in data:
            continue

        for child in data["data"].get("children", []):
            p = child.get("data", {})
            if p.get("stickied"):
                continue
            score = p.get("score", 0)
            if score < 20:
                continue
            posts.append({
                "title": p.get("title", ""),
                "url": p.get("url", ""),
                "reddit_url": f"https://reddit.com{p.get('permalink', '')}",
                "score": score,
                "comments_count": p.get("num_comments", 0),
                "subreddit": sub,
                "selftext": (p.get("selftext") or "")[:300],
            })

    return sorted(posts, key=lambda p: p["score"], reverse=True)[:MAX_REDDIT_POSTS]


# ---------------------------------------------------------------------------
# 数据源 5：TechCrunch AI RSS
# ---------------------------------------------------------------------------

def fetch_techcrunch():
    url = "https://techcrunch.com/category/artificial-intelligence/feed/"
    data = http_get(url)
    if not data:
        return []

    text = data.decode("utf-8", errors="ignore")
    articles = []
    items = text.split("<item>")[1:]

    for item in items[:10]:
        title = ""
        t_s = item.find("<title>")
        if t_s >= 0:
            # Handle CDATA
            t_content = item[t_s + 7:]
            t_e = t_content.find("</title>")
            title = t_content[:t_e].strip()
            title = re.sub(r'<!\[CDATA\[|\]\]>', '', title).strip()

        link = ""
        l_s = item.find("<link>")
        if l_s >= 0:
            l_content = item[l_s + 6:]
            l_e = l_content.find("</link>")
            link = l_content[:l_e].strip()
            link = re.sub(r'<!\[CDATA\[|\]\]>', '', link).strip()

        desc = ""
        d_s = item.find("<description>")
        if d_s >= 0:
            d_content = item[d_s + 13:]
            d_e = d_content.find("</description>")
            desc = d_content[:d_e].strip()
            desc = re.sub(r'<!\[CDATA\[|\]\]>', '', desc)
            desc = re.sub(r'<[^>]+>', '', desc).strip()[:300]

        if title:
            articles.append({"title": title, "url": link, "description": desc})

    return articles


# ---------------------------------------------------------------------------
# 数据源 6：Product Hunt（每日热门）
# ---------------------------------------------------------------------------

def fetch_producthunt():
    url = "https://www.producthunt.com/feed"
    data = http_get(url)
    if not data:
        return []

    text = data.decode("utf-8", errors="ignore")
    products = []
    items = text.split("<item>")[1:]

    ai_kw = ["ai", "gpt", "llm", "agent", "machine learning", "chatbot",
             "copilot", "automat", "generative", "neural", "model"]

    for item in items[:20]:
        title = ""
        t_s = item.find("<title>")
        if t_s >= 0:
            t_content = item[t_s + 7:]
            t_e = t_content.find("</title>")
            title = t_content[:t_e].strip()
            title = re.sub(r'<!\[CDATA\[|\]\]>', '', title).strip()

        link = ""
        l_s = item.find("<link>")
        if l_s >= 0:
            l_content = item[l_s + 6:]
            l_e = l_content.find("</link>")
            link = l_content[:l_e].strip()
            link = re.sub(r'<!\[CDATA\[|\]\]>', '', link).strip()

        desc = ""
        d_s = item.find("<description>")
        if d_s >= 0:
            d_content = item[d_s + 13:]
            d_e = d_content.find("</description>")
            desc = d_content[:d_e].strip()
            desc = re.sub(r'<!\[CDATA\[|\]\]>', '', desc)
            desc = re.sub(r'<[^>]+>', '', desc).strip()[:200]

        if title and any(kw in (title + " " + desc).lower() for kw in ai_kw):
            products.append({"title": title, "url": link, "description": desc})

    return products[:5]


# ---------------------------------------------------------------------------
# DeepSeek 内容生成（优化版 Prompt）
# ---------------------------------------------------------------------------

def generate_content(github, hn, arxiv, reddit, techcrunch, producthunt, api_key):
    if not api_key:
        log.info("未配置 DEEPSEEK_API_KEY，使用基础模板")
        return _fallback(github, hn, arxiv, reddit, techcrunch, producthunt)

    # 构建详细的输入数据
    gh_text = "\n".join(
        f"- {p['name']} (★{p['stars']:,}): {p['description']}\n  标签: {', '.join(p.get('topics', []))}"
        for p in github
    )

    hn_text = ""
    for s in hn:
        hn_text += f"\n- 《{s['title']}》(热度:{s['score']}分, {s['comments_count']}条讨论)\n  链接: {s['url']}\n  讨论: {s['hn_url']}"
        if s.get("top_comments"):
            hn_text += "\n  精选评论:"
            for c in s["top_comments"][:3]:
                hn_text += f"\n    [{c['by']}]: {c['text'][:200]}"

    arxiv_text = "\n".join(
        f"- 《{p['title']}》\n  作者: {', '.join(p['authors'])}\n  摘要: {p['summary'][:400]}\n  链接: {p['url']}"
        for p in arxiv
    )

    reddit_text = "\n".join(
        f"- [{p['subreddit']}] {p['title']} (↑{p['score']}, {p['comments_count']}评论)\n  {p['selftext'][:150]}"
        for p in reddit
    )

    tc_text = "\n".join(f"- {a['title']}: {a['description'][:150]}" for a in techcrunch)
    ph_text = "\n".join(f"- {p['title']}: {p['description'][:100]}" for p in producthunt)

    prompt = f"""你是一个专业的 AI 行业分析师，为中文读者编写每日 AI 日报。你的读者是对 AI 感兴趣但时间有限的人，他们希望在5分钟内了解 AI 世界正在发生什么。

以下是今日从 6 个来源收集的原始数据：

=== GitHub 热门项目 ===
{gh_text or "（今日无数据）"}

=== Hacker News 热议（含评论精华）===
{hn_text or "（今日无数据）"}

=== arXiv 新论文 ===
{arxiv_text or "（今日无数据）"}

=== Reddit AI 社区 ===
{reddit_text or "（今日无数据）"}

=== TechCrunch AI 新闻 ===
{tc_text or "（今日无数据）"}

=== Product Hunt 热门 AI 产品 ===
{ph_text or "（今日无数据）"}

请生成日报，严格按以下 JSON 格式返回：

{{
  "headline": "用一句话概括今天 AI 世界最重要的事（15-25字，要有信息量）",

  "top_stories": [
    {{
      "title": "中文标题（简洁有力）",
      "summary": "200-300字详细解读。要求：1) 发生了什么（事实）2) 为什么重要（影响）3) 对普通开发者/创业者意味着什么（行动建议）。语气像一个懂行的朋友在给你聊天，不要用官方口吻。",
      "source": "来源",
      "url": "链接",
      "importance": "high/medium"
    }}
  ],

  "notable_voices": [
    {{
      "who": "人名和身份（如 Andrej Karpathy, 前Tesla AI总监）",
      "said": "100-150字总结他说了什么。要求：1) 核心观点是什么 2) 他为什么这么说 3) 这个观点有什么争议或启发。必须基于真实数据（HN评论或Reddit帖子），不要编造。如果数据中没有明确的大佬言论，就提取HN/Reddit中最有洞察力的匿名评论。",
      "source_url": "来源链接"
    }}
  ],

  "papers": [
    {{
      "title": "论文英文原标题",
      "plain_explain": "150-200字大白话解释。要求：假设读者完全没有ML背景，用比喻和类比让他理解。结构：1) 这篇论文要解决什么问题？（一句话）2) 他们怎么做的？（核心方法，用比喻）3) 效果怎么样？（关键数字）4) 这意味着什么？（对行业的影响）",
      "why_care": "一句话说明「这对你有什么用」或「为什么你应该关心」",
      "url": "链接"
    }}
  ],

  "github_picks": [
    {{
      "name": "owner/repo",
      "summary": "150-200字详细简介。要求包含：1) 这个项目解决什么痛点？2) 核心功能有哪些？3) 为什么最近火了？4) 适合什么样的人用？语气要像在给朋友推荐一个好工具。",
      "stars": 数字,
      "url": "链接",
      "is_new": true/false,
      "tags": ["标签1", "标签2"]
    }}
  ],

  "landing_signals": [
    {{
      "title": "标题",
      "content": "100-150字。来自 TechCrunch/Product Hunt 的真实信息：谁做了什么产品、拿了多少融资、用了什么AI技术、解决什么问题。如果有商业模式的亮点也要提到。必须基于真实数据，不要编造。",
      "url": "来源链接"
    }}
  ],

  "trends": [
    "趋势一：100-150字深度洞察。不要泛泛而谈，要有具体的数据支撑和明确的判断。",
    "趋势二：同上",
    "趋势三：同上"
  ]
}}

重要要求：
1. 所有内容用中文，项目名/人名/论文标题保留英文
2. notable_voices 必须基于真实数据（HN评论或Reddit帖子），不知道是谁说的就写「HN 用户 @xxx」
3. landing_signals 必须基于 TechCrunch/Product Hunt 真实数据，没有就留空数组
4. 每段描述的信息密度要高，读者看完日报里的描述就不需要点开原文
5. 语气要像懂行的朋友聊天，不要像新闻稿或学术报告"""

    try:
        req_body = json.dumps({
            "model": "deepseek-chat",
            "max_tokens": 6000,
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "你是专业 AI 行业分析师，输出纯 JSON，不要 markdown 代码块。"},
                {"role": "user", "content": prompt},
            ],
        }).encode()

        req = Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=req_body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode())

        text = result["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]

        return json.loads(text.strip())

    except Exception as e:
        log.warning(f"DeepSeek 调用失败: {e}")
        return _fallback(github, hn, arxiv, reddit, techcrunch, producthunt)


def _fallback(github, hn, arxiv, reddit, techcrunch, producthunt):
    return {
        "headline": "AI 生态持续活跃",
        "top_stories": [{"title": s["title"], "summary": f"HN 热议，{s['score']}分，{s['comments_count']}条讨论。", "source": "HN", "url": s["url"], "importance": "medium"} for s in hn[:3]],
        "notable_voices": [],
        "papers": [{"title": p["title"], "plain_explain": p["summary"][:300], "why_care": "值得关注的最新研究。", "url": p["url"]} for p in arxiv[:2]],
        "github_picks": [{"name": p["name"], "summary": p["description"], "stars": p["stars"], "url": p["url"], "is_new": p.get("is_new", False), "tags": p.get("topics", [])[:4]} for p in github],
        "landing_signals": [{"title": a["title"], "content": a["description"], "url": a["url"]} for a in techcrunch[:2]],
        "trends": ["Agent/Skill 生态持续扩展。", "多模态和推理能力是竞争焦点。", "AI 落地应用场景不断拓宽。"],
    }


# ---------------------------------------------------------------------------
# 网页渲染（优化版，信息密度更高）
# ---------------------------------------------------------------------------

def render_webpage(content):
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%Y年%m月%d日")
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays[now.weekday()]

    headline = html_escape(content.get("headline", ""))
    top_stories = content.get("top_stories", [])
    voices = content.get("notable_voices", [])
    papers = content.get("papers", [])
    gh_picks = content.get("github_picks", [])
    landing = content.get("landing_signals", [])
    trends = content.get("trends", [])

    def card(inner, url=None, extra_class=""):
        tag = "a" if url else "div"
        href = f' href="{html_escape(url)}" target="_blank"' if url else ""
        return f'<{tag}{href} class="card {extra_class}">{inner}</{tag}>'

    # 今日必知
    stories_html = ""
    for s in top_stories[:3]:
        imp = "imp-high" if s.get("importance") == "high" else "imp-med"
        imp_label = "重要" if s.get("importance") == "high" else "关注"
        stories_html += card(f'''
          <div class="ch"><span class="ct">{html_escape(s.get("title",""))}</span><span class="badge {imp}">{imp_label}</span></div>
          <p class="cd">{html_escape(s.get("summary",""))}</p>
          <span class="cs">来源：{html_escape(s.get("source",""))} · <span class="link-hint">点击查看原文 →</span></span>
        ''', s.get("url"))

    # 大佬说了啥
    voices_html = ""
    for v in voices[:3]:
        voices_html += card(f'''
          <div class="voice-who">{html_escape(v.get("who",""))}</div>
          <p class="cd">{html_escape(v.get("said",""))}</p>
        ''', v.get("source_url"))

    # 论文
    papers_html = ""
    for p in papers[:3]:
        papers_html += card(f'''
          <div class="paper-title">{html_escape(p.get("title",""))}</div>
          <p class="cd">{html_escape(p.get("plain_explain",""))}</p>
          <div class="paper-why">{html_escape(p.get("why_care",""))}</div>
        ''', p.get("url"))

    # GitHub 项目
    github_html = ""
    for g in gh_picks[:5]:
        badge = '<span class="badge imp-new">新项目</span>' if g.get("is_new") else ""
        stars = f"{g.get('stars',0):,}"
        tags = "".join(f'<span class="tag">{html_escape(t)}</span>' for t in g.get("tags", [])[:4])
        github_html += card(f'''
          <div class="ch"><span class="ct">{html_escape(g.get("name",""))}</span>{badge}</div>
          <p class="cd">{html_escape(g.get("summary",""))}</p>
          <div class="cm"><span>★ {stars}</span></div>
          <div class="tags">{tags}</div>
        ''', g.get("url"))

    # 落地风向标
    landing_html = ""
    for l in landing[:3]:
        landing_html += card(f'''
          <div class="ct">{html_escape(l.get("title",""))}</div>
          <p class="cd">{html_escape(l.get("content",""))}</p>
        ''', l.get("url"), "landing-card")

    # 趋势
    trends_html = ""
    for i, t in enumerate(trends[:3]):
        trends_html += f'<div class="trend-item"><span class="trend-num">{i+1}</span><p>{html_escape(t)}</p></div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0">
<title>AI 日报 · {date_str}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans SC','PingFang SC','Hiragino Sans GB',sans-serif;background:#fafaf9;color:#1c1917;line-height:1.6;-webkit-font-smoothing:antialiased;-webkit-text-size-adjust:100%}}
.container{{max-width:640px;margin:0 auto;padding:12px 16px}}
.header{{padding:16px 0 12px}}
.date{{font-size:11px;color:#a8a29e;letter-spacing:.5px}}
h1{{font-size:20px;font-weight:600;margin:2px 0}}
.headline{{font-size:14px;padding:8px 12px;background:#fef3c7;border-radius:8px;color:#92400e;font-weight:500;margin:8px 0 0}}
.section{{margin:20px 0 0}}
.st{{font-size:15px;font-weight:600;margin:0 0 8px;display:flex;align-items:center;gap:6px}}
.si{{width:22px;height:22px;border-radius:5px;display:flex;align-items:center;justify-content:center;font-size:12px;color:#fff;flex-shrink:0}}
.card{{display:block;background:#fff;border:1px solid #e7e5e4;border-radius:10px;padding:12px 14px;margin:0 0 8px;text-decoration:none;color:inherit;transition:border-color .15s}}
.card:hover{{border-color:#a8a29e}}
.ch{{display:flex;justify-content:space-between;align-items:flex-start;gap:6px}}
.ct{{font-size:14px;font-weight:600;color:#1c1917;line-height:1.4}}
.badge{{font-size:10px;padding:2px 6px;border-radius:4px;white-space:nowrap;font-weight:500;flex-shrink:0}}
.imp-high{{background:#fee2e2;color:#dc2626}}
.imp-med{{background:#fef3c7;color:#d97706}}
.imp-new{{background:#dcfce7;color:#166534}}
.cd{{font-size:13px;color:#44403c;margin:6px 0;line-height:1.7}}
.cs{{font-size:11px;color:#a8a29e}}
.link-hint{{color:#2563eb}}
.cm{{display:flex;gap:12px;font-size:12px;color:#a8a29e;margin:6px 0 0}}
.voice-who{{font-size:13px;font-weight:600;color:#7c3aed}}
.paper-title{{font-size:12px;font-weight:500;color:#78716c;line-height:1.4}}
.paper-why{{font-size:12px;color:#0369a1;background:#e0f2fe;padding:5px 8px;border-radius:5px;margin:6px 0 0;line-height:1.5}}
.tags{{display:flex;flex-wrap:wrap;gap:3px;margin:6px 0 0}}
.tag{{font-size:10px;padding:1px 6px;border-radius:4px;background:#f5f5f4;color:#78716c}}
.landing-card{{background:#fffbeb;border-color:#fde68a}}
.trend-item{{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid #f5f5f4}}
.trend-item:last-child{{border-bottom:none}}
.trend-num{{width:22px;height:22px;border-radius:50%;background:#e7e5e4;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#78716c;flex-shrink:0}}
.trend-item p{{font-size:13px;color:#44403c;line-height:1.6}}
.footer{{text-align:center;padding:20px 0;font-size:11px;color:#a8a29e;line-height:1.8}}
@media(prefers-color-scheme:dark){{
body{{background:#1c1917;color:#e7e5e4}}
.card{{background:#292524;border-color:#44403c}}
.card:hover{{border-color:#78716c}}
.ct{{color:#e7e5e4}}
.cd{{color:#a8a29e}}
.headline{{background:#451a03;color:#fbbf24}}
.landing-card{{background:#451a03;border-color:#92400e}}
.tag{{background:#292524;color:#a8a29e}}
.trend-num{{background:#44403c;color:#a8a29e}}
.trend-item{{border-color:#292524}}
.paper-why{{background:#0c4a6e;color:#7dd3fc}}
.paper-title{{color:#a8a29e}}
.voice-who{{color:#a78bfa}}
}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="date">{date_str} · {weekday}</div>
    <h1>AI 日报</h1>
    <div class="headline">{headline}</div>
  </div>

  {"<div class='section'><div class='st'><span class='si' style='background:#dc2626'>!</span>今日必知</div>" + stories_html + "</div>" if stories_html else ""}
  {"<div class='section'><div class='st'><span class='si' style='background:#7c3aed'>&#x1f4ac;</span>大佬说了啥</div>" + voices_html + "</div>" if voices_html else ""}
  {"<div class='section'><div class='st'><span class='si' style='background:#0369a1'>&#x1f4c4;</span>值得读的论文</div>" + papers_html + "</div>" if papers_html else ""}
  {"<div class='section'><div class='st'><span class='si' style='background:#059669'>&#x2605;</span>热门开源项目</div>" + github_html + "</div>" if github_html else ""}
  {"<div class='section'><div class='st'><span class='si' style='background:#d97706'>&#x1f4e1;</span>落地风向标</div>" + landing_html + "</div>" if landing_html else ""}
  {"<div class='section'><div class='st'><span class='si' style='background:#78716c'>&#x2192;</span>趋势洞察</div>" + trends_html + "</div>" if trends_html else ""}

  <div class="footer">
    AI 日报 · GitHub / HN / arXiv / Reddit / TechCrunch / Product Hunt<br>
    由 DeepSeek + GitHub Actions 自动生成
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 企业微信推送（优化版）
# ---------------------------------------------------------------------------

def send_wecom(content, page_url, webhook_url):
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%m月%d日")
    headline = content.get("headline", "")

    lines = [f"## AI 日报 · {date_str}", ""]
    if headline:
        lines.append(f"> {headline}")
        lines.append("")

    lines.append("**今日必知：**")
    for s in content.get("top_stories", [])[:3]:
        imp = "🔴" if s.get("importance") == "high" else "🟡"
        lines.append(f"{imp} **{s.get('title','')}**")
        summary = s.get('summary', '')
        if len(summary) > 80:
            summary = summary[:80] + "..."
        lines.append(f"   {summary}")
    lines.append("")

    gh = content.get("github_picks", [])
    if gh:
        lines.append("**热门项目：**")
        for g in gh[:3]:
            stars = f"{g.get('stars',0):,}"
            lines.append(f"⭐ **{g.get('name','')}** ({stars} star)")
        lines.append("")

    if page_url:
        lines.append(f"[👉 查看完整日报（含论文解读、大佬观点、落地信号）]({page_url})")

    payload = json.dumps({"msgtype": "markdown", "markdown": {"content": "\n".join(lines)}}).encode()

    try:
        req = Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            if result.get("errcode") == 0:
                log.info("企业微信推送成功")
            else:
                log.warning(f"企业微信错误: {result}")
    except Exception as e:
        log.warning(f"企业微信失败: {e}")


# ---------------------------------------------------------------------------
# 邮件（优化版，更多摘要内容）
# ---------------------------------------------------------------------------

def send_email(content, page_url):
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    to_email = os.environ.get("TO_EMAIL")

    if not all([smtp_server, smtp_user, smtp_pass, to_email]):
        log.info("邮件配置不完整，跳过")
        return

    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%m月%d日")
    headline = content.get("headline", "")

    stories_li = ""
    for s in content.get("top_stories", [])[:3]:
        summary_short = s.get("summary", "")[:120] + "..."
        stories_li += f'<li style="margin:8px 0"><a href="{s.get("url","#")}" style="color:#1d4ed8;text-decoration:none;font-weight:500">{html_escape(s.get("title",""))}</a><br><span style="font-size:13px;color:#78716c">{html_escape(summary_short)}</span></li>'

    gh_li = ""
    for g in content.get("github_picks", [])[:3]:
        gh_li += f'<li style="margin:6px 0"><strong>{html_escape(g.get("name",""))}</strong> (★{g.get("stars",0):,})<br><span style="font-size:13px;color:#78716c">{html_escape(g.get("summary","")[:80])}...</span></li>'

    email_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#fafaf9;font-family:-apple-system,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:20px 16px">
<table width="100%" style="max-width:560px">
<tr><td style="padding:0 0 12px">
  <p style="font-size:11px;color:#a8a29e;margin:0">{date_str}</p>
  <h1 style="font-size:18px;margin:2px 0">AI 日报</h1>
  <p style="font-size:13px;color:#92400e;background:#fef3c7;padding:6px 10px;border-radius:6px;margin:6px 0">{html_escape(headline)}</p>
</td></tr>
<tr><td>
  <h2 style="font-size:14px;margin:0 0 6px">今日必知</h2>
  <ul style="padding:0 0 0 16px;margin:0;font-size:13px">{stories_li}</ul>
</td></tr>
{"<tr><td style='padding:12px 0 0'><h2 style='font-size:14px;margin:0 0 6px'>热门项目</h2><ul style='padding:0 0 0 16px;margin:0;font-size:13px'>" + gh_li + "</ul></td></tr>" if gh_li else ""}
<tr><td style="padding:16px 0">
  <a href="{page_url}" style="display:inline-block;background:#1c1917;color:#fff;padding:8px 20px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:500">查看完整日报 →</a>
  <p style="font-size:11px;color:#a8a29e;margin:6px 0 0">含论文解读、大佬观点、落地信号等</p>
</td></tr>
<tr><td style="padding:12px 0 0;border-top:1px solid #e7e5e4">
  <p style="font-size:11px;color:#a8a29e;text-align:center">AI 日报 · DeepSeek + GitHub Actions</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"AI 日报 · {date_str} · {headline}"
    msg["From"] = f"AI 日报 <{smtp_user}>"
    msg["To"] = to_email
    msg.attach(MIMEText("请用 HTML 邮件客户端查看。", "plain", "utf-8"))
    msg.attach(MIMEText(email_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
        log.info(f"邮件已发送至 {to_email}")
    except Exception as e:
        log.warning(f"邮件发送失败: {e}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("AI 日报 v3 —— 开始生成")
    log.info("=" * 60)

    gh_token = os.environ.get("GITHUB_TOKEN")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    wecom_webhook = os.environ.get("WECOM_WEBHOOK_URL")
    pages_url = os.environ.get("PAGES_URL", "")

    # 采集数据
    log.info("📡 GitHub ...")
    github = fetch_github(gh_token)
    log.info(f"   {len(github)} 个项目")

    log.info("📡 Hacker News（含评论）...")
    hn = fetch_hn()
    log.info(f"   {len(hn)} 条话题")

    log.info("📡 arXiv ...")
    arxiv = fetch_arxiv()
    log.info(f"   {len(arxiv)} 篇论文")

    log.info("📡 Reddit ...")
    reddit = fetch_reddit()
    log.info(f"   {len(reddit)} 条帖子")

    log.info("📡 TechCrunch ...")
    techcrunch = fetch_techcrunch()
    log.info(f"   {len(techcrunch)} 条新闻")

    log.info("📡 Product Hunt ...")
    producthunt = fetch_producthunt()
    log.info(f"   {len(producthunt)} 个产品")

    # 生成内容
    log.info("🤖 DeepSeek 生成日报 ...")
    content = generate_content(github, hn, arxiv, reddit, techcrunch, producthunt, deepseek_key)

    # 渲染网页
    log.info("🎨 渲染网页 ...")
    html = render_webpage(content)

    now = datetime.now(CN_TZ)
    date_slug = now.strftime("%Y-%m-%d")
    docs = Path("docs")
    docs.mkdir(exist_ok=True)

    (docs / f"{date_slug}.html").write_text(html, encoding="utf-8")
    (docs / "index.html").write_text(html, encoding="utf-8")

    page_url = f"{pages_url.rstrip('/')}/{date_slug}.html" if pages_url else ""

    # 推送
    if wecom_webhook:
        log.info("📱 企业微信 ...")
        send_wecom(content, page_url, wecom_webhook)

    log.info("📧 邮件 ...")
    send_email(content, page_url or "#")

    log.info("=" * 60)
    log.info("完成！")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
