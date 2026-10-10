# Ubuntu 24.04 授权服务部署与回滚

2026-10-09 已在用户Ubuntu服务器完成现场部署，公网HTTPS、真实客户端激活、dist启动和证书续期dry-run通过；原博客 `txblog.cn` / `www.txblog.cn` 保留。以下仍提供首次部署、重部署和恢复参考，不应在已初始化的运行目录重新执行init。

现场目录 `/opt/dy-license`；管理员入口 https://license.txblog.cn/admin ，用户名admin，随机密码在开发机任务专属受限文件保存（不放进发行包）。签名私钥只留服务器。证书使用专属 `runtime/certificates`，续期定时器 `dy-license-renew.timer` 已启用。

新增 `/root/blog-deploy/docker-compose.override.yml` 会自动合并授权HTTPS挂载/私有网络；原docker-compose.yml和nginx.conf未覆盖。回滚可将新增override改名保留，然后使用原始Compose显式 `docker compose -f /root/blog-deploy/docker-compose.yml up -d --no-deps frontend` 恢复仅博客前端；数据库/后端不需重建。原配置备份目录为 `/root/blog-deploy/license-backup-20261009-first/`。授权数据和密钥不要删除。

## 部署前

在 Xshell 运行 `sh license_server/deploy/preflight.sh`，确认 Docker Compose 可用、博客挂载和网络、现有配置位置与 443 发布方式。不要发送 `docker inspect` 的完整输出（包含环境和秘密），也不要发送私钥或密码。

在尚未检查真实博客 Compose 文件前，不直接安装本 Nginx 模板、不覆盖 default.conf、不停止博客。记录博客原域名访问结果，安全备份 Compose/Nginx 配置。若需重建 blog-frontend 添加 443 或挂载，先约定短暂中断时机。

把服务源码上传到一个新的专用目录，建议 `/opt/dy-license`，保持根目录的 `license_protocol.py` 和 `license_server/` 结构。不要把 `live-room-profile`、旧版明文卡密文件或其他工作区文件上传。初始化前确认 runtime 目录没有既有生产数据。

### 首次上传（只新增授权目录，不动博客）

本地交付的 `license-server-20261009.zip` 已采用源码白名单，不含任何真实卡密、用户资料或密钥。通过 Xftp/SFTP 上传到服务器 `/root/license-server-20261009.zip` 后，可运行以下首次部署步骤。如果专用目录已存在，停止并检查，不要覆盖：

```sh
test ! -e /opt/dy-license && mkdir /opt/dy-license
```

只在上一步成功时继续（若提示失败请勿执行后续）：

```sh
python3 -m zipfile -e /root/license-server-20261009.zip /opt/dy-license
cd /opt/dy-license
docker compose version
```

若服务器没有可用 python3 或 Docker Compose 插件，先报告错误，不改博客或改用旧版不兼容命令。以下初始化和公钥导出可以在接入博客前独立完成；HTTPS/前端重建安排为下一阶段。

## 服务初始化（现场预检后执行）

从专用目录执行：

```sh
docker compose -f license_server/compose.yaml build license-api
docker compose -f license_server/compose.yaml --profile setup run --rm license-bootstrap init --username admin
docker compose -f license_server/compose.yaml up -d license-api
docker compose -f license_server/compose.yaml exec license-api python -m license_server.cli export-public-key
```

init 会交互询问密码两次，不显示输入，不把密码写到 shell 历史。管理员密码至少 12 字符。私钥/digest 秘密仅保存在 `license_server/runtime/secrets`，新生成文件 600 权限；数据及秘密属于服务 UID 10001。再次 init 不会替换既有密钥或管理员。只把 export-public-key 的公钥 JSON 发给客户端构建者，不能发 signing.pem 或 digest.key。

服务端不发布宿主机端口。把博客 Nginx 容器加入 `dy-license-private` 网络，并将该网络加入博客原 Compose 的持久配置；仅 `docker network connect` 的临时连接在重建后可能丢失。具体补丁以真实现场路径为准。

### 2026-10-09 现场已确认的路径

博客当前只有 `/root/blog-deploy/nginx.conf` 单文件挂载为 default.conf；HTML 挂载 `/root/blog-deploy/frontend`，网络 `blog-deploy_default`，仅发布 80。原配置含博客 API/WebSocket 等路由，不能用授权配置覆盖它。

