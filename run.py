"""打包入口：以 Streamlit CLI 的方式启动 app/ui.py。

开发环境直接运行 `python run.py` 也可以启动同一个页面。
打包后由 build.spec 以本文件作为入口生成 bili-analyzer.exe。
"""

from __future__ import annotations

import atexit
import multiprocessing
import os
import socket
import sys
import traceback
from datetime import datetime
from pathlib import Path


def resource_root() -> Path:
    """返回资源根目录：打包后为 PyInstaller 的临时解包目录，否则为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def work_root() -> Path:
    """返回可写目录：打包后为 exe 所在目录，否则为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def ui_script() -> Path:
    """定位 Streamlit 页面脚本 app/ui.py。"""
    return resource_root() / "app" / "ui.py"


def log_file() -> Path:
    return work_root() / "bili-analyzer-startup.log"


def log_startup(message: str) -> None:
    """把启动过程写入日志文件，便于排查"双击没反应"。"""
    try:
        with open(log_file(), "a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


def port_in_use(port: int) -> bool:
    """判断端口是否已被占用。

    这里用「尝试连接」而不是「尝试 bind」：Windows 上若已有进程监听 IPv6 的
    同一端口，IPv4 的 bind 仍可能成功，会误判为空闲。
    """
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.35)
                if sock.connect_ex((address, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def pick_free_port(preferred: int = 8501, limit: int = 20) -> int:
    """优先使用 8501，被占用时顺延找空闲端口。"""
    for candidate in range(preferred, preferred + limit):
        if not port_in_use(candidate):
            return candidate
    return preferred


def report_error(message: str) -> Path | None:
    """把错误写入日志文件，并（打包环境下）弹窗提示，便于排查启动失败。"""
    log_path: Path | None = work_root() / "bili-analyzer-error.log"
    try:
        log_path.write_text(message, encoding="utf-8")
    except OSError:
        log_path = None

    if getattr(sys, "frozen", False):
        detail = message
        if log_path is not None:
            detail += f"\n\n详细日志：{log_path}"
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, detail, "bili-analyzer 启动失败", 0x10)
        except Exception:  # noqa: BLE001 - 弹窗失败不影响日志
            pass
    else:
        print(message)
    return log_path


def main() -> int:
    """设置环境变量并通过 streamlit.web.cli 启动服务。"""
    # 批处理/双击启动时，用环境变量保证行为与开发环境一致
    os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "false")
    # 关键：不设置这一项时，双击启动会弹出「Welcome to Streamlit! Email:」询问，
    # 打包程序没有控制台可输入，会直接静默退出（表现为"双击没反应"）
    os.environ.setdefault("STREAMLIT_SERVER_SHOW_EMAIL_PROMPT", "false")
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    os.environ.setdefault("STREAMLIT_SERVER_FILE_WATCHER_TYPE", "none")
    os.environ.setdefault("STREAMLIT_GLOBAL_DEVELOPMENT_MODE", "false")

    script = ui_script()
    if not script.is_file():
        message = f"未找到页面脚本：{script}"
        if getattr(sys, "frozen", False):
            message += "\n请确认打包时已把 app/ 目录加入 build.spec 的 datas。"
        report_error(message)
        return 1

    from streamlit.web import cli as stcli

    # 用户自定义参数（放在最后，优先级最高）
    # 例如：bili-analyzer.exe --server.port=8600
    user_args = sys.argv[1:]
    user_set_port = any(arg.startswith("--server.port") for arg in user_args)
    port = None if user_set_port else pick_free_port()

    # 这些参数必须显式传给 Streamlit：
    # 1. showEmailPrompt=false —— 否则首次运行会等待输入邮箱而静默退出；
    # 2. 主题相关 —— 打包后工作目录是 exe 所在目录，读不到项目里的 .streamlit/config.toml，
    #    不显式传就会退回 Streamlit 默认配色，界面与开发环境不一致。
    sys.argv = [
        "streamlit",
        "run",
        str(script),
        "--global.developmentMode=false",
        "--server.headless=false",
        "--server.showEmailPrompt=false",
        "--browser.gatherUsageStats=false",
        "--server.fileWatcherType=none",
        "--theme.base=light",
        "--theme.primaryColor=#00A1D6",
        "--theme.backgroundColor=#FFFFFF",
        "--theme.secondaryBackgroundColor=#F6F8FA",
        "--theme.textColor=#1F2329",
        "--theme.font=sans serif",
    ]
    if port is not None:
        sys.argv.append(f"--server.port={port}")
    sys.argv.extend(user_args)

    log_startup(
        f"启动 Streamlit，端口 {port if port else '（由命令行指定）'}，"
        f"页面脚本 {script}，pid {os.getpid()}"
    )
    if port:
        log_startup(f"访问地址：http://localhost:{port}")

    # 正常退出时留一条记录，便于判断"页面打不开"是程序已停止还是别的原因
    atexit.register(lambda: log_startup("程序已退出（浏览器页面此后会显示 Connection error）"))

    exit_code = stcli.main()
    if exit_code not in (0, None):
        report_error(
            f"Streamlit 未能正常启动（退出码 {exit_code}）。\n"
            "常见原因：端口被占用、cookie / 数据库配置问题。\n"
            f"启动日志：{log_file()}"
        )
    else:
        log_startup("Streamlit 已退出（正常结束）")
    return exit_code


def entrypoint() -> int:
    """包裹 main，保证任何异常都能落盘并提示，而不是静默闪退。"""
    try:
        return main()
    except SystemExit as exc:
        code = exc.code
        if code in (0, None):
            raise
        report_error(f"启动失败，退出码：{code}\n启动日志：{log_file()}")
        return 1 if not isinstance(code, int) else code
    except KeyboardInterrupt:
        return 130
    except BaseException:  # noqa: BLE001 - 启动阶段的所有异常都要记录下来
        report_error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(entrypoint())
