# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：bili-analyzer 可视化一键启动器。

构建方式（在项目根目录执行）::

    .\\.venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean launcher.spec

或直接双击 build-launcher.bat。产物：dist/bili-launcher/bili-launcher.exe

说明
----
启动器只需要 Python 标准库（含 tkinter），因此这里把数据分析 / Streamlit 等
大依赖整体排除，打出来的体积只有几 MB，构建也很快。
启动器通过「向上查找 launcher.config.json」定位本项目，所以它是绑定本项目的
工具：把 dist/bili-launcher 留在项目目录内使用即可（也可以把 exe 复制到项目
根目录）。想换配置可用 --config 或环境变量 BILI_LAUNCHER_CONFIG 指定。
"""

import os

PROJECT_ROOT = SPECPATH  # noqa: F821 - 由 PyInstaller 注入
ENTRY_POINT = os.path.join(PROJECT_ROOT, "launcher.py")
ICON_PATH = os.path.join(PROJECT_ROOT, "assets", "icon.ico")

# tkinter 在部分环境下需要显式声明；其余依赖一律不打包
hiddenimports = [
    "tkinter",
    "tkinter.ttk",
    "tkinter.font",
    "tkinter.messagebox",
    "tkinter.constants",
]

# 本项目的数据分析栈与启动器无关，排除后体积从数百 MB 降到几 MB
excludes = [
    "numpy",
    "pandas",
    "scipy",
    "sklearn",
    "matplotlib",
    "plotly",
    "streamlit",
    "jieba",
    "snownlp",
    "pymysql",
    "pyarrow",
    "PIL",
    "torch",
    "tensorflow",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "PyQt5",
    "PySide6",
    "setuptools",
    "pip",
]

block_cipher = None

a = Analysis(  # noqa: F821 - PyInstaller 注入的类
    [ENTRY_POINT],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=[],
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
    name="bili-launcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 图形界面程序，不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="bili-launcher",
)