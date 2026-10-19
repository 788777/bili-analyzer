"""MySQL 连接与基础读写操作。"""

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import pymysql

from . import config as _config

# MySQL 常见错误码 → 可操作的中文提示
ERROR_HINTS = {
    1045: "MySQL 账号或密码不正确（1045）。请把正确密码填到 .env 的 MYSQL_PASSWORD，"
          "并确认 MYSQL_USER 有权限；如果密码为空，MySQL 会报 “using password: NO”。",
    1049: "数据库不存在（1049）。请先执行 sql/init.sql 创建数据库与表，"
          "或把 .env 里的 MYSQL_DATABASE 改成已存在的库名。",
    2003: "连不上 MySQL 服务（2003）。请确认 MySQL 已启动、端口正确"
          "（默认 3306），以及 .env 里的 MYSQL_HOST 是否正确。",
    1130: "MySQL 不允许该主机连接（1130）。请检查 MYSQL_HOST 是否为 localhost/127.0.0.1。",
    1044: "当前账号没有访问该数据库的权限（1044）。请换用有权限的账号，"
          "或给该账号授权。",
}


def describe_error(exc: BaseException) -> str:
    """把 pymysql 异常翻译成「原始错误 + 可操作提示」。"""
    code = None
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        code = args[0]

    raw = str(exc)
    settings = _config.MYSQL_CONFIG
    if code is None or code not in ERROR_HINTS:
        return raw

    extra = []
    if not settings.get("password"):
        extra.append("当前配置里密码为空")
    extra.append(f"当前连接：{settings.get('user')}@"
                 f"{settings.get('host')}:{settings.get('port')}"
                 f"/{settings.get('database')}")
    env_file = _config.ENV_FILE
    extra.append(f"配置文件：{env_file if env_file else '未找到 .env，正在使用默认值'}")
    return f"{raw}\n{ERROR_HINTS[code]}\n（{'；'.join(extra)}）"


def test_connection() -> tuple[bool, str]:
    """测试数据库连接，返回 (是否成功, 说明)。"""
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION()")
                version = cursor.fetchone()
        return True, f"连接成功，MySQL 版本：{version[0] if version else '未知'}"
    except pymysql.MySQLError as exc:
        return False, describe_error(exc)
    except Exception as exc:  # noqa: BLE001 - 其它异常也给出可读信息
        return False, str(exc)


@contextmanager
def get_connection() -> Iterator[pymysql.connections.Connection]:
    """获取数据库连接（自动提交/回滚，退出时关闭）。"""
    conn = pymysql.connect(**_config.MYSQL_CONFIG, autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(sql: str, params: Sequence[Any] | None = None) -> int:
    """执行写操作，返回受影响行数。"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            return cursor.execute(sql, params)


def fetch_all(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    """执行查询，返回字典列表。"""
    with get_connection() as conn:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())


def fetch_one(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    """执行查询，返回首行或 None。"""
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def execute_many(sql: str, rows: Sequence[Sequence[Any]]) -> int:
    """批量执行写操作，返回受影响行数。"""
    rows = list(rows)
    if not rows:
        return 0
    with get_connection() as conn:
        with conn.cursor() as cursor:
            return cursor.executemany(sql, rows)
