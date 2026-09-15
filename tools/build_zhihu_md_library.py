from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def clean_text(value: Any) -> str:
    value = str(value or "")
    # Some Zhihu posts use RLO/PDF pairs to visually reverse short Chinese
    # fragments. Restore the intended character order before removing all
    # invisible bidi and zero-width formatting controls.
    rlo_fragment = re.compile("\u202e([^\u202c]*)\u202c")
    while rlo_fragment.search(value):
        value = rlo_fragment.sub(lambda match: match.group(1)[::-1], value)
    value = re.sub("[\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]", "", value)
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def display_title(item: dict[str, Any]) -> str:
    title = clean_text(item.get("title"))
    if title:
        return title
    seed = clean_text(item.get("desc")) or clean_text(item.get("content_text"))
    seed = re.sub(r"\s+", " ", seed)
    return (seed[:56] + "…") if len(seed) > 56 else (seed or f"知乎回答 {item.get('content_id', '')}")


def md_cell(value: Any) -> str:
    return clean_text(value).replace("|", "\\|").replace("\n", " ")


def timestamp_text(value: Any) -> str:
    ts = safe_int(value)
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except (OverflowError, OSError, ValueError):
        return ""


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    records: dict[str, dict[str, Any]] = {}
    invalid = 0
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            content_id = clean_text(item.get("content_id"))
            if not content_id:
                invalid += 1
                continue
            previous = records.get(content_id)
            if previous is None or safe_int(item.get("last_modify_ts")) >= safe_int(previous.get("last_modify_ts")):
                records[content_id] = item
    return list(records.values()), invalid


