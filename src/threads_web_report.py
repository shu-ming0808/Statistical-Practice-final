"""Describe a single-platform public search sample, never platform totals.

Input is a JSONL log produced by threads_web_collect.py or ptt_web_collect.py. This
module makes no network requests and never reads browser or API credentials.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Callable, Iterable
import unicodedata


DEFAULT_FONT = Path("C:/Windows/Fonts/mingliu.ttc")
TAIPEI = timezone(timedelta(hours=8))
SOURCE_TYPE = "threads_visible_web"
SOURCE_LABEL = "Threads 公開搜尋樣本，非全量"
SOURCE_LABELS = {SOURCE_TYPE: SOURCE_LABEL, "ptt_public_web": "PTT 指定看板標題搜尋樣本，非全量"}
PURPOSE_LABEL = "活動期間回顧性探索，不構成因果證據"
DISPLAY_EXCLUSIONS = frozenset({"大稻埕", "煙火", "烟火", "大稻埕夏日節", "夏日節", "花火", "煙火秀"})
STOPWORDS = frozenset(
    "的 了 是 在 和 與 及 或 也 都 就 很 到 把 被 為 對 以 而 但 又 等 "
    "我們 你們 他們 她們 自己 這個 那個 這些 那些 這裡 那裡 什麼 怎麼 "
    "可以 可能 就是 不是 因為 所以 還是 但是 不過 然後 已經 真的 一個 一下 "
    "一下子 一起 一直 今天 昨天 明天 現在 這次 這樣 那樣 看到 覺得 知道 "
    "大家 有人 沒有 還有 以及 其中 相關 更多 分享 轉發 轉貼 連結 網址 "
    "哈哈 哈哈哈 謝謝 喜歡 感謝 https http www com threads net the and for with this that from are was you".split()
)
CUSTOM_WORDS = (
    "大稻埕", "無人機", "北門站", "大橋頭站", "雙連站", "中山站", "交通管制",
    "夏日節", "大稻埕夏日節", "延平河濱公園", "河濱公園", "煙火秀", "花火",
    "捷運", "人潮", "迪化街", "民生西路", "忠孝橋", "下雨", "塞車", "散場",
)
WORD_FIELDS = ("word", "document_frequency", "document_share", "source_type", "sample_label", "analysis_purpose")
DAILY_FIELDS = (
    "published_date_taipei", "observed_post_count", "count_kind", "day_coverage",
    "source_type", "sample_label", "analysis_purpose",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("日期必須使用 YYYY-MM-DD 格式。")
    return parsed


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _observation_day(row: dict) -> str | None:
    """Do not infer a year from a relative time or a month/day display label."""
    precision = row.get("date_precision")
    supplied_day = row.get("published_date_taipei")
    try:
        local_day = _parse_date(supplied_day).isoformat() if isinstance(supplied_day, str) else None
    except ValueError:
        return None
    if precision == "exact_timestamp":
        timestamp = _timestamp(row.get("published_at_utc"))
        if timestamp is None:
            return None
        calculated_day = timestamp.astimezone(TAIPEI).date().isoformat()
        return calculated_day if local_day in (None, calculated_day) else None
    if precision == "day":
        return local_day
    return None


def load_sample(raw_path: Path, start_date: str, end_date: str, *, now: datetime | None = None,
                source_type: str = SOURCE_TYPE) -> dict:
    """Select retained, complete, included posts and preserve counts of omissions.

