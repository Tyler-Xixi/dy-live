# DY 脚本自动化控制台

> 本项目为竞赛学习/自动化流程验证项目，用于将原有 DY 直播间商品脚本封装为可视化 GUI，并保留 Node.js + Playwright 自动化能力。

## 项目简介

本项目围绕 dy-live(懂的都懂)商品场景，提供桌面 GUI、Web 控制台和命令行三种运行方式。脚本可以打开直播间、复用浏览器登录态、识别商品列表中的目标商品，并在配置的监控时间点内进行轮询、匹配和下单流程推进。

项目默认开启或支持 Dry Run、诊断快照、运行日志等功能，方便在学习和调试阶段观察自动化流程。

## 功能特性

- 桌面 GUI：基于 Python + Tkinter 的可视化操作界面。
- Web 控制台：基于 Node.js HTTP 服务和浏览器页面的本地控制台。
- 抢商品模式：支持直播间链接、商品关键词、商品编号、目标价格和定时监控配置。
- 批量购买模式：支持现有商品链接、购买数量和购买次数配置。
- 定时监控：支持最多四个时间点，按窗口进行商品状态轮询。
- 商品匹配：优先按商品编号匹配，失败时可按关键词和价格兜底匹配。
- 订单推进：命中购买按钮后继续推进订单流程，可选择提交后放弃支付。
- 实时日志：GUI 和 Web 控制台均可查看当前脚本运行状态。
- 诊断输出：可保存页面 HTML、截图、网络命中日志，便于排查问题。

## 技术栈

- Python 3.10+
- Tkinter
- Node.js
- Playwright
- Chromium

## 目录结构

```text
.
├── automation_runner.mjs       # Node.js Playwright 核心自动化逻辑
├── live_room_auto_grab.mjs     # Node.js CLI 入口
├── gui_server.mjs              # 本地 Web 控制台服务
├── public/                     # Web 控制台前端页面
│   ├── index.html
│   ├── app.css
│   └── app.js
├── dy_grab_gui.py              # Python Tkinter 桌面 GUI
├── requirements-python.txt     # Python 依赖
├── 启动PythonGUI.bat           # Windows 一键启动脚本
├── diagnostics/                # 运行诊断产物
├── live-room-profile/          # Playwright 浏览器登录态/profile
├── live_room_network_hits.jsonl # 网络命中日志
├── 需求.md
└── 交付报告.md
```

> `node_modules/`、`diagnostics/`、`live-room-profile/`、`readonly-check-profile/`、`live_room_network_hits.jsonl` 属于依赖或运行产物，上传 GitHub 前建议加入 `.gitignore`。

## 环境要求

### Python GUI

- Windows
- Python 3.10 或更高版本
- 可正常安装 Playwright Chromium

### Node.js 版本

- Node.js 18 或更高版本
- npm

## 安装依赖

### Python 依赖

```powershell
python -m pip install -r requirements-python.txt
python -m playwright install chromium
```

### Node.js 依赖

```powershell
npm install
npx playwright install chromium
```

## 使用方式

### 方式一：启动 Python 桌面 GUI

Windows 下可直接双击：

```text
启动PythonGUI.bat
```

也可以手动运行：

```powershell
python dy_grab_gui.py
```

首次启动时，批处理脚本会自动安装 Python Playwright 依赖并安装 Chromium。

### 方式二：启动 Web 控制台

```powershell
npm run gui
```

启动后访问：

```text
http://127.0.0.1:8787
```

如需修改端口：

```powershell
$env:PORT=8888
npm run gui
```

### 方式三：命令行运行 Node.js 脚本

默认运行：

```powershell
npm run cli
```

批量购买模式示例：

```powershell
$env:MODE="batch"
$env:PRODUCT_URL="https://example.com/item"
$env:BUY_QUANTITY="2"
$env:BUY_TIMES="3"
npm run cli
```

## 常用配置

### 抢商品模式

| 配置项 | 说明 |
| --- | --- |
| 直播间链接 | DY 直播间 URL |
| 商品关键词 | 用于匹配商品名称、SKU 或关键词片段 |
| 商品编号 | 商品列表中的编号，优先级高于关键词 |
| 目标价格 | 用于价格匹配 |
| 定时监控 | 最多四个时间点，例如 `10:29:00` |
| 轮询间隔 | 商品状态扫描间隔，单位 ms |
| 随机抖动 | 在轮询间隔上增加随机延迟 |
| Dry Run | 只记录命中动作，不真正点击 |
| 保存诊断快照 | 保存截图、HTML、网络日志等排查材料 |

### 批量购买模式

| 配置项 | 说明 |
| --- | --- |
| 商品链接 | 现有商品页面 URL |
| 购买数量 | 单次购买数量 |
| 购买次数 | 批次购买次数 |
| 下单推进步数 | 进入订单流程后的最大推进次数 |

