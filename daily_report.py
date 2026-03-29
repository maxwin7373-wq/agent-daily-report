#!/usr/bin/env python3
"""
AI 日报 v2 —— 每日 AI 信息过滤器

信息源：
  1. GitHub Trending — 热门 Agent/Skill/AI 项目
  2. Hacker News    — 技术社区 AI 热议话题
  3. arXiv          — 高影响力 AI 新论文
  4. 科技媒体       — 通过 HN + GitHub 间接覆盖产品发布/融资/落地

输出：
  1. GitHub Pages 精美网页（完整日报，卡片式，手机友好）
  2. 企业微信群机器人推送（摘要 + 网页链接）
  3. 邮件推送（备份渠道）

环境变量（必需）：
  TO_EMAIL       - 收件邮箱
  SMTP_SERVER / SMTP_PORT / SMTP_USER / SMTP_PASSWORD - 邮件配置

环境变量（可选）：
  DEEPSEEK_API_KEY    - DeepSeek API Key（生成中文简介）
  GITHUB_TOKEN        - GitHub Token（提升 API 限额）
  WECOM_WEBHOOK_URL   - 企业微信群机器人 Webhook URL
  PAGES_URL    - GitHub Pages 地址（如 https://用户名.github.io/agent-daily-report）
"""

import os
import json
import sys
import smtplib
import logging
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from pathlib import Path

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
MAX_HN_STORIES = 5
MAX_ARXIV_PAPERS = 3
CN_TZ = timezone(timedelta(hours=8))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 通用 HTTP 请求
# ---------------------------------------------------------------------------

def http_get(url: str, headers: dict | None = None, timeout: int = 30) -> bytes | None:
    """通用 GET 请求"""
    hdrs = {"User-Agent": "AIDailyReport/2.0"}
    if headers:
        hdrs.update(headers)
    try:
        req = Request(url, headers=hdrs)
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (HTTPError, URLError, TimeoutError) as e:
        log.warning(f"HTTP 请求失败: {url} -> {e}")
        return None


def http_get_json(url: str, headers: dict | None = None) -> dict | list | None:
    """GET 请求并解析 JSON"""
    data = http_get(url, headers)
    if data:
        try:
            return json.loads(data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
    return None


# ---------------------------------------------------------------------------
# 数据源 1：GitHub Trending
# ---------------------------------------------------------------------------

def fetch_github_trending(token: str | None = None) -> list[dict]:
    """通过 GitHub Search API 获取热门 AI 项目"""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repos = {}
    one_week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    for topic in SEARCH_TOPICS:
        url = (
            f"https://api.github.com/search/repositories"
            f"?q=topic:{quote(topic)}+pushed:>{one_week_ago}"
            f"&sort=stars&order=desc&per_page=8"
        )
        data = http_get_json(url, headers)
        if data and "items" in data:
            for item in data["items"]:
                repos[item["full_name"]] = item

    for q in ["AI agent framework 2026", "MCP server tool", "LLM agent skill"]:
        url = (
            f"https://api.github.com/search/repositories"
            f"?q={quote(q)}+pushed:>{one_week_ago}"
            f"&sort=stars&order=desc&per_page=5"
        )
        data = http_get_json(url, headers)
        if data and "items" in data:
            for item in data["items"]:
                repos[item["full_name"]] = item

    # 过滤和排序
    filtered = []
    for repo in sorted(repos.values(), key=lambda r: r.get("stargazers_count", 0), reverse=True):
        combined = (
            repo.get("full_name", "").lower() + " " +
            (repo.get("description") or "").lower() + " " +
            " ".join(t.lower() for t in repo.get("topics", []))
        )
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
                "license": (repo.get("license") or {}).get("spdx_id", "未标注"),
                "topics": repo.get("topics", [])[:6],
                "is_new": is_new,
            })

    return filtered[:MAX_GITHUB_PROJECTS]


# ---------------------------------------------------------------------------
# 数据源 2：Hacker News
# ---------------------------------------------------------------------------

