# bili-analyzer

B站单视频数据采集与用户画像分析工具。

## 功能规划

- 采集单视频的基础信息与统计数据
- 采集评论区数据并入库 MySQL
- 基于 jieba / SnowNLP / scikit-learn 做文本与用户画像分析
- Streamlit + Plotly 可视化展示

## 分析流程与配额控制

`analyzer.process_video(target)` 的执行流程：

1. 先把 target 解析成视频（调用 `BiliAPI.resolve_video`）并获取播放量；
2. 播放量 **>= 18000（1.8 万）** 时直接返回 `{"status": "skipped", "reason": ...}`，
   不再请求评论和弹幕接口，用于控制 API 配额消耗，同时把分析重点放在长尾小众内容上；
3. 播放量 < 1.8 万时继续采集评论与弹幕，并写入数据库。

### 支持的输入格式

页面输入框可以直接粘贴 **B 站分享链接**，也可以只输入编号，程序会自动识别：

| 输入示例 | 说明 |
| --- | --- |
| `https://www.bilibili.com/video/BV1xx411c7mD/?share_source=copy_web` | 视频分享链接（自带 BV 号） |
| `【视频标题】 https://b23.tv/xxxxxxx` | **b23.tv 短链**会先展开再解析，分享文本里的标题不影响识别 |
| `BV1xx411c7mD` | BV 号 |
| `av170001` / `https://www.bilibili.com/video/av170001` | av 号（会换算成对应 BV 号） |
| `ep2243599` / `https://www.bilibili.com/bangumi/play/ep2243599` | 番剧单集（ep 号） |
| `ss46085` / `https://www.bilibili.com/bangumi/play/ss46085` | 番剧季（ss 号，取该季第一集） |

番剧的处理方式：官方 `pgc` 剧集接口对未授权请求返回空数据，所以程序改为解析番剧播放页
HTML 拿到 `aid` / `cid` / `bvid`，再走普通视频接口取播放量等统计；`cid` 用于弹幕、`aid` 用于评论。

采集说明：

- 只调用 B 站公开接口；请求头仅带正常浏览器 User-Agent 与 Referer，默认匿名访问，
  提供了 cookie 文件时使用你自己的真实登录态（不伪造、不冒用他人账号）；
- 请求超时默认 10 秒（可用 `REQUEST_TIMEOUT` 覆盖），所有调用均捕获异常，返回 `status=error/partial` 而不抛出；
- 评论走带 WBI 签名的 `/x/v2/reply/wbi/main`，失败时回退到旧的 `/x/v2/reply`；
- 每次采集会先清空该 `bvid` 的旧评论/弹幕再写入，重复采集不会产生重复数据。

评论支持两种排序，页面上可切换：

| 排序 | 接口 mode | 翻页方式 | 适用场景 |
| --- | --- | --- | --- |
| 热门优先（默认） | `mode=3` | `next` 页码 | 看高赞评论、典型观点 |
| 最新优先 | `mode=2` | `pagination_str` 里的 offset 游标 | 看近期舆情、最新反馈 |

两种排序**匿名都可以用**。注意「最新优先」的结果里，最前面可能出现一条时间较早的
UP 主置顶评论（接口把置顶放在 `top_replies` 中，程序会一并采集）。

## 登录态（可选，用于降低风控风险）

程序默认以**匿名方式**运行，核心功能不受影响——热门排序和最新排序匿名都能正常采集。

提供 cookie 的收益：

- **降低触发风控（`-412 请求被拦截`）的概率**，连续批量分析多个视频时更稳；
- 页面顶部会显示当前账号，方便确认使用的是哪个号。

实测对比（同一视频、同样的采集参数）：匿名与带 cookie 采集「最新排序」分别拿到
`60 / 60`、`58 / 58` 条评论，完全一致。所以 cookie 不是使用最新排序的前提条件，
它的价值在于被限流时更可靠。

### 用法

有三种方式，任选一种：

1. **在页面里上传**（推荐，最简单）：打开「⚙️ 设置」标签页 → 「方式一：上传 cookie 文件」，
   选择本地 txt 文件即可。程序会把它保存为程序目录下的 `cookie.txt` 并立即启用。