The source log is not edited. Its collector is responsible for retention purge.
A newer complete exclude/review record supersedes an older include record;
an incomplete card cannot overwrite a complete observation of the same post.
"""
    source_label = SOURCE_LABELS[source_type]
    first, last = _parse_date(start_date), _parse_date(end_date)
    if first > last:
        raise ValueError("結束日期不可早於開始日期。")
    current = now or _utc_now()
    if current.tzinfo is None:
        raise ValueError("目前時間必須包含時區。")
    current = current.astimezone(timezone.utc)
    cutoff = current - timedelta(days=90)
    omitted: Counter = Counter()
    observations: dict[str, list[tuple[datetime, int, dict]]] = {}
    rows_read = 0
    with Path(raw_path).open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            rows_read += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                omitted["invalid_json"] += 1
                continue
            if not isinstance(row, dict) or row.get("source_type") != source_type:
                omitted["invalid_source"] += 1
                continue
            post_id = row.get("post_id")
            if not isinstance(post_id, str) or not post_id.strip():
                omitted["missing_post_id"] += 1
                continue
            collected = _timestamp(row.get("collected_at_utc"))
            if collected is None or collected > current:
                omitted["invalid_collection_time"] += 1
                continue
            if collected < cutoff:
                omitted["expired_observation"] += 1
                continue
            observations.setdefault(post_id, []).append((collected, line_number, row))

    posts = []
    for versions in observations.values():
        complete = [item for item in versions if item[2].get("text_complete") is True]
        if not complete:
            omitted["incomplete_post"] += 1
            continue
        _, _, row = max(complete, key=lambda item: (item[0], item[1]))
        if row.get("relevance") != "include":
            omitted["excluded_or_review_post"] += 1
            continue
        day = _observation_day(row)
        if day is None:
            omitted["uncertain_or_conflicting_date_post"] += 1
            continue
        if not start_date <= day <= end_date:
            omitted["out_of_period_post"] += 1
            continue
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            omitted["empty_text_post"] += 1
            continue
        posts.append({"post_id": row["post_id"], "text": row["text"], "published_date_taipei": day})
    posts.sort(key=lambda row: (row["published_date_taipei"], row["post_id"]))
    daily_counts = Counter(row["published_date_taipei"] for row in posts)
    daily = []
    for offset in range((last - first).days + 1):
        day = (first + timedelta(days=offset)).isoformat()
        count = daily_counts.get(day)
        daily.append({
            "published_date_taipei": day, "observed_post_count": count,
            "count_kind": "observed_samples",
            "day_coverage": "sample_observed_not_complete" if count else "not_observed_unknown",
            "source_type": source_type, "sample_label": source_label, "analysis_purpose": PURPOSE_LABEL,
        })
    return {"posts": posts, "daily": daily, "input_observation_count": rows_read,
            "retained_unique_post_count": len(observations), "omission_counts": dict(sorted(omitted.items()))}


def sanitize_text(text: str) -> str:
    """Remove visible contact identifiers before tokenization, not only afterward."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?:https?://|www\.)\S+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", " ", text)
    text = re.sub(r"@[\w.]+", " ", text)
    # Taiwan mobile/landline and other long telephone-like sequences, including
    # spaced international prefixes. This is intentionally conservative.
    text = re.sub(r"(?<![A-Za-z0-9])\+?\d[\d\s().-]{5,}\d(?![A-Za-z0-9])", " ", text)
    return text


def _jieba_tokenizer() -> Callable[[str], Iterable[str]]:
    try:
        import jieba
    except ImportError as exc:
        raise RuntimeError("缺少 jieba；請先執行 uv sync --group notebook --extra social。") from exc
    tokenizer = jieba.Tokenizer()
    for word in CUSTOM_WORDS:
        tokenizer.add_word(word, freq=200000)
    return tokenizer.cut


def document_frequencies(posts: Iterable[dict], tokenizer: Callable[[str], Iterable[str]] | None = None,
                         extra_stopwords: Iterable[str] = ()) -> Counter:
    """Document frequency counts a term once per unique post, never per repetition."""
    tokenize = tokenizer or _jieba_tokenizer()
    excluded = STOPWORDS | DISPLAY_EXCLUSIONS | frozenset(
        unicodedata.normalize("NFKC", word).strip().casefold() for word in extra_stopwords
    )
    frequencies: Counter = Counter()
    seen = set()
    for post in posts:
        if post["post_id"] in seen:
            continue
        seen.add(post["post_id"])
        tokens = {str(word).strip().casefold() for word in tokenize(sanitize_text(post["text"]))}
        frequencies.update(word for word in tokens if len(word) >= 2 and word not in excluded
                           and re.fullmatch(r"[\u3400-\u9fffA-Za-z]+", word))
    return frequencies


def _safe_csv_cell(value: object) -> object:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: _safe_csv_cell(value) for key, value in row.items()} for row in rows)


def render_wordcloud(frequencies: Counter, path: Path, summary: dict, font_path: Path) -> None:
    """Draw a circular, white-background cloud from unchanged document counts."""
    if not Path(font_path).is_file():
        raise FileNotFoundError("找不到中文字型，請用 --font 指定支援繁體中文的字型。")
    try:
        import numpy as np
        from PIL import Image
        from wordcloud import WordCloud
    except ImportError as exc:
        raise RuntimeError("缺少 wordcloud/Pillow；請先執行 uv sync --group notebook --extra social。") from exc
    canvas = Image.new("RGB", (1600, 1200), "white")
    # White pixels exclude words; leave the disk itself available for packing.
    size = 1080
    yy, xx = np.ogrid[:size, :size]
    mask = np.where((xx - (size - 1) / 2) ** 2 + (yy - (size - 1) / 2) ** 2
                    <= (size / 2 - 8) ** 2, 0, 255).astype(np.uint8)
    palette = ("#B8A04F", "#7D8B45", "#426F6D", "#963F3D")
    cloud = WordCloud(mask=mask, background_color="white", font_path=str(font_path),
                      max_words=180, max_font_size=200, min_font_size=16, margin=5,
                      prefer_horizontal=1.0, relative_scaling=1.0,
                      random_state=42, collocations=False, repeat=False,
                      color_func=lambda word, *args, **kwargs: palette[sum(map(ord, word)) % len(palette)])
    # Stable tie order makes rerendering the same counts reproducible.
    ordered = dict(sorted(frequencies.items(), key=lambda item: (-item[1], item[0])))
    canvas.paste(cloud.generate_from_frequencies(ordered).to_image(), (260, 60))
    canvas.save(path)


