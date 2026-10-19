"""Streamlit 可视化页面：B站视频用户画像分析工具。

运行方式（在项目根目录 bili-analyzer 下执行）::

    .\\.venv\\Scripts\\python.exe -m streamlit run app\\ui.py

页面分为「分析」与「设置」两个标签页：分析页负责采集与展示，
设置页负责查看登录态、指定 cookie 文件。
"""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path
from typing import Any

# 直接执行 `streamlit run app/ui.py` 时，脚本所在目录不在包路径里，
# 这里把项目根目录加入 sys.path，保证 `from app import ...` 可用。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from app import analyzer, database
from app.bili_api import BiliAPI
from app.config import ENV_FILE, MYSQL_CONFIG, mask_secret, read_cookie_file, refresh_mysql_config

# 播放量超限时的提示文案
SKIPPED_TIP = "该视频播放量超过 1.8 万，为控制 API 配额已跳过深度分析。"

# 输入提示：支持分享链接与各种编号
INPUT_HELP = (
    "支持直接粘贴 B 站分享链接（含 b23.tv 短链），也支持输入 BV 号、av 号、ep 号。"
    "分享链接里一般自带编号，程序会自动识别。"
)

# 评论排序选项
SORT_OPTIONS = {"热门优先（默认）": "hot", "最新优先（按发布时间）": "latest"}
SORT_TEXT = {"hot": "热门优先", "latest": "最新优先"}

# 情感标签的中文映射与配色
SENTIMENT_TEXT = {"positive": "正向", "neutral": "中性", "negative": "负向"}
SENTIMENT_COLORS = {"正向": "#2ecc71", "中性": "#95a5a6", "负向": "#e74c3c"}

# 图表统一风格
CHART_COLORWAY = ["#00A1D6", "#FB7299", "#F0932B", "#6AB04C", "#7C5CFF"]

LOGO_FILE = Path(__file__).resolve().parent / "assets" / "logo.png"

SELECT_VIDEO_SQL = """
SELECT title, up_name, view_count, like_count, coin_count, favorite_count,
       danmaku_count, reply_count, publish_time, duration
FROM videos
WHERE bvid = %s
"""

SELECT_PROFILE_SQL = """
SELECT cluster_id, cluster_label, user_count, avg_level, top_keywords, top_ip_locations
FROM user_profile_summary
WHERE bvid = %s
ORDER BY cluster_id
"""

SELECT_SENTIMENT_SQL = """
SELECT sentiment_label, COUNT(*) AS count
FROM comments
WHERE bvid = %s
GROUP BY sentiment_label
"""

# 「生成 .env 模板」写出的内容
ENV_TEMPLATE = """# bili-analyzer 配置（由程序生成，请把 MYSQL_PASSWORD 改成真实密码）
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DATABASE=bili_analyzer
BILI_API_BASE_URL=https://api.bilibili.com
"""