2. **在页面里指定路径**：同一个设置页的「方式二」填入文件的完整路径，点「使用该文件」。
   适合 cookie 文件放在别处（如 `D:\我的资料\bili_cookie.txt`）的情况；
   点「恢复默认查找」可以清除这个指定。
3. **放到默认位置**：把 cookie 文件放在**项目根目录**（打包后放在 `bili-analyzer.exe` 同目录），
   文件名支持 `cookie.txt`、`cokie.txt`、`cookies.txt`，程序启动后会自动查找。
   也可以在 `.env` 里用 `BILI_COOKIE_FILE=路径` 指定别的位置。

支持多种粘贴格式，下面几种都能识别：

```
BILIBILI_SESSDATA=xxxx%2Cxxxx%2Cxxxx
BILIBILI_BILI_JCT=xxxx
BILIBILI_DEDE_USER_ID=xxxx
```

```
SESSDATA=xxx; bili_jct=xxx; DedeUserID=xxx
```

也支持 JSON 格式、直接粘贴整条 `Cookie:` 头，或整份文件只有一个 SESSDATA 值。
最少只需要 `SESSDATA`。

### 过期了怎么办

直接**替换 cookie 文件的内容**即可，无需改任何代码：

- 页面顶部会显示当前登录态：有效时显示用户名，失效时给出黄色提示并**自动回退匿名模式**；
- 替换文件后点一下页面上的「重新加载登录态」按钮立即生效（程序每次分析都会重新读取该文件，重启程序同样可以）。

### 安全提醒

- cookie 等同于登录凭据，**不要提交到 Git、不要打包进 exe、不要发给他人**（`.gitignore` 已排除这些文件名）；
- 自动化采集存在账号被风控的风险（限流/封禁），建议使用小号；
- 不再使用时，在 B 站「退出登录」可使该 cookie 立即失效。

## 用户画像分析

`analyzer.analyze_user_profile(bvid)` 读取该视频在 `comments` 表中的评论，
完成四项分析并全部写入数据库，供后续可视化使用：

| 分析 | 做法 | 写入位置 |
| --- | --- | --- |
| 情感分析 | SnowNLP 得分 0-1；>=0.6 为 positive，<=0.4 为 negative，其余 neutral | `comments.sentiment_score` / `sentiment_label` |
| 关键词提取 | jieba 分词，过滤停用词（内置词表 + 可选 `app/stopwords.txt`）与单字，取 Top 20 | `user_profile_summary.top_keywords` |
| 用户画像聚类 | 以 `user_level` + `sentiment_score`（标准化后）做 K-Means(k=3)，按平均等级与平均情感命名簇 | `user_profile_summary` 每簇一行 |
| IP 属地分布 | 统计 `ip_location`，取 Top 5 | `user_profile_summary.top_ip_locations` |

约定说明：

- `user_profile_summary.cluster_id = 0` 表示该视频的**整体汇总**行，`1..k` 表示各聚类簇；
- 重复分析会先删除该 `bvid` 的旧汇总行再写入，不会累积重复记录；
- 情感得分默认复用 `comments` 中已存的值，需要重算时传 `recompute=True`；
- `process_video` 默认在采集入库后自动执行画像分析，可用 `with_profile=False` 关闭。

## 目录结构

