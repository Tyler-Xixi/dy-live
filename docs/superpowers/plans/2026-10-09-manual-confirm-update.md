# 手动确认更新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给文件夹发行的 Windows EXE 增加用户确认更新、签名完整包、事务替换/恢复和明确发布流程，最后更新 dist 并验证正式更新源。

**Architecture:** 更新协议、下载、安装事务、独立更新程序和 GUI 分成独立模块；不修改购买核心。发布工具白名单打包，服务器独立签名，静态提供包及最后切换的索引。原位更新只处理程序文件，用户数据完全独立。

**Tech Stack:** 现有 Python 3.13、Tkinter、cryptography/Ed25519、unittest、urllib、zipfile、Windows ctypes、PyInstaller；服务器 Ubuntu 24.04、Docker/Nginx、现有 SSH/SFTP。不新增生产第三方依赖。

**Spec:** `docs/superpowers/specs/2026-10-09-manual-confirm-update-design.md`（用户已确认）。

## Global Constraints

- 未经确认不下载程序包、不关闭软件、不替换文件。无强制升级、静默安装或自动恢复任务。
- 更新不进入下单热路径，不中断秒杀、批量任务或登录。联网及旧离线激活保留。
- 首个支持更新版本为 6.1.0；三段数字版本；协议 1；Windows x64。
- 固定 HTTPS 源 `https://license.txblog.cn/updates/`；同源包仅位于 `/updates/releases/`。
- 清单最大 2 MiB，ZIP 最大 512 MiB，解压总大小最大 2 GiB，文件最多 15000。
- 检查请求限时 10 秒；下载有连接/读取超时和总时限 10 分钟；退出/启动确认各最多 30 秒。
- 仅管理 `DYLiveAssistant.exe`、`DYLiveUpdater.exe`、`_internal/`、`用户使用说明.md`、`release-info.json`。
- 不覆盖账号、浏览器资料、设置、激活信息或其他安装目录文件；重叠自定义资料必须拒绝更新。
- 独立更新签名私钥只在服务器非公开受限目录；不得复用授权私钥、加入发行包或输出秘密。
- 不自动提权、不按进程名杀进程、不带锁强行恢复、不自动递归清理历史备份。
- 不以本地测试冒充正式服务器上线或终端用户更新成功；不使用真实平台下单测试。
- 本目录无 Git：先复核，保存基线/哈希/完整发行备份和任务 ledger，不伪造提交。
- 当前普通 unittest 曾因 Tk 跨线程循环回收中断；保留原始失败证据，主线程回收测试器作为补充，不混淆两种结果。

## Review Focus

1. 用户刚确认更新，另一入口启动任务或验证浏览器仍存活：不得下载/关闭/替换；Task 5 覆盖。
2. 断电发生在文件移动与日志提交之间：独立恢复工具必须能还原，不依赖主 EXE 可启动；Task 3/4 覆盖。
3. 安装位于中文/空格路径、目录联接、只读位置或用户自定义 profile 重叠：不越界、不丢资料、不提权；Task 2/3 覆盖。
4. 第二个软件实例/更新程序、父 PID 被复用或新版仍活着但未应答：不误判退出、不强杀、不带锁回滚；Task 4 覆盖。
5. 最新版索引早于包上传完成、缓存旧索引或合法签名包被回退：不下载错误内容、不覆盖同版发布、不降级；Task 1/6 覆盖。

## 文件结构、公共模型与执行环境

