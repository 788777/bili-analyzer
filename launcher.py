"""bili-analyzer 可视化一键启动器（Windows）。

用途
----
把本项目所有服务的「域名 / 端口 → 页面」对应关系集中显示在一个图形界面里，
并提供 启动 / 停止 / 重启 / 打开页面 的一键操作。

三条硬性设计约束
----------------
1. 只控制本项目
   启动器只管理由它自己拉起、并登记在 .launcher/state.json 里的进程；停止前核对
   「进程创建时间 + 可执行文件路径」，即使 PID 被系统复用也绝不会误杀别的进程。
2. 与其它项目互不影响
   端口被别的程序占用时自动顺延到空闲端口，既不抢占也不受其影响；
   全程不枚举、不结束任何未登记的进程。
3. 不受语言（与区域设置）限制
   界面支持 中文 / English 即时切换，所有文本按语言键查找，服务名可只写一种语言；
   文件读写统一 UTF-8，子进程注入 PYTHONUTF8 / PYTHONIOENCODING，与系统代码页无关。

入口
----
GUI：    python launcher.py
命令行： python launcher.py --check
         python launcher.py --status
         python launcher.py --start all
         python launcher.py --stop dev
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

APP_TITLE = "bili-analyzer Launcher"
APP_VERSION = "1.0.0"
CONFIG_NAME = "launcher.config.json"
STATE_DIR_NAME = ".launcher"
STATE_FILE_NAME = "state.json"
LOG_DIR_NAME = "logs"
CONFIG_ENV_VAR = "BILI_LAUNCHER_CONFIG"
CREATE_NO_WINDOW = 0x08000000

RUNNING_STATES = ("running", "starting", "noport")

TEXT: dict[str, dict[str, str]] = {
    "zh": {
        "app_title": "项目一键启动器",
        "subtitle": "仅管理本项目 · 启动 / 停止互不影响其它项目",
        "lang_button": "English",
        "col_status": "状态",
        "col_service": "服务",
        "col_address": "域名 / 访问地址",
        "col_pages": "对应页面",
        "col_port": "端口",
        "btn_start": "启动",
        "btn_stop": "停止",
        "btn_restart": "重启",
        "btn_open": "打开页面",
        "btn_start_all": "全部启动",
        "btn_stop_all": "全部停止",
        "btn_refresh": "刷新",
        "btn_exit": "退出",
        "grp_details": "域名 → 页面 对应关系",
        "grp_log": "运行日志",
        "detail_empty": "请在列表中选择一个服务。",
        "state_running": "运行中",
        "state_starting": "启动中",
        "state_noport": "页面未就绪",
        "state_stopped": "已停止",
        "state_disabled": "已停用",
        "state_missing": "文件缺失",
        "port_auto": "自动",
        "db_title": "数据库依赖",
        "db_ok": "MySQL {host}:{port} 可连接",
        "db_down": "MySQL {host}:{port} 不可达（页面里查询数据会报错）",
        "db_none": "未配置 MySQL",
        "updated": "更新于 {time}",
        "need_select": "请先在列表里选中一个服务。",
        "already_running": "「{name}」已经在运行，无需重复启动。",
        "start_failed": "启动失败：{reason}（详情见日志 {log}）",
        "started_ok": "已启动「{name}」→ {url}",
        "stopped_ok": "已停止「{name}」。",
        "stop_stale": "登记信息已过期，只清理了记录，未触碰任何进程。",
        "not_running": "「{name}」当前没有运行。",
        "missing_files": "缺少必需文件：{path}",
        "disabled_service": "该服务在配置里被停用（enabled = false）。",
        "foreign_hint": "端口 {port} 已被其它程序占用，启动时会自动改用空闲端口，不会干扰对方。",
        "exit_title": "退出启动器",
        "exit_message": "还有服务正在运行：\n{names}\n\n「是」= 停止它们后退出；「否」= 保留运行直接退出；「取消」= 返回。",
        "open_failed": "无法打开页面：{url}",
        "no_command": "配置里没有可执行的 command。",
        "f_service": "服务",
        "f_status": "状态",
        "f_pid": "进程 PID",
        "f_domain": "域名",
        "f_pages": "页面",
        "f_note": "说明",
        "f_command": "启动命令",
        "f_log": "日志文件",
        "f_port_free": "首选端口空闲",
        "f_port_foreign": "首选端口 {port} 被其它程序占用",
        "lang_name": "中文",
        "kind_dep": "依赖",
    },
    "en": {
        "app_title": "One-click Project Launcher",
        "subtitle": "Manages this project only - start/stop never touches other projects",
        "lang_button": "中文",
        "col_status": "Status",
        "col_service": "Service",
        "col_address": "Domain / URL",
        "col_pages": "Pages",
        "col_port": "Port",
        "btn_start": "Start",
        "btn_stop": "Stop",
        "btn_restart": "Restart",
        "btn_open": "Open page",
        "btn_start_all": "Start all",
        "btn_stop_all": "Stop all",
        "btn_refresh": "Refresh",
        "btn_exit": "Exit",
        "grp_details": "Domain → page mapping",
        "grp_log": "Activity log",
        "detail_empty": "Select a service in the list.",
        "state_running": "Running",
        "state_starting": "Starting",
        "state_noport": "Page not ready",
        "state_stopped": "Stopped",
        "state_disabled": "Disabled",
        "state_missing": "Files missing",
        "port_auto": "auto",
        "db_title": "Database dependency",
        "db_ok": "MySQL {host}:{port} reachable",
        "db_down": "MySQL {host}:{port} unreachable (data queries on the page will fail)",
        "db_none": "MySQL not configured",
        "updated": "updated {time}",
        "need_select": "Select a service in the list first.",
        "already_running": "\"{name}\" is already running.",
        "start_failed": "Start failed: {reason} (see log {log})",
        "started_ok": "Started \"{name}\" -> {url}",
        "stopped_ok": "Stopped \"{name}\".",
        "stop_stale": "Stale record only; no process was touched.",
        "not_running": "\"{name}\" is not running.",
        "missing_files": "Required file missing: {path}",
        "disabled_service": "This service is disabled in the config (enabled = false).",
        "foreign_hint": "Port {port} is used by another program; an idle port will be chosen automatically.",
        "exit_title": "Exit launcher",
        "exit_message": "Services still running:\n{names}\n\nYes = stop them and exit; No = keep them running and exit; Cancel = go back.",
        "open_failed": "Cannot open page: {url}",
        "no_command": "No command configured for this service.",
        "f_service": "Service",
        "f_status": "Status",
        "f_pid": "PID",
        "f_domain": "Domain",
        "f_pages": "Pages",
        "f_note": "Note",
        "f_command": "Command",
        "f_log": "Log file",
        "f_port_free": "preferred port is free",
        "f_port_foreign": "preferred port {port} is used by another program",
        "lang_name": "English",
        "kind_dep": "dependency",
    },
}


def tr(lang: str, key: str, **fmt: Any) -> str:
    """按语言键取文本，缺失时回退到英文，再回退到键名本身。"""
    table = TEXT.get(lang) or TEXT["en"]
    text = table.get(key) or TEXT["en"].get(key, key)
    if fmt:
        try:
            text = text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            pass
    return text


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def system_language() -> str:
    """按系统界面语言给出默认界面语言：中文系统 → zh，其它 → en。"""
    if os.name == "nt":
        try:
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if (langid & 0x3FF) == 0x04:
                return "zh"
        except Exception:
            pass
    return "en"


def pick(value: Any, lang: str, fallback: str = "") -> str:
    """从「字符串」或「{zh: ..., en: ...}」里取指定语言的文本。"""
    if value is None:
        return fallback
    if isinstance(value, dict):
        for key in (lang, "zh", "en"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        for item in value.values():
            if isinstance(item, str) and item.strip():
                return item
        return fallback
    text = str(value)
    return text if text.strip() else fallback

# ----------------------------------------------------------------------
# Windows 进程身份识别：判断某个 PID 是否仍是「我们登记过的那一个进程」
# ----------------------------------------------------------------------
if os.name == "nt":
    import ctypes.wintypes as wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PATH_BUFFER = 32768

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
else:
    _kernel32 = None


def _open_process(pid: int) -> Any:
    """以「只查询」权限打开进程；进程不存在或权限不足时返回 None。"""
    if _kernel32 is None or pid <= 0:
        return None
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    return handle or None


def _close(handle: Any) -> None:
    try:
        _kernel32.CloseHandle(handle)
    except Exception:
        pass


def process_creation_time(pid: int) -> int | None:
    """进程创建时间（100ns 单位的时间戳）。进程不存在时返回 None。

    这是判断「PID 是否被系统复用」的关键：同一个 PID 在不同时刻创建，
    创建时间必然不同，因此可以据此确认某个 PID 还是不是我们当初拉起的进程。
    """
    handle = _open_process(pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
        if not _kernel32.GetProcessTimes(handle, created, exited, kernel, user):
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    except Exception:
        return None
    finally:
        _close(handle)


def process_image_path(pid: int) -> str | None:
    """进程的可执行文件完整路径。取不到时返回 None。"""
    handle = _open_process(pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(_PATH_BUFFER)
        size = wintypes.DWORD(_PATH_BUFFER)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value or None
    except Exception:
        return None
    finally:
        _close(handle)


def is_registered_process(pid: int, creation: Any, image: Any) -> bool:
    """判断 pid 是否仍是我们登记过的那个进程。

    以创建时间为准（PID 复用必然导致创建时间不同）；创建时间缺失时才退回
    用可执行文件路径比对。任何不确定的情况都返回 False —— 宁可不动进程，
    也不能误杀其它项目的进程。
    """
    if not pid or pid <= 0:
        return False
    current = process_creation_time(pid)
    if current is None:
        return False
    if creation not in (None, ""):
        try:
            return int(creation) == int(current)
        except (TypeError, ValueError):
            return False
    if image:
        current_image = process_image_path(pid) or ""
        return current_image.lower() == str(image).lower()
    return False


def terminate_tree(pid: int) -> bool:
    """结束指定进程及其子进程。只应传入本启动器登记过的 PID。"""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            return False
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            timeout=20,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ----------------------------------------------------------------------
# 网络探测
# ----------------------------------------------------------------------
def tcp_open(host: str, port: int, timeout: float = 0.3) -> bool:
    """端口上是否有程序在监听。"""
    if not port or port <= 0:
        return False
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for family, socktype, proto, _canon, sockaddr in infos:
        try:
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(timeout)
                if sock.connect_ex(sockaddr) == 0:
                    return True
        except OSError:
            continue
    return False


def http_probe(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    """请求一次页面，返回 (是否可达, 描述)。"""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "bili-launcher-probe"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - 探测失败不影响主流程
        return False, type(exc).__name__


# ----------------------------------------------------------------------
# 配置模型
# ----------------------------------------------------------------------
@dataclass
class Page:
    """一个页面：域名 + 路径 → 人可读的页面名称。"""

    path: str
    short: Any
    title: Any


@dataclass
class Service:
    """一个可启动的服务（配置驱动，与实现语言无关）。"""

    id: str
    enabled: bool
    name: Any
    note: Any
    host: str
    port: int
    cwd: Path
    command: list[str]
    env: dict[str, str]
    open_browser: bool
    no_browser_args: list[str]
    require_exists: list[Path]
    pages: list[Page] = field(default_factory=list)

    def label(self, lang: str) -> str:
        return pick(self.name, lang, self.id)

    def note_text(self, lang: str) -> str:
        return pick(self.note, lang, "")

    def page_label(self, lang: str) -> str:
        return " · ".join(pick(page.short, lang, page.path) for page in self.pages)

    def base_url(self, port: int) -> str:
        return f"http://{self.host}:{port}"

    def url(self, page_path: str, port: int) -> str:
        suffix = page_path if page_path.startswith("/") else "/" + page_path
        return f"http://{self.host}:{port}{suffix}"

    def missing(self) -> list[Path]:
        return [item for item in self.require_exists if not item.exists()]


def app_dir() -> Path:
    """启动器自身所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_config_path(explicit: str | None = None) -> Path:
    """定位 launcher.config.json：命令行 > 环境变量 > 自身目录向上查找 > 当前目录。"""
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"{CONFIG_NAME} not found: {candidate}")

    env_path = os.getenv(CONFIG_ENV_VAR)
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.is_file():
            return candidate

    candidates: list[Path] = []
    directory = app_dir()
    for _ in range(6):
        candidates.append(directory / CONFIG_NAME)
        parent = directory.parent
        if parent == directory:
            break
        directory = parent
    candidates.append(Path.cwd() / CONFIG_NAME)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{CONFIG_NAME} not found near {app_dir()}")


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    return data


