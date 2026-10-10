# 6.1.3 可靠更新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 安全原位更新、可用的新目录备用安装及后续文件级增量升级，覆盖客户截图对应的失败条件。

**Architecture:** 保留协议 1 完整包；协议 2 用签名增量方案和完整目标目录校验组装暂存文件，再沿用五根事务安装。无法安全替换时保留原版，提供经验证的新目录完整安装；不通过忽略保护来伪造更新成功。

**Tech Stack:** Python、unittest、Win32 ctypes、Tkinter、Ed25519、ZIP、PyInstaller、Ubuntu Docker Nginx。

**Spec:** `docs/superpowers/specs/2026-10-10-locking-incremental-update-design.md` 第 3—6 节。锁单独立计划为 `2026-10-10-locking-ui.md`。

## Global Constraints

- 正式版本 6.1.3；基线 6.1.2；6.1.0—6.1.2 首次升级仍全量，未来增量先支持一个正式基线。
- 五管理根保持不变，完整目标暂存不是服务器完整 ZIP，绝不能跳过验证或冒用原 ZIP 哈希。
- 同源官方 HTTPS、签名、路径/链接/数量/大小限制、防回放、备份和启动确认必须保留。
- 用户手动确认；完整回退另行确认下载量；无强杀、自动提权、关闭杀毒、自动下单。
- 不复制/删除账号资料或激活，隐私日志本地脱敏；客户文件不是实施前提。
- 没有 Git；每任务基线快照、红/绿记录替代提交；发布前完整 dist 备份。
- 新路由先备份及 nginx 检查；若要重建博客前端另行取得中断许可。

## Review Focus

- 空白、带空格、自定义资料路径：任务 1 同登录解析并保护真实资料。
- 权限不同但同名实例：任务 1 保持拦截并给具体原因，不误报已退出。
- 断线续传收到完整 200：任务 2 从零重启，不拼接响应。
- 组装期间源文件变化：任务 4 验证复制后哈希，不硬链接。
- 新目录位于旧目录或资料目录内：任务 5 拒绝相互嵌套，不覆盖旧程序或用户资料。

### Task 1: 目录与进程诊断

**Files:** Modify `dy_grab_gui.py:update_protected_paths`、`update_process.py`、`update_client.py`、`dy_live_updater.py:show_result`；Create `update_diagnostics.py`、`tests/test_update_diagnostics.py`；Extend `tests/test_update_exit_race.py`。

**Interfaces:** `resolve_profile_directory(value: str) -> Path` 供启动和更新共用；`UpdateDiagnosticLog(data_dir: Path)` 的 `record(stage: str, fields: dict) -> None`、`export(destination: Path) -> Path`，仅允许固定字段白名单，最大 1 MiB、保留最近 3 文件。

- [ ] 添加失败测试：空白及空格路径都使用默认资料目录；真正资料与管理根重叠拒绝；失效 PID 跳过、活的不可查询 PID 拦截；未产生 journal 时不显示恢复命令；导出不含用户名、令牌、Cookie、卡密。
- [ ] 运行 `-m unittest tests.test_update_diagnostics tests.test_update_exit_race -v`，留存准确失败。
- [ ] 实现上述接口及阶段记录；进程身份查询失败保留具体 Win32 错误，不改变安全判定。主程序和 helper 错误均区分是否开始替换。
- [ ] 重跑上述测试与 `tests.test_updater_entry tests.test_update_process tests.test_update_transaction`，要求全部通过。
- [ ] 记录原截图两原因与本地复现的区别；不能断言客户每个具体原因已证实。

### Task 2: 有界下载与续传

**Files:** Modify `update_client.py:UpdateTransport`、`update_config.py`；Extend `tests/fixtures/update_server.py`、`tests/test_update_client.py`。

**Interfaces:** 保持 `download(manifest, output, cancel, progress)` 外部调用兼容；内部 `download_verified(path: str, size: int, sha256: str, output: Path, cancel, progress) -> Path` 用于两协议。连接 10 秒、空闲 30 秒、总时限 600 秒、最多 3 次重新连接；元数据仍受 2 MiB 限制。

- [ ] 添加慢流、暂停超过旧 0.5 秒、断线后正确 206、错误偏移/总量/编码、返回 200、超限、重定向、取消和哈希损坏测试；使用可注入时钟/短测试超时，不实际等待 600 秒。
- [ ] 运行 `-m unittest tests.test_update_client -v`，确认旧实现失败。
- [ ] 实现新连接续传及 `.part` 管理；200 截断重启；完成哈希正确后才标记可用。签名/哈希异常不当网络错误重试。
- [ ] 重跑客户端与协议测试；取消必须有界且不安装残包。记录速度、阶段和重试，不泄露代理认证信息。
- [ ] 保存测试证据。

### Task 3: 严格增量元数据和发行工具

**Files:** Create `update_incremental.py`、`tests/test_update_incremental.py`；Modify `tools/update_release.py`、`tools/update_publish.py`；Extend `tests/test_update_release.py`、`tests/test_update_publish.py`。

**Interfaces:** `IncrementalPlan` 不可变模型含产品/平台、base_version、target_version、sequence、target_manifest_sha256、package_path/size/sha256、changed_files；`parse_incremental_plan(raw: bytes, public_keys: dict, target_raw: bytes, base_version: str) -> IncrementalPlan`。签名索引含目标版本/序号及单一基线方案引用/hash；`build_incremental(base_dist: Path, target_dist: Path, target_manifest_raw: bytes, output_dir: Path) -> Path` 生成变化文件 ZIP 与待签名方案。

