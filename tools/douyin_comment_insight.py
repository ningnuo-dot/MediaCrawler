from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import jieba
from wordcloud import WordCloud


STOPWORDS = set("的 了 是 我 你 他 她 它 我们 你们 他们 这个 那个 一个 就 也 都 在 有 和 与 及 啊 呀 吧 呢 吗 哦 哈哈哈 哈哈 真的 感觉 觉得 还是 不是 没有 可以 怎么 什么 为什么 因为 所以 但是 然后 已经 这样 那样 自己 现在 看到 视频 作者 抖音 评论 回复".split())


def number(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def load_jsonl(path: Path) -> list[dict]:
    rows, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = str(item.get("comment_id") or "")
        if cid and cid not in seen:
            seen.add(cid); rows.append(item)
    return rows


def tokens(text: str) -> list[str]:
    clean = re.sub(r"https?://\S+|@[\w\-一-龥]+|[^0-9A-Za-z一-龥]+", " ", text or "")
    return [w.strip().lower() for w in jieba.cut(clean) if len(w.strip()) >= 2 and w.strip().lower() not in STOPWORDS and not w.isdigit()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("comments", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", default="")
    parser.add_argument("--title", default="抖音单视频评论洞察")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = load_jsonl(args.comments)
    by_id = {str(x.get("comment_id")): x for x in rows}
    children: dict[str, list[dict]] = defaultdict(list)

    def root_id(item: dict) -> str:
        cid = str(item.get("comment_id") or "")
        parent = str(item.get("parent_comment_id") or "0")
        visited = {cid}
        while parent not in {"", "0"} and parent in by_id and parent not in visited:
            visited.add(parent); cid = parent
            parent = str(by_id[parent].get("parent_comment_id") or "0")
        return cid

    roots = [x for x in rows if str(x.get("parent_comment_id") or "0") in {"", "0"}]
    for item in rows:
        rid = root_id(item)
        if str(item.get("comment_id")) != rid:
            children[rid].append(item)

    kept, skipped = [], []
    frequencies: Counter[str] = Counter()
    for item in roots:
        cid = str(item.get("comment_id") or "")
        replies = children.get(cid, [])
        reply_count = max(number(item.get("sub_comment_count")), len(replies))
        participants = {str(x.get("creator_hash") or x.get("nickname") or x.get("comment_id")) for x in replies}
        participants.discard("")
        unique_people = len(participants)
        likes = number(item.get("like_count"))
        low_diversity = reply_count >= 8 and unique_people <= 3
        extreme_loop = reply_count >= 15 and unique_people / max(reply_count, 1) < 0.25
        is_noise = likes <= 2 and low_diversity or likes < 5 and extreme_loop
        diversity = min(1.0, unique_people / max(3.0, math.sqrt(max(reply_count, 1)))) if reply_count else 0
        score = round(math.log1p(likes) * 6 + math.log1p(reply_count) * 3 * diversity, 2)
        record = {**item, "reply_count": reply_count, "unique_reply_users": unique_people, "effective_score": score, "filtered_as_loop": is_noise}
        (skipped if is_noise else kept).append(record)
        if not is_noise:
            weight = max(1.0, 1 + math.log1p(likes) + math.log1p(reply_count) * diversity)
            for word in tokens(str(item.get("content") or "")):
                frequencies[word] += weight
            for reply in replies:
                for word in tokens(str(reply.get("content") or "")):
                    frequencies[word] += max(0.5, weight * 0.35)

    kept.sort(key=lambda x: (x["effective_score"], number(x.get("like_count"))), reverse=True)
    words = frequencies.most_common(100)
    font = next((p for p in [Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simhei.ttf")] if p.exists()), None)
    if frequencies and font:
        WordCloud(width=1600, height=900, background_color="#f7f3ea", colormap="OrRd", font_path=str(font), max_words=100, collocations=False).generate_from_frequencies(frequencies).to_file(str(args.output / "评论词云.png"))

    lines = [f"# {args.title}", "", f"> 来源：{args.source}", f"> 采集评论：{len(rows)}｜一级评论：{len(roots)}｜有效一级评论：{len(kept)}｜排除疑似少数用户灌水：{len(skipped)}", f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "", "## 加权规则", "", "- 点赞是主要权重；楼中楼只在参与用户具有一定多样性时增加权重。", "- 一级评论点赞不超过 2、回复不少于 8 且回复参与者不超过 3 人时，视为疑似少数用户反复灌水。", "- 回复不少于 15、参与者占比低于 25% 且点赞少于 5 时，同样排除。", "", "## 高频关键词", "", "| 排名 | 关键词 | 加权频次 |", "|---:|---|---:|"]
    for i, (word, weight) in enumerate(words[:50], 1):
        lines.append(f"| {i} | {word} | {weight:.1f} |")
    lines += ["", "## 有效互动评论排行", "", "| 排名 | 评论 | 点赞 | 楼中楼 | 参与人数 | 有效分 |", "|---:|---|---:|---:|---:|---:|"]
    for i, item in enumerate(kept[:100], 1):
        content = str(item.get("content") or "").replace("|", "｜").replace("\n", " ")
        lines.append(f"| {i} | {content} | {number(item.get('like_count'))} | {item['reply_count']} | {item['unique_reply_users']} | {item['effective_score']} |")
    (args.output / "评论洞察.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (args.output / "评论明细.json").write_text(json.dumps({"effective": kept, "filtered": skipped, "keywords": words}, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"title": args.title, "comments": len(rows), "root_comments": len(roots), "effective": len(kept), "filtered": len(skipped), "output": str(args.output.resolve())}
    (args.output / "任务结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("INSIGHT_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