- `app_version.py`：`APP_VERSION='6.1.0'`、`PRODUCT_ID='DYLiveAssistant'`、`PLATFORM='windows-x64'`。
- `update_config.py`：固定更新源、`UPDATE_PUBLIC_KEYS`、协议/大小/时限；只有公共配置。
- `update_protocol.py`：签名封装及不可变模型、版本/路径/ZIP/清单验证。
- `update_client.py`：只读检查、确认后下载、取消、进度和更新状态原子保存。
- `update_transaction.py`：安装目录、文件锁、事务日志、程序文件备份/替换/恢复。
- `update_process.py`：Windows 精确进程身份与创建时间、握手、启动确认。
- `dy_live_updater.py` / `dy_updater.spec`：独立 onefile 更新/恢复入口，不依赖安装中的 `_internal`。
- `update_ui.py`：更新说明/进度窗口与主线程状态；App 仅接入入口及忙碌检查。
- `tools/update_release.py`：白名单打包及清单生成；`tools/update_publish.py`：服务器受控签名/原子公开，不提供 HTTP 写入接口。
- `updates/deploy/README.md`、`updates/deploy/nginx.location.conf`：独立静态挂载及现场部署说明。
- 各任务 `tests/test_update_*.py`；`tests/fixtures/update_server.py` 为自有本地响应服务。
- `build/manual-update/`：基线、源码范围对比、完整回归/打包验收工具和证据。

执行前使用 `.superpowers/sdd/2026-10-09-manual-confirm-update/` 独立 ledger 与 venv。依赖来自现有 `requirements-python.txt`、`requirements-build.txt`、`license_server/requirements.txt`，测试如需要 Paramiko 只装专属 venv；不读取其他任务的私密文件。shell 每次显式赋值 `$updatePython` 为该 venv 的 Scripts/python.exe。

每任务 RED → 最小实现 → GREEN → 保存证据。全量命令 `& $updatePython -m unittest discover -s tests -v`；若 Tk 回收再次中断，记录后用专属 `build/manual-update/run_suite.py` 在主线程每个测试后 gc.collect 跑同一 discovery，不能删除或跳过用例。任何失败必须按名称记录并解释。

原有 GUI/source/dist 先快照；不隔离到不存在的 Git worktree，不改无关文件。模块测试用临时安装/应用数据目录和临时测试签名钥；测试钥不进入产品配置。正式签名公钥安装前需明确服务器初始化范围，不能把空公钥或测试公钥当正式发行。

---

### Task 1: 版本与签名更新协议

**Files:** Create `app_version.py`, `update_config.py`, `update_protocol.py`, `tests/test_update_protocol.py`。

**Interfaces:**
- frozen `ReleaseFile(path: str, size: int, sha256: str)`。
- frozen `UpdateManifest(protocol: int, product: str, platform: str, version: str, sequence: int, published_at: str, notes: str, package_path: str, package_size: int, package_sha256: str, files: tuple[ReleaseFile,...], updater_protocol: int)`。
- `version_tuple(value: str) -> tuple[int,int,int]`，只接受非负整数规范三段格式。
- `signed_bytes(payload: dict) -> bytes`：UTF-8、sort_keys、紧凑 separators、ensure_ascii=False；前缀 `b'DYLiveAssistant:update-manifest:v1\x00'`。
- `parse_manifest(raw: bytes, public_keys: dict[str,bytes], highest_sequence: int=0) -> UpdateManifest`；严格封装字段 `key_id,payload,signature`，严格 payload 同名字段，不把签名字符串直接当可信。
- `is_newer(manifest: UpdateManifest, current_version: str) -> bool`；同版本为正常“无更新”，旧版拒绝升级，不把最新版和当前版相同视为网络错误。
- `validate_archive(zip_path: Path, manifest: UpdateManifest) -> None`、`extract_verified(zip_path: Path, destination: Path, manifest: UpdateManifest) -> None`。错误 `UpdateError(ValueError)`。

- [ ] 写测试 `test_valid_manifest_and_numeric_order`：6.10.0 > 6.9.9，同版无更新；有效临时签名返回所有固定字段。
- [ ] 写 `test_rejects_unsigned_tampered_wrong_product_platform_or_replay`：伪造、错误密钥、重复 JSON key、未知协议、较低 sequence 拒绝；原应用数据不变。
- [ ] 写 `test_archive_rejects_traversal_links_duplicates_bombs_and_missing_files`：参数表覆盖绝对/UNC/盘符/ADS/设备名/路径穿越、大小写冲突、链接、清单缺失及多余文件、超过固定大小/数量、逐文件哈希不符。
- [ ] Run `& $updatePython -m unittest discover -s tests -p test_update_protocol.py -v`，确认 RED 是新增协议缺失或拒绝分支缺失。
- [ ] 实现签名先于字段信任；包固定路径形如 `/updates/releases/6.1.1/DYLiveAssistant-6.1.1-windows-x64.zip`。验证 entry 和父路径无链接后解压，拒绝异常大小且按实际读取字节计上限；所有受管根完整检查，不用“只要 exe 存在”作为完整包判断。
- [ ] 同命令 GREEN；运行项目全量，保存 ledger 和文件哈希。