def write_rank(path: Path, title: str, items: list[dict[str, Any]], key: str) -> None:
    lines = [
        f"# {title}",
        "",
        f"共 {len(items)} 条；收藏数未由 MediaCrawler 的知乎接口提供。",
        "",
        "| 排名 | 回答 | 点赞 | 评论 | 收藏 | 综合热度 | 发布时间 |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    for rank, item in enumerate(items, 1):
        content_id = clean_text(item.get("content_id"))
        item_path = f"answers/{content_id}.md"
        lines.append(
            f"| {rank} | [{md_cell(display_title(item))}]({item_path}) | "
            f"{safe_int(item.get('voteup_count'))} | {safe_int(item.get('comment_count'))} | "
            f"不可用 | {safe_int(item.get('heat_score'))} | {timestamp_text(item.get('created_time'))[:10]} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_answer(
    path: Path,
    item: dict[str, Any],
    source_author: str,
    source_profile: str,
) -> None:
    content_id = clean_text(item.get("content_id"))
    title = display_title(item)
    lines = [
        "---",
        'platform: "知乎"',
        'content_type: "answer"',
        f'content_id: "{content_id}"',
        f'question_id: "{clean_text(item.get("question_id"))}"',
        f'source_author_internal: "{source_author.replace(chr(34), chr(39))}"',
        f'source_profile_internal: "{source_profile}"',
        f'created_at: "{timestamp_text(item.get("created_time"))}"',
        f'updated_at: "{timestamp_text(item.get("updated_time"))}"',
        f'likes: {safe_int(item.get("voteup_count"))}',
        f'comments: {safe_int(item.get("comment_count"))}',
        "favorites: null",
        f'heat_score: {safe_int(item.get("heat_score"))}',
        f'source: "{clean_text(item.get("content_url"))}"',
        "---",
        "",
        f"# {title}",
        "",
        "## 数据表现",
        "",
        f"- 点赞：{safe_int(item.get('voteup_count'))}",
        f"- 评论：{safe_int(item.get('comment_count'))}",
        "- 收藏：平台接口未提供",
        f"- 综合热度：{safe_int(item.get('heat_score'))}（点赞 + 2×评论）",
        "",
        "## 正文",
        "",
        clean_text(item.get("content_text")),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return ordered[index]


def write_operations_analysis(path: Path, items: list[dict[str, Any]]) -> None:
    likes = [safe_int(x.get("voteup_count")) for x in items]
    comments = [safe_int(x.get("comment_count")) for x in items]
    total_likes = sum(likes)
    total_comments = sum(comments)
    ranked = sorted(items, key=lambda x: safe_int(x.get("heat_score")), reverse=True)
    top_10_count = max(1, len(items) // 10)
    top_10 = ranked[:top_10_count]
    top_10_likes = sum(safe_int(x.get("voteup_count")) for x in top_10)

    year_stats: dict[str, dict[str, int]] = {}
    length_stats: dict[str, list[dict[str, Any]]] = {
        "短（<500字）": [],
        "中（500–1499字）": [],
        "长（≥1500字）": [],
    }
    for item in items:
        created = timestamp_text(item.get("created_time"))
        year = created[:4] if created else "未知"
        stat = year_stats.setdefault(year, {"count": 0, "likes": 0, "comments": 0})
        stat["count"] += 1
        stat["likes"] += safe_int(item.get("voteup_count"))
        stat["comments"] += safe_int(item.get("comment_count"))
        length = len(clean_text(item.get("content_text")))
        bucket = "短（<500字）" if length < 500 else ("中（500–1499字）" if length < 1500 else "长（≥1500字）")
        length_stats[bucket].append(item)

    lines = [
        "# 知乎账号运营数据分析",
        "",
        "> 本报告只描述已采集的公开回答数据；综合热度 = 点赞 + 2×评论。收藏数因接口未提供，不参与计算。",
        "",
        "## 核心指标",
        "",
        f"- 回答总数：{len(items)}",
        f"- 累计点赞：{total_likes}",
        f"- 累计评论：{total_comments}",
        f"- 平均点赞：{total_likes / len(items):.1f}" if items else "- 平均点赞：0",
        f"- 点赞中位数：{statistics.median(likes):.1f}" if likes else "- 点赞中位数：0",
        f"- 平均评论：{total_comments / len(items):.1f}" if items else "- 平均评论：0",
        f"- 评论中位数：{statistics.median(comments):.1f}" if comments else "- 评论中位数：0",
        f"- 点赞 Top 10% 门槛：{percentile(likes, 0.90)}",
        f"- 评论 Top 10% 门槛：{percentile(comments, 0.90)}",
        f"- 头部 10% 回答贡献点赞占比：{(top_10_likes / total_likes * 100):.1f}%" if total_likes else "- 头部 10% 回答贡献点赞占比：0%",
        "",
        "## 内容长度表现",
        "",
        "| 长度 | 回答数 | 平均点赞 | 平均评论 |",
        "|---|---:|---:|---:|",
    ]
    for label, bucket_items in length_stats.items():
        count = len(bucket_items)
        avg_likes = sum(safe_int(x.get("voteup_count")) for x in bucket_items) / count if count else 0
        avg_comments = sum(safe_int(x.get("comment_count")) for x in bucket_items) / count if count else 0
        lines.append(f"| {label} | {count} | {avg_likes:.1f} | {avg_comments:.1f} |")

    lines += [
        "",
        "## 年度表现",
        "",
        "| 年份 | 回答数 | 累计点赞 | 平均点赞 | 累计评论 |",
        "|---|---:|---:|---:|---:|",
    ]
    for year in sorted(year_stats, reverse=True):
        stat = year_stats[year]
        lines.append(
            f"| {year} | {stat['count']} | {stat['likes']} | {stat['likes'] / stat['count']:.1f} | {stat['comments']} |"
        )

    lines += [
        "",
        "## 运营使用建议",
        "",
        "1. 从综合热度排行前 10% 建立核心选题池，优先拆解标题钩子、开场、论点和案例结构。",
        "2. 点赞高但评论低的回答适合改造成观点卡片、短视频口播和清单；评论高的回答适合做争议议题与互动选题。",
        "3. 二次创作时保留原文链接与事实来源，避免逐字搬运；重新组织观点、补充案例并形成自己的判断。",
        "4. 每次重新采集后运行构建脚本，排行榜和本报告会基于最新指标刷新。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_clean_collection(path: Path, items: list[dict[str, Any]], author_name: str) -> None:
    ordered = sorted(
        items,
        key=lambda x: (
            safe_int(x.get("heat_score")),
            safe_int(x.get("voteup_count")),
            safe_int(x.get("comment_count")),
            safe_int(x.get("created_time")),
            safe_int(x.get("content_id")),
        ),
        reverse=True,
    )
    lines = [
        f"# {author_name}·纯净知乎回答库",
        "",
        f"> 标签：知乎回答｜收录：{len(ordered)} 篇｜排序：综合热度从高到低",
        "",
        "> 综合热度 = 点赞 + 2×评论；同热度时按点赞、评论、发布时间依次排序。标题为该回答对应的知乎问题标题。",
        "",
        "---",
        "",
    ]
    for index, item in enumerate(ordered, 1):
        title = display_title(item)
        lines += [
            f"## {index:04d}. {title}",
            "",
            f"> 日期：{timestamp_text(item.get('created_time'))[:10]}｜点赞：{safe_int(item.get('voteup_count')):,}｜评论：{safe_int(item.get('comment_count')):,}｜综合热度：{safe_int(item.get('heat_score')):,}",
            f"> 原文：{clean_text(item.get('content_url'))}",
            "",
            clean_text(item.get("content_text")),
            "",
            "---",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a Markdown material library from MediaCrawler Zhihu JSONL data.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-author", default="知乎来源作者（内部标识）")
    parser.add_argument("--source-profile", default="")
    parser.add_argument("--author-folder-name", default="知乎作者")
    args = parser.parse_args()

    items, invalid = load_jsonl(args.input)
    answers = [item for item in items if clean_text(item.get("content_type")) == "answer"]
    for item in answers:
        item["heat_score"] = safe_int(item.get("voteup_count")) + 2 * safe_int(item.get("comment_count"))

    output = args.output
    answers_dir = output / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    for item in answers:
        write_answer(
            answers_dir / f"{clean_text(item.get('content_id'))}.md",
            item,
            args.source_author,
            args.source_profile,
        )

    by_heat = sorted(answers, key=lambda x: (safe_int(x.get("heat_score")), safe_int(x.get("voteup_count"))), reverse=True)
    by_likes = sorted(answers, key=lambda x: (safe_int(x.get("voteup_count")), safe_int(x.get("comment_count"))), reverse=True)
    by_comments = sorted(answers, key=lambda x: (safe_int(x.get("comment_count")), safe_int(x.get("voteup_count"))), reverse=True)
    write_rank(output / "rank_by_heat.md", "知乎回答综合热度排行", by_heat, "heat_score")
    write_rank(output / "rank_by_likes.md", "知乎回答点赞排行", by_likes, "voteup_count")
    write_rank(output / "rank_by_comments.md", "知乎回答评论排行", by_comments, "comment_count")
    write_operations_analysis(output / "operations_analysis.md", answers)
    write_clean_collection(output / f"{args.author_folder_name}_纯净合集.md", answers, args.author_folder_name)

    total_likes = sum(safe_int(x.get("voteup_count")) for x in answers)
    total_comments = sum(safe_int(x.get("comment_count")) for x in answers)
    top = by_heat[:20]
    readme = [
        "# 知乎运营素材库",
        "",
        f"- 回答数：{len(answers)}",
        f"- 累计点赞：{total_likes}",
        f"- 累计评论：{total_comments}",
        "- 收藏数：MediaCrawler 当前知乎接口未提供",
        f"- 无效原始记录：{invalid}",
        f"- 构建时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}",
        "",
        "## 排行入口",
        "",
        "- [综合热度排行](rank_by_heat.md)",
        "- [点赞排行](rank_by_likes.md)",
        "- [评论排行](rank_by_comments.md)",
        "- [运营数据分析](operations_analysis.md)",
        "",
        "## 综合热度 Top 20",
        "",
        "| 排名 | 回答 | 点赞 | 评论 | 热度 |",
        "|---:|---|---:|---:|---:|",
    ]
    for rank, item in enumerate(top, 1):
        content_id = clean_text(item.get("content_id"))
        readme.append(
            f"| {rank} | [{md_cell(display_title(item))}](answers/{content_id}.md) | "
            f"{safe_int(item.get('voteup_count'))} | {safe_int(item.get('comment_count'))} | {safe_int(item.get('heat_score'))} |"
        )
    (output / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    provenance = [
        "# 内部来源说明",
        "",
        "> 本文件仅用于内部溯源，不在后续内容展示中引用作者名。",
        "",
        f"- 来源平台：知乎",
        f"- 来源作者标识：{args.source_author}",
        f"- 作者主页：{args.source_profile}",
        "- 数据范围：该主页公开回答正文及采集时点的点赞、评论数据",
        "- 收藏数据：MediaCrawler 当前知乎接口未提供",
        "",
    ]
    (output / "_内部来源说明.md").write_text("\n".join(provenance), encoding="utf-8")
    print(json.dumps({"answers": len(answers), "invalid": invalid, "output": str(output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
