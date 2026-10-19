"""分析流程：文本处理、情感倾向、关键词提取、用户画像聚类与入库。"""

from __future__ import annotations

import logging
import re
import warnings
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymysql

from . import database
from .bili_api import BiliAPI, BiliAPIError
from .config import BASE_DIR

logger = logging.getLogger(__name__)

# 播放量达到该值时跳过深度分析（评论/弹幕采集），用于控制 API 配额消耗，
# 同时把分析重点放在长尾小众内容上。
VIEW_COUNT_LIMIT = 18000
SKIP_REASON = (
    "Video view count is 18000 or above. "
    "Skipping deep analysis to save API quota."
)

# 默认采集条数
DEFAULT_MAX_COUNT = 100

# 情感标签阈值
POSITIVE_THRESHOLD = 0.6
NEGATIVE_THRESHOLD = 0.4

# 画像参数
DEFAULT_CLUSTERS = 3
TOP_KEYWORDS = 20
TOP_IP_LOCATIONS = 5
# 聚类标签里区分“高/低等级”的分界线（B 站等级为 0-6）
LEVEL_REFERENCE = 3.0
# user_profile_summary.cluster_id 约定：0 表示整体汇总，1 起为聚类簇
OVERVIEW_CLUSTER_ID = 0
# top_keywords / top_ip_locations 字段长度上限
TEXT_FIELD_LIMIT = 500

# 关键词最小长度（过滤单字）
MIN_TOKEN_LENGTH = 2
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z]")

# 内置中文停用词（可在 app/stopwords.txt 或项目根目录 stopwords.txt 中追加）
DEFAULT_STOPWORDS = frozenset(
    """
    的 了 是 在 我 你 他 她 它 我们 你们 他们 这 那 这个 那个 这些 那些 一个 一下 一些
    什么 怎么 为什么 怎样 哪个 哪些 谁 就是 还是 也是 但是 但 而且 然而 然后 因为 所以
    如果 虽然 可以 应该 可能 已经 还有 没有 不是 不过 不能 不会 不要 自己 大家 现在 时候
    真的 感觉 觉得 知道 希望 喜欢 有点儿 有点 一点 一样 一直 一起 以后 之前 之后 目前
    电影 片子 视频 弹幕 up主 up 阿婆主 b站 哔哩哔哩 三连 一 二 三 十 个 们 啊 吧 呢 吗
    哦 呀 哈 哈哈 哈哈哈 嗯 额 额额 哇 唉 哎 诶 噢 喔 啦 嘛 嗯嗯 233 666 2333 卧槽 我擦
    the a an and or of to in is are was were it this that i you he she we they for on with
    """.split()
)


def load_stopwords() -> set[str]:
    """加载停用词：内置词表 + 可选的 stopwords.txt（每行一个词，# 开头为注释）。"""
    words = set(DEFAULT_STOPWORDS)
    for path in (Path(__file__).parent / "stopwords.txt", BASE_DIR / "stopwords.txt"):
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                word = line.strip()
                if word and not word.startswith("#"):
                    words.add(word)
            logger.info("已加载自定义停用词表：%s", path)
        except OSError as exc:
            logger.warning("读取停用词表失败 %s：%s", path, exc)
    return words


STOPWORDS = load_stopwords()


# ----------------------------------------------------------------------
# 入库 SQL
# ----------------------------------------------------------------------
UPSERT_VIDEO_SQL = """
INSERT INTO videos (
    bvid, title, up_name, view_count, danmaku_count, reply_count,
    like_count, coin_count, favorite_count, publish_time, duration,
    description, scraped_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
)
ON DUPLICATE KEY UPDATE
    title          = VALUES(title),
    up_name        = VALUES(up_name),
    view_count     = VALUES(view_count),
    danmaku_count  = VALUES(danmaku_count),
    reply_count    = VALUES(reply_count),
    like_count     = VALUES(like_count),
    coin_count     = VALUES(coin_count),
    favorite_count = VALUES(favorite_count),
    publish_time   = VALUES(publish_time),
    duration       = VALUES(duration),
    description    = VALUES(description),
    scraped_at     = NOW()
"""