### Task 2: 检查、下载与取消

**Files:** Create `update_client.py`, `tests/test_update_client.py`, `tests/fixtures/update_server.py`。

**Interfaces:**
- `UpdateStateStore(data_dir: Path).load() -> dict` / `.save(highest_sequence: int, last_result: str='') -> None`；文件 `update_state.json`，损坏不静默重置防回退计数，提示修复但不影响旧软件。
- `UpdateTransport(origin: str, *, opener=None)`；`.fetch_manifest(cancel: threading.Event) -> bytes`、`.download(manifest: UpdateManifest, output: Path, cancel: threading.Event, progress: Callable[[int,int],None]) -> None`。正式源固定；仅测试注入 opener，不新增产品 HTTP 绕过开关。
- `UpdateClient(data_dir: Path, public_keys: dict[str,bytes], transport: UpdateTransport)`；`.check(current_version: str, cancel: threading.Event) -> UpdateManifest | None`、`.prepare(manifest: UpdateManifest, install_dir: Path, protected_paths: tuple[Path,...], cancel: threading.Event, progress: Callable[[int,int],None]) -> PreparedUpdate`。
- frozen `PreparedUpdate(transaction_id: str, work_dir: Path, archive: Path, manifest_file: Path, staged_dir: Path, install_dir: Path)`；全部绝对、受限专属路径。

- [ ] 写 `test_check_never_downloads_package`：发现/忽略新版仅获取索引；服务端包请求数为 0，安装目录哈希不变。
- [ ] 写 `test_confirmed_download_cancel_timeout_truncation_and_hash_failure`：取消/超时/截断/错误 Content-Length/哈希不符均不替换；流式实际字节限额，回调整数已下载量不超过总量。
- [ ] 写 `test_redirect_and_credentials_rejected`：跨源/HTTP/带 userinfo/含查询凭据/错误版本路径拒绝；HTTPS 校验不得被禁用。
- [ ] 写 `test_unicode_space_path_and_protected_profile`：中文空格目录成功准备；profile 在 `_internal` 内、路径联接或空间不足拒绝，默认 APPDATA/未知安装文件内容不变。
- [ ] Run `& $updatePython -m unittest discover -s tests -p test_update_client.py -v`，确认 RED；实施上述接口，用单调时钟总时限、有限读取、临时文件原子状态保存；记录最高验证序号不等于自动安装。
- [ ] 同命令 GREEN + 全量；保留失败暂存证据，不使用 broad cleanup。

### Task 3: 同卷事务、备份与可重入恢复

**Files:** Create `update_transaction.py`, `tests/test_update_transaction.py`。

**Interfaces:**
- `InstallationLock(install_dir: Path)` context manager：命名互斥与独占锁文件绑定规范安装路径，跨实例/会话竞争不得同时移动文件。
- `UpdateTransaction(prepared: PreparedUpdate)`；`.preflight(protected_paths: tuple[Path,...]) -> None`、`.install() -> Path` 返回完整备份路径、`.rollback() -> None`、`.commit() -> None`。
- `recover_transaction(journal_path: Path) -> str` 返回 `restored` / `already-restored` / `committed`，未知/受污染日志拒绝；不用任意日志路径直接当删除/移动授权。
- journal 协议1，阶段 `prepared, backing_up, replacing, installed, starting, committed, restoring, restored, awaiting_manual_close`；每一项记录源、目标、旧/新哈希和移动 intent/completed。专属同卷目录为安装根内 `.dy-update-transactions/<transaction_id>`，不移动整个安装根。