```
bili-analyzer/
├── app/
│   ├── __init__.py
│   ├── assets/
│   │   └── logo.png    # 页面顶部 logo（由 tools/make_icon.py 生成）
│   ├── config.py       # 环境变量与配置
│   ├── database.py     # MySQL 连接与读写
│   ├── bili_api.py     # B 站 API 采集
│   ├── analyzer.py     # 文本处理与用户画像分析
│   └── ui.py           # Streamlit 界面
├── assets/
│   └── icon.ico        # exe 图标
├── .streamlit/
│   └── config.toml     # Streamlit 服务与主题配置（打包时一并带上）
├── sql/
│   └── init.sql        # 数据库初始化脚本
├── tools/
│   └── make_icon.py    # 重新生成图标与 logo
├── run.py              # 启动入口（打包入口，内部调用 streamlit.web.cli）
├── build.spec          # PyInstaller 打包配置
├── build.bat           # 双击即可打包
├── version_info.txt    # exe 属性里的版本信息
├── launcher.py         # 可视化一键启动器（图形界面 + 命令行）
├── launcher.config.json # 启动器的服务 / 端口 / 页面配置
├── launcher.spec       # 启动器打包配置
├── build-launcher.bat  # 双击打包启动器
├── start-launcher.bat  # 双击启动启动器（免打包）
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## 环境准备

要求 Python 3.12。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 配置

复制 `.env.example` 为 `.env` 并填写数据库连接信息：

```powershell
Copy-Item .env.example .env
```

`.env` 的查找顺序：环境变量 `BILI_ENV_FILE` 指定的路径 → **exe 同目录**（打包版）→ 项目根目录
→ 当前工作目录。打包版请把 `.env` 放在 `bili-analyzer.exe` 同级目录。

### 数据库连不上怎么办

页面「⚙️ 设置」标签页里有「数据库连接」区块，可以直接看到当前用的主机/账号/库名，
并有一个「测试连接」按钮；报错会附带原因和解决办法。常见错误：

| 报错 | 原因与处理 |
| --- | --- |
| `1045 Access denied ... (using password: NO)` | 没读到密码。在 `.env` 里填 `MYSQL_PASSWORD=你的密码`，然后点「重新加载配置」再测试 |
| `1045 Access denied ... (using password: YES)` | 账号或密码不对；确认 `MYSQL_USER` / `MYSQL_PASSWORD` |
| `1049 Unknown database 'bili_analyzer'` | 库还没建，先执行 `mysql -u root -p < sql\init.sql` |
| `2003 Can't connect to MySQL server` | MySQL 服务没启动，或 `MYSQL_HOST` / `MYSQL_PORT` 不对 |

修改 `.env` 后无需重启程序：点页面上的「重新加载配置」即可生效。

## 初始化数据库

```powershell
mysql -u root -p < sql\init.sql
```

## 运行

```powershell
python -m streamlit run app/ui.py
```

启动后浏览器会自动打开 http://localhost:8501 ，页面标题为「B站视频用户画像分析工具」。
在顶部输入 BV 号并点击「开始分析」即可，运行前请确保 `.env` 已配置好 MySQL 连接。

也可以用入口脚本启动（与打包后的运行方式一致）：

```powershell
python run.py
```

`run.py` 会自动挑选空闲端口（默认 8501，被占用则顺延），并把启动信息写入
`bili-analyzer-startup.log`。

### 界面说明

页面顶部是品牌区（logo + 功能标签 + 当前登录态徽标），下面分两个标签页：

| 标签页 | 内容 |
| --- | --- |
| 📊 分析 | 输入 BV 号、选择评论排序、点击「开始分析」，下方展示视频信息卡、情感分布饼图、关键词条形图、聚类表格、IP 属地条形图 |
| ⚙️ 设置 | 查看登录态与 cookie 键名；上传 cookie 文件、指定 cookie 文件路径、重新加载登录态 |

界面的配色、圆角、卡片阴影来自 `app/ui.py` 里的自定义 CSS 与 `.streamlit/config.toml` 的主题设置，
想改品牌色只需调整这两处的颜色值。

> 注意：`.streamlit/config.toml` 里为了打包后稳定运行设置了 `fileWatcherType = "none"`，
> 因此开发时**改了代码需要重启 Streamlit**（Ctrl+C 后重新运行），页面不会自动热重载。

## 可视化一键启动器

`launcher.py` 是本项目专用的图形化控制台：把「哪个域名 / 端口对应哪个页面」集中显示出来，
并提供 启动 / 停止 / 重启 / 打开页面 的一键操作（有开启有关闭）。仅管理本项目，与其它项目互不影响。

### 三种打开方式

| 方式 | 命令 | 说明 |
| --- | --- | --- |
| 双击脚本 | `start-launcher.bat` | 用项目 `.venv` 直接运行 `launcher.py`，最快，无需打包 |
| 源码运行 | `python launcher.py` | 需要 Python 环境 |
| 打包版 | `dist\bili-launcher\bili-launcher.exe` | 双击即可，见下方「打包启动器」 |

### 界面说明

- 列表区：每行一个服务，列出 **状态 / 服务 / 域名（访问地址） / 对应页面 / 端口**；
- 按钮区：启动、停止、重启、全部启动、全部停止、刷新、退出；
- 「域名 → 页面 对应关系」区：列出选中服务的每个页面完整地址与人可读名称，
  用下拉框选中具体页面后点「打开页面」即可在浏览器打开；双击列表行可直接打开主页面；