def project_root(data: dict[str, Any], config_path: Path) -> Path:
    raw = (data.get("project") or {}).get("root") or "."
    return (config_path.parent / str(raw)).resolve()


def _resolve_against(value: str, base: Path) -> Path:
    text = str(value).replace("\\", "/")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = base / text
    return candidate.resolve()


def build_services(data: dict[str, Any], root: Path) -> list[Service]:
    """把配置里的 services 展开成 Service 列表。"""
    defaults = data.get("defaults") or {}
    host_default = str(defaults.get("host") or "127.0.0.1")
    default_open_browser = bool(defaults.get("open_browser", True))

    services: list[Service] = []
    for raw in data.get("services") or []:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("id") or "").strip()
        if not sid:
            continue

        pages: list[Page] = []
        for page in raw.get("pages") or []:
            if isinstance(page, str):
                pages.append(Page(path=page, short=page, title=page))
            elif isinstance(page, dict):
                path_text = str(page.get("path") or "/")
                pages.append(
                    Page(
                        path=path_text,
                        short=page.get("short") or page.get("title") or path_text,
                        title=page.get("title") or page.get("short") or path_text,
                    )
                )

        services.append(
            Service(
                id=sid,
                enabled=bool(raw.get("enabled", True)),
                name=raw.get("name") or sid,
                note=raw.get("note") or "",
                host=str(raw.get("host") or host_default),
                port=int(raw.get("port") or 0),
                cwd=_resolve_against(str(raw.get("cwd") or "."), root),
                command=[str(item) for item in (raw.get("command") or [])],
                env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
                open_browser=bool(raw.get("open_browser", default_open_browser)),
                no_browser_args=[str(item) for item in (raw.get("no_browser_args") or [])],
                require_exists=[
                    _resolve_against(str(item), root) for item in (raw.get("require_exists") or [])
                ],
                pages=pages,
            )
        )
    return services