CUSTOM_CSS = """
<style>
/* 页脚与右上角菜单：保留菜单（含 Rerun），仅隐藏默认页脚 */
footer {visibility: hidden;}

/* 顶部品牌区 */
.hero {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 18px 22px;
    border-radius: 18px;
    background: linear-gradient(120deg, rgba(0,161,214,0.10), rgba(251,114,153,0.10));
    border: 1px solid #E6EAF0;
    margin-bottom: 14px;
}
.hero-logo { width: 54px; height: 54px; border-radius: 14px; flex: 0 0 auto; }
.hero-titles { flex: 1 1 auto; min-width: 0; }
.hero-title { font-size: 1.42rem; font-weight: 700; color: #1F2329; line-height: 1.3; }
.hero-sub { font-size: 0.86rem; color: #6B7280; margin-top: 2px; }
.hero-tags { margin-top: 8px; }
.tag {
    display: inline-block; font-size: 0.74rem; padding: 2px 9px; margin: 0 6px 4px 0;
    border-radius: 999px; background: #FFFFFF; border: 1px solid #E0E6ED; color: #4B5563;
}
.status {
    font-size: 0.78rem; padding: 6px 12px; border-radius: 999px; white-space: nowrap;
    border: 1px solid transparent;
}
.status-ok { background: #E8F8EF; color: #12805C; border-color: #BFE9D3; }
.status-warn { background: #FFF6E5; color: #A15C00; border-color: #FFE1AE; }
.status-info { background: #EEF4FB; color: #3B5B7A; border-color: #D6E4F2; }

/* 指标卡片 */
div[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid #E6EAF0;
    border-radius: 14px;
    padding: 12px 16px;
    box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
}
div[data-testid="stMetricLabel"] p { font-size: 0.82rem; color: #6B7280; }
div[data-testid="stMetricValue"] { font-size: 1.45rem; font-weight: 650; color: #1F2329; }

/* 按钮 */
div.stButton > button {
    border-radius: 10px;
    font-weight: 600;
    padding: 0.45rem 1rem;
}
div.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #00A1D6, #23ADE5);
    border: none;
}
div.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #0093C4, #1E9ED3);
}

/* 标签页 */
button[data-baseweb="tab"] { font-size: 0.96rem; font-weight: 600; }

/* 输入控件圆角 */
div[data-baseweb="input"] > div, div[data-baseweb="select"] > div { border-radius: 10px; }

/* 小标题 */
h4 { color: #1F2329; }
</style>
"""


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------
def work_root() -> Path:
    """可写目录：打包后为 exe 所在目录，否则为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


@st.cache_data(show_spinner=False)
def logo_data_uri() -> str:
    """把 logo 转成 data URI，方便嵌进 HTML 头部。"""
    try:
        payload = base64.b64encode(LOGO_FILE.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{payload}"


def parse_pairs(text: str | None) -> list[tuple[str, int]]:
    """把 "词:次数, 词:次数" 解析回 (词, 次数) 列表。"""
    if not text:
        return []
    pairs: list[tuple[str, int]] = []
    for chunk in str(text).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        word, _, count = chunk.rpartition(":")
        if not word:
            continue
        try:
            pairs.append((word, int(count)))
        except ValueError:
            continue
    return pairs


# ----------------------------------------------------------------------
# 数据读取
# ----------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def load_login_status(cookie_file: str | None = None) -> dict[str, Any]:
    """检查 cookie 登录态（5 分钟内复用结果，避免每次重绘都发请求）。"""
    try:
        api = BiliAPI(cookie_file=cookie_file) if cookie_file else BiliAPI()
        return api.login_status()
    except Exception as exc:  # noqa: BLE001 - 状态检查失败不应影响页面
        return {
            "has_cookie": False,
            "is_login": False,
            "user_name": None,
            "cookie_file": cookie_file,
            "cookie_keys": [],
            "reason": f"登录态检查失败：{exc}",
        }


@st.cache_data(ttl=60, show_spinner=False)
def load_video_info(bvid: str) -> dict[str, Any] | None:
    """从 videos 表读取视频基础信息。"""
    try:
        return database.fetch_one(SELECT_VIDEO_SQL, (bvid,))
    except Exception:  # 数据库不可用时页面仍可展示接口取到的结果
        return None


@st.cache_data(ttl=60, show_spinner=False)
def load_saved_snapshot(bvid: str) -> dict[str, Any] | None:
    """从数据库读取该视频已保存的分析结果（用于展示历史数据）。"""
    try:
        rows = database.fetch_all(SELECT_PROFILE_SQL, (bvid,))
        sentiments = database.fetch_all(SELECT_SENTIMENT_SQL, (bvid,))
    except Exception:
        return None
    if not rows and not sentiments:
        return None

    overview = next(
        (row for row in rows if row.get("cluster_id") == analyzer.OVERVIEW_CLUSTER_ID), None
    )
    clusters = [row for row in rows if row.get("cluster_id") != analyzer.OVERVIEW_CLUSTER_ID]
    return {
        "sentiment": {
            (row.get("sentiment_label") or "neutral"): int(row.get("count") or 0)
            for row in sentiments
        },
        "keywords": parse_pairs((overview or {}).get("top_keywords")),
        "ip_locations": parse_pairs((overview or {}).get("top_ip_locations")),
        "clusters": clusters,
    }


# ----------------------------------------------------------------------
# 图表
# ----------------------------------------------------------------------
def _style(figure: Any) -> Any:
    """统一图表观感。"""
    figure.update_layout(
        template="plotly_white",
        colorway=CHART_COLORWAY,
        margin=dict(t=18, b=10, l=10, r=10),
        font=dict(family='-apple-system, "Segoe UI", "Microsoft YaHei", sans-serif', size=13),
    )
    return figure


def sentiment_pie(sentiment: dict[str, int]) -> Any:
    """评论情感分布饼图。"""
    data = {
        SENTIMENT_TEXT.get(key, key): int(sentiment.get(key) or 0)
        for key in ("positive", "neutral", "negative")
    }
    figure = px.pie(
        names=list(data.keys()),
        values=list(data.values()),
        hole=0.5,
        color=list(data.keys()),
        color_discrete_map=SENTIMENT_COLORS,
    )
    figure.update_traces(textinfo="label+percent", textfont_size=13)
    figure.update_layout(showlegend=False)
    return _style(figure)


def keyword_bar(keywords: list[tuple[str, int]]) -> Any:
    """Top 20 关键词条形图（横向，按词频升序排列便于阅读）。"""
    data = keywords[: analyzer.TOP_KEYWORDS]
    words = [word for word, _ in data][::-1]
    counts = [count for _, count in data][::-1]
    figure = px.bar(x=counts, y=words, orientation="h", labels={"x": "出现次数", "y": "关键词"})
    figure.update_traces(marker_color="#00A1D6")
    return _style(figure)


def ip_bar(ip_locations: list[tuple[str, int]]) -> Any:
    """IP 属地 Top 5 条形图。"""
    data = ip_locations[: analyzer.TOP_IP_LOCATIONS]
    names = [name for name, _ in data][::-1]
    counts = [count for _, count in data][::-1]
    figure = px.bar(x=counts, y=names, orientation="h", labels={"x": "评论人数", "y": "IP 属地"})
    figure.update_traces(marker_color="#FB7299")
    return _style(figure)


def cluster_table(clusters: list[dict[str, Any]]) -> pd.DataFrame:
    """用户画像聚类结果表格。"""
    return pd.DataFrame(
        [
            {
                "聚类标签": cluster.get("cluster_label"),
                "用户数": int(cluster.get("user_count") or 0),
                "平均等级": float(cluster.get("avg_level") or 0),
            }
            for cluster in clusters
        ]
    )


def render_cluster_table(clusters: list[dict[str, Any]]) -> None:
    st.dataframe(
        cluster_table(clusters),
        hide_index=True,
        column_config={
            "平均等级": st.column_config.NumberColumn("平均等级", format="%.2f"),
            "用户数": st.column_config.NumberColumn("用户数", format="%d"),
        },
    )


# ----------------------------------------------------------------------
# 页面片段
# ----------------------------------------------------------------------
def render_hero(login: dict[str, Any]) -> None:
    """顶部品牌区：logo + 标题 + 功能标签 + 登录态徽标。"""
    if login.get("is_login"):
        status_class, status_text = "status-ok", f"登录态已启用 · {login.get('user_name')}"
    elif login.get("has_cookie"):
        status_class, status_text = "status-warn", "cookie 已失效 · 匿名模式"
    else:
        status_class, status_text = "status-info", "匿名模式"

    logo_uri = logo_data_uri()
    logo_tag = f'<img class="hero-logo" src="{logo_uri}" alt="logo" />' if logo_uri else ""
    st.markdown(
        f"""
