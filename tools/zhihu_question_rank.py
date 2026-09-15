from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from media_platform.zhihu.core import ZhihuCrawler
from media_platform.zhihu.login import ZhiHuLogin
from tools import utils


def question_id(value: str) -> str:
    match = re.search(r"(?:question/)?(\d{5,})", value)
    if not match:
        raise ValueError("无法从输入中识别知乎问题 ID")
    return match.group(1)


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return (value[:80] or "知乎问题")


def md_escape(value: str) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


async def run(source: str, top_n: int, output: Path, cache_root: Path | None = None) -> None:
    qid = question_id(source)
    output.mkdir(parents=True, exist_ok=True)
    ranking_cache = output / "采集缓存"
    library_output = output / "入库文件"
    ranking_cache.mkdir(exist_ok=True)
    library_output.mkdir(exist_ok=True)
    config.PLATFORM = "zhihu"
    config.LOGIN_TYPE = "qrcode"
    config.HEADLESS = False
    config.ENABLE_CDP_MODE = False

    crawler = ZhihuCrawler()
    async with async_playwright() as playwright:
        crawler.browser_context = await crawler.launch_browser(
            playwright.chromium, None, crawler.user_agent, headless=False
        )
        await crawler.browser_context.add_init_script(path="libs/stealth.min.js")
        crawler.context_page = await crawler.browser_context.new_page()
        await crawler.context_page.goto(crawler.index_url, wait_until="domcontentloaded")
        crawler.zhihu_client = await crawler.create_zhihu_client(None)
        if not await crawler.zhihu_client.pong():
            login = ZhiHuLogin(
                login_type="qrcode", login_phone="", browser_context=crawler.browser_context,
                context_page=crawler.context_page, cookie_str=config.COOKIES,
            )
            await login.begin()
            await crawler.zhihu_client.update_cookies(crawler.browser_context, crawler.cookie_urls)

        await crawler.context_page.goto(f"https://www.zhihu.com/question/{qid}", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await crawler.zhihu_client.update_cookies(crawler.browser_context, crawler.cookie_urls)

        rows: dict[str, dict] = {}
        offset, limit, total = 0, 20, None
        while True:
            result = await crawler.zhihu_client.get_question_answers(qid, offset, limit)
            paging = result.get("paging", {})
            total = paging.get("totals", total)
            for item in result.get("data", []):
                aid = str(item.get("id") or "")
                if not aid:
                    continue
                likes = int(item.get("voteup_count") or 0)
                comments = int(item.get("comment_count") or 0)
                rows[aid] = {
                    "answer_id": aid,
                    "question_id": qid,
                    "question_title": item.get("question", {}).get("title", ""),
                    "author": item.get("author", {}).get("name", "匿名用户"),
                    "likes": likes,
                    "comments": comments,
                    "heat": likes + 2 * comments,
                    "created_time": int(item.get("created_time") or 0),
                    "excerpt": item.get("excerpt", ""),
                    "url": f"https://www.zhihu.com/question/{qid}/answer/{aid}",
                }
            print(f"RANK_PROGRESS={len(rows)}/{total or '?'}", flush=True)
            if paging.get("is_end") or not result.get("data"):
                break
            offset += limit
            await asyncio.sleep(0.8)

        ranking = sorted(rows.values(), key=lambda x: (x["heat"], x["likes"], x["comments"]), reverse=True)
        title = next((x["question_title"] for x in ranking if x["question_title"]), f"知乎问题 {qid}")
        (ranking_cache / "全部回答热度排行.json").write_text(json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8")
        rank_lines = [f"# {title}·全部回答热度排行", "", f"> 问题：https://www.zhihu.com/question/{qid}", f"> 可读取回答：{len(ranking)}｜综合热度＝点赞＋2×评论", "", "| 排名 | 回答作者 | 点赞 | 评论 | 综合热度 | 原文 |", "|---:|---|---:|---:|---:|---|"]
        for i, item in enumerate(ranking, 1):
            rank_lines.append(f"| {i} | {md_escape(item['author'])} | {item['likes']} | {item['comments']} | {item['heat']} | [打开]({item['url']}) |")
        (ranking_cache / "全部回答热度排行.md").write_text("\n".join(rank_lines) + "\n", encoding="utf-8")

        selected = ranking[: min(top_n, len(ranking))]
        answers_dir = library_output / "answers"
        answers_dir.mkdir(exist_ok=True)
        generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        collection = [
            "---", 'platform: "知乎"', 'content_type: "question_answer_collection"',
            f'question_id: "{qid}"', f'title: "{title.replace(chr(34), chr(39))}"',
            f'total_public_answers: {len(ranking)}', f'selected_answers: {len(selected)}',
            'heat_formula: "likes + 2 * comments"', f'source: "https://www.zhihu.com/question/{qid}"',
            f'generated_at: "{generated_at}"', "---", "", f"# {title}", "",
            f"> 已收录综合热度前 {len(selected)} 篇回答全文",
            f"> 原问题：https://www.zhihu.com/question/{qid}", "",
        ]
        collection.extend([f"## 热度前 {len(selected)} 篇回答全文", ""])
        downloaded = 0
        reused = 0
        for index, item in enumerate(selected, 1):
            cached = None
            if cache_root and cache_root.exists():
                cached = next(cache_root.glob(f"**/answers/{item['answer_id']}.md"), None)
            if cached:
                cached_text = cached.read_text(encoding="utf-8")
                parts = cached_text.split("\n\n", 3)
                content_text = parts[3].strip() if len(parts) == 4 else cached_text.strip()
                created = datetime.fromtimestamp(item["created_time"]).strftime("%Y-%m-%d") if item["created_time"] else ""
                reused += 1
            else:
                detail = await crawler.zhihu_client.get_answer_info(qid, item["answer_id"])
                if not detail:
                    continue
                created = datetime.fromtimestamp(detail.created_time).strftime("%Y-%m-%d") if detail.created_time else ""
                content_text = detail.content_text.strip()
            header = [f"# {title}", "", f"> 排名：{index}｜日期：{created}｜点赞：{item['likes']}｜评论：{item['comments']}｜综合热度：{item['heat']}", f"> 原文：{item['url']}", "", content_text, ""]
            text = "\n".join(header)
            (answers_dir / f"{item['answer_id']}.md").write_text(text, encoding="utf-8")
            collection.extend([f"### {index:04d}. {item['author']} 的回答", "", f"> 点赞：{item['likes']}｜评论：{item['comments']}｜综合热度：{item['heat']}", f"> 原文：{item['url']}", "", content_text, "", "---", ""])
            downloaded += 1
            print(f"DOWNLOAD_PROGRESS={downloaded}/{len(selected)}", flush=True)
            await asyncio.sleep(0.8)
        collection_path = library_output / "问题回答合集.md"
        collection_path.write_text("\n".join(collection), encoding="utf-8")
        summary = {"question_id": qid, "title": title, "total": len(ranking), "requested": top_n, "downloaded": downloaded, "reused": reused, "new_downloads": downloaded - reused, "collection_file": str(collection_path.resolve()), "output": str(output.resolve())}
        (output / "任务结果.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("RESULT=" + json.dumps(summary, ensure_ascii=False), flush=True)
        await crawler.browser_context.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--top", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path)
    args = parser.parse_args()
    if not 1 <= args.top <= 1000:
        raise SystemExit("top 必须在 1 到 1000 之间")
    asyncio.run(run(args.source, args.top, args.output, args.cache_root))


if __name__ == "__main__":
    main()