INSERT_COMMENT_SQL = """
INSERT INTO comments (
    bvid, user_name, user_level, user_sex, user_sign, content,
    like_count, reply_time, ip_location, sentiment_score, sentiment_label
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
"""

INSERT_DANMAKU_SQL = """
INSERT INTO danmaku (bvid, user_name, content, send_time, danmaku_type)
VALUES (%s, %s, %s, %s, %s)
"""

INSERT_PROFILE_SQL = """
INSERT INTO user_profile_summary (
    bvid, cluster_id, cluster_label, user_count, avg_level,
    top_keywords, top_ip_locations
) VALUES (
    %s, %s, %s, %s, %s, %s, %s
)
"""

SELECT_COMMENTS_SQL = """
SELECT id, user_name, user_level, user_sex, user_sign, content,
       like_count, reply_time, ip_location, sentiment_score, sentiment_label
FROM comments
WHERE bvid = %s
ORDER BY id
"""

UPDATE_COMMENT_SENTIMENT_SQL = """
UPDATE comments SET sentiment_score = %s, sentiment_label = %s WHERE id = %s
"""

DELETE_COMMENTS_SQL = "DELETE FROM comments WHERE bvid = %s"
DELETE_DANMAKU_SQL = "DELETE FROM danmaku WHERE bvid = %s"
DELETE_PROFILE_SQL = "DELETE FROM user_profile_summary WHERE bvid = %s"


# ----------------------------------------------------------------------
# 文本处理
# ----------------------------------------------------------------------
def segment(texts: Iterable[str]) -> list[list[str]]:
    """使用 jieba 对文本分词（未过滤停用词）。"""
    import jieba

    return [list(jieba.cut(text or "")) for text in texts]


def sentiment(text: str) -> float:
    """使用 SnowNLP 计算情感得分（0~1，越大越正向）。"""
    from snownlp import SnowNLP

    return float(SnowNLP(text or "").sentiments)


def sentiment_label(score: float | None) -> str | None:
    """把情感得分映射成标签：>=0.6 positive，<=0.4 negative，其余 neutral。"""
    if score is None:
        return None
    if score >= POSITIVE_THRESHOLD:
        return "positive"
    if score <= NEGATIVE_THRESHOLD:
        return "negative"
    return "neutral"


def tokenize(texts: Iterable[str]) -> list[str]:
    """分词并过滤停用词、单字、纯数字与纯标点。"""
    import jieba

    tokens: list[str] = []
    for text in texts:
        for token in jieba.cut(text or ""):
            word = token.strip().lower()
            if len(word) < MIN_TOKEN_LENGTH:
                continue
            if word in STOPWORDS or word.isdigit():
                continue
            if not _TOKEN_PATTERN.search(word):
                continue
            tokens.append(word)
    return tokens


def extract_keywords(texts: Iterable[str], top_n: int = TOP_KEYWORDS) -> list[tuple[str, int]]:
    """统计词频，返回 Top N 关键词。"""
    return Counter(tokenize(texts)).most_common(top_n)


def top_ip_locations(
    records: Sequence[dict[str, Any]],
    top_n: int = TOP_IP_LOCATIONS,
) -> list[tuple[str, int]]:
    """统计评论用户的 IP 属地分布，返回 Top N。"""
    counter: Counter[str] = Counter()
    for record in records:
        location = (record.get("ip_location") or "").strip()
        if location and location not in {"未知", "其他"}:
            counter[location] += 1
    return counter.most_common(top_n)


def format_pairs(pairs: Sequence[tuple[str, int]], limit: int = TEXT_FIELD_LIMIT) -> str | None:
    """把 (词, 次数) 列表拼成 "词:次数" 字符串，并截断到字段长度内。"""
    if not pairs:
        return None
    parts: list[str] = []
    for word, count in pairs:
        candidate = f"{word}:{count}"
        if len(", ".join([*parts, candidate])) > limit:
            break
        parts.append(candidate)
    return ", ".join(parts) or None