## Node.js 环境变量

CLI 入口支持通过环境变量覆盖默认配置：

| 环境变量 | 说明 |
| --- | --- |
| `MODE` | 运行模式：`flash` 或 `batch` |
| `LIVE_URL` | 直播间链接 |
| `PRODUCT_URL` | 商品链接 |
| `PRODUCT_NAME` | 商品关键词 |
| `PRODUCT_ID` | 商品编号 |
| `TARGET_PRICE` | 目标价格 |
| `DRY_RUN` | 是否 Dry Run，`1/true/on` 表示开启 |
| `HEADLESS` | 是否无头运行 |
| `POLL_MS` | 轮询间隔 |
| `JITTER_MS` | 随机抖动 |
| `SCHEDULE_WINDOWS` | 监控时间点，逗号分隔 |
| `BUY_QUANTITY` | 购买数量 |
| `BUY_TIMES` | 购买次数 |
| `PROFILE_DIR` | 浏览器 profile 目录 |
| `DIAGNOSTICS_DIR` | 诊断输出目录 |
| `CLOSE_BROWSER_ON_FINISH` | 结束后是否关闭浏览器 |

## 验证命令

检查 Node.js 语法：

```powershell
npm run check
```

检查 Python 语法：

```powershell
python -m py_compile dy_grab_gui.py
```

## 运行产物

项目运行过程中可能生成以下内容：

- `diagnostics/`：页面截图、HTML、JSON 诊断信息。
- `live-room-profile/`：浏览器登录态和缓存。
- `readonly-check-profile/`：只读检查用浏览器 profile。
- `live_room_network_hits.jsonl`：网络请求命中日志，文件可能较大。

这些文件通常不需要提交到 GitHub。

## 注意事项

- 请仅在学习、竞赛、测试或已获授权的环境中使用本项目。
- 使用自动化脚本前，请确认目标平台规则、账号安全和相关法律法规要求。
- 建议先开启 Dry Run 观察日志，确认匹配和流程无误后再进行真实操作。
- 如果提示浏览器 profile 被占用，请关闭旧的自动化 Chromium 窗口后重新启动。
- 直播间页面结构可能变化，若按钮或商品卡片无法识别，需要根据最新页面更新匹配逻辑。

## 许可证

当前项目未声明许可证。如需开源发布，建议补充 `LICENSE` 文件。
"# dy-live" 


## 2026-10-07 桌面版更新与源码构建

本次在原有项目上增量更新，保留原有 Node.js / Web 控制台和历史文件。以下新增能力主要对应 Python 桌面版（`dy_grab_gui.py`），不代表旧版 Node.js 页面具备完全相同功能。

- 双任务模块：日常秒杀监控、同一商品顺序批量下单。
- 等待开售时提前锁定目标商品，监听按钮与页面状态变化。
- 支持配置规格组与选项名称，例如 `补价=12`、`颜色=粉色 | 尺寸=大号`。
- 支付前再次核对金额，批量按单价乘每单数量校验；金额差异会停止支付。
- 支付点击不重试，结果不明确时停止后续操作并保留页面；成功计数基于页面成功提示。
- 优化界面分区、填写提示及参数说明，新增 49 项本地回归测试。
- 完整操作步骤请阅读 [用户使用说明](用户使用说明.md)，包括学习研究用途与责任声明。

### 私密数据不随源码发布

源码不包含真实卡密、卡密数据库、客户端卡密哈希、激活状态或新的浏览器登录资料。公开源码缺少 `local_license_keys.py` 时不会放行激活，也不会自动生成可用卡密。

管理员首次构建自己的发行版前，先在本地生成自己的卡密：

```powershell
python card_admin/generate_cards.py --count 100
python card_admin/export_local_keys.py
```

首次生成会创建本地 `card_admin/cards.db`、`card_admin/generated_cards.csv` 和 `local_license_keys.py`。不要提交这些私密文件，也不要覆盖或混用原发行版卡密。已有数据库和导出文件时请按 [卡密管理说明](card_admin/README.md) 操作。

```powershell
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -v
python -m PyInstaller --noconfirm dy_live.spec
```

也可运行 `构建EXE.bat`。浏览器回归测试需要安装 Edge；默认测试启动本地无头浏览器，不会向直播间发送真实订单。卡密测试使用人工测试数据，不依赖真实卡密表。

打包完成后分发整个 `dist/DYLiveAssistant` 文件夹，不能只复制 EXE。构建产物没有直接提交进源码仓库。

> 注意：`.gitignore` 只阻止未跟踪文件被新增，不会移除仓库历史中已提交的浏览器资料或诊断数据。保留原文件不代表其可以安全公开，应单独评估并处理隐私风险。