- [ ] 写 `test_replace_program_files_preserves_unknown_and_user_data`：五种根更新，新旧包依赖不混合；未知文本/旧 ZIP、外部账号/激活/profile 哈希相同。
- [ ] 写 `test_fault_at_every_move_restores_complete_old_version`：对每个根的 intent 写入、rename 前后、completed 写入、新文件校验注入失败；恢复后旧文件/目录完整哈希等同基线。
- [ ] 写 `test_recovery_is_idempotent_after_interrupted_recovery`：恢复再中断可重试，且 committed 不误回退；磁盘/权限失败不删除已有备份。
- [ ] 写 `test_linked_directory_invalid_journal_or_second_installer_cannot_escape`：链接、目录冲突、坏日志、错误安装身份/路径、并发拒绝；其他目录哨兵不变。
- [ ] Run `& $updatePython -m unittest discover -s tests -p test_update_transaction.py -v`，确认 RED；最小实现显式受管白名单和持久化逐项日志，用 os.replace/受限路径重命名，不先删 `_internal`；备份文件完整校验后才开始新文件移动。
- [ ] GREEN + 全量，记录故障矩阵和跨进程锁证据。

### Task 4: 独立更新程序与启动确认

**Files:** Create `update_process.py`, `dy_live_updater.py`, `dy_updater.spec`, `tests/test_update_process.py`, `tests/test_updater_entry.py`; Modify `dy_grab_gui.py:main` 仅接入确认；Create `build/manual-update/verify_updater.py`。

**Interfaces:**
- frozen `ProcessIdentity(pid: int, created_at: int, executable: str)`，created_at 为 Windows FILETIME 整数；`process_identity(pid: int) -> ProcessIdentity | None`。
- `launch_updater(prepared: PreparedUpdate, parent: ProcessIdentity) -> UpdateLaunch`；frozen `UpdateLaunch(process: subprocess.Popen, ready_file: Path, request_file: Path, token: str)`。
- `wait_for_installation_exit(parent: ProcessIdentity, install_dir: Path, timeout: float=30) -> bool`，按 PID+创建时间+完整镜像路径检查，其他同目录实例亦需退出。
- `acknowledge_startup() -> None` 只在校验命令参数/事务令牌/新进程身份后回执；激活/主窗口第一次 after_idle 回执，不等输入卡密，不绕过授权。
- updater CLI `--request <job.json>` 或 `--recover <journal.json>`；job协议1含规范 PreparedUpdate 路径、父身份及随机令牌。不接受清单指定安装根；回查父镜像与目录，并保存绑定证据供重启恢复。

- [ ] 写 `test_other_instance_or_reused_pid_blocks_replace`、`test_parent_exit_timeout_keeps_old_files`，用真实自有辅助进程，断言无 kill、无新文件替换。
- [ ] 写 `test_ready_handshake_precedes_app_close_and_helper_rechecks_signature`：helper 未 ready 不退出旧应用；请求伪造、签名/哈希改变拒绝；argv 中文空格路径无 shell 拼接。
- [ ] 写 `test_new_process_ack_failure_restores_or_waits_for_manual_close`：新程序退出恢复；仍活着则 awaiting_manual_close，用户关闭后恢复；不得强杀。
- [ ] 写 `test_recovery_runs_when_main_exe_is_missing`：安装 EXE 不可用，外部 helper 从签名/哈希可信副本恢复；每阶段断电 journal 可重试。
- [ ] Run `& $updatePython -m unittest discover -s tests -p test_update_process.py -v` 和 `& $updatePython -m unittest discover -s tests -p test_updater_entry.py -v`，确认 RED；实现 onefile helper 含自身 runtime/public keys，不依赖待替换 `_internal`。从专属受限 work_dir 启动；提供同目录“恢复更新.cmd”仅用绝对已验证 helper 路径，无不可信 shell 参数；保留外部 helper 不自动删除。
- [ ] GREEN；打包 helper 并在无 Python PATH 的临时安装中测试独立运行、正常/失败启动确认。激活没有授权时仍可完成启动确认，但不执行任何任务。

### Task 5: 手动确认界面、进度和互斥

**Files:** Create `update_ui.py`, `tests/test_update_ui.py`; Modify `dy_grab_gui.py:App.__init__,build_ui,start_task,launch_login,start_network_test,on_close`；Modify `用户使用说明.md`。

