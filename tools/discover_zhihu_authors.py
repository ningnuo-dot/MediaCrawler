from __future__ import annotations

import argparse
import asyncio
import html
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parsel import Selector
from playwright.async_api import async_playwright

import config
from media_platform.zhihu.core import ZhihuCrawler
from media_platform.zhihu.field import SearchSort, SearchType
from media_platform.zhihu.login import ZhiHuLogin


DEFAULT_KEYWORDS = [
    "国学 人生智慧",
    "易经 人生",
    "道家 智慧",
    "传统文化 人生",
    "命理 人生",
    "因果 福报",
    "婚姻 国学",
    "人性 国学",
    "财富 国学",
    "风水 人生",
]


def text(value: Any) -> str:
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def content_from_search_item(item: dict[str, Any]) -> dict[str, Any] | None:
    obj = item.get("object") if isinstance(item.get("object"), dict) else item
    if obj.get("type") not in {"answer", "article"}:
        return None
    author = obj.get("author") or {}
    token = text(author.get("url_token") or author.get("urlToken"))
    author_id = text(author.get("id"))
    if not token and not author_id:
        return None
    content_id = text(obj.get("id"))
    content_type = text(obj.get("type"))
    question = obj.get("question") or {}
    title = text(question.get("title") or obj.get("title"))
    if content_type == "answer":
        url = f"https://www.zhihu.com/question/{question.get('id', '')}/answer/{content_id}"
    else:
        url = f"https://zhuanlan.zhihu.com/p/{content_id}"
    return {
        "author_id": author_id,
        "token": token,
        "name": text(author.get("name")),
        "headline": text(author.get("headline")),
        "content_id": content_id,
        "content_type": content_type,
        "title": title,
        "url": url,
        "voteup_count": number(obj.get("voteup_count")),
        "comment_count": number(obj.get("comment_count")),
        "excerpt": text(obj.get("excerpt") or obj.get("description"))[:300],
    }


def extract_profile(html_content: str, token: str, fallback: dict[str, Any]) -> dict[str, Any]:
    script = Selector(text=html_content).xpath("//script[@id='js-initialData']/text()").get(default="").strip()
    if not script:
        return fallback
    try:
        state = json.loads(script)
    except json.JSONDecodeError:
        return fallback
    users = state.get("initialState", {}).get("entities", {}).get("users", {})
    profile = users.get(token)
    if not profile:
        profile = next(
            (
                value
                for value in users.values()
                if text(value.get("urlToken") or value.get("url_token")) == token
            ),
            None,
        )
    if not profile:
        return fallback
    return {
        **fallback,
        "name": text(profile.get("name")) or fallback.get("name", ""),
        "headline": text(profile.get("headline")) or fallback.get("headline", ""),
        "followers": number(profile.get("followerCount")),
        "following": number(profile.get("followingCount")),
        "answers": number(profile.get("answerCount")),
        "articles": number(profile.get("articlesCount")),
        "total_upvotes": number(profile.get("voteupCount")),
    }


def normalize_log(value: int, maximum: int) -> float:
    return math.log1p(value) / math.log1p(maximum) if maximum > 0 else 0.0


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    max_followers = max((x.get("followers", 0) for x in candidates), default=0)
    max_upvotes = max((x.get("total_upvotes", 0) for x in candidates), default=0)
    max_answer_vote = max((x.get("max_answer_vote", 0) for x in candidates), default=0)
    max_topics = max((len(x.get("matched_keywords", [])) for x in candidates), default=1)
    for candidate in candidates:
        relevance = len(candidate["matched_keywords"]) / max_topics
        followers = normalize_log(candidate.get("followers", 0), max_followers)
        upvotes = normalize_log(candidate.get("total_upvotes", 0), max_upvotes)
        answer_vote = normalize_log(candidate.get("max_answer_vote", 0), max_answer_vote)
        candidate["score"] = round(40 * relevance + 20 * followers + 20 * upvotes + 20 * answer_vote, 1)
    return sorted(
        candidates,
        key=lambda x: (x["score"], x.get("followers", 0), x.get("total_upvotes", 0)),
        reverse=True,
    )