<div class="hero">
  {logo_tag}
  <div class="hero-titles">
    <div class="hero-title">B站视频用户画像分析工具</div>
    <div class="hero-sub">单视频数据采集 · 情感分析 · 关键词提取 · 用户聚类 · 属地分布</div>
    <div class="hero-tags">
      <span class="tag">评论 + 弹幕采集</span>
      <span class="tag">SnowNLP 情感分析</span>
      <span class="tag">jieba 关键词 Top20</span>
      <span class="tag">K-Means 用户聚类</span>
      <span class="tag">IP 属地 Top5</span>
    </div>
  </div>
  <div class="status {status_class}">{status_text}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_video_card(bvid: str, info: dict[str, Any] | None) -> None:
    """视频基础信息卡片：标题、UP 主、播放量、点赞数等。"""
    info = info or {}
    title = info.get("title") or "（数据库中暂无该视频的基础信息）"
    up_name = info.get("up_name") or "未知"

    st.subheader(title)
    st.caption(f"UP 主：{up_name} ｜ BV 号：{bvid}")

    columns = st.columns(4)
    columns[0].metric("播放量", f"{int(info.get('view_count') or 0):,}")
    columns[1].metric("点赞数", f"{int(info.get('like_count') or 0):,}")
    columns[2].metric("评论数", f"{int(info.get('reply_count') or 0):,}")
    columns[3].metric("弹幕数", f"{int(info.get('danmaku_count') or 0):,}")