# ----------------------------------------------------------------------
# 聚类
# ----------------------------------------------------------------------
def cluster(vectors: Any, n_clusters: int = DEFAULT_CLUSTERS, random_state: int = 42) -> list[int]:
    """使用 KMeans 对特征向量聚类，返回每个样本的簇标签。"""
    from sklearn.cluster import KMeans

    model = KMeans(n_clusters=n_clusters, n_init=10, random_state=random_state)
    return [int(label) for label in model.fit_predict(vectors)]


def cluster_users(
    levels: Sequence[float],
    scores: Sequence[float],
    n_clusters: int = DEFAULT_CLUSTERS,
) -> list[int]:
    """基于 user_level 与 sentiment_score 两个特征做 K-Means 聚类。

    两个特征量纲不同，先做标准化再聚类，避免等级（0-6）压过情感得分（0-1）。
    样本数不足时退化为单簇。
    """
    import numpy as np
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.preprocessing import StandardScaler

    features = np.column_stack(
        [
            np.asarray(levels, dtype=float),
            np.asarray(scores, dtype=float),
        ]
    )
    sample_count = features.shape[0]
    if sample_count == 0:
        return []

    k = max(1, min(int(n_clusters), sample_count))
    if k == 1:
        return [0] * sample_count

    scaled = StandardScaler().fit_transform(features)
    with warnings.catch_warnings():
        # 特征高度重复时 KMeans 会告警并少出簇，这里静默处理并记日志
        warnings.simplefilter("ignore", ConvergenceWarning)
        labels = cluster(scaled, n_clusters=k)
    if len(set(labels)) < k:
        logger.info(
            "聚类只形成 %s 个簇（k=%s），样本特征可能高度重复", len(set(labels)), k
        )
    return labels


def describe_cluster(avg_level: float, avg_score: float, level_reference: float = LEVEL_REFERENCE) -> str:
    """按簇的平均等级与平均情感给出中文标签，如「高等级积极用户」。"""
    level_part = "高等级" if avg_level >= level_reference else "低等级"
    if avg_score >= POSITIVE_THRESHOLD:
        mood_part = "积极"
    elif avg_score <= NEGATIVE_THRESHOLD:
        mood_part = "消极"
    else:
        mood_part = "中性"
    return f"{level_part}{mood_part}用户"


def _unique_labels(labels: Sequence[str]) -> list[str]:
    """同名标签追加序号，保证每个簇标签可区分。"""
    seen: Counter[str] = Counter()
    result: list[str] = []
    for label in labels:
        seen[label] += 1
        result.append(label if seen[label] == 1 else f"{label}({seen[label]})")
    return result


# ----------------------------------------------------------------------
# 画像主逻辑
# ----------------------------------------------------------------------
def build_portrait(
    records: Sequence[dict[str, Any]],
    n_clusters: int = DEFAULT_CLUSTERS,
    top_n_keywords: int = TOP_KEYWORDS,
    top_n_ip: int = TOP_IP_LOCATIONS,
) -> dict[str, Any]:
    """基于评论记录（内存中）计算完整用户画像。

    记录中若已带 sentiment_score 则直接复用，否则现场用 SnowNLP 计算。
    """
    if not records:
        return {
            "comment_count": 0,
            "avg_level": None,
            "sentiment": {"positive": 0, "neutral": 0, "negative": 0, "avg_score": None},
            "keywords": [],
            "ip_locations": [],
            "clusters": [],
        }

    scores: list[float] = []
    for record in records:
        stored = record.get("sentiment_score")
        score = float(stored) if stored is not None else sentiment(record.get("content") or "")
        score = min(max(score, 0.0), 1.0)
        record["sentiment_score"] = score
        record["sentiment_label"] = sentiment_label(score)
        scores.append(score)

    levels = [float(record["user_level"]) for record in records if record.get("user_level") is not None]
    default_level = round(sum(levels) / len(levels), 2) if levels else LEVEL_REFERENCE
    level_values = [
        float(record["user_level"]) if record.get("user_level") is not None else default_level
        for record in records
    ]

    labels = cluster_users(level_values, scores, n_clusters=n_clusters)
    grouped: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        grouped.setdefault(label, []).append(index)

    # 按平均等级、平均情感从高到低排序，保证多次运行的簇编号稳定
    ordered = sorted(
        grouped.items(),
        key=lambda item: (
            -sum(level_values[i] for i in item[1]) / len(item[1]),
            -sum(scores[i] for i in item[1]) / len(item[1]),
        ),
    )

    clusters: list[dict[str, Any]] = []
    for _, indices in ordered:
        members = [records[i] for i in indices]
        avg_level = round(sum(level_values[i] for i in indices) / len(indices), 2)
        avg_score = round(sum(scores[i] for i in indices) / len(indices), 3)
        clusters.append(
            {
                "cluster_id": len(clusters) + 1,  # 1 起编号，0 留给整体汇总
                "cluster_label": describe_cluster(avg_level, avg_score),
                "user_count": len(indices),
                "avg_level": avg_level,
                "avg_sentiment": avg_score,
                "keywords": extract_keywords([m["content"] for m in members], top_n_keywords),
                "ip_locations": top_ip_locations(members, top_n_ip),
            }
        )

    for cluster_info, label in zip(clusters, _unique_labels([c["cluster_label"] for c in clusters])):
        cluster_info["cluster_label"] = label

    distribution = Counter(record["sentiment_label"] for record in records)
    return {
        "comment_count": len(records),
        "avg_level": round(sum(level_values) / len(level_values), 2),
        "sentiment": {
            "positive": distribution.get("positive", 0),
            "neutral": distribution.get("neutral", 0),
            "negative": distribution.get("negative", 0),
            "avg_score": round(sum(scores) / len(scores), 3),
        },
        "keywords": extract_keywords([record["content"] for record in records], top_n_keywords),
        "ip_locations": top_ip_locations(records, top_n_ip),
        "clusters": clusters,
    }