def build_reports(raw_path: Path, output_dir: Path, start_date: str, end_date: str,
                  font_path: Path = DEFAULT_FONT, extra_stopwords: Iterable[str] = (), *,
                  source_type: str = SOURCE_TYPE) -> dict:
    """Write labeled derived reports; no raw text or account names are exported."""
    raw_path, output_dir = Path(raw_path), Path(output_dir)
    source_label = SOURCE_LABELS[source_type]
    sample = load_sample(raw_path, start_date, end_date, source_type=source_type)
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "wordcloud.png"
    image_path.unlink(missing_ok=True)
    extra_stopwords = tuple(extra_stopwords)
    frequencies = document_frequencies(sample["posts"], extra_stopwords=extra_stopwords) if sample["posts"] else Counter()
    _write_csv(output_dir / "daily_observed_counts.csv", DAILY_FIELDS, sample["daily"])
    count = len(sample["posts"])
    word_rows = [dict(word=word, document_frequency=value, document_share=round(value / count, 8),
                      source_type=source_type, sample_label=source_label, analysis_purpose=PURPOSE_LABEL)
                 for word, value in sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))]
    _write_csv(output_dir / "words.csv", WORD_FIELDS, word_rows)
    summary = {
        "source_type": source_type, "sample_label": source_label, "analysis_purpose": PURPOSE_LABEL,
        "start_date": start_date, "end_date": end_date, "timezone": "Asia/Taipei",
        "window_day_count": len(sample["daily"]), "sample_post_count": count,
        "input_observation_count": sample["input_observation_count"],
        "retained_unique_post_count": sample["retained_unique_post_count"],
        "omission_counts": sample["omission_counts"], "unique_displayed_word_count": len(frequencies),
        "wordcloud_created": False, "platform_total": False, "causal_evidence": False,
        "pre_event_prediction_variable": False, "raw_retention_days": 90,
        "frequency_definition": "含該詞的不同貼文篇數；同篇同詞只計一次。",
        "daily_count_definition": "可用公開搜尋樣本的不同貼文篇數，不代表全站或完整搜尋結果。",
        "missing_value_definition": "CSV 空白表示當日未觀測到可用樣本，不能當成零聲量。",
        "retention_note": "本報告忽略取得超過 90 天的觀測；原始檔清除由擷取程式負責。",
        "display_exclusions": sorted(DISPLAY_EXCLUSIONS), "extra_stopwords": sorted(extra_stopwords),
        "limitations": ["搜尋排序、登入狀態及可見範圍會影響樣本。", "日期不確定、文字未展開或待人工判定的貼文不納入。",
                        "活動後回顧詞彙不可當成活動前已知變數。", "本輸出不使用擷取當下的按讚、回覆或分享數作為歷史回歸變數。"],
        "generated_at_utc": _utc_now().isoformat(),
    }
    if source_type == "ptt_public_web":
        summary.update(search_scope="selected_board_title_search", comments_included=False)
        summary["limitations"] = [
            "僅涵蓋指定看板的標題搜尋；只在正文提及關鍵字的文章可能漏掉。",
            "不含推噓文、作者欄位與簽名；日期不明或待核對文章不納入。",
            "不同文章可能轉貼同一新聞，不能解讀為不同人的獨立意見。",
            "文章刪除、存取限制與搜尋索引會影響涵蓋；不是全站聲量。",
            "活動期間的回顧文字不可當成活動事前已知特徵或因果證據。",
        ]
    # Write a truthful status before attempting rendering, so a missing font or
    # other drawing error cannot leave a stale successful summary beside new CSVs.
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if frequencies:
        render_wordcloud(frequencies, image_path, summary, Path(font_path))
        summary["wordcloud_created"] = True
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="從單一平台公開網頁樣本產生文字雲與詞頻；不代表平台全量。")
    parser.add_argument("--input", type=Path, required=True, help="擷取程式寫入的 JSONL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD，臺北時間，含當日")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD，臺北時間，含當日")
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT)
    parser.add_argument("--stopword", action="append", default=[])
    parser.add_argument("--source", choices=tuple(SOURCE_LABELS), default=SOURCE_TYPE)
    args = parser.parse_args(argv)
    try:
        report = build_reports(args.input, args.output, args.start_date, args.end_date, args.font, args.stopword,
                               source_type=args.source)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"報告未完成：{exc}\n")
    print(json.dumps({key: report[key] for key in ("sample_post_count", "unique_displayed_word_count", "wordcloud_created", "sample_label")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