def render_charts(
    sentiment: dict[str, int],
    keywords: list[tuple[str, int]],
    ip_locations: list[tuple[str, int]],
    clusters: list[dict[str, Any]],
    key_prefix: str,
) -> None:
    """四块图表：情感饼图、关键词、聚类表格、属地条形图。"""
    left, right = st.columns(2)
    with left:
        st.markdown("#### 评论区情感分布")
        if any(sentiment.values()):
            st.plotly_chart(sentiment_pie(sentiment), key=f"{key_prefix}_pie")
        else:
            st.info("暂无情感数据。")
    with right:
        st.markdown("#### Top 20 关键词")
        if keywords:
            st.plotly_chart(keyword_bar(keywords), key=f"{key_prefix}_keywords")
        else:
            st.info("暂无关键词数据。")

    table_column, ip_column = st.columns([1, 1])
    with table_column:
        st.markdown("#### 用户画像聚类结果")
        if clusters:
            render_cluster_table(clusters)
        else:
            st.info("暂无聚类结果。")
    with ip_column:
        st.markdown("#### IP 属地 Top 5")
        if ip_locations:
            st.plotly_chart(ip_bar(ip_locations), key=f"{key_prefix}_ip")
        else:
            st.info("暂无 IP 属地数据。")


def render_result(bvid: str, result: dict[str, Any]) -> None:
    """展示一次完整分析的结果。"""
    profile = result.get("profile") or {}
    info = load_video_info(bvid) or {}

    render_video_card(bvid, info)

    input_text = result.get("input_text")
    if input_text and input_text.strip() != bvid:
        st.caption(f"已从输入中解析出视频：{bvid}")

    used_sort = result.get("comment_sort")
    if used_sort:
        st.caption(
            f"评论采集排序：{SORT_TEXT.get(used_sort, used_sort)}"
            f"（共采集 {result.get('comments_fetched', 0)} 条评论）"
        )

    if not profile.get("comment_count"):
        st.info("暂无可用于画像分析的评论数据。")
        return

    sentiment = profile.get("sentiment") or {}
    total = sum(int(sentiment.get(key) or 0) for key in ("positive", "neutral", "negative"))
    stat_columns = st.columns(3)
    stat_columns[0].metric("分析评论数", f"{int(profile.get('comment_count') or 0):,}")
    stat_columns[1].metric("平均情感得分", f"{float(sentiment.get('avg_score') or 0):.3f}")
    stat_columns[2].metric("参与聚类用户数", f"{total:,}")

    st.divider()
    render_charts(
        sentiment,
        profile.get("keywords") or [],
        profile.get("ip_locations") or [],
        profile.get("clusters") or [],
        key_prefix="result",
    )


