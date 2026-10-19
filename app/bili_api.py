"""B 站数据采集：封装官方公开接口的调用。

说明：
- 只使用 B 站公开的 web 接口，请求头只带正常浏览器 User-Agent 与 Referer，
  不携带 Cookie、不伪造登录态。
- 所有网络调用都设置超时（默认 10 秒，见 config.REQUEST_TIMEOUT），
  并用 BiliAPIError 统一封装异常，方便上层做配额控制与降级处理。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.parse
import zlib
from datetime import datetime
from typing import Any
from xml.etree import ElementTree

import requests

from .config import (
    BILI_API_BASE_URL,
    REQUEST_TIMEOUT,
    USER_AGENT,
    current_cookies,
)

logger = logging.getLogger(__name__)

# 评论接口单页最多返回的条数（B 站限制 ps <= 49）
COMMENT_PAGE_SIZE = 20
# 翻页之间的间隔，避免触发风控
REQUEST_INTERVAL = 0.3
# WBI 签名密钥的缓存时间（秒）
WBI_KEY_TTL = 3600

# WBI 签名用的混淆表（B 站前端固定算法）
WBI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]

# 参与签名时需要过滤掉的字符
WBI_FILTER_CHARS = "!'()*"

# 评论排序：latest 走 mode=2（最新，用 offset 游标翻页），hot 走 mode=3（热门，用 next 页码）
COMMENT_SORT_MODES = {"hot": 3, "latest": 2}
COMMENT_SORT_NAMES = {3: "hot", 2: "latest"}


class BiliAPIError(RuntimeError):
    """B 站接口调用失败（网络异常、超时、返回值非 0 等）。"""


# 从分享链接/文本里识别视频编号（BV 号、av 号、ep 号、ss 号）
BV_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")
AV_PATTERN = re.compile(r"(?:^|[^0-9A-Za-z])av(\d+)", re.IGNORECASE)
EP_PATTERN = re.compile(r"(?:^|[^0-9A-Za-z])ep(\d+)", re.IGNORECASE)
SS_PATTERN = re.compile(r"(?:^|[^0-9A-Za-z])ss(\d+)", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s\u4e00-\u9fff]+")

# 番剧播放页（PGC 的 ep/ss 接口对未授权请求返回空数据，改用页面 HTML 提取编号）
BANGUMI_EP_URL = "https://www.bilibili.com/bangumi/play/ep{ep_id}"
BANGUMI_SS_URL = "https://www.bilibili.com/bangumi/play/ss{season_id}"


def _url_key(url: Any) -> str:
    """从 wbi_img 的 URL 中取出文件名（不含扩展名）。"""
    if not url:
        return ""
    return str(url).rsplit("/", 1)[-1].split(".")[0]


def _clean_param(value: Any) -> str:
    """签名前过滤掉 !'()* 这些字符。"""
    text = str(value)
    return "".join(ch for ch in text if ch not in WBI_FILTER_CHARS)


def find_short_link(text: str) -> str | None:
    """从分享文本里找出 b23.tv 短链（短链里看不出编号，需要先展开）。"""
    for url in URL_PATTERN.findall(text or ""):
        if "b23.tv" in url:
            return url
    return None


def parse_video_reference(text: str) -> tuple[str, str] | None:
    """从分享链接或任意文本中解析出视频编号。

    支持 BV 号、av 号、ep 号（番剧单集）、ss 号（番剧季），返回 (类型, 值)；
    识别不出来时返回 None。分享文本里常见的「【标题】 https://... 」形式也可以。
    """
    if not text:
        return None

    bv_match = BV_PATTERN.search(text)
    if bv_match:
        return "bvid", bv_match.group(0)

    av_match = AV_PATTERN.search(text)
    if av_match:
        return "aid", av_match.group(1)

    ep_match = EP_PATTERN.search(text)
    if ep_match:
        return "epid", ep_match.group(1)

    ss_match = SS_PATTERN.search(text)
    if ss_match:
        return "season_id", ss_match.group(1)

    return None


