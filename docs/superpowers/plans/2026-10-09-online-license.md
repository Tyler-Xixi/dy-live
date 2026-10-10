# 联网激活与卡密管理网站 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供单管理员中文卡密网站和联网绑定激活，在保留旧版离线逻辑及博客的前提下交付更新 EXE。

**Architecture:** 独立 FastAPI 容器使用专属 SQLite，现有 Nginx 仅新增授权域名分流。公用协议模块定义签名数据，客户端独立授权模块异步联网，通过本地状态快照接入 Tk GUI 和自动化线程。管理页由同一服务提供，不另建用户注册系统。

**Tech Stack:** Python、FastAPI/Starlette、Jinja2、SQLite、cryptography Ed25519、argon2-cffi、原生 JavaScript/CSS、Docker/Nginx；客户端保留 Tkinter/Playwright/PyInstaller。

**Spec:** D:/SoftWare-Work/CodexProject/dy-live-main/docs/superpowers/specs/2026-10-09-online-license-design.md（2026-10-09 用户已确认）

## Global Constraints

- 管理地址：https://license.txblog.cn/admin；授权 API 与管理网站同源。
- 一个管理员，账号密码登录；无公开注册、用户中心、在线支付、分销或多管理员权限系统。
- 管理网站只控制新联网卡密。旧版离线卡密继续原逻辑，不受远程停用、期限编辑或设备解绑控制。
- 联网验证失败不自动转换成旧版离线激活；用户必须明确选择入口并持有该入口的有效凭证。
- 已成功联网激活且凭证有效的电脑，在网络故障时无额外宽限时限；卡密自身到期时间仍有效。
- 授权检查不得进入商品开售到点击下单的关键路径，不改变现有秒杀、批量下单、人工验证暂停恢复逻辑。
- 完整卡密仅生成时展示，并允许该次下载批次文件。网站后续不支持找回明文；未保存时需停用并重新生成。一次展示不是防泄露保证。
- 当前目录不是 Git 仓库，不运行 git commit 或创建 worktree；用任务检查项、测试报告和发行备份记录阶段结果。
- 只操作本地模拟测试，不进行真实抖音交易；不得读取旧版明文卡密文件、浏览器资料、生产密钥或博客数据库。
- 当前只批准设计，计划须用户批准并选择执行方式后才安装依赖或写产品代码。
- 执行前主代理读取 security-best-practices 技能及 Python FastAPI 服务端参考；按所选执行方法读取执行技能、TDD 和验证技能。

## Review Focus

1. 校验响应乱序：旧成功晚于新停用响应到达，不能重新授权；任务 1、5 测试请求序号和 nonce。
2. 批次响应丢失：数据库已提交但管理员未收到卡密，不能从后续接口找回明文；任务 2、4 测试提交/显示边界并提供批次编号用于归档重发。
3. 凭证到期后的续期：客户端仍可用真实旧凭证请求校验，服务端按当前期限签发新凭证；任务 3、5 测试过期凭证签名校验与当前状态校验分离。
4. 授权失效发生在人工验证或订单确认期间：不关闭浏览器，不中断已提交订单的只读确认；任务 6 测试三个阶段及 explicit stop 区别。
5. Nginx 挂载或端口变化：不能覆盖博客配置或将全站跳转应用到博客；任务 7、8 测试模板隔离、预检和回滚。

## File Map and Shared Interfaces

创建 `license_protocol.py`：只含数据类型、规范编码、签名验证与有效期判断，不含网络、私钥或 GUI。创建 `online_license.py`：本地存储、HTTPS 传输、状态协调与后台校验。服务端模块位于 `license_server/`，服务器依赖独立于桌面依赖。

公共类型由任务 1 定义：

