# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：bili-analyzer（Streamlit 应用）。

构建方式（在项目根目录执行）::

    .\\.venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean build.spec

或直接双击 build.bat。
产物：dist/bili-analyzer/bili-analyzer.exe
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

PROJECT_ROOT = SPECPATH  # noqa: F821 - 由 PyInstaller 注入
ENTRY_POINT = os.path.join(PROJECT_ROOT, "run.py")
ICON_PATH = os.path.join(PROJECT_ROOT, "assets", "icon.ico")
VERSION_FILE = os.path.join(PROJECT_ROOT, "version_info.txt")

# ----------------------------------------------------------------------
# 数据文件：app/ 与 .streamlit/ 必须一起打包，否则运行时会缺页面或配置
# ----------------------------------------------------------------------
datas = [
    (os.path.join(PROJECT_ROOT, "app"), "app"),
    (os.path.join(PROJECT_ROOT, ".streamlit"), ".streamlit"),
]
binaries = []
hiddenimports = []

# ----------------------------------------------------------------------
# 隐式依赖：这些包要么带数据文件（jieba 词典、snownlp 模型、streamlit 静态资源），
# 要么在运行时动态导入子模块，PyInstaller 静态分析抓不到，必须显式收集
# ----------------------------------------------------------------------
DATA_PACKAGES = (
    "streamlit",   # 前端静态资源、runtime 子模块、包元数据
    "altair",      # streamlit 图表依赖
    "jieba",       # dict.txt 分词词典
    "snownlp",     # 情感分析模型与停用词数据
)

for package in DATA_PACKAGES:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# Streamlit 自带的测试框架打包后用不到，过滤掉以减小体积
hiddenimports = [
    module for module in hiddenimports if not module.startswith("streamlit.testing")
]

# plotly 与 pyarrow 只取运行必需部分：
# 用 collect_all 会把 tests/include/src 等开发文件一起打包，白白多出几十 MB，
# 它们自带的 PyInstaller hook 已经能正确收集二进制与数据文件。
hiddenimports += [
    "plotly",
    "plotly.express",
    "plotly.graph_objects",
    "plotly.io",
    "plotly.subplots",
    "pyarrow",
    "pyarrow.ipc",
    "pyarrow.lib",
]

# ----------------------------------------------------------------------
# 其余隐式依赖
# ----------------------------------------------------------------------
hiddenimports += [
    # 项目自身模块（app/ui.py 是数据文件，其中的 import 不会被静态分析）
    *collect_submodules("app"),
    # streamlit 命令行入口与脚本执行相关的动态导入
    "streamlit.web.cli",
    "streamlit.runtime.scriptrunner.magic_funcs",
    "streamlit.runtime.scriptrunner.script_cache",
    "streamlit.runtime.scriptrunner.script_runner",
    # streamlit 1.6x 内部用 uvicorn/starlette 提供服务
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "starlette",
    "starlette.applications",
    "anyio",
    "websockets",
    "httptools",
    "python_multipart",
    # 数据分析链路
    "pandas",
    "numpy",
    "scipy",
    "sklearn.cluster",
    "sklearn.preprocessing",
    "sklearn.exceptions",
    # sklearn / scipy 内部动态导入的扩展模块
    "sklearn.utils._typedefs",
    "sklearn.utils._heap",
    "sklearn.utils._sorting",
    "sklearn.utils._vector_sentinel",
    "sklearn.neighbors._partition_nodes",
    "sklearn.tree._utils",
    "scipy.special.cython_special",
    # 采集与数据库
    "requests",
    "pymysql",
    "dotenv",
    "certifi",
    "charset_normalizer",
]

# 用不到的大体积可选依赖，排除后能显著减小体积
excludes = [
    "matplotlib",
    "tkinter",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "PyQt5",
    "PySide6",
    "torch",
    "tensorflow",
    "pyarrow.tests",
    "sklearn.tests",
    "scipy.tests",
    "pandas.tests",
]

block_cipher = None

a = Analysis(  # noqa: F821 - PyInstaller 注入的类
    [ENTRY_POINT],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="bili-analyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 隐藏命令行窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
    version=VERSION_FILE if os.path.exists(VERSION_FILE) else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="bili-analyzer",
)