def _resolve_executable(token: str, root: Path, cwd: Path) -> Path | str:
    """把命令里的可执行文件解析成绝对路径；只写名字的（如 python）交给 PATH。"""
    if not token:
        return token
    if "/" not in token and "\\" not in token:
        return token
    candidate = Path(token)
    if candidate.is_absolute():
        return candidate
    for base in (cwd, root):
        probe = (base / token).resolve()
        if probe.exists():
            return probe
    return (root / token).resolve()


def resolve_command(service: Service, root: Path, port: int, no_browser: bool = False) -> list[str]:
    """展开命令模板（{root} / {port} / {host} / {python}），并按需追加无浏览器参数。"""
    replacements = {
        "root": str(root),
        "port": str(port),
        "host": service.host,
        "python": sys.executable or "python",
        "cwd": str(service.cwd),
    }
    command: list[str] = []
    for index, token in enumerate(service.command):
        for key, value in replacements.items():
            token = token.replace("{" + key + "}", value)
        if index == 0:
            resolved = _resolve_executable(token, root, service.cwd)
            token = str(resolved)
        command.append(token)
    if no_browser and service.no_browser_args:
        command.extend(service.no_browser_args)
    return command

# ----------------------------------------------------------------------
# 状态存储：本启动器登记过的进程凭据（只控制本项目的关键）
# ----------------------------------------------------------------------
class StateStore:
    """读写 .launcher/state.json：记录语言偏好、上次端口、以及我们拉起的进程。"""

    def __init__(self, root: Path) -> None:
        self.dir = root / STATE_DIR_NAME
        self.path = self.dir / STATE_FILE_NAME
        self.log_dir = self.dir / LOG_DIR_NAME
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {"version": 1, "language": "", "services": {}, "last_port": {}}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                self.data.update(raw)
        except (OSError, ValueError):
            pass
        if not isinstance(self.data.get("services"), dict):
            self.data["services"] = {}
        if not isinstance(self.data.get("last_port"), dict):
            self.data["last_port"] = {}

    def save(self) -> None:
        with self.lock:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_name(STATE_FILE_NAME + ".tmp")
                temp.write_text(
                    json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(temp, self.path)
            except OSError:
                pass

    def language(self) -> str:
        value = str(self.data.get("language") or "")
        return value if value in TEXT else ""

    def set_language(self, lang: str) -> None:
        self.data["language"] = lang
        self.save()

    def entry(self, sid: str) -> dict[str, Any] | None:
        item = (self.data.get("services") or {}).get(sid)
        return item if isinstance(item, dict) else None

    def set_entry(self, sid: str, entry: dict[str, Any]) -> None:
        self.data.setdefault("services", {})[sid] = entry
        self.save()

    def clear_entry(self, sid: str) -> None:
        (self.data.get("services") or {}).pop(sid, None)
        self.save()

    def last_port(self, sid: str) -> int:
        try:
            return int((self.data.get("last_port") or {}).get(sid) or 0)
        except (TypeError, ValueError):
            return 0

    def set_last_port(self, sid: str, port: int) -> None:
        self.data.setdefault("last_port", {})[sid] = int(port)
        self.save()


@dataclass
class Snapshot:
    """某一时刻某个服务的状态快照。"""

    service: Service
    state: str
    pid: int | None
    port: int
    port_is_ours: bool
    foreign_port: int | None
    detail: str = ""

    @property
    def running(self) -> bool:
        return self.state in RUNNING_STATES


class Controller:
    """服务的启动 / 停止 / 状态查询。

    所有写操作都围绕 .launcher/state.json 中登记的 PID 展开，并且每次操作前
    都会用 is_registered_process() 复核身份，因此不会波及本启动器之外的进程。
    """

    def __init__(
        self,
        services: list[Service],
        root: Path,
        state: StateStore,
        port_scan_limit: int = 50,
        stop_timeout: float = 25.0,
    ) -> None:
        self.services = services
        self.root = root
        self.state = state
        self.port_scan_limit = max(1, int(port_scan_limit))
        self.stop_timeout = max(3.0, float(stop_timeout))

    # ---------- 辅助 ----------
    def log_path(self, sid: str) -> Path:
        self.state.log_dir.mkdir(parents=True, exist_ok=True)
        return self.state.log_dir / f"{sid}.log"

    def describe_command(self, service: Service, port: int) -> str:
        """展示将要执行的完整命令行（只做占位符替换，不启动任何进程）。"""
        return " ".join(resolve_command(service, self.root, port))

    def read_log_tail(self, sid: str, lines: int = 20) -> str:
        try:
            text = self.log_path(sid).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.strip().splitlines()[-lines:])

    def display_port(self, service: Service) -> int:
        """未运行时要显示的端口：优先上次用过的，其次配置里的首选端口。"""
        return self.state.last_port(service.id) or service.port

    def reserved_ports(self, exclude_id: str) -> set[int]:
        """已被本启动器其它服务占用的端口（只认登记在案、且身份复核通过的进程）。"""
        used: set[int] = set()
        for service in self.services:
            if service.id == exclude_id:
                continue
            entry = self.state.entry(service.id)
            if not entry:
                continue
            if is_registered_process(
                int(entry.get("pid") or 0), entry.get("creation"), entry.get("image")
            ):
                port = int(entry.get("port") or 0)
                if port:
                    used.add(port)
        return used

    def allocate_port(self, service: Service) -> int:
        """从首选端口开始找一个空闲端口；被占用的端口一律跳过，绝不抢占。"""
        start_port = service.port or 8501
        reserved = self.reserved_ports(service.id)
        for candidate in range(start_port, start_port + self.port_scan_limit):
            if candidate in reserved:
                continue
            if not tcp_open(service.host, candidate, 0.2):
                return candidate
        return start_port

    def service_by_id(self, sid: str) -> Service | None:
        for service in self.services:
            if service.id == sid:
                return service
        return None

    # ---------- 状态 ----------
    def status(self, service: Service) -> Snapshot:
        if not service.enabled:
            return Snapshot(service, "disabled", None, self.display_port(service), False, None)

        missing = service.missing()
        if missing:
            return Snapshot(service, "missing", None, self.display_port(service), False, None, str(missing[0]))

        entry = self.state.entry(service.id)
        if entry:
            pid = int(entry.get("pid") or 0)
            if is_registered_process(pid, entry.get("creation"), entry.get("image")):
                port = int(entry.get("port") or 0)
                if port and tcp_open(service.host, port, 0.35):
                    return Snapshot(service, "running", pid, port, True, None)
                started = str(entry.get("started_at") or "")
                elapsed = _seconds_since(started)
                reason = "starting" if elapsed < 90 else "noport"
                return Snapshot(service, reason, pid, port, True, None, started)
            self.state.clear_entry(service.id)

        preferred = service.port
        foreign = preferred if (preferred and tcp_open(service.host, preferred, 0.25)) else None
        # 停着的时候直接预告将会用到哪个端口，与 start() 的选端口逻辑完全一致
        return Snapshot(service, "stopped", None, self.allocate_port(service), False, foreign)

    def all_status(self) -> dict[str, Snapshot]:
        return {service.id: self.status(service) for service in self.services}

    # ---------- 启动 ----------
    def _build_env(self, service: Service) -> dict[str, str]:
        env = dict(os.environ)
        # 统一 UTF-8，避免子进程在不同系统代码页下出现乱码
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        for key, value in service.env.items():
            env[key] = value.replace("{root}", str(self.root))
        return env

    def start(self, service: Service, no_browser: bool = False) -> tuple[bool, str]:
        """启动服务。返回 (是否成功, 端口或失败原因)。"""
        snapshot = self.status(service)
        if snapshot.state == "disabled":
            return False, "disabled"
        if snapshot.state == "missing":
            return False, "missing"
        if snapshot.running:
            return False, "already"

        port = self.allocate_port(service)
        command = resolve_command(service, self.root, port, no_browser)
        if not command:
            return False, "nocommand"

        log_path = self.log_path(service.id)
        try:
            handle = open(log_path, "ab", buffering=0)
        except OSError:
            handle = None
        try:
            process = subprocess.Popen(
                command,
                cwd=str(service.cwd),
                env=self._build_env(service),
                stdin=subprocess.DEVNULL,
                stdout=handle if handle else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if handle else subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                close_fds=True,
            )
        except OSError as exc:
            return False, f"{type(exc).__name__}: {exc}"
        finally:
            if handle is not None:
                handle.close()

        entry = {
            "pid": process.pid,
            "creation": process_creation_time(process.pid),
            "image": process_image_path(process.pid) or "",
            "port": port,
            "cwd": str(service.cwd),
            "command": command,
            "started_at": now_str(),
        }
        self.state.set_entry(service.id, entry)
        self.state.set_last_port(service.id, port)

        # 等一小会儿：如果进程立刻退出，说明启动失败，直接把原因带回去
        deadline = time.time() + 2.5
        while time.time() < deadline:
            if process.poll() is not None:
                self.state.clear_entry(service.id)
                return False, f"exit({process.returncode})"
            if tcp_open(service.host, port, 0.2):
                break
            time.sleep(0.15)
        return True, str(port)

    # ---------- 停止 ----------
    def stop(self, service: Service) -> tuple[bool, str]:
        """停止服务。只会结束登记在案、且身份复核通过的进程。"""
        entry = self.state.entry(service.id)
        if not entry:
            return False, "notrunning"

        pid = int(entry.get("pid") or 0)
        if not is_registered_process(pid, entry.get("creation"), entry.get("image")):
            # 登记已失效（进程早就结束了、或 PID 已被复用）→ 只清记录，绝不发信号
            self.state.clear_entry(service.id)
            return False, "stale"

        terminate_tree(pid)
        deadline = time.time() + self.stop_timeout
        while time.time() < deadline:
            if process_creation_time(pid) is None:
                break
            time.sleep(0.25)

        gone = process_creation_time(pid) is None
        if gone:
            self.state.clear_entry(service.id)
        return gone, "stopped" if gone else "timeout"

    def restart(self, service: Service, no_browser: bool = False) -> tuple[bool, str]:
        if self.state.entry(service.id):
            self.stop(service)
            time.sleep(0.4)
        return self.start(service, no_browser)

    # ---------- 打开页面 ----------
    def open_page(self, service: Service, page_index: int = 0) -> str:
        snapshot = self.status(service)
        port = snapshot.port or self.display_port(service)
        pages = service.pages or [Page(path="/", short="/", title="/")]
        index = max(0, min(page_index, len(pages) - 1))
        url = service.url(pages[index].path, port)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 打不开浏览器不影响状态显示
            pass
        return url