- `LicenseClaims(version: int, product: str, license_id: str, device_id: str, binding_version: int, issued_at: int, duration_type: str, expires_at: int | None, key_id: str)`；UTC 时间为整数 Unix 秒，产品固定 `DYLiveAssistant`，版本固定 1，duration_type 为 limited/permanent 且必须与 expires_at 是否为空一致。
- `SignedDocument(payload: dict, signature: str)`；签名为 URL-safe Base64 Ed25519，消息为 UTF-8 规范 JSON（排序键、无额外空白、拒绝浮点与重复键）。
- `LicenseStatus(allowed: bool, mode: str, reason: str, expires_at: int | None)`，mode 为 `online` 或 `offline`，reason 用固定代码而非异常原文。
- `canonical_bytes(payload: dict) -> bytes`；`verify_document(document: dict, public_keys: dict[str, bytes]) -> dict`；`validate_claims(payload: dict, device_id: str, now: int, enforce_expiry: bool = True) -> LicenseClaims`。
- `Clock` 注入 `wall_time() -> int` 与 `monotonic_time() -> float`，测试不真实等待 10 分钟。

联网端点：`POST /api/v1/activate`（card/device_id/client_version/nonce），`POST /api/v1/check`（credential/device_id/client_version/nonce）。响应为签名 envelope，字段 version/product/key_id/nonce/server_time/result/credential；result 为 allow/disabled/archived/expired/binding_mismatch，credential 仅 allow 时存在。未知卡密不给本地可信拒绝状态，HTTP 400/429/5xx 均不授予权限。

## Task 1: 协议、配置与可验证签名

**Files:** Create `license_protocol.py`, `license_server/__init__.py`, `license_server/config.py`, `license_server/signing.py`, `license_server/requirements.txt`, `tests/test_license_protocol.py`.

**Interfaces:** 产出公共类型与函数；`ServerSettings.from_env() -> ServerSettings`；`Signer.sign(payload: dict) -> dict`，私钥只从服务器受限文件加载。

- [ ] 执行前记录当前 EXE/source 哈希，运行原有 `python -m unittest discover -s tests -v` 保存基线；既有失败先区分环境与原有问题，不把它们伪装成此次授权改动。
- [ ] 写失败测试 `test_signature_device_expiry_and_nonce`、`test_invalid_shapes_and_duplicate_keys`、`test_old_key_and_wrong_product_rejected`：

```python
self.assertEqual(validate_claims(valid, "device-a", 1000).device_id, "device-a")
with self.assertRaises(ValueError): validate_claims(valid, "device-b", 1000)
with self.assertRaises(ValueError): validate_claims(valid, "device-a", valid["expires_at"])
self.assertEqual(canonical_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}')
```

- [ ] 运行 `python -m unittest discover -s tests -p test_license_protocol.py -v`，确认因缺失模块/函数失败，而非测试语法错误。
- [ ] 实现规范协议、严格类型/长度校验、受信任 key ID、签名验证、服务器签名和环境配置。测试用临时密钥，绝不写生产私钥到源码。部署生产模式强制 HTTPS origin；本地测试以显式 test 配置使用回环地址。
- [ ] 在本地独立虚拟环境核实兼容发行版并锁定服务端依赖；桌面仅增加其实际使用的签名依赖，不将 FastAPI 安装包打进 EXE。
- [ ] 重跑协议测试，全部 PASS，记录所选版本与依据；配置缺少既有私钥时拒绝启动，不自动更换密钥。

## Task 2: 数据库与卡密状态机

**Files:** Create `license_server/database.py`, `license_server/cards.py`, `tests/test_license_cards.py`.

**Interfaces:** `Database(path: Path).initialize() -> None`, `.backup(destination: Path) -> None`；`CardService(db: Database, digest_secret: bytes, clock: Clock)`；`.create_batch(count: int, duration_days: int | None, note: str) -> GeneratedBatch`；`.bind(card: str, device_id: str) -> LicenseClaims`；`.check(claims: LicenseClaims, device_id: str) -> LicenseClaims`；`.update(card_id: str, action: str, values: dict, actor: str) -> CardView`；`.list_cards(filters: dict, page: int, page_size: int) -> CardPage`。

