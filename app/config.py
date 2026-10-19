"""项目配置：从 .env 读取环境变量，并加载可选的 B 站登录 cookie。"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent


def _candidate_dirs() -> list[Path]:
    """配置文件可能的存放目录：exe 同目录优先，其次项目根目录与当前工作目录。"""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):  # 打包后：exe 同目录
        dirs.append(Path(sys.executable).resolve().parent)
    dirs.append(BASE_DIR)
    try:
        dirs.append(Path.cwd())
    except OSError:  # pragma: no cover - 极少数情况下取不到当前目录
        pass

    unique: list[Path] = []
    for directory in dirs:
        if directory not in unique:
            unique.append(directory)
    return unique


def find_env_file() -> Path | None:
    """定位 .env：可用 BILI_ENV_FILE 指定，否则依次在 exe 同目录/项目根目录/当前目录查找。"""
    custom = os.getenv("BILI_ENV_FILE")
    if custom:
        candidate = Path(custom).expanduser()
        if candidate.is_file():
            return candidate
    for directory in _candidate_dirs():
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


# 记录由 .env 注入到进程环境里的键，刷新时先清理，避免文件删除后旧值残留
_ENV_FILE_KEYS: set[str] = set()


def apply_env_file(path: Path | None) -> None:
    """把指定 .env 的内容注入进程环境变量。

    规则：真实的系统环境变量优先级高于 .env 文件；.env 里被删除的键会在下次刷新时清理掉。
    """
    global _ENV_FILE_KEYS

    for key in _ENV_FILE_KEYS:
        os.environ.pop(key, None)
    _ENV_FILE_KEYS = set()

    if path is None:
        return

    try:
        values = dotenv_values(path)
    except OSError:
        return

    for key, value in values.items():
        if value is None or key in os.environ:  # 已有系统环境变量 → 以系统变量为准
            continue
        os.environ[key] = value
        _ENV_FILE_KEYS.add(key)


ENV_FILE = find_env_file()
apply_env_file(ENV_FILE)


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else int(value)


def _read_mysql_settings() -> dict[str, Any]:
    """按当前环境变量（含 .env）读出 MySQL 连接参数。"""
    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": _get_int("MYSQL_PORT", 3306),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "bili_analyzer"),
        "charset": "utf8mb4",
    }


MYSQL_CONFIG = _read_mysql_settings()

MYSQL_HOST = MYSQL_CONFIG["host"]
MYSQL_PORT = MYSQL_CONFIG["port"]
MYSQL_USER = MYSQL_CONFIG["user"]
MYSQL_PASSWORD = MYSQL_CONFIG["password"]
MYSQL_DATABASE = MYSQL_CONFIG["database"]

BILI_API_BASE_URL = os.getenv("BILI_API_BASE_URL", "https://api.bilibili.com")

# 请求相关配置：可直接写死在代码里，也可在 .env 中覆盖（可选）
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
USER_AGENT = os.getenv("USER_AGENT", DEFAULT_USER_AGENT)
REQUEST_TIMEOUT = _get_int("REQUEST_TIMEOUT", 10)


def refresh_mysql_config() -> dict[str, Any]:
    """重新读取 .env / 环境变量并更新全局配置（供界面上「重新加载配置」使用）。

    database.py 里引用的是同一个 MYSQL_CONFIG 对象，因此原地更新即可生效。
    """
    global ENV_FILE, MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

    env_file = find_env_file()
    ENV_FILE = env_file
    apply_env_file(env_file)

    settings = _read_mysql_settings()
    MYSQL_CONFIG.update(settings)

    MYSQL_HOST = settings["host"]
    MYSQL_PORT = settings["port"]
    MYSQL_USER = settings["user"]
    MYSQL_PASSWORD = settings["password"]
    MYSQL_DATABASE = settings["database"]
    return MYSQL_CONFIG


# ----------------------------------------------------------------------
# B 站登录 cookie（可选）
# ----------------------------------------------------------------------
# 依次尝试这些文件名，命中第一个存在的即可
COOKIE_FILE_NAMES = ("cookie.txt", "cokie.txt", "cookies.txt", "cookie")

# 常见写法 → 真实 cookie 名（忽略大小写与下划线，且允许 BILIBILI_ 前缀）
_COOKIE_KEY_ALIASES = {
    "sessdata": "SESSDATA",
    "bilijct": "bili_jct",
    "biliticket": "bili_ticket",
    "buvid3": "buvid3",
    "buvid4": "buvid4",
    "buvid": "buvid3",
    "dedeuserid": "DedeUserID",
    "dedeuseridckmd5": "DedeUserID__ckMd5",
    "sid": "sid",
}


def _cookie_search_dirs() -> list[Path]:
    """cookie 文件的查找目录：先 exe/项目根目录，再当前工作目录。"""
    dirs: list[Path] = []
    env_path = os.getenv("BILI_COOKIE_FILE")
    if env_path:
        candidate = Path(env_path).expanduser()
        dirs.append(candidate.parent if candidate.is_file() else candidate)
    if getattr(sys, "frozen", False):  # 打包后：exe 同目录优先
        dirs.append(Path(sys.executable).resolve().parent)
    dirs.append(BASE_DIR)
    dirs.append(Path.cwd())

    unique: list[Path] = []
    for directory in dirs:
        if directory not in unique:
            unique.append(directory)
    return unique


def find_cookie_file() -> Path | None:
    """定位 cookie 文件，找不到返回 None。"""
    env_path = os.getenv("BILI_COOKIE_FILE")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.is_file():
            return candidate
    for directory in _cookie_search_dirs():
        for name in COOKIE_FILE_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def normalize_cookie_key(raw: str) -> str:
    """把各种写法统一成真实 cookie 名。"""
    key = raw.strip()
    if key.upper().startswith("BILIBILI_"):
        key = key[len("BILIBILI_"):]
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return _COOKIE_KEY_ALIASES.get(compact, key)


def _cookie_pairs(text: str) -> dict[str, str]:
    """解析 cookie 文本，兼容多种粘贴格式。"""
    text = text.strip()
    if not text:
        return {}

    # 形式一：JSON，如 {"SESSDATA": "...", "bili_jct": "..."}
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            return {
                normalize_cookie_key(str(k)): str(v).strip()
                for k, v in payload.items()
                if str(v).strip()
            }

    # 去掉可能粘贴进来的 "Cookie:" 前缀
    text = re.sub(r"^\s*cookie\s*:\s*", "", text, flags=re.IGNORECASE)

    pairs: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for chunk in line.split(";"):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            key, _, value = chunk.partition("=")
            value = value.strip().strip('"').strip("'").strip()
            if key.strip() and value:
                pairs[normalize_cookie_key(key)] = value

    # 形式二：整份文件只有一个值（例如只复制了 SESSDATA 本身）
    if not pairs and len(text) > 20:
        pairs["SESSDATA"] = text.splitlines()[0].strip().strip('"').strip("'")
    return pairs


def read_cookie_file(path: str | Path) -> dict[str, str]:
    """读取指定的 cookie 文件并解析；文件不存在或读取失败时返回空字典。"""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    return _cookie_pairs(text)


def load_cookies(path: str | Path | None = None) -> tuple[dict[str, str], Path | None]:
    """读取 cookie，返回 (cookie 字典, 文件路径)。

    传入 path 时只读指定的那个文件（用于「指定 cookie 文件」功能）；
    不传时按默认顺序自动查找。
    """
    if path:
        candidate = Path(path).expanduser()
        if not candidate.is_file():
            return {}, None
        return read_cookie_file(candidate), candidate

    found = find_cookie_file()
    if found is None:
        return {}, None
    return read_cookie_file(found), found


def mask_secret(value: str, keep: int = 4) -> str:
    """日志用：只保留少量首尾字符，避免凭据泄露。"""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}(len={len(value)})"


def current_cookies(path: str | Path | None = None) -> tuple[dict[str, str], Path | None]:
    """每次都重新读取 cookie 文件。

    这样运行中替换 cookie 文件内容后无需重启程序，下一次分析即可生效。
    """
    return load_cookies(path)


# 导入时的快照，供只读场景（如打印状态）使用；实际请求走 current_cookies()
COOKIE_DICT, COOKIE_FILE = load_cookies()
HAS_COOKIE = bool(COOKIE_DICT.get("SESSDATA"))