def analyze_sentiment(
    bvid: str,
    records: Sequence[dict[str, Any]] | None = None,
    persist: bool = True,
    recompute: bool = False,
) -> dict[str, Any]:
    """对 comments 表中的评论做情感分析，并把得分与标签写回数据库。

    默认复用已存在的 sentiment_score，recompute=True 时重新计算。
    """
    rows = list(records) if records is not None else fetch_comments(bvid)
    if not rows:
        return {"total": 0, "positive": 0, "neutral": 0, "negative": 0, "avg_score": None}

    updates: list[tuple[Any, ...]] = []
    scores: list[float] = []
    for row in rows:
        stored = row.get("sentiment_score")
        if stored is not None and not recompute:
            score = min(max(float(stored), 0.0), 1.0)
        else:
            try:
                score = min(max(sentiment(row.get("content") or ""), 0.0), 1.0)
            except Exception as exc:  # noqa: BLE001 - SnowNLP 出错不应中断整体分析
                logger.warning("情感分析失败（id=%s）：%s", row.get("id"), exc)
                continue
        row["sentiment_score"] = score
        row["sentiment_label"] = sentiment_label(score)
        scores.append(score)
        updates.append((round(score, 3), row["sentiment_label"], row["id"]))

    if persist and updates:
        database.execute_many(UPDATE_COMMENT_SENTIMENT_SQL, updates)

    distribution = Counter(row.get("sentiment_label") for row in rows)
    return {
        "total": len(rows),
        "positive": distribution.get("positive", 0),
        "neutral": distribution.get("neutral", 0),
        "negative": distribution.get("negative", 0),
        "avg_score": round(sum(scores) / len(scores), 3) if scores else None,
    }


def save_profile_summary(bvid: str, portrait: dict[str, Any]) -> int:
    """写入 user_profile_summary：先写入 k 个聚类簇，再写一条整体汇总行。"""
    rows: list[tuple[Any, ...]] = []
    for cluster_info in portrait.get("clusters", []):
        rows.append(
            (
                bvid,
                cluster_info["cluster_id"],
                cluster_info["cluster_label"],
                cluster_info["user_count"],
                cluster_info["avg_level"],
                format_pairs(cluster_info["keywords"]),
                format_pairs(cluster_info["ip_locations"]),
            )
        )

    if portrait.get("comment_count"):
        rows.append(
            (
                bvid,
                OVERVIEW_CLUSTER_ID,
                "整体汇总",
                portrait["comment_count"],
                portrait.get("avg_level"),
                format_pairs(portrait.get("keywords", [])),
                format_pairs(portrait.get("ip_locations", [])),
            )
        )

    database.execute(DELETE_PROFILE_SQL, (bvid,))
    return database.execute_many(INSERT_PROFILE_SQL, rows)