`GeneratedBatch` 包含 batch_id 和只用于该响应的明文卡密；`CardView` 不包含摘要秘密或完整卡密。update action 限定 edit/renew/disable/restore/unbind/archive。各操作在同一写事务内更新审计与卡状态。

业务拒绝抛出 `LicenseDenied(reason: str)`，并发测试捕获后映射为 None，不给 LicenseClaims 添加未定义的 allowed 属性。

- [ ] 写失败测试 `test_concurrent_first_bind_only_one_device`、`test_same_device_idempotent`、`test_expiry_renew_and_unbind_keep_term`、`test_transaction_rollback_and_restart_backup`、`test_batch_database_contains_no_plaintext`：

```python
self.assertEqual(sum(result is not None for result in concurrent_claims_or_none), 1)
self.assertEqual(first.expires_at, second.expires_at)
self.assertEqual(after_unbind.expires_at, before_unbind.expires_at)
self.assertNotIn(generated_card, database_dump)
```

- [ ] 运行 `python -m unittest discover -s tests -p test_license_cards.py -v`，确认 RED。
- [ ] 实现 SQLite 版本化 schema、唯一约束、事务绑定与审计、HMAC 摘要、128 位以上随机卡密（`DYL-` 前缀）、默认批次 1–1000、分页 1–100、备注最多 500 字符。禁止归档卡重新启用；永久改限时必须指定未来到期时间。
- [ ] 固定续期算法为 `max(expires_at, server_now) + days * 86400`，首次限时绑定启动期限；解绑递增版本，不重置期限。批次写入失败全部回滚，响应丢失通过 batch_id 查询掩码并归档整批，不找回原码。
- [ ] 重跑所有状态机测试，确认数据库重启和 SQLite backup 恢复后卡密及审计一致，记录 PASS。

## Task 3: 公共激活 API 与管理员认证

**Files:** Create `license_server/app.py`, `license_server/public_api.py`, `license_server/auth.py`, `license_server/admin_api.py`, `license_server/cli.py`, `tests/test_license_api.py`, `tests/test_license_auth.py`.

**Interfaces:** `create_app(settings: ServerSettings, clock: Clock | None = None) -> FastAPI`；`AuthService.login(username: str, password: str, source: str) -> AdminSession`；`.require(session_id: str) -> AdminSession`；`.logout(session_id: str) -> None`；`.reset_password(password: str) -> None`。管理路由 `/admin/login`, `/admin/logout`, `/admin/cards`, `/admin/audit`；写入 `/admin/cards/generate` 与 `/admin/cards/{id}/{action}`。

- [ ] 写失败测试 `test_api_two_devices_and_nonce_signed`、`test_expired_authentic_credential_can_refresh_after_renewal`、`test_unauthenticated_and_csrf_write_denied`、`test_session_idle_absolute_and_reset`、`test_login_rate_limits_do_not_trust_forged_forwarded_ip`：

```python
self.assertEqual(unsigned_admin_write.status_code, 403)
self.assertEqual(reply_payload["nonce"], request_nonce)
self.assertEqual(renewed_payload["result"], "allow")
self.assertFalse(session_valid_after_password_reset)
```

- [ ] 分别运行 `python -m unittest discover -s tests -p test_license_api.py -v` 与 `python -m unittest discover -s tests -p test_license_auth.py -v`，确认 RED。
- [ ] 实现认证、受限单管理员初始化/重置 CLI、Argon2id、服务端随机会话和 CSRF token、登录限流、输入上限、请求体上限、无调试错误响应、管理响应 `Cache-Control: no-store`。Cookie 为 Secure/HttpOnly/SameSite=Lax，空闲 1800 秒、绝对 28800 秒。
- [ ] 实现 activate/check API：旧凭证先验签和身份，再按数据库最新状态评估期限；校验接口不能因凭证旧期限已过直接禁止服务端续期恢复。每次当前响应签 nonce；卡密与令牌不写日志，健康接口只返回可用状态。
- [ ] 重跑两组测试及协议/数据库测试，全 PASS；验证 429 和 5xx 不伪装成签名授权拒绝。