**Interfaces:**
- `UpdateController(app, client: UpdateClient)`；`.check(manual: bool=False) -> None`、`.request_download(manifest: UpdateManifest) -> None`、`.cancel() -> None`、`.busy() -> bool`、`.close() -> None`。
- `UpdateDialog(master, manifest: UpdateManifest, current_version: str, on_confirm: Callable[[],None], on_defer: Callable[[],None])`；Tk 回调只在主线程。
- `App.update_blocked() -> bool`：worker存活、登录 session 浏览器生命周期未结束、网络 busy、closing、更新下载/接管。复用已有判断，不能把 browser 暂停人工验证当空闲。
- 无 worker 强制关闭；helper ready 后才通过已有安全退出入口保存偏好，失败保留旧窗口。onefile主确认令牌与正式 helper 使用 Task4 接口。

- [ ] 写 `test_defer_makes_zero_package_requests_and_manual_check_can_reopen`；启动空闲仅一次，忙碌跳过，不周期轮询；自动失败不弹窗，手动失败有提示。
- [ ] 写 `test_busy_or_became_busy_after_prompt_rejects_download` 参数化秒杀/普通批量/多账号/人工验证浏览器/登录/测速/退出；用户提示不自动 stop，安装目录不变。
- [ ] 写 `test_download_blocks_new_tasks_and_cancel_releases_controls`；保持真实按钮变量/回调链，检查、下载和窗口关闭互斥，线程不直接操作 Tk。
- [ ] 写 `test_source_mode_cannot_install_and_network_failure_keeps_offline_activation`；确认 App 正常可用，激活文件字节不变。
- [ ] Run `& $updatePython -m unittest discover -s tests -p test_update_ui.py -v`，确认 RED；实现 header中版本/检查按钮、轻量说明窗口与进度；启动检测 after_idle 经 queue 交回，所有购买入口读取内存 busy，不新增购买 HTTP/磁盘。
- [ ] GREEN + 全量；最小980x720/默认1180x860、高 DPI实际截图检查，切换保持原固定布局；手册简短说明选择/取消/恢复和首次手动安装。

### Task 6: 白名单发行、服务器签名和静态源

**Files:** Create `tools/update_release.py`, `tools/update_publish.py`, `updates/deploy/README.md`, `updates/deploy/nginx.location.conf`, `tests/test_update_release.py`, `tests/test_update_publish.py`; Modify `dy_live.spec`, `build/verify_packaged_source.py`；按现场结果新增独立 Compose override/Nginx 补丁，不覆盖博客原配置。

**Interfaces:**
- `build_release(dist_dir: Path, output_dir: Path, version: str, sequence: int, notes: str) -> Path` 返回新版本目录，含 ZIP 和未签 payload；只打五类根，ZIP不嵌入自己的清单造成循环哈希。
- 服务器CLI `init-key --secrets-dir <path>`：目录不存在/没有key才创建独立Ed25519，权限0600/目录0700；只输出公钥 JSON，已有key不覆盖。
- `publish_release(source_dir: Path, public_root: Path, secrets_dir: Path) -> Path`：锁内校验版本/sequence严格递增，拒绝版本目录已存在，验证包后签名，再原子替换索引，旧索引单独备份。
- 公开根 `/opt/dy-updates/public`；秘密 `/opt/dy-updates/secrets`；Nginx只读挂载 public，绝不挂载 secrets。路径 `/updates/stable/latest.json`、`/updates/releases/<version>/<zip>`。