def write_report(path: Path, candidates: list[dict[str, Any]], keywords: list[str]) -> None:
    lines = [
        "# 知乎同类型国学作者候选名单",
        "",
        "> 用途：二次人工审核；本轮只采集公开主页指标与搜索代表作，未批量采集候选作者全文。",
        "",
        f"- 搜索主题：{'、'.join(keywords)}",
        f"- 候选人数：{len(candidates)}",
        "- 综合分：主题覆盖 40% + 粉丝 20% + 累计获赞 20% + 搜索代表作最高赞 20%",
        "- 指标为采集时点快照，最终是否同类型需人工阅读代表作确认。",
        "",
        "## 候选总表",
        "",
        "| 排名 | 作者 | 综合分 | 粉丝 | 累计获赞 | 回答 | 主题数 | 搜索最高赞 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for index, item in enumerate(candidates, 1):
        lines.append(
            f"| {index} | [{item['name'] or item['token']}]({item['profile_url']}) | {item['score']:.1f} | "
            f"{item.get('followers', 0):,} | {item.get('total_upvotes', 0):,} | {item.get('answers', 0):,} | "
            f"{len(item['matched_keywords'])} | {item.get('max_answer_vote', 0):,} |"
        )
    lines += ["", "## 审核卡片", ""]
    for index, item in enumerate(candidates, 1):
        lines += [
            f"### {index:02d}. {item['name'] or item['token']}",
            "",
            f"- [知乎主页]({item['profile_url']})",
            f"- 简介：{item.get('headline') or '无'}",
            f"- 粉丝：{item.get('followers', 0):,}",
            f"- 累计获赞：{item.get('total_upvotes', 0):,}",
            f"- 回答／文章：{item.get('answers', 0):,}／{item.get('articles', 0):,}",
            f"- 命中主题：{'、'.join(item['matched_keywords'])}",
            "- 搜索代表作：",
        ]
        for work in item.get("representative_works", [])[:3]:
            lines.append(
                f"  - [{work['title'] or '未命名内容'}]({work['url']})（赞同 {work['voteup_count']:,}，评论 {work['comment_count']:,}）"
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def discover(args: argparse.Namespace) -> list[dict[str, Any]]:
    keywords = args.keywords or DEFAULT_KEYWORDS
    crawler = ZhihuCrawler()
    config.PLATFORM = "zhihu"
    config.ENABLE_CDP_MODE = False
    config.SAVE_LOGIN_STATE = True

    async with async_playwright() as playwright:
        crawler.browser_context = await crawler.launch_browser(
            playwright.chromium, None, crawler.user_agent, headless=True
        )
        try:
            crawler.context_page = await crawler.browser_context.new_page()
            await crawler.context_page.goto(crawler.index_url, wait_until="domcontentloaded")
            crawler.zhihu_client = await crawler.create_zhihu_client(None)
            if not await crawler.zhihu_client.pong():
                login = ZhiHuLogin(
                    login_type="qrcode",
                    login_phone="",
                    browser_context=crawler.browser_context,
                    context_page=crawler.context_page,
                    cookie_str="",
                )
                await login.begin()
                await crawler.zhihu_client.update_cookies(crawler.browser_context, crawler.cookie_urls)
            await crawler.context_page.goto(
                "https://www.zhihu.com/search?q=%E5%9B%BD%E5%AD%A6&type=content",
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(3)
            await crawler.zhihu_client.update_cookies(crawler.browser_context, crawler.cookie_urls)

            author_map: dict[str, dict[str, Any]] = {}
            for keyword in keywords:
                for page in range(1, args.pages + 1):
                    params = {
                        "gk_version": "gz-gaokao",
                        "t": "general",
                        "q": keyword,
                        "correction": 1,
                        "offset": (page - 1) * 20,
                        "limit": 20,
                        "filter_fields": "",
                        "lc_idx": (page - 1) * 20,
                        "show_all_topics": 0,
                        "search_source": "Filter",
                        "time_interval": "",
                        "sort": SearchSort.UPVOTED_COUNT.value,
                        "vertical": SearchType.ANSWER.value,
                    }
                    result = await crawler.zhihu_client.get("/api/v4/search_v3", params)
                    for raw_item in result.get("data", []):
                        work = content_from_search_item(raw_item)
                        if not work:
                            continue
                        token = work["token"] or work["author_id"]
                        if token in {args.exclude_token, args.exclude_id}:
                            continue
                        candidate = author_map.setdefault(
                            token,
                            {
                                "token": token,
                                "name": work["name"],
                                "headline": work["headline"],
                                "matched_keywords": set(),
                                "works": {},
                            },
                        )
                        candidate["matched_keywords"].add(keyword)
                        candidate["works"][work["content_id"]] = work
                    await asyncio.sleep(args.delay)

            shortlist = sorted(
                author_map.values(),
                key=lambda x: (
                    len(x["matched_keywords"]),
                    max((w["voteup_count"] for w in x["works"].values()), default=0),
                ),
                reverse=True,
            )[: args.profile_limit]

            candidates: list[dict[str, Any]] = []
            for index, candidate in enumerate(shortlist, 1):
                token = candidate["token"]
                fallback = {
                    **candidate,
                    "followers": 0,
                    "following": 0,
                    "answers": 0,
                    "articles": 0,
                    "total_upvotes": 0,
                }
                try:
                    profile_html = await crawler.zhihu_client.get(
                        f"/people/{token}", return_response=True
                    )
                    candidate = extract_profile(profile_html, token, fallback)
                except Exception:
                    candidate = fallback
                works = sorted(
                    candidate.pop("works").values(),
                    key=lambda x: (x["voteup_count"], x["comment_count"]),
                    reverse=True,
                )
                candidate["matched_keywords"] = sorted(candidate["matched_keywords"])
                candidate["representative_works"] = works[:5]
                candidate["max_answer_vote"] = max((x["voteup_count"] for x in works), default=0)
                candidate["profile_url"] = f"https://www.zhihu.com/people/{token}"
                if candidate.get("followers", 0) >= args.min_followers or candidate.get("total_upvotes", 0) >= args.min_upvotes:
                    candidates.append(candidate)
                await asyncio.sleep(args.delay)
            return rank_candidates(candidates)[: args.output_limit]
        finally:
            await crawler.browser_context.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--keywords", nargs="*")
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--profile-limit", type=int, default=60)
    parser.add_argument("--output-limit", type=int, default=30)
    parser.add_argument("--min-followers", type=int, default=1000)
    parser.add_argument("--min-upvotes", type=int, default=5000)
    parser.add_argument("--delay", type=float, default=0.8)
    parser.add_argument("--exclude-token", default="d82bc38265fd52b8a5667720164a21e4")
    parser.add_argument("--exclude-id", default="d82bc38265fd52b8a5667720164a21e4")
    args = parser.parse_args()
    candidates = asyncio.run(discover(args))
    keywords = args.keywords or DEFAULT_KEYWORDS
    write_report(args.output, candidates, keywords)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"candidates": len(candidates), "report": str(args.output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