## Task 4: 中文管理员网站

**Files:** Create `license_server/templates/login.html`, `license_server/templates/cards.html`, `license_server/templates/card_detail.html`, `license_server/templates/generated.html`, `license_server/templates/audit.html`, `license_server/static/admin.css`, `license_server/static/admin.js`, `tests/test_license_admin_ui.py`.

**Interfaces:** 消费任务 3 同源管理路由；模板仅展示 CardView 和生成当次 GeneratedBatch；日期输入/显示 Asia/Hong_Kong，服务端存 UTC。

- [ ] 写失败测试 `test_admin_generate_edit_revoke_unbind_archive_flow`、`test_plaintext_only_once_and_batch_export`、`test_note_html_escaped`、`test_month_year_custom_days_and_expiry_timezone`：

```python
self.assertIn(generated_card, generation_response.text)
self.assertNotIn(generated_card, later_list_response.text)
self.assertNotIn("<script>alert(1)</script>", rendered_note)
self.assertEqual((month_days, year_days), (30, 365))
```

- [ ] 运行 `python -m unittest discover -s tests -p test_license_admin_ui.py -v`，确认 RED。
- [ ] 实现登录、卡密筛选分页、生成/当次下载、详情修改、续期、停用恢复、解绑、归档确认、审计页；登录或权限过期显示清晰提示。禁用空批次、负期限与非法日期，避免错误返回后继续展示上一批明文。
- [ ] 重跑测试 PASS，在仅监听回环的本地测试服务使用浏览器核对完整管理流程及桌面/窄屏布局，使用临时测试管理员与随机测试卡密，不用生产账号。
- [ ] 验证完整码不进入 URL、浏览器持久化存储或后续接口；报告一次展示仅减少暴露，并不能阻止截图复制。

## Task 5: 独立桌面联网授权模块

**Files:** Create `online_license.py`, `tests/test_online_license.py`; Modify `requirements-python.txt`, `requirements-build.txt` only for client dependencies.

**Interfaces:** `LicenseTransport.post(path: str, body: dict) -> dict`（HTTPS 校验、超时 5 秒、拒绝跨源重定向）；`OnlineLicenseClient(data_dir: Path, device_id: str, public_keys: dict, transport: LicenseTransport, clock: Clock)`；`.activate(card: str) -> LicenseStatus`；`.verify_saved() -> LicenseStatus`；`.refresh() -> LicenseStatus`；`.snapshot() -> LicenseStatus`；`.start_background(on_change: Callable[[LicenseStatus], None]) -> None`；`.close() -> None`。GUI 负责线程队列，不在模块里操作 Tk。

- [ ] 写失败测试 `test_offline_cache_expiry_and_first_activation`、`test_valid_denial_sticky_on_network_failure`、`test_old_success_cannot_override_new_denial`、`test_nonce_wrong_signature_and_device`、`test_clock_rollback_requires_confirmation`、`test_expired_cache_renewal_refresh`：

```python
self.assertFalse(first_activation_without_server.allowed)
self.assertTrue(valid_cache_during_outage.allowed)
self.assertFalse(status_after_denial_then_outage.allowed)
self.assertFalse(status_after_out_of_order_success.allowed)
```