新增 `deploy/blog-license.override.yaml` 模板，仅为博客前端增加独立授权配置文件、ACME/证书挂载、私有网络和 443，不改原 nginx.conf。使用前必须确认 `docker inspect blog-frontend --format '{{index .Config.Labels "com.docker.compose.service"}}'` 输出 `frontend`，并确认允许短暂重建该前端容器。原后端、Redis/MySQL不重建。先将 ACME-only 文件复制为 active.nginx.conf，证书签发前不能启用最终 SSL 文件。合并配置用 `docker compose -f /root/blog-deploy/docker-compose.yml -f /opt/dy-license/license_server/deploy/blog-license.override.yaml config --quiet` 校验，不输出包含环境秘密的完整 config。

Docker 构建使用源码白名单 COPY 和 Dockerfile.dockerignore；更新时不能把 runtime 私钥/数据库装进镜像。上传包也仅包含公共源码和部署模板。

## HTTPS 接入

先使用只含授权域名 HTTP/ACME 验证路径的临时 server 块，现有博客规则不变；完成证书签发后再启用含 443 的最终模板。不要在证书尚未存在时加载最终 ssl 配置。

证书建议 ACME HTTP-01 webroot：只映射 `license.txblog.cn/.well-known/acme-challenge/` 到专用共享目录；不能使用停止整个博客来独占 80 的 standalone 模式。根据现场挂载确定验证目录与 `/etc/letsencrypt` 只读证书挂载、443 映射。只开放必要云防火墙规则，不开放授权后端和数据库。

续期使用实际安装的 ACME 客户端定时任务，dry-run 验证成功后启用；deploy hook 在证书变化时执行 `docker exec blog-frontend nginx -t`，通过才执行 `docker exec blog-frontend nginx -s reload`。续期失败应保留有效证书并报警，不替换成自签名证书或关闭 TLS 校验。

上线验证：真实证书域名匹配、`https://license.txblog.cn/health` 可用、管理登录有效、原博客的两个域名仍走原路由；授权 HTTP 跳转只作用于 license 子域名。无这些结果不能宣称部署完成。

授权 API 在 Nginx 按实际来源 IP 限流（60/min，burst 20，超限429），不信任用户提供的转发头。Compose 的 `LICENSE_LIMIT_AT_PROXY=1` 只适用于此私有无公开端口后端，禁止脱离受限 Nginx 单独公开部署；否则使用默认应用内来源限流。验证两来源互不抢占配额后才算生产限流验收。

## 管理与恢复

忘记密码，在专用目录执行 `docker compose -f license_server/compose.yaml exec license-api python -m license_server.cli reset-admin`，交互设置新密码并撤销所有管理员会话。

数据库备份示例：`docker compose -f license_server/compose.yaml exec license-api python -m license_server.cli backup /data/backup-20261009.sqlite3`。目标文件必须不存在，使用 SQLite backup API 获得一致性快照。时间戳文件名由操作者换成实际时间。

签名私钥与 digest.key 另做加密/受限离线备份，数据库和密钥都不能公开下载。密钥丢失不可静默重生成，否则旧凭证/卡密将不匹配。

恢复先停止独立授权服务（不是博客）；CLI `restore SOURCE DESTINATION` 只恢复到新路径，校验数据库完整性并清除会话。保留原数据库及其 WAL/SHM 文件，确认恢复数据后才安排路径切换，不直接覆盖运行中的 SQLite 文件。

回滚时仅撤销本次新增授权 server 块与相关挂载/网络改动，恢复备份的博客配置；`nginx -t` 通过后再 reload。客户端恢复完整旧发行备份，不删除用户授权与浏览器资料。

## 已知限制

联网卡密可远程管理，旧版离线卡密不可。无限断网宽限不会延长卡密到期时间，但停用和解绑无法即时传达到长期离线电脑；旧机断网仍可能继续使用。首次激活一定需要联网。

离线观察到的时间上限在独立后台合并保存，正常退出还会保存；购买检查不等待磁盘。突然断电可能丢失最近约一秒的观察记录。无限断网与普通客户端存储不能提供防系统时间操纵、文件恢复或程序修改的绝对 DRM 保证；时间回拨提示联网确认，续期/恢复后可在激活窗口点击“重新联网校验”，无需找回卡密明文。

本机开发环境没有 Docker，模板检查与本地 SQLite 重启/恢复测试不能冒充真实 Docker/Nginx/证书续期验证，必须按现场验收完成。