def analyze_user_profile(
    bvid: str,
    n_clusters: int = DEFAULT_CLUSTERS,
    top_n_keywords: int = TOP_KEYWORDS,
    top_n_ip: int = TOP_IP_LOCATIONS,
    persist: bool = True,
) -> dict[str, Any]:
    """对某个视频的评论做完整用户画像分析并入库。

    步骤：读取 comments → 情感分析（写回 comments）→ 关键词 Top20 →
    基于 user_level + sentiment_score 做 K-Means(k=3) → IP 属地 Top5 →
    聚类与汇总结果写入 user_profile_summary。
    """
    try:
        rows = fetch_comments(bvid)
    except pymysql.MySQLError as exc:
        logger.error("读取评论失败：%s", exc)
        return {"status": "error", "bvid": bvid, "reason": f"读取评论失败：{database.describe_error(exc)}"}

    if not rows:
        return {
            "status": "empty",
            "bvid": bvid,
            "reason": "comments 表中没有该视频的评论数据，请先执行采集。",
        }

    try:
        sentiment_summary = analyze_sentiment(bvid, records=rows, persist=persist)
        portrait = build_portrait(
            rows,
            n_clusters=n_clusters,
            top_n_keywords=top_n_keywords,
            top_n_ip=top_n_ip,
        )
        saved = save_profile_summary(bvid, portrait) if persist else 0
    except pymysql.MySQLError as exc:
        logger.error("画像分析写入数据库失败：%s", exc)
        return {"status": "error", "bvid": bvid, "reason": f"写入数据库失败：{database.describe_error(exc)}"}

    return {
        "status": "success",
        "bvid": bvid,
        "comment_count": portrait["comment_count"],
        "sentiment": sentiment_summary,
        "keywords": portrait["keywords"],
        "ip_locations": portrait["ip_locations"],
        "clusters": portrait["clusters"],
        "summary_rows_saved": saved,
    }


def fetch_comments(bvid: str) -> list[dict[str, Any]]:
    """读取某个视频在 comments 表中的全部评论。"""
    return database.fetch_all(SELECT_COMMENTS_SQL, (bvid,))


# ----------------------------------------------------------------------
# 入库
# ----------------------------------------------------------------------
def save_video(info: dict[str, Any]) -> int:
    """写入/更新视频基础信息。"""
    params = (
        info.get("bvid"),
        info.get("title"),
        info.get("up_name"),
        info.get("view_count", 0),
        info.get("danmaku_count", 0),
        info.get("reply_count", 0),
        info.get("like_count", 0),
        info.get("coin_count", 0),
        info.get("favorite_count", 0),
        info.get("publish_time"),
        info.get("duration", 0),
        info.get("description"),
    )
    return database.execute(UPSERT_VIDEO_SQL, params)


def save_comments(comments: list[dict[str, Any]], with_sentiment: bool = True) -> int:
    """清空该视频旧评论后批量写入，避免重复采集产生脏数据。"""
    if not comments:
        return 0

    bvid = comments[0]["bvid"]
    rows = []
    for item in comments:
        score = None
        if with_sentiment:
            try:
                score = round(sentiment(item["content"]), 3)
            except Exception as exc:  # SnowNLP 出错不应中断入库
                logger.warning("情感分析失败，跳过该条：%s", exc)
        rows.append(
            (
                item["bvid"],
                item.get("user_name"),
                item.get("user_level"),
                item.get("user_sex"),
                item.get("user_sign"),
                item["content"],
                item.get("like_count", 0),
                item.get("reply_time"),
                item.get("ip_location"),
                score,
                sentiment_label(score),
            )
        )

    database.execute(DELETE_COMMENTS_SQL, (bvid,))
    return database.execute_many(INSERT_COMMENT_SQL, rows)


