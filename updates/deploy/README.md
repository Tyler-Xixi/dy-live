# 手动确认更新发布

首次：管理员在服务器初始化独立签名密钥，将输出的公钥写入客户端配置，再重新构建正式主程序和独立更新程序。私钥只存在服务器，不要复制到 dist 或发送给用户。

- 公开目录：`/opt/dy-updates/public`，仅静态下载。
- 私钥目录：`/opt/dy-updates/secrets`，0700；`update-ed25519.key` 为0600。
- 发布工具：独立目录，上传源码白名单；不上传账号、激活文件、诊断图片和 SSH 凭据。
- Nginx只读挂载公开目录至 `/srv/dy-updates`，在授权域名的 HTTPS server 中加入 `nginx.location.conf`，先检查配置再 reload。
- 只在管理员明确发布时执行命令，不开放 HTTP 管理发布接口。

每次修改、测试后：提升 `app_version.py` 的三段版本，构建更新程序，再构建完整主程序。包必须带主EXE、独立更新EXE、`_internal`、手册和版本信息。

本地打包（不上传）：

```powershell
python -m tools.update_release --dist build/release/DYLiveAssistant --output build/update-releases --version 6.1.0 --sequence 1 --notes "首个支持手动确认更新的版本"
```

上传版本目录后，服务器明确发布：

```bash
cd /opt/dy-updates/tools
python3 -m tools.update_publish publish --source /opt/dy-updates/incoming/6.1.0 --public-root /opt/dy-updates/public --secrets-dir /opt/dy-updates/secrets
```

版本和序号必须严格递增，不能覆盖已存在版本。最新索引最后切换，失败不会发布不完整包；若版本已入公开目录但索引切换失败，保留证据，由管理员处理，不自动覆盖。

旧索引备份保存在秘密目录的 `index-history`，用于排查，不应直接拿旧索引给用户降级。备份签名私钥和历史数据库至安全位置；私钥丢失需要发布一次手动迁移版本。

6.1.0之前用户需手动获取一次完整新版；之后发现更新可选择接受、稍后或取消下载。更新不恢复任何下单任务。不要只发送单个EXE。
