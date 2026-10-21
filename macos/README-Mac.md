# DY直播间助手 Mac 6.2.39

独立Mac项目，面向macOS15及以上。Intel x86_64与Apple Silicon arm64分别打包。App内部包含Python和全部依赖，用户无需安装开发环境。

同步Windows6.2.39的UI、账号切换、规格与下单信息、秒杀与批量流程。AutomationRunner与Windows基线AST一致；本次只改变Mac平台适配、打包与Mac更新。Windows源文件与成品不修改。

构建：在对应架构的Mac上运行 `bash build_mac.sh arm64` 或 `bash build_mac.sh x86_64`，通过GitHub macos-test-build独立分支构建。公开仓库不包含离线卡密验证哈希、私钥、个人资料或日志。默认通过原联网卡密管理服务激活。

检查更新：Mac专用HTTPS路径，独立签名上下文，验证平台、架构、版本、发布序号和ZIP哈希。只下载完整Mac ZIP，退出后由用户通过Finder替换.app，资料保存在应用外。不会调用Windows更新器或访问Windows更新清单。

此版没有Developer ID证书，未完成Apple公证。构建中的ad-hoc签名完整性检查不是Apple公证，系统可能拦截运行。必须明确告知接收方此限制。

验收范围：两种芯片云端构建、源码GUI与打包应用启动、ZIP完整性和Mach-O架构、无用户资料、更新协议测试。云端启动不等于所有用户电脑正常启动，也不等于真实抖音登录或锁单已验证。