def render_saved_snapshot(bvid: str) -> None:
    """展示数据库中已保存的历史分析结果。"""
    snapshot = load_saved_snapshot(bvid)
    if not snapshot:
        return

    st.divider()
    st.markdown("#### 数据库中已有的历史分析结果")
    render_video_card(bvid, load_video_info(bvid))
    render_charts(
        snapshot.get("sentiment") or {},
        snapshot.get("keywords") or [],
        snapshot.get("ip_locations") or [],
        snapshot.get("clusters") or [],
        key_prefix="saved",
    )


def render_result_by_status(bvid: str, result: dict[str, Any]) -> None:
    """按 process_video 返回的状态渲染页面。"""
    status = result.get("status")

    if status == "skipped":
        st.warning(SKIPPED_TIP)
        render_saved_snapshot(bvid)
        return

    if status in {"success", "partial"}:
        if status == "partial":
            st.warning("部分数据采集失败，以下为已成功获取的结果。")
            for message in result.get("errors") or []:
                st.caption(f"· {message}")
        render_result(bvid, result)
        return

    if status == "empty":
        st.info(result.get("reason") or "没有可用于分析的评论数据。")
        return

    st.error(result.get("reason") or "分析失败，请稍后重试。")


# ----------------------------------------------------------------------
# 设置页：登录态与 cookie 文件
# ----------------------------------------------------------------------
def render_database_settings() -> None:
    """数据库连接信息与连通性检测。"""
    st.markdown("#### 数据库连接（MySQL）")

    columns = st.columns(4)
    columns[0].metric("主机", f"{MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}")
    columns[1].metric("账号", str(MYSQL_CONFIG["user"]))
    columns[2].metric("数据库", str(MYSQL_CONFIG["database"]))
    columns[3].metric("密码", "已设置" if MYSQL_CONFIG.get("password") else "未设置")

    env_path = ENV_FILE
    if env_path:
        st.caption(f"配置文件：{env_path}")
    else:
        st.caption(f"未找到 .env，当前使用默认值。建议生成到：{work_root() / '.env'}")

    if not MYSQL_CONFIG.get("password"):
        st.warning(
            "当前没有读到 MYSQL_PASSWORD，程序会以空密码连接。"
            "如果 MySQL 账号设了密码，写入数据时会报 `1045 Access denied ... using password: NO`。"
            "请填写下方 .env 里的 `MYSQL_PASSWORD=` 后点「重新加载配置」。"
        )

    action_columns = st.columns(3)
    with action_columns[0]:
        if st.button("测试连接", width="stretch"):
            refresh_mysql_config()
            ok, message = database.test_connection()
            st.session_state["db_check"] = (ok, message)
    with action_columns[1]:
        if st.button("重新加载配置", width="stretch", help="修改 .env 后点这里即可生效，无需重启程序"):
            refresh_mysql_config()
            st.rerun()
    with action_columns[2]:
        if st.button("生成 .env 模板", width="stretch", help=f"在 {work_root()} 下生成 .env，填密码后重新加载即可"):
            target = work_root() / ".env"
            if target.exists():
                st.error(f"{target} 已存在，请直接编辑它。")
            elif env_path:
                st.error(f"已经在使用 {env_path}，请直接编辑该文件。")
            else:
                try:
                    target.write_text(ENV_TEMPLATE, encoding="utf-8")
                except OSError as exc:
                    st.error(f"写入失败：{exc}")
                else:
                    refresh_mysql_config()
                    st.success(f"已生成：{target}，请填写 MYSQL_PASSWORD 后重新加载")
                    st.rerun()

    if not env_path:
        st.caption(
            "说明：打包版请在 exe 同目录放 .env；开发版放在项目根目录。"
            "也可以用环境变量 BILI_ENV_FILE 指定任意路径。"
        )

    checked = st.session_state.get("db_check")
    if checked:
        ok, message = checked
        if ok:
            st.success(message)
        else:
            st.error(message)