- [ ] 添加测试：新增/变化/删除文件的集合准确；错版本/序号/目标原始清单hash拒绝；清单外、路径穿越、大小写重复、链接和超限 ZIP 拒绝；全量协议仍通过。
- [ ] 运行增量、发行、发布测试，确认新增行为失败。
- [ ] 实现严格 schema、独立签名及变化包构建，保留完整清单与完整 ZIP。发布不可变资源及索引先于 latest；版本已存在不能覆盖。
- [ ] 重跑测试，注入每个发布阶段中断，旧 latest 始终指向可下载有效发行。
- [ ] 保存 schema 文档及测试证据；不上传私钥，不访问授权私钥。

### Task 4: 完整暂存与协议 2 helper

**Files:** Modify `update_client.py`、`update_process.py`、`dy_live_updater.py`、`update_transaction.py`；Extend `update_incremental.py`、`tests/test_update_incremental.py`、`tests/test_updater_entry.py`、`tests/test_update_transaction.py`。

**Interfaces:** 保持 `PreparedUpdate` 协议 1 字段兼容；新增单独 `PreparedIncrementalUpdate`，包含相同事务路径及目标清单、方案文件和增量包路径。`assemble_incremental(plan: IncrementalPlan, target_manifest, install_dir: Path, archive: Path, staged_dir: Path, cancel) -> Path`。helper `validate_request` 严格按 protocol 分支验证，不能向旧 job 混入新字段。

- [ ] 添加失败测试：复制未变化文件、替换变化文件、不复制已删除文件，全部暂存hash与目标一致；源文件在复制期间变化拒绝；缺失/修改基线要求重新确认全量；断电恢复无需联网。
- [ ] 运行增量、helper、事务测试，确认 protocol 2 失败而 protocol 1 基线通过。
- [ ] 实现独立组装及 helper 再验签/验目录；拷贝不是硬链接；协议 1 仍 validate_archive。保存足够签名材料供恢复验证，沿用五根快照与启动确认。
- [ ] 重跑协议 1/2 安装、文件锁、备份中断、无启动确认和恢复测试；所有用户文件哨兵必须不变。
- [ ] 保存证据及兼容矩阵。

### Task 5: 用户确认、备用完整安装和诊断入口

**Files:** Modify `update_ui.py`、`update_client.py`、`dy_grab_gui.py`、`用户使用说明.md`；Create `update_portable_install.py`、`tests/test_update_portable_install.py`；Extend `tests/test_update_ui.py`。

**Interfaces:** `install_full_to_new_directory(manifest, archive: Path, destination: Path, protected_paths: tuple[Path, ...]) -> Path` 验签/完整包验证后完整解压到用户确认的空白持久目录，不更改旧安装。`UpdateController` 提供全量/增量方案确认、进度、取消、诊断导出和备用安装入口。

- [ ] 添加测试：预检失败不下载；明确压缩预览临时目录拒绝原位更新但普通 AppData 不误拒；全量回退需要确认；新目录与原安装/资料互相嵌套、非空、只读均拒绝；新安装所有目标文件正确且原文件不变。
- [ ] 运行 UI/备用安装测试，确认新增行为失败。
- [ ] 实现备用安装和界面阶段提示；启动新版必须是用户操作并先退出旧版，不自动启动任务；原自定义资料路径给出明确保留说明。旧客户端另提供官方全量 ZIP 手动解压说明。
- [ ] 重跑测试和 GUI 实际布局检查；模拟拒绝/取消，不得残留已标记完成的半安装。
- [ ] 保存截图、用户操作最短步骤及隐私导出测试证据。

### Task 6: 实际 EXE、审查及发布

**Files:** Modify `app_version.py` 至 6.1.3；Create `build/update-6.1.3/` 专用验收脚本/证据；复用 `build/manual-update/acceptance.py`、`acceptance_failures.py`、`publish_dist.ps1` 和 `build/verify_packaged_source.py`，不覆盖旧证据。

**Interfaces:** 使用现有独立更新签名、公网固定主机指纹及凭证加载器；发行包输出 immutable `release/6.1.3`；私有下一版本测试包不得写入正式 stable。

- [ ] 完成锁单计划；运行 `-m unittest discover -s tests -v`，全通过后构建 helper/main，两次 PyInstaller 使用现有 spec 和 `build/manual-update/*` 工作路径。
- [ ] 校验源码与 EXE 内嵌代码一致及离线激活保持不变；实际 6.1.0/6.1.1/6.1.2 EXE 升级至候选 6.1.3，资料哨兵不变、无自动任务。
- [ ] 实际 6.1.3 向私有测试目标进行增量、基线损坏全量回退、新目录备用安装、文件占用、断电和启动未确认验收。记录下载字节/时长及锁单阶段 ms，不能宣称真实直播成功率。
- [ ] 使用 requesting-code-review 做一次独立审查；严重问题修复并重跑受影响测试，保留报告。
- [ ] 服务器备份工具/路由，检查配置；若需 frontend 重建先另问许可。部署精确白名单增量路由及兼容工具，检查博客/授权健康、私钥404及目录权限。
- [ ] 正式完整包签名发布并从官方 HTTPS 实际下载验签/哈希；旧协议仍能检查和取得完整包。6.1.3 为未来增量基线，不发布虚假 6.1.4。
- [ ] 用现有安全 dist 发布脚本保留完整 6.1.2 备份，替换五根及完整 ZIP，实际启动和正常关闭新版。交付下载链接、备用安装步骤、测试对比和已知限制。

## 审核与执行顺序

先批准本计划及独立锁单计划；按用户已选方式在本会话逐项实施，最后独立审查。更新任务 1—5 与锁单任务分别可测试，联合经过任务 6 才正式发布。当前仅文档变更，线上及 dist 仍是 6.1.2。
