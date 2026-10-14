# DY直播间自助工具：独立Mac版

基于Windows 6.1.21，独立开发标识6.1.21-mac.1。源码目录、账号资料和安装包与Windows分开；商品秒杀与批量下单执行函数保留原实现。

## 开发与运行

在Mac上安装包含Tk的Python 3.13，例如Python.org对应芯片安装程序。安装Google Chrome或Microsoft Edge。在本项目目录运行：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-python.txt
python -m tkinter
python dy_grab_gui.py
```

`python -m tkinter`用于确认Tk安装正常，关闭测试窗口后再启动软件。Mac资料位置为 `~/Library/Application Support/DYLiveAssistantMac`，包含偏好、账号资料、授权缓存和诊断。应用不向 `.app` 内部写入资料。使用Finder“前往文件夹”可打开该位置。

使用步骤见《用户使用说明.md》。规格、编号、秒杀和批量操作保持原方式。授权继续使用现有卡密服务；Mac属于新设备，原绑定Windows的卡密需要管理员按既有规则解绑或更换，不能直接复制Windows授权缓存或浏览器资料。

## 两种芯片打包

Apple Silicon使用原生arm64 Python，在M系列Mac运行：

```sh
bash build_mac.sh arm64
```

Intel使用原生x86_64 Python，在Intel Mac运行：

```sh
bash build_mac.sh x86_64
```

脚本检查Python架构，拒绝生成错配包。分别输出对应架构的`.app`和ZIP；不能仅改文件名把其中一个当作另一种芯片的安装包。浏览器本体不捆绑；用户安装Chrome/Edge即可，自动选择失败会提示原因。不要在Windows上执行Mac打包。

可通过环境变量 `DY_MAC_SIGNING_IDENTITY` 使用已经安装在Mac钥匙串里的Developer ID进行签名。未提供时PyInstaller只生成本地测试用签名，不能视为已通过Apple公证的正式发行包。正式分发还需要Developer ID签名、公证及staple流程，当前未执行。

## 更新隔离

第一版没有接入Mac自动更新通道，“检查更新”仅显示说明，完全不会连接Windows更新索引或下载EXE。公告、反馈、卡密接口仍可访问；它们不负责替换软件文件。Mac后续自动更新需单独设计平台清单、签名包和原子替换，不应复用Windows更新器。

## 必须在真实Mac验证

两种芯片分别检查：Tk启动、中文字体和缩放、滚轮/触控板、浏览器选择、登录及资料保存、卡密激活/离线恢复、文件夹打开、公告/反馈、人工页面的商品/规格/数量识别、连续批量确认和恢复。测试不得自动支付；真实订单由用户自行决定是否测试。代码在Windows做的检查不能替代Mac平台验证。

当前未生成可运行的Mac成品、未公证、未发布Mac服务器更新。现有Windows6.1.21与服务器均未因本次开发修改。

## 共用GitHub仓库的云端构建

仓库 `Tyler-Xixi/dy-live` 可继续共用。Mac源码放在 `macos/`，工作流放在仓库根目录 `.github/workflows/macos-test-build.yml`。独立测试分支 `macos-test-build` 的提交触发Intel和M系列两项构建；不提交到Windows main分支、不创建Release、不上传服务器。

Apple Silicon使用 `macos-15`，Intel使用 `macos-15-intel`；两种包分别存为Actions附件，保留14天。构建后还运行不启动浏览器、不下单的源代码GUI启动/关闭检查。此检查不代表真实抖音登录或锁单验收。第一轮测试范围为macOS15，旧系统兼容性尚未确认。

仓库公开，上传清单排除 `local_license_keys.py`、运行资料、日志和私钥。公开云端测试包默认使用联网卡密；私有离线卡密如需支持，由维护者另在私有构建环境加入现有哈希清单，不能把它提交到公开仓库。