def extract_bangumi_ids(html: str) -> dict[str, str | None]:
    """从番剧播放页 HTML 中提取 aid / cid / bvid 与标题。

    番剧的 ep/ss 官方接口对未授权请求会返回空数据，因此改为解析播放页里的
    内联 JSON（页面里同时带有这三个编号）。
    """
    def search(pattern: str) -> str | None:
        match = re.search(pattern, html)
        return match.group(1) if match else None

    return {
        "aid": search(r'"aid":(\d+)'),
        "cid": search(r'"cid":(\d+)'),
        "bvid": search(r'"bvid":"(BV[0-9A-Za-z]{10})"'),
        "season_title": search(r'og:title"\s+content="([^"]{1,80})"'),
        "episode_title": search(r'"long_title":"([^"]{1,80})"'),
    }


def _to_datetime(timestamp: Any) -> datetime | None:
    """把秒级时间戳转成本地时间，非法或缺失时返回 None。"""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts)
    except (OSError, OverflowError, ValueError):
        return None


def _decompress(raw: bytes) -> bytes:
    """弹幕接口可能返回 deflate 压缩数据，这里做兼容处理。"""
    if raw[:1] == b"<":
        return raw
    for wbits in (-zlib.MAX_WBITS, zlib.MAX_WBITS, zlib.MAX_WBITS | 16):
        try:
            decompressor = zlib.decompressobj(wbits)
            data = decompressor.decompress(raw)
            if data.strip():
                return data
        except zlib.error:
            continue
    return raw