- [ ] 写 `test_bundle_excludes_profiles_license_secrets_old_zip_and_diagnostics`：在 dist放诱饵，包只包含五类，源码/测试钥不打包；元数据版本与嵌入 APP_VERSION 相同，不从旧 ZIP 猜版本。
- [ ] 写 `test_publish_checks_complete_bundle_before_latest_switch`：每上传/校验/签名阶段失败，旧索引原字节不变；并发发布串行；已存在版本/旧序号不覆盖；缓存旧索引客户端不会降级。
- [ ] 写 `test_wrong_key_or_origin_and_inaccessible_file_prevent_publish`：签名后客户端验证、下载完整hash一致；init不更改既有独立key，不读取授权私钥。
- [ ] Run `& $updatePython -m unittest discover -s tests -p test_update_release.py -v` 和 `& $updatePython -m unittest discover -s tests -p test_update_publish.py -v`，确认 RED；实现无 shell 字符串拼接的 CLI，普通打包不自动上传/公开。正式 publish明确执行，服务端密钥不通过SSH输出回本机。
- [ ] GREEN + 全量；更新主 spec 包含已构建 helper/release-info，检测公钥是正式导出值非空非测试值。先本地测试钥端到端，再现场公钥重建最终包。
- [ ] 服务器只读预检确认Nginx/挂载/DNS/证书/磁盘空间/现有API和博客健康；现场新增配置先备份，若需 frontend重建单独请求短暂中断许可。不能预先假定之前的挂载仍完全相同。
- [ ] 仅部署静态更新目录/独立key/受限发布工具；校验Nginx后reload，健康检查原blog两域名、授权health/admin、更新TLS/缓存/目录不可浏览/秘密不可下载。未拿到结果不算现场完成。

### Task 7: 真实打包验收、审查和交付

**Files:** Create `build/manual-update/acceptance.py`, `build/manual-update/verify_scope.py`, `build/manual-update/run_suite.py`, `build/manual-update/publish_dist.ps1`, `build/manual-update/交付报告.md`; Modify dist仅经过完整备份和校验发布。

**Interfaces:** acceptance CLI `--old-dist <path> --new-dist <path> --evidence <dir>`；只接受自有临时安装/APPDATA。verify_scope AST 比较固定购买方法和 AutomationRunner 全体，许可主界面、版本及启动确认改动；不能忽略购买差异。

- [ ] 写失败验收：同一临时目录安装带更新旧构建，再用完整新构建触发用户确认的正常更新；断言版本、文件哈希、启动确认、用户资料不变、任务未自动启动；不能伪造新版成功信号。
- [ ] 写失败包/文件锁/断电阶段的实际 helper 接管验收，断言旧完整目录恢复、新进程存活时不强杀，两个实例不会一起安装；网络错误/暂不更新时0程序包请求。
- [ ] 完整项目回归，两种测试器结果如实记录；打包一致性工具覆盖所有更新模块、主EXE、独立helper和旧离线验证器；真实EXE隔离激活启动/关闭；可用事件循环回执后才认定启动通过。
- [ ] 单次最终独立审查代码、协议、事务故障矩阵与现场证据；修复Critical/Important后重新验证受影响用例和完整suite，保留Minor和范围外事项，不无故重复审查。
- [ ] 在正式源提供6.1.0 首个完整包/签名索引；测试客户端当前6.1.0 显示最新，无伪造6.1.1 的公开空升级。后续升级通过局部自有服务的实际第二构建验证，不为测试发布假版本到生产。
- [ ] 用户关闭旧EXE后完整备份 dist（包括未知文件），替换受管发行、同步手册，1810等数量只作测量不硬编码；核验整包文件哈希和正式公钥，失败可恢复；旧ZIP保留并明确其不是新版分发包。
- [ ] 输出完整新发行ZIP、更新发布工具/说明、管理员独立私钥保管与服务器路径说明（不含secret值）、启动检查/回归/现场结果，以及首次老用户需手动安装一次说明。发布撤回保留索引历史，但不触发客户端降级。

## 计划自审与执行门槛

- Spec1–3由Task5/7覆盖；4–6由Task1/2/6覆盖；7由Task3/4覆盖；8由Task6/7覆盖；9全量、故障及打包验收由Task7覆盖。
- 所有签名验证使用同一 Task1模型，GUI/下载/helper/publish接口名称一致。不得把测试私钥或测试网络绕过打包为正式配置。
- 本计划在本会话逐项实施后只做一次最终独立审查，推荐沿用用户之前选择的本会话执行方式；若用户改选逐任务子代理则按对应技能执行。
- 本文为待用户确认的计划，不开始实现或连接服务器。确认后保持用户选择的执行方式；需要服务器短暂重建时另行确认。