def _seconds_since(timestamp: str) -> float:
    """距离 timestamp（%Y-%m-%d %H:%M:%S）过去了多少秒；解析失败返回 0。"""
    try:
        moment = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (datetime.now() - moment).total_seconds())

# ----------------------------------------------------------------------
# 运行上下文
# ----------------------------------------------------------------------
def state_label(lang: str, state: str) -> str:
    return tr(lang, f"state_{state}")


def mysql_target(root: Path) -> tuple[str, int] | None:
    """从项目 .env 里读出 MySQL 地址（不依赖任何第三方库）；没配置返回 None。"""
    host, port, found = "localhost", 3306, False
    try:
        text = (root / ".env").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().upper()
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        if key == "MYSQL_HOST":
            host, found = value, True
        elif key == "MYSQL_PORT":
            try:
                port, found = int(value), True
            except ValueError:
                pass
    return (host, port) if found else None


class Context:
    """一份配置对应一个上下文：项目根目录、服务列表、状态存储与控制器。"""

    def __init__(self, config_explicit: str | None = None) -> None:
        self.config_path = find_config_path(config_explicit)
        data = load_config(self.config_path)
        self.data = data
        self.root = project_root(data, self.config_path)
        self.services = build_services(data, self.root)
        if not self.services:
            raise ValueError(f"no services defined in {self.config_path}")
        defaults = data.get("defaults") or {}
        self.state = StateStore(self.root)
        self.controller = Controller(
            self.services,
            self.root,
            self.state,
            port_scan_limit=int(defaults.get("port_scan_limit") or 50),
            stop_timeout=float(defaults.get("stop_timeout_sec") or 25),
        )
        self.project_name = (data.get("project") or {}).get("name")