- [ ] 运行 `python -m unittest discover -s tests -p test_online_license.py -v`，确认 RED。
- [ ] 实现 `online_license.json` 独立原子存储、不保存卡密明文、严格凭证和 envelope 验证、nonce/request generation 顺序保护，拒绝状态不能因旧响应覆盖。缓存损坏不授予权限，不修改旧版 license.json。
- [ ] 实现 600 秒正常后台校验、失败 30/60/120/300/600 秒退避；刷新调用串行且合并，关闭中不更新已销毁窗口。证书或签名失败按无法确认提示，已有有效凭证仍遵守断网规则。不得引入 TLS 绕过。
- [ ] 实现 UTC 上限和单调时间约束；按测试时钟验证当前进程时间流逝，永久凭证同样保留已知拒绝状态。启动时不在 GUI 主线程执行磁盘之外的联网工作。
- [ ] 重跑测试 PASS，核对本地文件、异常和日志不含卡密明文。

## Task 6: 激活窗口、任务授权与浏览器保留

**Files:** Modify `dy_grab_gui.py` at `ActivationDialog`（约 283 行）、`AutomationRunner`（约 817 行）、`App.__init__`（约 3241 行）、`App.start_task`（约 3897 行）、`main`（约 4049 行）；Create `tests/test_license_gui.py`, `tests/test_license_task_guard.py`；Modify `用户使用说明.md`, `README.md`.

**Interfaces:** `ActivationDialog(local_client, online_client)` 明确两种入口；`App(license_controller=None)` 保留测试可构造形式；`AutomationRunner(..., license_status=None)` 添加可选快照读取函数兼容现有测试。新增 `AuthorizationExpired(GracefulStop)` 与 `assert_purchase_authorized()`，只读取状态与单调时间，绝不网络调用。

- [ ] 写失败测试 `test_legacy_saved_license_unchanged`、`test_failed_online_never_auto_falls_back`、`test_gui_remains_responsive_while_transport_blocks`、`test_revocation_retains_browser_during_captcha`、`test_revocation_after_submit_keeps_readonly_ack`、`test_explicit_stop_still_closes`：

```python
self.assertEqual(legacy_file_before, legacy_file_after)
self.assertFalse(online_failure_uses_legacy_without_explicit_selection)
self.assertEqual(payment_clicks_after_revocation, payment_clicks_before_revocation)
self.assertFalse(context_closed_by_license_expiry)
```

- [ ] 分别运行 `python -m unittest discover -s tests -p test_license_gui.py -v` 与 `python -m unittest discover -s tests -p test_license_task_guard.py -v`，确认 RED。
- [ ] 改造窗口与 main，通过 Tk queue/after 发布异步结果，已有旧版激活可继续启动且不自动启用联网模式；模式选择独立记录，不更改旧版卡密逻辑或读取管理员明文文件。
- [ ] 在任务启动及购买安全检查点读取状态：拒绝阻止新增购买，使用独立授权失效原因并设置 retain_browser，不设置用户 stop_event；提交后的订单确认继续只读，人工验证浏览器保留。explicit stop 保持原行为。
- [ ] 更新帮助及边界提示：无限断网、到期、解绑旧机残留、离线入口不可远程管理、机器指纹非不可破解。日志仅状态变化与低频失败摘要，不打印卡密或设备 ID。
- [ ] 运行新增测试和全部现有回归 `python -m unittest discover -s tests -v`，全部 PASS；在本地商品模拟里给远程校验注入延迟/超时，记录开售→点击分布及网络调用位置，不用真实直播间。

## Task 7: 部署材料、运维与可回滚预检

**Files:** Create `license_server/Dockerfile`, `license_server/compose.yaml`, `license_server/.env.example`, `license_server/deploy/license.nginx.conf`, `license_server/deploy/README.md`, `license_server/deploy/preflight.sh`, `tests/test_license_deploy.py`.

**Interfaces:** CLI `python -m license_server.cli init`, `reset-admin`, `backup`, `restore`；所有生产凭据交互输入/从受限文件加载。`preflight.sh` 只检查目标环境并报告，不修改博客、安装软件或打印容器环境变量。

- [ ] 写失败测试 `test_compose_no_public_backend_or_database_ports`、`test_nginx_exact_license_domain_only`、`test_missing_key_fail_closed_and_restart_persists`、`test_backup_restore_audit_and_card_bindings`：