def render_cookie_settings(login: dict[str, Any], override: str | None) -> None:
    """cookie 设置区：上传/指定文件、查看状态、重新加载。"""
    st.markdown("#### 登录态与 cookie 文件")

    status_columns = st.columns(3)
    status_columns[0].metric("登录态", "已登录" if login.get("is_login") else "未登录")
    status_columns[1].metric("账号", login.get("user_name") or "-")
    cookie_path = login.get("cookie_file")
    status_columns[2].metric("cookie 键数量", f"{len(login.get('cookie_keys') or [])}")

    if cookie_path:
        st.caption(f"当前使用的 cookie 文件：{cookie_path}")
    else:
        st.caption("当前未找到 cookie 文件（按项目根目录 / exe 同目录自动查找）。")

    if login.get("cookie_keys"):
        st.caption("识别到的键：" + "、".join(login["cookie_keys"]))
    if login.get("is_login"):
        st.success("登录态有效：采集会带上你的登录态，可降低被风控拦截的概率。")
    elif login.get("has_cookie"):
        st.warning(login.get("reason") or "cookie 无效")
    else:
        st.info("未配置 cookie：以匿名模式运行，评论按热度采集。")

    st.divider()
    st.markdown("##### 方式一：上传 cookie 文件")
    st.caption(
        f"上传后会覆盖保存为 {work_root() / 'cookie.txt'}，并立即切换为使用该文件。"
        "支持浏览器导出的 cookie 文本，最少只需要 SESSDATA。"
    )
    uploaded = st.file_uploader("选择 cookie 文本文件（.txt）", type=["txt", "text", "cookie"])
    if uploaded is not None:
        payload = uploaded.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        if st.session_state.get("cookie_upload_digest") != digest:
            target = work_root() / "cookie.txt"
            try:
                target.write_bytes(payload)
            except OSError as exc:
                st.error(f"保存失败：{exc}")
            else:
                st.session_state["cookie_upload_digest"] = digest
                st.session_state["cookie_file_override"] = str(target)
                load_login_status.clear()
                st.success(f"已保存并启用：{target}")
                st.rerun()

    st.divider()
    st.markdown("##### 方式二：指定已存在的文件路径")
    path_column, button_column = st.columns([4, 1], vertical_alignment="bottom")
    with path_column:
        typed_path = st.text_input(
            "cookie 文件路径",
            value=override or "",
            placeholder=r"例如 D:\我的资料\bili_cookie.txt",
        )
    with button_column:
        use_path = st.button("使用该文件", width="stretch")

    if use_path:
        candidate = Path(typed_path.strip()).expanduser() if typed_path.strip() else None
        if candidate is None:
            st.warning("请先填写文件路径。")
        elif not candidate.is_file():
            st.error(f"文件不存在：{candidate}")
        else:
            cookies = read_cookie_file(candidate)
            if not cookies:
                st.error("该文件里没有解析出任何 cookie 内容，请检查文件格式。")
            elif not cookies.get("SESSDATA"):
                st.warning("解析成功，但没有找到 SESSDATA，登录态可能不可用。")
                st.session_state["cookie_file_override"] = str(candidate)
                load_login_status.clear()
                st.rerun()
            else:
                st.session_state["cookie_file_override"] = str(candidate)
                load_login_status.clear()
                st.success(f"已切换为使用：{candidate}")
                st.rerun()

    action_columns = st.columns(2)
    with action_columns[0]:
        if st.button("重新加载登录态", width="stretch", help="替换 cookie 文件内容后点这里立即生效"):
            load_login_status.clear()
            st.rerun()
    with action_columns[1]:
        if st.button("恢复默认查找", width="stretch", help="清除手动指定的路径，回到自动查找 cookie.txt"):
            st.session_state["cookie_file_override"] = None
            st.session_state["cookie_upload_digest"] = None
            load_login_status.clear()
            st.rerun()

    st.divider()
    st.caption(
        "安全提示：cookie 等同于登录凭据，请勿提交到 Git、勿随程序分发；"
        "自动化采集存在账号风控风险，建议使用小号。"
    )
    st.caption(
        "运行说明：页面是由本机后台程序提供的。如果浏览器出现 "
        "“Connection error / Is Streamlit still running?”，说明后台程序已经停止，"
        "重新双击 bili-analyzer.exe（或重新运行 run.py）即可恢复；"
        "关闭浏览器标签不会停止程序，要停止程序请在任务管理器里结束 bili-analyzer.exe。"
    )


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def run_analysis(bvid: str, comment_sort: str, cookie_file: str | None) -> dict[str, Any]:
    """调用 process_video，异常统一转成可展示的字典。"""
    try:
        api = BiliAPI(cookie_file=cookie_file) if cookie_file else None
        return analyzer.process_video(bvid, api=api, comment_sort=comment_sort)
    except Exception as exc:  # noqa: BLE001 - 页面需要兜住所有异常
        return {"status": "error", "bvid": bvid, "reason": f"分析过程中出现异常：{exc}"}