def _describe_port(ctx: Context, snapshot: Snapshot, lang: str) -> str:
    if snapshot.running:
        return str(snapshot.port)
    if snapshot.foreign_port:
        return tr(lang, "f_port_foreign", port=snapshot.foreign_port)
    return tr(lang, "f_port_free")


# ----------------------------------------------------------------------
# 命令行模式（便于脚本化 / 自检；不带参数时进入图形界面）
# ----------------------------------------------------------------------
def _failure_reason(lang: str, service: Service, info: str) -> str:
    """把控制器返回的原因码翻译成界面文案。"""
    if info == "disabled":
        return tr(lang, "disabled_service")
    if info == "missing":
        missing = service.missing()
        return tr(lang, "missing_files", path=str(missing[0]) if missing else "")
    if info == "nocommand":
        return tr(lang, "no_command")
    if info == "already":
        return tr(lang, "already_running", name=service.label(lang))
    return info


def _resolve_targets(ctx: Context, name: str) -> list[Service]:
    if name == "all":
        return [service for service in ctx.services if service.enabled]
    service = ctx.controller.service_by_id(name)
    if service is None:
        known = ", ".join(service.id for service in ctx.services)
        raise SystemExit(f"[error] unknown service '{name}' (known: {known})")
    return [service]


def cmd_check(ctx: Context, lang: str) -> int:
    print(f"{APP_TITLE} {APP_VERSION}  [{tr(lang, 'app_title')}]")
    print(f"{'config':<7}: {ctx.config_path}")
    print(f"{'root':<7}: {ctx.root}")
    print(f"{'state':<7}: {ctx.state.path}")
    print("")
    for service in ctx.services:
        snapshot = ctx.controller.status(service)
        print(f"* {service.id}  [{state_label(lang, snapshot.state)}]")
        print(f"    {tr(lang, 'f_service'):<6}: {service.label(lang)}")
        print(f"    {tr(lang, 'f_domain'):<6}: {service.base_url(snapshot.port)}")
        print(f"    {tr(lang, 'col_port'):<6}: {snapshot.port}  ({_describe_port(ctx, snapshot, lang)})")
        note = service.note_text(lang)
        if note:
            print(f"    {tr(lang, 'f_note'):<6}: {note}")
        for page in service.pages:
            print(
                f"    - {service.url(page.path, snapshot.port).ljust(42)}"
                f" -> {pick(page.title, lang, page.path)}"
            )
        print("")
    target = mysql_target(ctx.root)
    if target is None:
        print(tr(lang, "db_none"))
    else:
        reachable = tcp_open(target[0], target[1], 0.4)
        print(
            tr(lang, "db_ok" if reachable else "db_down", host=target[0], port=target[1])
        )
    return 0


def cmd_status(ctx: Context, lang: str) -> int:
    print(f"{tr(lang, 'col_service')} | {tr(lang, 'col_status')} | "
          f"{tr(lang, 'col_address')} | {tr(lang, 'col_pages')} | {tr(lang, 'col_port')}")
    for service in ctx.services:
        snapshot = ctx.controller.status(service)
        print(
            f"{service.id} | {state_label(lang, snapshot.state)} | "
            f"{service.base_url(snapshot.port)} | {service.page_label(lang)} | {snapshot.port}"
        )
    return 0


def run_cli(args: argparse.Namespace) -> int:
    try:
        ctx = Context(args.config)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"[error] {exc}")
        return 2

    lang = args.lang or ctx.state.language() or system_language()

    if args.check:
        return cmd_check(ctx, lang)
    if args.status:
        return cmd_status(ctx, lang)

    if args.start:
        exit_code = 0
        for service in _resolve_targets(ctx, args.start):
            ok, info = ctx.controller.start(service, no_browser=args.no_browser)
            if ok:
                print(tr(lang, "started_ok", name=service.label(lang),
                         url=service.base_url(int(info))))
            elif info == "already":
                print(tr(lang, "already_running", name=service.label(lang)))
            else:
                exit_code = 1
                print(tr(lang, "start_failed", reason=_failure_reason(lang, service, info),
                         log=ctx.controller.log_path(service.id)))
        return exit_code

    if args.stop:
        exit_code = 0
        for service in _resolve_targets(ctx, args.stop):
            ok, info = ctx.controller.stop(service)
            if ok:
                print(tr(lang, "stopped_ok", name=service.label(lang)))
            else:
                exit_code = 1
                if info == "stale":
                    print(tr(lang, "stop_stale"))
                else:
                    print(tr(lang, "not_running", name=service.label(lang)))
        return exit_code

    if args.restart:
        exit_code = 0
        for service in _resolve_targets(ctx, args.restart):
            ok, info = ctx.controller.restart(service, no_browser=args.no_browser)
            if ok:
                print(tr(lang, "started_ok", name=service.label(lang),
                         url=service.base_url(int(info))))
            else:
                exit_code = 1
                print(tr(lang, "start_failed", reason=info,
                         log=ctx.controller.log_path(service.id)))
        return exit_code

    if args.open_id:
        exit_code = 0
        for service in _resolve_targets(ctx, args.open_id):
            url = ctx.controller.open_page(service, max(0, int(args.page or 0)))
            print(url)
        return exit_code

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="launcher.py",
        description=f"{APP_TITLE} {APP_VERSION} - start/stop this project's services only",
    )
    parser.add_argument("--config", help="path to launcher.config.json")
    parser.add_argument("--lang", choices=["zh", "en"], help="UI/CLI language")
    parser.add_argument("--check", action="store_true", help="print the domain -> page mapping")
    parser.add_argument("--status", action="store_true", help="print a one-line status table")
    parser.add_argument("--start", metavar="ID", help="start a service id, or 'all'")
    parser.add_argument("--stop", metavar="ID", help="stop a service id, or 'all'")
    parser.add_argument("--restart", metavar="ID", help="restart a service id, or 'all'")
    parser.add_argument("--open", dest="open_id", metavar="ID", help="open a page in the browser")
    parser.add_argument("--page", type=int, default=0, help="page index used by --open")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not let the child process open a browser")
    parser.add_argument("--version", action="version", version=f"{APP_TITLE} {APP_VERSION}")
    return parser

# ----------------------------------------------------------------------
# 图形界面
# ----------------------------------------------------------------------
try:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, ttk

    TK_AVAILABLE = True
except Exception:  # noqa: BLE001 - 没有 tkinter 时仍可使用命令行模式
    TK_AVAILABLE = False