class BiliAPI:
    """B 站视频、评论、弹幕采集客户端。

    用法::

        api = BiliAPI()
        info = api.get_video_info("BV1xx411c7mD")
        comments = api.get_comments("BV1xx411c7mD", max_count=100)
        danmaku = api.get_danmaku("BV1xx411c7mD", max_count=100)
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int | None = None,
        session: requests.Session | None = None,
        cookies: dict[str, str] | None = None,
        cookie_file: str | None = None,
    ) -> None:
        self.base_url = (base_url or BILI_API_BASE_URL).rstrip("/")
        self.timeout = timeout or REQUEST_TIMEOUT
        self.session = session or requests.Session()
        # 只设置正常的浏览器标识；cookie 为空时就是匿名访问，
        # 有 cookie 时使用真实登录态，不做任何伪造。
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": "https://www.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        # cookies=None 时每次实例化都重新读文件，便于替换 cookie 后立即生效；
        # 传入 cookie_file 时只读指定的文件（界面上的「指定 cookie 文件」）
        if cookies is not None:
            self.cookies, self.cookie_file = dict(cookies), None
        else:
            self.cookies, self.cookie_file = current_cookies(cookie_file)
        if self.cookies:
            self.session.headers["Cookie"] = "; ".join(
                f"{key}={value}" for key, value in self.cookies.items()
            )
        self._video_cache: dict[str, dict[str, Any]] = {}
        self._wbi_mixin_key: str | None = None
        self._wbi_key_time = 0.0
        self._login_info: dict[str, Any] | None = None
        self.last_comment_sort: str | None = None

    # ------------------------------------------------------------------
    # 登录态
    # ------------------------------------------------------------------
    @property
    def has_cookie(self) -> bool:
        """是否提供了可用于登录的 cookie（至少要能读到 SESSDATA）。"""
        return bool(self.cookies.get("SESSDATA"))

    @property
    def has_login(self) -> bool:
        """cookie 是否通过了登录校验（会触发一次 nav 请求）。"""
        return bool(self.login_status().get("is_login"))

    def login_status(self, force: bool = False) -> dict[str, Any]:
        """校验 cookie 对应的登录态。

        返回 {has_cookie, cookie_file, is_login, user_name, cookie_keys, reason}。
        结果会缓存，force=True 时重新校验。注意：只记录 cookie 的键名与遮罩信息，
        不会输出任何 cookie 值。
        """
        if self._login_info is not None and not force:
            return self._login_info

        info: dict[str, Any] = {
            "has_cookie": self.has_cookie,
            "cookie_file": str(self.cookie_file) if self.cookie_file else None,
            "cookie_keys": sorted(self.cookies.keys()),
            "is_login": False,
            "user_name": None,
            "reason": None,
        }
        if not self.has_cookie:
            info["reason"] = "未配置 cookie，使用匿名模式"
            self._login_info = info
            return info

        try:
            data = self._get_json("/x/web-interface/nav", ok_codes=(0, -101))
        except BiliAPIError as exc:
            info["reason"] = f"登录态校验失败：{exc}"
            logger.warning("%s", info["reason"])
            self._login_info = info
            return info

        is_login = bool(data.get("isLogin"))
        info["is_login"] = is_login
        info["user_name"] = data.get("uname")
        if not is_login:
            info["reason"] = "cookie 已失效，已回退到匿名模式，请更新 cookie 文件内容"
            logger.warning(
                "cookie 校验未通过（keys=%s），回退匿名模式",
                ",".join(sorted(self.cookies.keys())),
            )
        else:
            logger.info("B 站登录态有效，用户：%s", data.get("uname"))

        self._login_info = info
        return info

    def resolve_comment_mode(self, sort: str = "hot") -> int:
        """把排序偏好转成接口的 mode。

        - sort="hot"（默认）→ mode=3 热门评论（next 页码翻页）；
        - sort="latest" → mode=2 最新评论（offset 游标翻页）。

        两种排序匿名都可用；登录态的作用是降低被风控（-412）的概率。
        """
        mode = COMMENT_SORT_MODES.get(str(sort).lower(), COMMENT_SORT_MODES["hot"])
        self.last_comment_sort = COMMENT_SORT_NAMES.get(mode, "hot")
        if not self.has_login:
            logger.debug("匿名模式采集评论（mode=%s），登录态可降低风控概率", mode)
        return mode

    # ------------------------------------------------------------------
    # 内部请求方法
    # ------------------------------------------------------------------
    def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        ok_codes: tuple[int, ...] = (0,),
    ) -> dict[str, Any]:
        """请求返回 JSON 的接口，返回 data 字段。"""
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
        except requests.Timeout as exc:
            raise BiliAPIError(f"请求 {path} 超时（>{self.timeout}s）") from exc
        except requests.RequestException as exc:
            raise BiliAPIError(f"请求 {path} 失败：{exc}") from exc

        try:
            payload = resp.json()
        except ValueError as exc:
            raise BiliAPIError(f"{path} 返回内容不是合法 JSON（HTTP {resp.status_code}）") from exc

        code = payload.get("code")
        if code not in ok_codes:
            message = payload.get("message") or payload.get("msg")
            raise BiliAPIError(f"{path} 返回错误 code={code} message={message}")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def _get_wbi_mixin_key(self, force: bool = False) -> str:
        """获取并缓存 WBI 签名密钥（未登录状态下 nav 接口返回 -101，属正常）。"""
        now = time.time()
        if (
            not force
            and self._wbi_mixin_key
            and now - self._wbi_key_time < WBI_KEY_TTL
        ):
            return self._wbi_mixin_key

        # nav 接口未登录时 code=-101，但 wbi_img 依然可用
        data = self._get_json("/x/web-interface/nav", ok_codes=(0, -101))
        wbi_img = data.get("wbi_img") or {}
        raw = _url_key(wbi_img.get("img_url")) + _url_key(wbi_img.get("sub_url"))
        if len(raw) < 64:
            raise BiliAPIError("获取 WBI 签名密钥失败：nav 接口未返回有效的 wbi_img")

        self._wbi_mixin_key = "".join(raw[i] for i in WBI_MIXIN_KEY_ENC_TAB)[:32]
        self._wbi_key_time = now
        return self._wbi_mixin_key

    def _sign(self, params: dict[str, Any]) -> dict[str, str]:
        """按 B 站 WBI 规则给参数签名，返回带 wts / w_rid 的参数。"""
        mixin_key = self._get_wbi_mixin_key()
        signed = {key: _clean_param(value) for key, value in params.items()}
        signed["wts"] = str(int(time.time()))
        query = urllib.parse.urlencode(sorted(signed.items()))
        signed["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        return signed

    def _require_video(self, bvid: str) -> dict[str, Any]:
        """取视频信息，优先用缓存（评论/弹幕都需要 aid、cid）。"""
        if bvid not in self._video_cache:
            self.get_video_info(bvid)
        return self._video_cache[bvid]

    # ------------------------------------------------------------------
    # 对外方法
    # ------------------------------------------------------------------
    def get_video_info(self, bvid: str) -> dict[str, Any]:
        """获取视频基础信息。

        返回的字典包含 view_count（播放量）以及 aid、cid、title、up_name、
        各项统计数、发布时间、时长、简介等字段。
        """
        data = self._get_json("/x/web-interface/view", {"bvid": bvid})
        if not data:
            raise BiliAPIError(f"未获取到视频信息，请检查 bvid 是否正确：{bvid}")
        return self._build_video_info(data, fallback_bvid=bvid)

    def _build_video_info(
        self,
        data: dict[str, Any],
        fallback_bvid: str | None = None,
    ) -> dict[str, Any]:
        """把 view 接口返回的数据整理成统一结构并写入缓存。"""
        stat = data.get("stat") or {}
        owner = data.get("owner") or {}
        info = {
            "bvid": data.get("bvid") or fallback_bvid,
            "aid": data.get("aid"),
            "cid": data.get("cid"),
            "title": data.get("title"),
            "up_name": owner.get("name"),
            "up_mid": owner.get("mid"),
            "view_count": int(stat.get("view") or 0),
            "danmaku_count": int(stat.get("danmaku") or 0),
            "reply_count": int(stat.get("reply") or 0),
            "like_count": int(stat.get("like") or 0),
            "coin_count": int(stat.get("coin") or 0),
            "favorite_count": int(stat.get("favorite") or 0),
            "publish_time": _to_datetime(data.get("pubdate")),
            "duration": int(data.get("duration") or 0),
            "description": data.get("desc"),
        }
        if not info["bvid"]:
            raise BiliAPIError("接口没有返回 BV 号，无法继续采集")
        self._video_cache[info["bvid"]] = info
        return info

    # ------------------------------------------------------------------
    # 分享链接 / 各种编号解析
    # ------------------------------------------------------------------
    def resolve_short_url(self, url: str) -> str:
        """展开 b23.tv 短链，返回最终地址；失败时原样返回。"""
        try:
            resp = self.session.get(url, allow_redirects=True, timeout=self.timeout, stream=True)
            final_url = resp.url or url
            resp.close()
            return final_url
        except requests.RequestException as exc:
            logger.warning("短链展开失败（%s）：%s", url, exc)
            return url

    def resolve_video(self, target: str) -> dict[str, Any]:
        """把「分享链接 / BV 号 / av 号 / ep 号 / ss 号」解析成视频信息。

        返回值与 get_video_info 相同，额外带 input_type / input_value 便于界面提示。
        """
        text = (target or "").strip()
        if not text:
            raise BiliAPIError("请输入视频链接或 BV 号")

        reference = parse_video_reference(text)
        if reference is None:
            # 分享短链（b23.tv）里看不出编号，先展开再解析
            short_link = find_short_link(text)
            if short_link:
                expanded = self.resolve_short_url(short_link)
                reference = parse_video_reference(expanded)

        if reference is None:
            raise BiliAPIError(
                "无法从输入中识别视频编号。请粘贴 B 站分享链接，"
                "或直接输入 BV 号 / av 号 / ep 号（例如 BV1xx411c7mD、av170001、ep123456）"
            )

        kind, value = reference
        if kind == "bvid":
            info = self.get_video_info(value)
        elif kind == "aid":
            info = self._get_video_info_by_aid(int(value))
        elif kind == "epid":
            info = self._get_video_info_by_ep(int(value))
        else:  # season_id
            info = self._get_video_info_by_season(int(value))

        info["input_type"] = kind
        info["input_value"] = value
        info["input_text"] = text
        return info

    def _get_video_info_by_aid(self, aid: int) -> dict[str, Any]:
        """用 av 号（aid）获取视频信息。"""
        data = self._get_json("/x/web-interface/view", {"aid": aid})
        if not data:
            raise BiliAPIError(f"未获取到视频信息，请检查 av 号是否正确：av{aid}")
        return self._build_video_info(data)

    def _get_video_info_by_ep(self, ep_id: int) -> dict[str, Any]:
        """用番剧单集号（ep）获取对应视频信息。"""
        html = self._fetch_html(BANGUMI_EP_URL.format(ep_id=ep_id))
        ids = extract_bangumi_ids(html)
        if not (ids.get("bvid") or ids.get("aid")):
            raise BiliAPIError(f"未能从番剧页面解析出视频编号：ep{ep_id}")

        try:
            if ids.get("bvid"):
                info = self.get_video_info(str(ids["bvid"]))
            else:
                info = self._get_video_info_by_aid(int(str(ids["aid"])))
        except BiliAPIError:
            # 普通视频接口查不到时（部分番剧会有限制），用剧集信息接口的统计兜底
            info = self._bangumi_info_from_pgc(ep_id, ids)

        if ids.get("cid"):
            info["cid"] = int(str(ids["cid"]))
        info["ep_id"] = ep_id

        titles = [ids.get("season_title"), ids.get("episode_title")]
        title = " ".join(part for part in titles if part)
        if title:
            info["title"] = title
        return info

    def _get_video_info_by_season(self, season_id: int) -> dict[str, Any]:
        """用番剧季号（ss）获取其第一集对应的视频信息。"""
        html = self._fetch_html(BANGUMI_SS_URL.format(season_id=season_id))
        match = re.search(r"/bangumi/play/ep(\d+)", html)
        if not match:
            raise BiliAPIError(f"未能从番剧页面解析出剧集编号：ss{season_id}")
        return self._get_video_info_by_ep(int(match.group(1)))

    def _fetch_html(self, url: str) -> str:
        """抓取网页 HTML（用于番剧 play 页）。"""
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.Timeout as exc:
            raise BiliAPIError(f"请求页面超时（>{self.timeout}s）：{url}") from exc
        except requests.RequestException as exc:
            raise BiliAPIError(f"请求页面失败：{exc}") from exc
        return resp.text

    def _bangumi_info_from_pgc(self, ep_id: int, ids: dict[str, Any]) -> dict[str, Any]:
        """兜底：用剧集信息接口的统计数据拼出视频信息。"""
        bvid = ids.get("bvid")
        if not bvid:
            raise BiliAPIError(f"ep{ep_id} 缺少 BV 号，无法继续采集")
        data = self._get_json("/pgc/season/episode/web/info", {"ep_id": ep_id})
        stat = data.get("stat") or {}
        info = {
            "bvid": str(bvid),
            "aid": int(str(ids["aid"])) if ids.get("aid") else None,
            "cid": int(str(ids["cid"])) if ids.get("cid") else None,
            "title": ids.get("season_title") or ids.get("episode_title"),
            "up_name": None,
            "up_mid": None,
            "view_count": int(stat.get("view") or 0),
            "danmaku_count": int(stat.get("dm") or 0),
            "reply_count": int(stat.get("reply") or 0),
            "like_count": int(stat.get("like") or 0),
            "coin_count": int(stat.get("coin") or 0),
            "favorite_count": int(stat.get("favorite") or 0),
            "publish_time": None,
            "duration": 0,
            "description": None,
        }
        self._video_cache[info["bvid"]] = info
        return info

    def get_comments(
        self,
        bvid: str,
        max_count: int = 100,
        sort: str = "hot",
    ) -> list[dict[str, Any]]:
        """获取评论列表，最多返回 max_count 条。

        sort="hot" 按热度排序（mode=3，匿名可用）；
        sort="latest" 按时间排序（mode=2），需要有效登录态，否则自动降级为热度排序。

        优先使用带 WBI 签名的 /x/v2/reply/wbi/main，该接口不可用时回退到旧的
        /x/v2/reply 接口（旧接口在未登录状态下每页只返回少量热门评论）。
        """
        if max_count <= 0:
            return []

        mode = self.resolve_comment_mode(sort)
        video = self._require_video(bvid)
        aid = video.get("aid")
        if not aid:
            raise BiliAPIError(f"视频 {bvid} 缺少 aid，无法获取评论")

        try:
            return self._get_comments_wbi(video, aid, max_count, mode=mode)
        except BiliAPIError as exc:
            logger.warning("WBI 评论接口不可用（%s），回退到旧接口", exc)
        return self._get_comments_legacy(video, aid, max_count)

    def _get_comments_wbi(
        self,
        video: dict[str, Any],
        aid: int,
        max_count: int,
        mode: int = 3,
    ) -> list[dict[str, Any]]:
        """翻页采集评论。

        两种模式的翻页方式不同，这里分别处理：
        - mode=3（热门）：用 next 页码翻页；
        - mode=2（最新）：用 pagination_str={"offset": ...} 游标翻页，
          游标取自上一页 cursor.pagination_reply.next_offset
          （用 next 页码翻页只会重复返回同一页）。

        注意：最新排序下，接口会把 UP 主置顶评论放在 top_replies 里，
        这里会一并采集，因此结果最前面可能出现一条时间较早的置顶评论。
        """
        comments: list[dict[str, Any]] = []
        seen: set[Any] = set()
        page_next = 1
        cursor_offset = ""

        while len(comments) < max_count:
            if mode == COMMENT_SORT_MODES["latest"]:
                params: dict[str, Any] = {
                    "oid": aid,
                    "type": 1,
                    "mode": mode,
                    "ps": COMMENT_PAGE_SIZE,
                    "plat": 1,
                    "web_location": 1315875,
                    "pagination_str": json.dumps(
                        {"offset": cursor_offset}, separators=(",", ":")
                    ),
                }
            else:
                params = {
                    "oid": aid,
                    "type": 1,
                    "mode": mode,
                    "next": page_next,
                    "ps": COMMENT_PAGE_SIZE,
                }

            data = self._get_json("/x/v2/reply/wbi/main", self._sign(params))

            batch = list(data.get("top_replies") or []) + list(data.get("replies") or [])
            if not batch:
                break

            added = 0
            for reply in batch:
                rpid = reply.get("rpid")
                if rpid in seen:
                    continue
                seen.add(rpid)
                comment = self._parse_comment(reply, video)
                if comment:
                    comments.append(comment)
                    added += 1
                if len(comments) >= max_count:
                    break

            cursor = data.get("cursor") or {}
            if cursor.get("is_end") or added == 0:
                break

            if mode == COMMENT_SORT_MODES["latest"]:
                next_offset = (cursor.get("pagination_reply") or {}).get("next_offset")
                if not next_offset or next_offset == cursor_offset:
                    break
                cursor_offset = next_offset
            else:
                next_page = cursor.get("next")
                if not next_page or next_page == page_next:
                    break
                page_next = next_page
            time.sleep(REQUEST_INTERVAL)

        return comments[:max_count]

    def _get_comments_legacy(
        self,
        video: dict[str, Any],
        aid: int,
        max_count: int,
    ) -> list[dict[str, Any]]:
        """旧评论接口（兜底）：按 pn 翻页。"""
        comments: list[dict[str, Any]] = []
        seen: set[Any] = set()
        page = 1

        while len(comments) < max_count:
            data = self._get_json(
                "/x/v2/reply",
                {"type": 1, "oid": aid, "pn": page, "ps": COMMENT_PAGE_SIZE, "sort": 2},
            )
            replies = data.get("replies") or []
            if not replies:
                break

            for reply in replies:
                rpid = reply.get("rpid")
                if rpid in seen:
                    continue
                seen.add(rpid)
                comment = self._parse_comment(reply, video)
                if comment:
                    comments.append(comment)
                if len(comments) >= max_count:
                    break

            if len(replies) < COMMENT_PAGE_SIZE:
                break
            page += 1
            time.sleep(REQUEST_INTERVAL)

        return comments[:max_count]

    def get_danmaku(self, bvid: str, max_count: int = 100) -> list[dict[str, Any]]:
        """获取弹幕列表，最多返回 max_count 条（接口默认最新在前）。"""
        if max_count <= 0:
            return []

        video = self._require_video(bvid)
        cid = video.get("cid")
        if not cid:
            raise BiliAPIError(f"视频 {bvid} 缺少 cid，无法获取弹幕")

        url = f"{self.base_url}/x/v1/dm/list.so"
        try:
            resp = self.session.get(url, params={"oid": cid}, timeout=self.timeout)
            resp.raise_for_status()
        except requests.Timeout as exc:
            raise BiliAPIError(f"请求弹幕接口超时（>{self.timeout}s）") from exc
        except requests.RequestException as exc:
            raise BiliAPIError(f"请求弹幕接口失败：{exc}") from exc

        try:
            root = ElementTree.fromstring(_decompress(resp.content))
        except ElementTree.ParseError as exc:
            raise BiliAPIError(f"弹幕数据解析失败：{exc}") from exc

        danmaku: list[dict[str, Any]] = []
        for node in root.findall("d"):
            item = self._parse_danmaku(node, video)
            if item:
                danmaku.append(item)
            if len(danmaku) >= max_count:
                break

        return danmaku

    # ------------------------------------------------------------------
    # 解析方法
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_comment(reply: dict[str, Any], video: dict[str, Any]) -> dict[str, Any] | None:
        """把评论接口返回的一条数据转成数据库字段。"""
        content = ((reply.get("content") or {}).get("message") or "").strip()
        if not content:
            return None

        member = reply.get("member") or {}
        level_info = member.get("level_info") or {}
        location = ((reply.get("reply_control") or {}).get("location") or "").strip()
        if "：" in location:
            location = location.split("：")[-1]

        return {
            "rpid": reply.get("rpid"),
            "bvid": video["bvid"],
            "user_name": member.get("uname"),
            "user_level": level_info.get("current_level"),
            "user_sex": member.get("sex"),
            "user_sign": (member.get("sign") or "")[:255] or None,
            "content": content,
            "like_count": int(reply.get("like") or 0),
            "reply_time": _to_datetime(reply.get("ctime")),
            "ip_location": location[:64] or None,
        }

    @staticmethod
    def _parse_danmaku(node: ElementTree.Element, video: dict[str, Any]) -> dict[str, Any] | None:
        """把弹幕 XML 节点转成数据库字段。

        p 属性依次为：出现时间(秒), 类型, 字号, 颜色, 发送时间戳, 弹幕池,
        用户哈希, 弹幕 ID。弹幕接口不返回昵称，因此 user_name 留空。
        """
        content = (node.text or "").strip()
        if not content:
            return None

        parts = (node.get("p") or "").split(",")
        danmaku_type = None
        if len(parts) > 1:
            try:
                danmaku_type = int(parts[1])
            except ValueError:
                danmaku_type = None

        send_time = _to_datetime(parts[4]) if len(parts) > 4 else None
        return {
            "bvid": video["bvid"],
            "user_name": None,
            "content": content[:500],
            "send_time": send_time,
            "danmaku_type": danmaku_type,
        }