- 底部状态栏：显示 MySQL 依赖能否连接、最近一次刷新时间；
- 右上角按钮可以 **中文 / English 即时切换**（无需重启，默认跟随系统语言）。

### 只控制本项目，与其它项目互不影响

这是启动器的硬性设计约束：

- **只认自己拉起的进程**：启动器把进程登记在 `.launcher/state.json`（PID + 创建时间 + 可执行文件路径）。
  停止前先核对进程的**创建时间**，即使 PID 被系统复用也不会误杀；核对不通过就只清理记录、绝不动手。
- **不枚举、不乱杀**：不会扫描或结束任何未登记在案的进程，因此不会波及其它项目。
- **端口被占自动顺延**：默认 8501 / 8502 被别的程序占用时自动改用空闲端口，
  界面上会明确提示「首选端口被其它程序占用」，既不抢占也不受其影响。
- **状态与日志只留在本项目**：进程登记与日志放在项目根目录的 `.launcher/` 下（已在 `.gitignore` 中忽略）。

### 不受语言限制

- 界面文本全部走语言键查找，服务名可以只写一种语言，缺失时自动回退；
- 文件读写统一 UTF-8，子进程注入 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`；
- 命令行输出在中文 GBK 控制台、UTF-8 终端、重定向到文件等情况下都不会因为 emoji 或中文崩溃；
- 界面字体在中文系统用「微软雅黑」、英文系统用「Segoe UI」，并做了高分屏适配。

### 命令行用法（可选）

```powershell
python launcher.py --check              # 打印「域名 → 页面」对应关系
python launcher.py --status             # 一行一个服务的状态表
python launcher.py --start all          # 启动全部服务
python launcher.py --stop dev           # 停止 dev 服务
python launcher.py --restart packaged   # 重启 packaged 服务
python launcher.py --open dev --page 1  # 在浏览器打开 dev 的第 2 个页面
```

换了配置位置时，可用 `--config <路径>` 或环境变量 `BILI_LAUNCHER_CONFIG` 指定。

### 服务与页面配置

服务、端口、页面都在 `launcher.config.json` 里定义，改配置不需要动代码：

```json
{
  "services": [
    {
      "id": "dev",
      "port": 8501,
      "cwd": ".",
      "require_exists": [".venv/Scripts/python.exe", "run.py"],
      "command": [".venv/Scripts/python.exe", "run.py", "--server.port={port}"],
      "pages": [
        {
          "path": "/",
          "short": { "zh": "主页面", "en": "Main page" },
          "title": { "zh": "B站视频用户画像分析工具（标签页：分析 / 设置）", "en": "..." }
        }
      ]
    }
  ]
}
```

- `command` 支持 `{root}` / `{port}` / `{host}` / `{python}` 占位符，所以启动器**不挑语言**：
  配置里写什么命令就执行什么命令（Node、Java、Go 项目同样适用）；
- 默认定义两个服务：`dev`（用 `.venv` 跑源码，端口 8501）与 `packaged`
  （跑 `dist/bili-analyzer/bili-analyzer.exe`，端口 8502）；后者需要先执行 `build.bat`，
  未打包时它会显示「文件缺失」，属正常现象。

### 打包启动器

```powershell
.\build-launcher.bat
```

产物：`dist/bili-launcher/bili-launcher.exe`（约 28 MB，只依赖 Python 标准库 + tkinter，构建约 30 秒）。

启动器通过「向上查找 `launcher.config.json`」定位本项目，所以请把 `dist/bili-launcher`
**留在项目目录内**使用（也可以直接把 `bili-launcher.exe` 复制到项目根目录）。

## 打包成 Windows 可执行程序

### 打包方式

双击项目根目录下的 `build.bat` 即可，脚本会自动检查 PyInstaller、执行打包并暂停显示结果。

也可以在命令行手动执行：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean build.spec
```

### 产物位置

```
dist/
└── bili-analyzer/
    ├── bili-analyzer.exe   ← 双击即可运行
    ├── _internal/          ← 依赖的库与 Streamlit 静态资源
    └── ...
```

程序在 `dist/bili-analyzer/` 目录下，双击 `bili-analyzer.exe` 即可启动，
浏览器会自动打开页面。**分发时需要整个 `dist/bili-analyzer` 目录一起拷贝**，
不能只拷 exe 文件。