def enable_dpi_awareness() -> None:
    """高分屏下让界面保持清晰（失败也不影响运行）。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class Monitor(threading.Thread):
    """后台探测线程：定时刷新状态，避免在界面线程里做网络探测导致卡顿。"""

    def __init__(
        self,
        controller: Controller,
        services: list[Service],
        out: "queue.Queue[dict[str, Any]]",
        interval: float = 2.0,
    ) -> None:
        super().__init__(daemon=True)
        self.controller = controller
        self.services = services
        self.out = out
        self.interval = interval
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    def wake(self) -> None:
        self._wake_event.set()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                snapshots = {service.id: self.controller.status(service) for service in self.services}
                db_info = None
                target = mysql_target(self.controller.root)
                if target is not None:
                    db_info = (target[0], target[1], tcp_open(target[0], target[1], 0.25))
                payload = {"services": snapshots, "db": db_info}
                try:
                    self.out.get_nowait()
                except queue.Empty:
                    pass
                self.out.put_nowait(payload)
            except Exception:  # noqa: BLE001 - 探测异常不应该影响界面
                pass
            self._wake_event.wait(self.interval)
            self._wake_event.clear()


class LauncherApp:
    """启动器主窗口。"""

    def __init__(self, ctx: Context, lang: str) -> None:
        self.ctx = ctx
        self.controller = ctx.controller
        self.services = ctx.services
        self.lang = lang if lang in TEXT else "en"
        self.selected = self.services[0].id if self.services else ""
        self.snapshots: dict[str, Snapshot] = {}
        self.db_info: tuple[str, int, bool] | None = None
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=4)
        self.action_queue: queue.Queue[list[str]] = queue.Queue()
        self.monitor = Monitor(self.controller, self.services, self.queue, interval=2.0)
        self.log_lines: list[str] = []
        self._busy = False

        self.window = tk.Tk()
        self.window.geometry("1200x760")
        self.window.minsize(1020, 660)
        self.font_family = self._detect_font()
        self._set_icon()
        self._build()
        self._apply_theme()
        self._apply_language()
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)
        self.monitor.start()
        self._append_log(f"{APP_TITLE} {APP_VERSION}  ready")
        self._refresh_now()
        self.window.after(400, self._drain)

    # ---------- 外观 ----------
    def _detect_font(self) -> str:
        try:
            families = set(tkfont.families(self.window))
        except Exception:
            return "TkDefaultFont"
        order = (
            ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial"]
            if self.lang == "zh"
            else ["Segoe UI", "Microsoft YaHei UI", "Arial"]
        )
        for name in order:
            if name in families:
                return name
        return "TkDefaultFont"

    def _set_icon(self) -> None:
        for candidate in (self.ctx.root / "assets" / "icon.ico", app_dir() / "assets" / "icon.ico"):
            if candidate.is_file():
                try:
                    self.window.iconbitmap(str(candidate))
                except Exception:
                    pass
                return

    def _apply_theme(self) -> None:
        style = ttk.Style(self.window)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        family = self.font_family
        style.configure("Treeview", rowheight=30, font=(family, 10))
        style.configure("Treeview.Heading", font=(family, 10, "bold"))
        style.configure("Head.TLabel", font=(family, 13, "bold"))
        style.configure("Sub.TLabel", font=(family, 9), foreground="#5b6470")
        style.configure("Status.TLabel", font=(family, 9), foreground="#40464e")
        style.configure("TButton", font=(family, 9))
        style.configure("TCombobox", font=(family, 9))
        style.configure("TLabelframe.Label", font=(family, 10, "bold"))

    def _build(self) -> None:
        container = ttk.Frame(self.window, padding=12)
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container)
        header.pack(fill="x")
        self.title_label = ttk.Label(header, style="Head.TLabel")
        self.title_label.pack(side="left")
        self.lang_button = ttk.Button(header, width=10, command=self.toggle_language)
        self.lang_button.pack(side="right")

        self.subtitle_label = ttk.Label(container, style="Sub.TLabel")
        self.subtitle_label.pack(fill="x", pady=(2, 10))

        tree_frame = ttk.Frame(container)
        tree_frame.pack(fill="x")
        columns = ("status", "service", "address", "pages", "port")
        widths = {"status": 110, "service": 210, "address": 210, "pages": 320, "port": 80}
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="browse", height=5
        )
        for column in columns:
            self.tree.column(column, width=widths[column], anchor="w", stretch=(column == "pages"))
        self.tree.tag_configure("running", foreground="#0a7f3f")
        self.tree.tag_configure("starting", foreground="#b06a00")
        self.tree.tag_configure("noport", foreground="#b06a00")
        self.tree.tag_configure("stopped", foreground="#6b7280")
        self.tree.tag_configure("disabled", foreground="#9aa0a6")
        self.tree.tag_configure("missing", foreground="#c01f1f")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _event: self.action_open())
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="x", expand=True)
        scrollbar.pack(side="right", fill="y")

        buttons = ttk.Frame(container)
        buttons.pack(fill="x", pady=(10, 8))
        self.buttons: dict[str, Any] = {}
        for key, command in (
            ("start", self.action_start),
            ("stop", self.action_stop),
            ("restart", self.action_restart),
            ("start_all", self.action_start_all),
            ("stop_all", self.action_stop_all),
            ("refresh", self.action_refresh),
            ("exit", self.on_close),
        ):
            button = ttk.Button(buttons, width=12, command=command)
            button.pack(side="left", padx=(0, 6))
            self.buttons[key] = button

        self.details_group = ttk.LabelFrame(container, padding=10)
        self.details_group.pack(fill="x", pady=(0, 8))
        picker = ttk.Frame(self.details_group)
        picker.pack(fill="x", pady=(0, 8))
        self.page_combo = ttk.Combobox(picker, state="readonly", width=70)
        self.page_combo.pack(side="left", fill="x", expand=True)
        self.page_button = ttk.Button(picker, width=14, command=self.action_open_selected_page)
        self.page_button.pack(side="left", padx=(8, 0))
        self.details_text = tk.Text(
            self.details_group, height=9, wrap="none", relief="flat",
            background="#f7f8fa", font=(self.font_family, 9),
        )
        self.details_text.configure(state="disabled")
        self.details_text.pack(fill="x")

        self.log_group = ttk.LabelFrame(container, padding=10)
        self.log_group.pack(fill="both", expand=True, pady=(0, 8))
        self.log_text = tk.Text(
            self.log_group, height=5, wrap="none", relief="flat",
            background="#f7f8fa", font=(self.font_family, 9),
        )
        self.log_text.configure(state="disabled")
        self.log_text.pack(fill="both", expand=True)

        self.status_label = ttk.Label(container, style="Status.TLabel")
        self.status_label.pack(fill="x")

    def _apply_language(self) -> None:
        lang = self.lang
        project = pick(self.ctx.project_name, lang, "bili-analyzer")
        title = f"{project} · {tr(lang, 'app_title')}"
        self.window.title(title)
        self.title_label.configure(text=title)
        self.subtitle_label.configure(text=tr(lang, "subtitle"))
        self.lang_button.configure(text=tr(lang, "lang_button"))
        for column in ("status", "service", "address", "pages", "port"):
            self.tree.heading(column, text=tr(lang, f"col_{column}"))
        for key, button in self.buttons.items():
            button.configure(text=tr(lang, f"btn_{key}"))
        self.page_button.configure(text=tr(lang, "btn_open"))
        self.details_group.configure(text=tr(lang, "grp_details"))
        self.log_group.configure(text=tr(lang, "grp_log"))
    # ---------- 渲染 ----------
    @staticmethod
    def _set_text(widget: Any, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _append_log(self, message: str) -> None:
        if not message:
            return
        self.log_lines.append(f"[{now_str()}] {message}")
        del self.log_lines[:-400]
        self._set_text(self.log_text, "\n".join(self.log_lines))
        try:
            self.log_text.see("end")
        except Exception:
            pass

    def _set_status(self, message: str) -> None:
        self.status_message = message
        self._render_status()

    def _db_label(self) -> str:
        lang = self.lang
        if self.db_info is None:
            return tr(lang, "db_none")
        host, port, reachable = self.db_info
        return tr(lang, "db_ok" if reachable else "db_down", host=host, port=port)

    def _render_status(self) -> None:
        parts = [getattr(self, "status_message", "") or tr(self.lang, "subtitle")]
        parts.append(f"{tr(self.lang, 'db_title')}: {self._db_label()}")
        parts.append(tr(self.lang, "updated", time=datetime.now().strftime("%H:%M:%S")))
        self.status_label.configure(text="    |    ".join(parts))

    def _render(self) -> None:
        selected = self.selected
        for item in self.tree.get_children():
            self.tree.delete(item)
        for service in self.services:
            snapshot = self.snapshots.get(service.id) or self.controller.status(service)
            port_text = str(snapshot.port) if snapshot.port else tr(self.lang, "port_auto")
            self.tree.insert(
                "",
                "end",
                iid=service.id,
                values=(
                    state_label(self.lang, snapshot.state),
                    service.label(self.lang),
                    service.base_url(snapshot.port),
                    service.page_label(self.lang),
                    port_text,
                ),
                tags=(snapshot.state,),
            )
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
        elif self.services:
            self.selected = self.services[0].id
            self.tree.selection_set(self.selected)
        self._render_details()
        self._render_status()

    def _render_details(self) -> None:
        lang = self.lang
        service = self.controller.service_by_id(self.selected)
        if service is None:
            self._set_text(self.details_text, tr(lang, "detail_empty"))
            self.page_combo.configure(values=[])
            self.page_combo.set("")
            return

        snapshot = self.snapshots.get(service.id) or self.controller.status(service)
        lines = [f"{tr(lang, 'f_service')}: {service.label(lang)}"]
        status_text = state_label(lang, snapshot.state)
        if snapshot.pid:
            status_text += f"  ({tr(lang, 'f_pid')} {snapshot.pid})"
        lines.append(f"{tr(lang, 'f_status')}: {status_text}")
        lines.append(f"{tr(lang, 'f_domain')}: {service.base_url(snapshot.port)}")
        lines.append(f"{tr(lang, 'f_pages')}:")
        for page in service.pages:
            lines.append(
                f"    - {service.url(page.path, snapshot.port)}"
                f"  ->  {pick(page.title, lang, page.path)}"
            )
        note = service.note_text(lang)
        if note:
            lines.append(f"{tr(lang, 'f_note')}: {note}")
        if snapshot.state == "missing" and snapshot.detail:
            lines.append(tr(lang, "missing_files", path=snapshot.detail))
        if snapshot.foreign_port:
            lines.append(tr(lang, "foreign_hint", port=snapshot.foreign_port))
        lines.append(
            f"{tr(lang, 'f_command')}: "
            f"{self.controller.describe_command(service, snapshot.port)}"
        )
        lines.append(f"{tr(lang, 'f_log')}: {self.controller.log_path(service.id)}")
        self._set_text(self.details_text, "\n".join(lines))

        labels = [
            f"{service.url(page.path, snapshot.port)}  ->  {pick(page.short, lang, page.path)}"
            for page in service.pages
        ]
        index = self.page_combo.current()
        self.page_combo.configure(values=labels)
        if labels:
            self.page_combo.current(index if 0 <= index < len(labels) else 0)

    def _on_select(self, _event: Any = None) -> None:
        selection = self.tree.selection()
        if selection:
            self.selected = selection[0]
            self._render_details()

    def _refresh_now(self) -> None:
        try:
            self.snapshots = self.controller.all_status()
        except Exception:  # noqa: BLE001
            pass
        self._render()

    def _drain(self) -> None:
        try:
            while True:
                payload = self.queue.get_nowait()
                self.snapshots = payload.get("services") or {}
                self.db_info = payload.get("db")
                self._render()
        except queue.Empty:
            pass
        try:
            while True:
                self._finish_async(self.action_queue.get_nowait())
        except queue.Empty:
            pass
        self.window.after(400, self._drain)

    # ---------- 异步动作 ----------
    def _run_async(self, work: Any) -> None:
        if self._busy:
            return
        self._busy = True
        self.window.configure(cursor="watch")
        for button in self.buttons.values():
            button.configure(state="disabled")

        def runner() -> None:
            messages: list[str] = []
            try:
                work(messages)
            except Exception as exc:  # noqa: BLE001
                messages.append(f"[error] {type(exc).__name__}: {exc}")
            self.action_queue.put(messages)

        threading.Thread(target=runner, daemon=True).start()

    def _finish_async(self, messages: list[str]) -> None:
        self._busy = False
        self.window.configure(cursor="")
        for button in self.buttons.values():
            button.configure(state="normal")
        for message in messages:
            self._append_log(message)
        if messages:
            self._set_status(messages[-1])
        self.monitor.wake()
        self._refresh_now()

    def _reason_text(self, info: str, service: Service) -> str:
        return _failure_reason(self.lang, service, info)

    def _selected_service(self) -> Service | None:
        return self.controller.service_by_id(self.selected)

    # ---------- 动作 ----------
    def action_start(self) -> None:
        service = self._selected_service()
        if service is None:
            self._set_status(tr(self.lang, "need_select"))
            return
        self._start_service(service)

    def _start_service(self, service: Service) -> None:
        self._set_status(f"{tr(self.lang, 'btn_start')} ... {service.label(self.lang)}")
        self._run_async(lambda messages: self._start_worker(service, messages))

    def _start_worker(self, service: Service, messages: list[str]) -> None:
        lang = self.lang
        ok, info = self.controller.start(service)
        if ok:
            messages.append(
                tr(lang, "started_ok", name=service.label(lang), url=service.base_url(int(info)))
            )
            return
        if info == "already":
            messages.append(tr(lang, "already_running", name=service.label(lang)))
            return
        messages.append(
            tr(lang, "start_failed", reason=self._reason_text(info, service),
               log=self.controller.log_path(service.id))
        )
        tail = self.controller.read_log_tail(service.id, 6)
        if tail:
            messages.append(tail)

    def action_stop(self) -> None:
        service = self._selected_service()
        if service is None:
            self._set_status(tr(self.lang, "need_select"))
            return
        self._stop_service(service)

    def _stop_service(self, service: Service) -> None:
        self._set_status(f"{tr(self.lang, 'btn_stop')} ... {service.label(self.lang)}")
        self._run_async(lambda messages: self._stop_worker(service, messages))

    def _stop_worker(self, service: Service, messages: list[str]) -> None:
        ok, info = self.controller.stop(service)
        lang = self.lang
        if ok:
            messages.append(tr(lang, "stopped_ok", name=service.label(lang)))
        elif info == "stale":
            messages.append(tr(lang, "stop_stale"))
        else:
            messages.append(tr(lang, "not_running", name=service.label(lang)))

    def action_restart(self) -> None:
        service = self._selected_service()
        if service is None:
            self._set_status(tr(self.lang, "need_select"))
            return
        self._set_status(f"{tr(self.lang, 'btn_restart')} ... {service.label(self.lang)}")

        def work(messages: list[str]) -> None:
            self._stop_worker(service, messages)
            ok, info = self.controller.restart(service)
            if ok:
                messages.append(
                    tr(self.lang, "started_ok", name=service.label(self.lang),
                       url=service.base_url(int(info)))
                )
            else:
                messages.append(
                    tr(self.lang, "start_failed", reason=self._reason_text(info, service),
                       log=self.controller.log_path(service.id))
                )

        self._run_async(work)

    def action_start_all(self) -> None:
        services = [service for service in self.services if service.enabled]
        if not services:
            return

        def work(messages: list[str]) -> None:
            for service in services:
                self._start_worker(service, messages)

        self._run_async(work)

    def action_stop_all(self) -> None:
        services = [service for service in self.services if service.enabled]
        if not services:
            return

        def work(messages: list[str]) -> None:
            for service in services:
                self._stop_worker(service, messages)

        self._run_async(work)

    def action_refresh(self) -> None:
        self.monitor.wake()
        self._refresh_now()
        self._set_status(tr(self.lang, "btn_refresh"))

    def action_open(self) -> None:
        service = self._selected_service()
        if service is None:
            self._set_status(tr(self.lang, "need_select"))
            return
        snapshot = self.snapshots.get(service.id) or self.controller.status(service)
        if not snapshot.running:
            self._append_log(tr(self.lang, "not_running", name=service.label(self.lang)))
        url = self.controller.open_page(service, 0)
        self._set_status(url)

    def action_open_selected_page(self) -> None:
        service = self._selected_service()
        if service is None:
            self._set_status(tr(self.lang, "need_select"))
            return
        index = self.page_combo.current()
        url = self.controller.open_page(service, index if index >= 0 else 0)
        self._set_status(url)

    def toggle_language(self) -> None:
        self.lang = "en" if self.lang == "zh" else "zh"
        self.ctx.state.set_language(self.lang)
        family = self._detect_font()
        if family != self.font_family:
            self.font_family = family
            self._apply_theme()
            self.details_text.configure(font=(family, 9))
            self.log_text.configure(font=(family, 9))
        self._apply_language()
        self._refresh_now()

    def on_close(self) -> None:
        running = [
            service
            for service in self.services
            if (self.snapshots.get(service.id) or self.controller.status(service)).running
        ]
        if running and not self._busy:
            names = "\n".join(
                f"· {service.label(self.lang)}    {service.base_url((self.snapshots.get(service.id) or self.controller.status(service)).port)}"
                for service in running
            )
            answer = messagebox.askyesnocancel(
                tr(self.lang, "exit_title"),
                tr(self.lang, "exit_message", names=names),
                parent=self.window,
            )
            if answer is None:
                return
            if answer:
                for service in running:
                    self.controller.stop(service)
        self.monitor.shutdown()
        try:
            self.ctx.state.set_language(self.lang)
        except Exception:  # noqa: BLE001
            pass
        self.window.destroy()


def harden_standard_streams() -> None:
    """让标准输出在各种系统语言 / 代码页下都不会因为 emoji 之类字符崩溃。

    这是「不受语言限制」的一部分：中文 Windows 的控制台默认是 GBK 代码页，
    直接打印 📊 这类字符会抛 UnicodeEncodeError。这里统一把标准输出改成
    「本地控制台保留本地编码、重定向到文件/管道时用 UTF-8」，并且一律
    errors="replace"，保证任何语言环境下都不会因为打印而崩溃。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            is_console = bool(stream.isatty())
        except Exception:
            is_console = False
        try:
            if is_console:
                reconfigure(errors="replace")
            else:
                reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def attach_parent_console() -> None:
    """打包成窗口程序后，从命令行调用时把标准输出接回父控制台。

    这样 bili-launcher.exe --check 之类的命令在终端里也能看到输出；
    双击启动（父进程没有控制台）时不会有任何副作用。
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return
    except Exception:
        return
    codepage = 0
    try:
        codepage = int(ctypes.windll.kernel32.GetConsoleOutputCP())
    except Exception:
        codepage = 0
    encoding = "utf-8" if codepage == 65001 else "mbcs"
    try:
        stream = open("CONOUT$", "w", encoding=encoding, errors="replace", buffering=1)
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def run_gui(args: argparse.Namespace) -> int:
    if not TK_AVAILABLE:
        print("[error] tkinter is not available in this Python build; use --status/--start/--stop")
        return 3
    try:
        ctx = Context(args.config)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"[error] {exc}")
        return 2

    enable_dpi_awareness()
    lang = args.lang or ctx.state.language() or system_language()
    ctx.state.set_language(lang)
    app = LauncherApp(ctx, lang)
    app.window.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    harden_standard_streams()
    args = build_parser().parse_args(argv)
    if any((args.check, args.status, args.start, args.stop, args.restart, args.open_id)):
        attach_parent_console()
        return run_cli(args)
    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())