def main() -> None:
    st.set_page_config(
        page_title="B站视频用户画像分析工具",
        page_icon=str(LOGO_FILE) if LOGO_FILE.is_file() else "📊",
        layout="wide",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    override = st.session_state.get("cookie_file_override")
    login = load_login_status(override)
    render_hero(login)

    analyze_tab, settings_tab = st.tabs(["📊  分析", "⚙️  设置"])

    with settings_tab:
        render_database_settings()
        st.divider()
        render_cookie_settings(login, override)

    with analyze_tab:
        input_column, sort_column, button_column = st.columns([3, 2, 1], vertical_alignment="bottom")
        with input_column:
            bvid = st.text_input(
                "视频链接 / BV 号 / av 号 / ep 号",
                placeholder="粘贴分享链接，或输入 BV1xx411c7mD、av170001、ep123456",
                help=INPUT_HELP,
            )
        with sort_column:
            sort_label = st.selectbox(
                "评论排序",
                list(SORT_OPTIONS.keys()),
                index=0,
            help="「最新优先」按发布时间采集（适合看近期舆情）；「热门优先」采集高赞评论",
            )
        with button_column:
            clicked = st.button(
                "开始分析",
                type="primary",
                width="stretch",
                help="播放量超过 1.8 万的视频会跳过深度分析以节省 API 配额",
            )

        comment_sort = SORT_OPTIONS[sort_label]

        if clicked:
            bvid = (bvid or "").strip()
            if not bvid:
                st.warning("请先输入视频 BV 号。")
            else:
                with st.spinner("正在采集评论、弹幕并生成用户画像，请稍候…"):
                    st.session_state["bvid"] = bvid
                    st.session_state["result"] = run_analysis(bvid, comment_sort, override)
                # 结果已更新，清除缓存的历史数据读取
                load_video_info.clear()
                load_saved_snapshot.clear()

        result = st.session_state.get("result")
        current_bvid = st.session_state.get("bvid")
        if result:
            # 输入可能是分享链接/av 号，这里统一用解析出来的 BV 号查库与展示
            resolved_bvid = result.get("bvid") or current_bvid
            st.divider()
            render_result_by_status(str(resolved_bvid), result)


if __name__ == "__main__":
    main()