```python
self.assertNotIn("ports:", auth_service_config)
self.assertIn("server_name license.txblog.cn;", nginx_config)
self.assertNotIn("server_name txblog.cn", nginx_config)
self.assertEqual(restored_binding, original_binding)
```

- [ ] 运行 `python -m unittest discover -s tests -p test_license_deploy.py -v`，确认 RED。
- [ ] 实现非 root 容器、持久数据/只读秘密挂载、健康检查、单进程服务、版本锁定依赖、自有镜像；Compose 仅声明专用授权网络，现有 Nginx 加入网络的步骤独立说明。
- [ ] 文档明确备份、数据库一致性备份、TLS 证书取得/续期、云防火墙 443、管理员重置、密钥保全、迁移与回滚。生产 Nginx 挂载和 Compose 路径未检查前不生成覆盖式自动部署命令。
- [ ] 运行材料测试 PASS；本地若 Docker 可用则临时隔离容器验证重启/备份恢复，若不可用明确记录待服务器验证，不把静态测试称为容器实测。

## Task 8: 生产接入、发行与最终验收

**Files:** Modify `dy_live.spec`（仅必要打包设置）, `build/verify_packaged_source.py`（新增协议和授权模块校验）；Create `build/联网授权交付报告.md`；Deliver `dist/DYLiveAssistant/` 完整发行及部署材料。

**Interfaces:** 使用前七任务提供的模块与部署文件；服务器签名公钥通过用户提供的公钥文件接入客户端固定配置，私钥始终留在服务器。

- [ ] 执行新模块与全量回归；记录独立审查发现与修复，不用旧测试数量作为新测试已通过的证据。
- [ ] 通过用户 Xshell 只读检查 Nginx 配置、挂载/网络/Compose 路径和 443，占用冲突则停止部署步骤并报告；避免输出完整环境、密码或 TLS 私钥。
- [ ] 准备具体补丁与备份/回滚命令，由用户在服务器初始化管理员密码、摘要秘密和签名密钥后再启动服务；用户选择可重建前端容器的时间后接入域名并校验公网 TLS。新增服务部署失败恢复新增配置，不覆盖博客数据。
- [ ] 使用专用测试卡密完成网站和激活端验收：生成、两设备指纹并发、续期、停用、解绑、断网、重启及原博客域名访问。提供旧电脑长期断网不即时失效的真实提示。
- [ ] 在构建配置中固定真实服务器公钥，运行 `python -m PyInstaller --noconfirm --distpath build/release dy_live.spec`，再运行 `python build/verify_packaged_source.py`，所有嵌入模块与源码匹配。
- [ ] 先核实 DYLiveAssistant.exe 没有运行，完整备份旧 dist 到新的带日期发行备份目录，再替换完整发行目录内容，不能只拷单个 EXE 遗漏新依赖；不删除用户 profile 或授权记录。
- [ ] 启动新版进行激活/主界面 smoke，使用临时测试资料，不绕过激活、不自动真实下单。记录 EXE SHA256、测试命令及实际结果，注明任何尚未执行的生产或 Docker 测试。
- [ ] 输出中文交付报告和回滚说明：网站实际可访问地址、卡密操作方式、旧版离线边界、无限断网风险、密码由用户自行设置。若生产访问或密钥接入缺失，报告未完成并保留原发行，不宣称联网交付完成。

## Execution and Approval

建议选择 Native：当前会话由主代理按任务逐项实施，接口集中便于控制旧版兼容性，最后安排独立 reviewer 进行整体审查。也可以选择 Subagent-driven：每任务由独立执行代理实现并逐任务审查，消耗更多上下文。

审批前仅本计划与设计文档有新增；计划审批不代表服务器已能安全直接操作。生产操作按实际现场检查与用户提供的 Xshell 执行结果推进，不能请求或假设持有用户 SSH 密钥。