双击启动的行为：

- 默认使用 **8501** 端口，若该端口已被占用（例如开发版还开着），会**自动顺延到 8502、8503**……；
- 启动完成后浏览器会自动打开页面；首次启动需要解包，等待 10-30 秒属正常；
- 启动过程会记录到 exe 同目录的 `bili-analyzer-startup.log`（含实际使用的端口和访问地址）。

exe 已带图标与版本信息（右键 → 属性 → 详细信息可看到「B站视频用户画像分析工具」、版本 `1.0.0.0`）。
图标和页面 logo 由 `tools/make_icon.py` 用 Pillow 纯几何绘制生成，改配色后重新执行该脚本，
再打包即可替换：

```powershell
.\.venv\Scripts\python.exe tools\make_icon.py
```

### 关于体积

Streamlit 应用打包后体积偏大是**正常现象**，本项目实测约 **396MB**（不同依赖版本下大致在 300-450MB 区间），
不需要优化，原因是：

- Streamlit 自带完整前端静态资源（JS/CSS）与运行时；
- 科学计算栈体积大：scipy 约 66MB、numpy 约 26MB、pandas 约 12MB、scikit-learn 约 12MB；
- pyarrow 约 79MB（Streamlit 表格序列化必需，其中 `arrow.dll` 单个 21MB）；
- 中文处理需要附带 SnowNLP 情感模型（约 55MB）与 jieba 词典（约 36MB）；
- Streamlit 自身静态资源约 27MB，plotly 约 13MB，Pillow 约 11MB；
- Python 解释器与标准库本身也会被一起打包。

`build.spec` 里已经排除了 matplotlib、tkinter、IPython、pytest、torch 等用不到的大体积依赖，
以及 pyarrow/sklearn/scipy/pandas 的测试代码，进一步压缩没有太大空间。

### 打包后启动报错的排查方法

排查步骤如下：

1. **先看错误日志**：启动失败时程序会弹窗提示，并把完整报错堆栈写到
   exe 同目录下的 `bili-analyzer-error.log`（即 `dist/bili-analyzer/bili-analyzer-error.log`），
   打开这个文件即可看到具体原因。
2. **日志也没有时，让控制台可见**：把 `build.spec` 中 EXE 的 `console=False` 临时改成 `console=True`，
   重新执行一次打包，从命令行运行 exe，就能看到完整报错堆栈。定位完成后再改回 `False` 重新打包。
3. **提示缺少 Streamlit 静态资源**（如 `Failed to load static asset`、
   `No such file or directory: ...\streamlit\static`）：
   说明 Streamlit 的前端文件没被打进去。确认 `build.spec` 中
   `DATA_PACKAGES` 仍包含 `"streamlit"`（它通过 `collect_all` 收集静态资源与包元数据），
   然后执行 `build.bat`（内部带 `--clean`）重新打包。
4. **提示 `ModuleNotFoundError`（如 `sklearn.utils._typedefs`、`uvicorn.protocols...`）**：
   属于隐式依赖没被收集，把缺失的模块名加到 `build.spec` 的 `hiddenimports` 列表中重新打包。
5. **双击后一闪而过**：通常是页面脚本没打进包里，检查 `dist/bili-analyzer/_internal/app/ui.py`
   是否存在；不存在说明 `datas` 中的 `app` 配置丢了。
6. **双击后一直没反应、浏览器不弹出**：先看 exe 同目录的 `bili-analyzer-startup.log`，
   里面记录了实际使用的端口和访问地址，可以把该地址手动粘到浏览器。
   首次运行也可能是杀毒软件在扫描，等 10-30 秒再判断。
   （历史上曾出现"双击完全没反应"：Streamlit 首次运行会询问邮箱，
   打包程序没有控制台输入会静默退出；现在 `run.py` 已显式传入
   `--server.showEmailPrompt=false` 修掉这个问题。）
7. **端口被占用**（`Port 8501 is already in use`）：正常情况下程序会自动顺延端口；
   也可以手动指定，例如 `bili-analyzer.exe --server.port=8600`。
8. **首次启动很慢或被杀毒软件拦截**：打包后的程序首次运行需要加载大量依赖，
   等待 10-30 秒属正常；若被安全软件拦截，把整个 `dist/bili-analyzer` 目录加入白名单。