def save_danmaku(danmaku: list[dict[str, Any]]) -> int:
    """清空该视频旧弹幕后批量写入。"""
    if not danmaku:
        return 0

    bvid = danmaku[0]["bvid"]
    rows = [
        (
            item["bvid"],
            item.get("user_name"),
            item["content"],
            item.get("send_time"),
            item.get("danmaku_type"),
        )
        for item in danmaku
    ]
    database.execute(DELETE_DANMAKU_SQL, (bvid,))
    return database.execute_many(INSERT_DANMAKU_SQL, rows)


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def process_video(
    target: str,
    max_count: int = DEFAULT_MAX_COUNT,
    api: BiliAPI | None = None,
    persist: bool = True,
    with_profile: bool = True,
    comment_sort: str = "hot",
) -> dict[str, Any]:
    """采集单个视频的数据并入库，随后生成用户画像。

    target 可以是：B 站分享链接（含 b23.tv 短链）、BV 号、av 号、ep 号（番剧单集）、ss 号。
    链接里一般自带编号，程序会自动识别并解析出 BV 号。

    流程：
    1. 调用 get_video_info 获取播放量；
    2. 播放量 >= 18000（1.8 万）时直接返回 skipped，不再消耗评论/弹幕接口配额；
    3. 播放量 < 18000 时采集评论与弹幕并写入数据库；
    4. with_profile=True 时继续执行 analyze_user_profile（情感/关键词/聚类/IP 属地）。

    comment_sort="hot" 按热度采集评论（默认，匿名可用）；
    comment_sort="latest" 按时间采集，需要有效的 cookie 登录态，未登录会自动降级。
    所有接口调用都带异常处理，失败时返回 status=error 而不抛出。
    """
    client = api or BiliAPI()

    # 1. 解析输入（分享链接 / BV / av / ep）并获取视频基础信息
    try:
        info = client.resolve_video(target)
    except BiliAPIError as exc:
        logger.error("解析视频信息失败：%s", exc)
        return {"status": "error", "bvid": target, "reason": f"解析视频信息失败：{exc}"}

    bvid = info["bvid"]
    view_count = int(info.get("view_count") or 0)

    # 2. 配额控制：高播放量视频跳过深度分析
    if view_count >= VIEW_COUNT_LIMIT:
        logger.info("视频 %s 播放量 %s >= %s，跳过深度分析", bvid, view_count, VIEW_COUNT_LIMIT)
        return {"status": "skipped", "reason": SKIP_REASON}

    # 3. 采集评论与弹幕，单项失败不影响另一项
    comments: list[dict[str, Any]] = []
    danmaku: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        comments = client.get_comments(bvid, max_count=max_count, sort=comment_sort)
    except BiliAPIError as exc:
        logger.error("获取评论失败：%s", exc)
        errors.append(f"评论采集失败：{exc}")

    try:
        danmaku = client.get_danmaku(bvid, max_count=max_count)
    except BiliAPIError as exc:
        logger.error("获取弹幕失败：%s", exc)
        errors.append(f"弹幕采集失败：{exc}")

    result: dict[str, Any] = {
        "status": "success",
        "bvid": info["bvid"],
        "view_count": view_count,
        "comments_fetched": len(comments),
        "danmaku_fetched": len(danmaku),
        "comment_sort": getattr(client, "last_comment_sort", None) or comment_sort,
        "input_type": info.get("input_type"),
        "input_value": info.get("input_value"),
        "input_text": info.get("input_text"),
    }

    # 4. 入库
    if persist:
        try:
            save_video(info)
            result["comments_saved"] = save_comments(comments)
            result["danmaku_saved"] = save_danmaku(danmaku)
        except pymysql.MySQLError as exc:
            logger.error("写入数据库失败：%s", exc)
            result.update(status="error", reason=f"写入数据库失败：{database.describe_error(exc)}")
            return result

        # 5. 用户画像分析（读取刚入库的评论）
        if with_profile:
            profile = analyze_user_profile(info["bvid"], persist=True)
            result["profile"] = profile
            if profile.get("status") == "error":
                result["status"] = "partial"
                errors.append(f"画像分析失败：{profile.get('reason')}")

    if errors:
        result.update(status="partial", errors=errors)

    return result