def fetch_hacker_news() -> list[dict]:
    """获取 HN 上与 AI 相关的热门帖子"""
    # 获取 Top Stories
    top_ids = http_get_json("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not top_ids:
        return []

    ai_keywords = [
        "ai", "gpt", "llm", "claude", "openai", "anthropic", "deepseek",
        "gemini", "agent", "model", "transformer", "machine learning",
        "neural", "diffusion", "reasoning", "rag", "mcp", "skill",
        "copilot", "cursor", "coding assistant", "fine-tune", "embedding",
    ]

    stories = []
    for sid in top_ids[:60]:  # 检查前 60 条
        item = http_get_json(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
        if not item or item.get("type") != "story":
            continue
        title_lower = (item.get("title") or "").lower()
        if any(kw in title_lower for kw in ai_keywords):
            stories.append({
                "title": item.get("title", ""),
                "url": item.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                "hn_url": f"https://news.ycombinator.com/item?id={sid}",
                "score": item.get("score", 0),
                "comments": item.get("descendants", 0),
                "time": item.get("time", 0),
            })
        if len(stories) >= MAX_HN_STORIES:
            break

    return sorted(stories, key=lambda s: s["score"], reverse=True)[:MAX_HN_STORIES]


# ---------------------------------------------------------------------------
# 数据源 3：arXiv 论文
# ---------------------------------------------------------------------------

def fetch_arxiv_papers() -> list[dict]:
    """获取 arXiv 上最新的热门 AI 论文"""
    categories = ["cs.AI", "cs.CL", "cs.LG", "cs.MA"]
    query = "+OR+".join(f"cat:{c}" for c in categories)
    url = (
        f"http://export.arxiv.org/api/query"
        f"?search_query={query}"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&start=0&max_results=20"
    )
    data = http_get(url)
    if not data:
        return []

    # 简单 XML 解析（不引入额外依赖）
    text = data.decode("utf-8")
    papers = []
    entries = text.split("<entry>")[1:]  # 跳过 feed 头部

    ai_terms = [
        "agent", "llm", "language model", "transformer", "reasoning",
        "rag", "retrieval", "tool use", "multi-agent", "benchmark",
        "fine-tun", "prompt", "instruct", "reward", "rlhf", "alignment",
        "diffusion", "generation", "multimodal", "vision-language",
        "skill", "planning", "chain-of-thought", "mcp",
    ]

    for entry in entries:
        # 提取标题
        title_start = entry.find("<title>") + 7
        title_end = entry.find("</title>")
        title = entry[title_start:title_end].strip().replace("\n", " ")

        # 提取摘要
        summary_start = entry.find("<summary>") + 9
        summary_end = entry.find("</summary>")
        summary = entry[summary_start:summary_end].strip().replace("\n", " ")

        # 提取链接
        link = ""
        link_start = entry.find('<id>')
        if link_start >= 0:
            link_end = entry.find('</id>')
            link = entry[link_start + 4:link_end].strip()

        # 提取作者（取前3个）
        authors = []
        remaining = entry
        while "<name>" in remaining and len(authors) < 3:
            ns = remaining.find("<name>") + 6
            ne = remaining.find("</name>")
            authors.append(remaining[ns:ne].strip())
            remaining = remaining[ne + 7:]

        # 过滤：标题或摘要中包含 AI 相关词
        combined = (title + " " + summary).lower()
        if any(term in combined for term in ai_terms):
            papers.append({
                "title": title,
                "summary": summary[:500],
                "url": link,
                "authors": authors,
            })

        if len(papers) >= MAX_ARXIV_PAPERS:
            break

    return papers


# ---------------------------------------------------------------------------
# DeepSeek AI 内容生成
# ---------------------------------------------------------------------------

def generate_ai_content(github_projects, hn_stories, arxiv_papers, api_key):
    """调用 DeepSeek 生成完整的中文日报内容"""
    if not api_key:
        log.info("未配置 DEEPSEEK_API_KEY，使用基础模板")
        return _fallback_content(github_projects, hn_stories, arxiv_papers)

    # 构建输入数据
    gh_text = "\n".join(
        f"- {p['name']} ({p['stars']} star): {p['description']}"
        for p in github_projects
    )
    hn_text = "\n".join(
        f"- [{s['title']}]({s['url']}) (热度:{s['score']}分, {s['comments']}条讨论)"
        for s in hn_stories
    )
    arxiv_text = "\n".join(
        f"- {p['title']} (作者: {', '.join(p['authors'])})\n  摘要: {p['summary'][:200]}..."
        for p in arxiv_papers
    )

    prompt = f"""你是一个专业的 AI 行业分析师，负责为中文读者编写每日 AI 日报。

以下是今日从多个来源收集的 AI 相关信息：

【GitHub 热门项目】
{gh_text}

【Hacker News 热议】
{hn_text}

【arXiv 新论文】
{arxiv_text}

请根据以上信息，生成一份中文日报内容。严格按以下 JSON 格式返回：

{{
  "headline": "用一句话概括今天 AI 世界最重要的事（20字以内）",
  "top_stories": [
    {{
      "title": "新闻标题（中文）",
      "summary": "150-200字中文详细解读：发生了什么、为什么重要、对普通开发者/创业者意味着什么",
      "source": "来源（GitHub/HN/arXiv/媒体）",
      "url": "原文链接",
      "importance": "high/medium"
    }}
  ],
  "notable_voices": [
    {{
      "who": "人物名（如 Andrej Karpathy）",
      "said": "他说了什么 / 做了什么（中文总结，80-120字）",
      "source_url": "来源链接（如果有）"
    }}
  ],
  "papers": [
    {{
      "title": "论文标题（保留英文原标题）",
      "plain_explain": "用大白话解释这篇论文在说什么（100-150字，假设读者没有 ML 背景）",
      "why_care": "这对你有什么用？一句话说明实际意义",
      "url": "论文链接"
    }}
  ],
  "github_picks": [
    {{
      "name": "仓库全名 owner/repo",
      "summary": "150-200字中文详细简介：项目定位、核心功能、火爆原因、适合谁用",
      "stars": 数字,
      "url": "链接",
      "is_new": true/false,
      "tags": ["标签1", "标签2"]
    }}
  ],
  "landing_signal": {{
    "title": "落地风向标标题",
    "content": "100-150字：谁在用 AI 做了什么产品、拿了什么融资、找到了什么商业模式"
  }},
  "trends": [
    "趋势一：80-120字深度洞察",
    "趋势二：80-120字深度洞察",
    "趋势三：80-120字深度洞察"
  ]
}}

要求：
- 所有内容用中文，但项目名、人名、论文标题保留英文
- notable_voices 至少包含 2 条，可以从 HN 讨论中提取有价值的观点
- 语气专业但通俗，像一个懂行的朋友在给你聊天
- 每条信息都要回答「这对我有什么用」"""

    try:
        req_body = json.dumps({
            "model": "deepseek-chat",
            "max_tokens": 4000,
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "你是专业的 AI 行业分析师。请始终以 JSON 格式回复，不要包含 markdown 代码块标记。"},
                {"role": "user", "content": prompt},
            ],
        }).encode()

        req = Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=req_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())

        text = result["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]

        return json.loads(text.strip())

    except Exception as e:
        log.warning(f"DeepSeek API 调用失败: {e}")
        return _fallback_content(github_projects, hn_stories, arxiv_papers)


def _fallback_content(github_projects, hn_stories, arxiv_papers):
    """AI 不可用时的基础模板"""
    return {
        "headline": "AI 生态持续活跃，多个方向同步推进",
        "top_stories": [
            {
                "title": s["title"],
                "summary": f"Hacker News 热议话题，获得 {s['score']} 分和 {s['comments']} 条讨论。",
                "source": "Hacker News",
                "url": s["url"],
                "importance": "medium",
            }
            for s in hn_stories[:2]
        ],
        "notable_voices": [],
        "papers": [
            {
                "title": p["title"],
                "plain_explain": p["summary"][:200],
                "why_care": "值得关注的最新研究进展。",
                "url": p["url"],
            }
            for p in arxiv_papers[:2]
        ],
        "github_picks": [
            {
                "name": p["name"],
                "summary": p["description"],
                "stars": p["stars"],
                "url": p["url"],
                "is_new": p.get("is_new", False),
                "tags": p.get("topics", [])[:4],
            }
            for p in github_projects
        ],
        "landing_signal": {
            "title": "AI 落地应用持续涌现",
            "content": "本周多个 AI 项目在实际场景中取得进展，建议关注热门项目的应用案例。",
        },
        "trends": [
            "Agent 和 Skill 生态持续扩展，垂直化趋势明显。",
            "MCP 协议集成和多 Agent 协作仍是社区热点。",
            "科研自动化和代码辅助工具快速迭代中。",
        ],
    }


# ---------------------------------------------------------------------------
# GitHub Pages 网页生成
# ---------------------------------------------------------------------------

def render_webpage(content: dict) -> str:
    """渲染完整的卡片式网页日报"""
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%Y年%m月%d日")
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays[now.weekday()]

    headline = content.get("headline", "AI 生态持续活跃")
    top_stories = content.get("top_stories", [])
    voices = content.get("notable_voices", [])
    papers = content.get("papers", [])
    gh_picks = content.get("github_picks", [])
    landing = content.get("landing_signal", {})
    trends = content.get("trends", [])

    # 今日必知卡片
    stories_html = ""
    for s in top_stories[:3]:
        imp_color = "#dc2626" if s.get("importance") == "high" else "#d97706"
        imp_label = "重要" if s.get("importance") == "high" else "关注"
        stories_html += f"""
        <a href="{s.get('url','#')}" class="card" target="_blank">
          <div class="card-head">
            <span class="card-title">{s.get('title','')}</span>
            <span class="badge" style="background:{imp_color}15;color:{imp_color}">{imp_label}</span>
          </div>
          <p class="card-desc">{s.get('summary','')}</p>
          <span class="card-source">来源：{s.get('source','')}</span>
        </a>"""

    # 大佬说了啥
    voices_html = ""
    for v in voices[:3]:
        voices_html += f"""
        <div class="card">
          <div class="voice-who">{v.get('who','')}</div>
          <p class="card-desc">{v.get('said','')}</p>
        </div>"""

    # 论文
    papers_html = ""
    for p in papers[:2]:
        papers_html += f"""
        <a href="{p.get('url','#')}" class="card" target="_blank">
          <div class="card-title" style="font-size:13px">{p.get('title','')}</div>
          <p class="card-desc">{p.get('plain_explain','')}</p>
          <div class="paper-why">{p.get('why_care','')}</div>
        </a>"""

    # GitHub 项目
    github_html = ""
    for g in gh_picks[:5]:
        badge = '<span class="badge" style="background:#dcfce7;color:#166534">新项目</span>' if g.get("is_new") else ""
        stars = f"{g.get('stars',0):,}"
        tags_html = "".join(f'<span class="tag">{t}</span>' for t in g.get("tags", [])[:4])
        github_html += f"""
        <a href="{g.get('url','#')}" class="card" target="_blank">
          <div class="card-head">
            <span class="card-title">{g.get('name','')}</span>
            {badge}
          </div>
          <p class="card-desc">{g.get('summary','')}</p>
          <div class="card-meta">
            <span>Star <b>{stars}</b></span>
          </div>
          <div class="tags">{tags_html}</div>
        </a>"""

    # 落地风向标
    landing_html = ""
    if landing:
        landing_html = f"""
        <div class="card landing-card">
          <div class="card-title">{landing.get('title','')}</div>
          <p class="card-desc">{landing.get('content','')}</p>
        </div>"""

    # 趋势
    trends_html = ""
    for i, t in enumerate(trends[:3]):
        trends_html += f"""
        <div class="trend-item">
          <span class="trend-num">{i+1}</span>
          <p>{t}</p>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>AI 日报 · {date_str}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans SC',sans-serif;background:#f5f5f4;color:#1c1917;line-height:1.6;-webkit-font-smoothing:antialiased}}
.container{{max-width:640px;margin:0 auto;padding:16px}}
.header{{padding:20px 0 16px}}
.date{{font-size:12px;color:#a8a29e;letter-spacing:.5px}}
h1{{font-size:22px;font-weight:600;margin:4px 0}}
.headline{{font-size:15px;color:#78716c;margin:6px 0 0;padding:10px 14px;background:#fef3c7;border-radius:10px;color:#92400e;font-weight:500}}
.section{{margin:24px 0 0}}
.section-title{{font-size:16px;font-weight:600;margin:0 0 12px;display:flex;align-items:center;gap:8px}}
.section-icon{{width:24px;height:24px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:13px;color:#fff}}
.card{{display:block;background:#fff;border:1px solid #e7e5e4;border-radius:12px;padding:14px 16px;margin:0 0 10px;text-decoration:none;color:inherit;transition:border-color .15s}}
.card:hover{{border-color:#a8a29e}}
.card-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.card-title{{font-size:14px;font-weight:600;color:#1c1917}}
.badge{{font-size:11px;padding:2px 8px;border-radius:6px;white-space:nowrap;font-weight:500}}
.card-desc{{font-size:13px;color:#57534e;margin:8px 0;line-height:1.7}}
.card-source{{font-size:11px;color:#a8a29e}}
.card-meta{{display:flex;gap:16px;font-size:12px;color:#a8a29e;margin:8px 0 0}}
.card-meta b{{color:#78716c;font-weight:600}}
.voice-who{{font-size:14px;font-weight:600;color:#7c3aed;margin:0 0 4px}}
.paper-why{{font-size:12px;color:#0369a1;background:#e0f2fe;padding:6px 10px;border-radius:6px;margin:8px 0 0}}
.tags{{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0 0}}
.tag{{font-size:11px;padding:2px 8px;border-radius:6px;background:#f5f5f4;color:#78716c}}
.landing-card{{background:#fffbeb;border-color:#fde68a}}
.trend-item{{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px solid #f5f5f4}}
.trend-item:last-child{{border-bottom:none}}
.trend-num{{width:24px;height:24px;border-radius:50%;background:#e7e5e4;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;color:#78716c;flex-shrink:0}}
.trend-item p{{font-size:13px;color:#57534e;line-height:1.6}}
.footer{{text-align:center;padding:24px 0;font-size:12px;color:#a8a29e;line-height:1.8}}
@media(prefers-color-scheme:dark){{
body{{background:#1c1917;color:#e7e5e4}}
.card{{background:#292524;border-color:#44403c}}
.card:hover{{border-color:#78716c}}
.card-title{{color:#e7e5e4}}
.card-desc{{color:#a8a29e}}
.headline{{background:#451a03;color:#fbbf24}}
.landing-card{{background:#451a03;border-color:#92400e}}
.tag{{background:#292524;color:#a8a29e}}
.trend-num{{background:#44403c;color:#a8a29e}}
.trend-item{{border-color:#292524}}
.paper-why{{background:#0c4a6e;color:#7dd3fc}}
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

  <div class="section">
    <div class="section-title">
      <span class="section-icon" style="background:#dc2626">!</span> 今日必知
    </div>
    {stories_html}
  </div>

  {"<div class='section'><div class='section-title'><span class='section-icon' style=\"background:#7c3aed\">💬</span> 大佬说了啥</div>" + voices_html + "</div>" if voices_html else ""}

  {"<div class='section'><div class='section-title'><span class='section-icon' style=\"background:#0369a1\">📄</span> 值得读的论文</div>" + papers_html + "</div>" if papers_html else ""}

  <div class="section">
    <div class="section-title">
      <span class="section-icon" style="background:#059669">★</span> 热门开源项目
    </div>
    {github_html}
  </div>

  {"<div class='section'><div class='section-title'><span class='section-icon' style=\"background:#d97706\">📡</span> 落地风向标</div>" + landing_html + "</div>" if landing_html else ""}

  <div class="section">
    <div class="section-title">
      <span class="section-icon" style="background:#78716c">→</span> 趋势洞察
    </div>
    {trends_html}
  </div>

  <div class="footer">
    AI 日报 · 数据来源：GitHub / Hacker News / arXiv<br>
    由 DeepSeek + GitHub Actions 自动生成
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 企业微信群机器人推送
# ---------------------------------------------------------------------------

def send_wecom(content: dict, page_url: str, webhook_url: str):
    """通过企业微信群机器人发送 Markdown 摘要"""
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%m月%d日")
    headline = content.get("headline", "")

    # 构建 Markdown 消息
    lines = [f"## AI 日报 · {date_str}", ""]
    if headline:
        lines.append(f"> {headline}")
        lines.append("")

    lines.append("**今日必知：**")
    for s in content.get("top_stories", [])[:3]:
        lines.append(f"- [{s.get('title','')}]({s.get('url','')})")
    lines.append("")

    gh = content.get("github_picks", [])
    if gh:
        lines.append("**热门项目：**")
        for g in gh[:3]:
            stars = f"{g.get('stars',0):,}"
            lines.append(f"- **{g.get('name','')}** ({stars} star)")
        lines.append("")

    if page_url:
        lines.append(f"[👉 查看完整日报]({page_url})")

    markdown_text = "\n".join(lines)

    payload = json.dumps({
        "msgtype": "markdown",
        "markdown": {"content": markdown_text},
    }).encode()

    try:
        req = Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            if result.get("errcode") == 0:
                log.info("企业微信推送成功")
            else:
                log.warning(f"企业微信推送返回错误: {result}")
    except Exception as e:
        log.warning(f"企业微信推送失败: {e}")


# ---------------------------------------------------------------------------
# 邮件发送（备份渠道）
# ---------------------------------------------------------------------------

def send_email(content: dict, page_url: str):
    """发送精简摘要邮件"""
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    to_email = os.environ.get("TO_EMAIL")

    if not all([smtp_server, smtp_user, smtp_pass, to_email]):
        log.info("邮件配置不完整，跳过邮件发送")
        return

    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%m月%d日")
    headline = content.get("headline", "AI 生态持续活跃")

    # 构建精简邮件 HTML
    stories_li = ""
    for s in content.get("top_stories", [])[:3]:
        stories_li += f'<li style="margin:8px 0"><a href="{s.get("url","#")}" style="color:#1d4ed8;text-decoration:none;font-weight:500">{s.get("title","")}</a><br><span style="font-size:13px;color:#78716c">{s.get("summary","")[:100]}...</span></li>'

    email_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f5f4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 16px">
<table width="100%" style="max-width:560px">
<tr><td style="padding:0 0 16px">
  <p style="font-size:12px;color:#a8a29e;margin:0">{date_str}</p>
  <h1 style="font-size:20px;margin:4px 0">AI 日报</h1>
  <p style="font-size:14px;color:#92400e;background:#fef3c7;padding:8px 12px;border-radius:8px;margin:8px 0">{headline}</p>
</td></tr>
<tr><td>
  <h2 style="font-size:15px;margin:0 0 8px">今日必知</h2>
  <ul style="padding:0 0 0 16px;margin:0">{stories_li}</ul>
</td></tr>
<tr><td style="padding:20px 0">
  <a href="{page_url}" style="display:inline-block;background:#1c1917;color:#fff;padding:10px 24px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:500">查看完整日报 →</a>
</td></tr>
<tr><td style="padding:16px 0 0;border-top:1px solid #e7e5e4">
  <p style="font-size:12px;color:#a8a29e;text-align:center">AI 日报 · 由 DeepSeek + GitHub Actions 自动生成</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"AI 日报 · {date_str} · {headline}"
    msg["From"] = f"AI 日报 <{smtp_user}>"
    msg["To"] = to_email
    msg.attach(MIMEText("请使用 HTML 邮件客户端查看。", "plain", "utf-8"))
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
    log.info("AI 日报 v2 —— 开始生成")
    log.info("=" * 60)

    gh_token = os.environ.get("GITHUB_TOKEN")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    wecom_webhook = os.environ.get("WECOM_WEBHOOK_URL")
    pages_url = os.environ.get("PAGES_URL", "")

    # 第一步：抓取数据
    log.info("📡 采集 GitHub 热门项目 ...")
    github_projects = fetch_github_trending(gh_token)
    log.info(f"   找到 {len(github_projects)} 个项目")

    log.info("📡 采集 Hacker News 热议 ...")
    hn_stories = fetch_hacker_news()
    log.info(f"   找到 {len(hn_stories)} 条 AI 相关讨论")

    log.info("📡 采集 arXiv 新论文 ...")
    arxiv_papers = fetch_arxiv_papers()
    log.info(f"   找到 {len(arxiv_papers)} 篇论文")

    # 第二步：AI 生成内容
    log.info("🤖 调用 DeepSeek 生成中文日报内容 ...")
    content = generate_ai_content(github_projects, hn_stories, arxiv_papers, deepseek_key)

    # 第三步：生成网页
    log.info("🎨 渲染网页日报 ...")
    html = render_webpage(content)

    # 保存到 docs 目录（GitHub Pages 使用）
    now = datetime.now(CN_TZ)
    date_slug = now.strftime("%Y-%m-%d")
    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)

    # 保存当日页面
    daily_path = docs_dir / f"{date_slug}.html"
    daily_path.write_text(html, encoding="utf-8")
    log.info(f"   已保存至 {daily_path}")

    # 同时覆盖 index.html（最新一期）
    index_path = docs_dir / "index.html"
    index_path.write_text(html, encoding="utf-8")

    # 构建完整页面 URL
    if pages_url:
        page_url = f"{pages_url.rstrip('/')}/{date_slug}.html"
    else:
        page_url = ""

    # 第四步：推送通知
    if wecom_webhook:
        log.info("📱 推送企业微信 ...")
        send_wecom(content, page_url, wecom_webhook)

    log.info("📧 发送邮件 ...")
    send_email(content, page_url or "#")

    log.info("=" * 60)
    log.info("日报生成完毕！")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
