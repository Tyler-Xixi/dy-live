# 多账号依次批量下单 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给现有批量下单加入隔离登录资料的多账号顺序队列，保持单账号/秒杀不变，测试后更新dist EXE。

**Architecture:** 账号存储、队列和账号编辑窗口各用独立模块。AutomationRunner仅增加可选队列生命周期控制和只读结构化结果，购买核心及单账号返回值不变；GUI冻结配置并注入runner工厂，队列不依赖GUI或联网服务。

**Tech Stack:** Python 3.13、Tkinter/ttk、Playwright、unittest、JSON原子文件保存、PyInstaller；不新增生产第三方依赖。

**Spec:** `docs/superpowers/specs/2026-10-09-sequential-multi-account-design.md`（用户已确认）。

## Global Constraints

- 不做并发、不自动注册账号、不收集账号密码、不绕过验证码、限购或平台限制。
- 日常秒杀、详情页锁单和默认单账号流程保持原行为。多账号不是成功率保证。
- 移除账号仅移除注册表记录，并提示登录资料仍保留；不递归删除目录。
- 队列开始时冻结账号及商品配置，运行中不读取可变表单配置。
- 一个工作线程顺序执行，一次仅有一个账号浏览器。
- 任何账号异常停止整条队列，不跳过、不重试、不重复下单；第一版不自动恢复队列。
- 自动支付关闭时停止在当前订单支付前，不执行下一个账号。
- 购买热路径不新增HTTP、磁盘、签名校验；使用原内存授权快照。
- 不用真实抖音账号或真实支付测试；不改服务器授权服务及原用户资料。
- 本项目无Git：执行前再次确认；保留独立ledger、基线哈希、完整旧发行备份，不伪造commit或删除工作区。文件编辑用apply_patch。

## Review Focus

1. 默认资料目录与新账号路径碰撞、目录联接/符号链接：不能悄悄共享资料或越界；Task1覆盖。
2. 成功日志、run返回completed和实际成功订单数不一致：不能仅凭日志/返回值切换账号；Task2/3覆盖。
3. 当前账号浏览器未释放或用户正在核对订单：不得启动下一账号；Task2/5覆盖。
4. 切换窗口中点击停止、授权失效、重复启动：未开始账号不能抢跑；Task3/4覆盖。
5. 注册表损坏、持久化失败、备注含控制字符：不重置资料、不误报保存、不污染日志；Task1/4覆盖。

## 文件职责和测试环境

- `batch_accounts.py`：不可变账号/任务模型、注册表校验与保存、受限profile解析；不导入GUI。
- `batch_queue.py`：不可变执行结果及顺序调度，runner接口由工厂注入；不导入dy_grab_gui。
- `batch_accounts_ui.py`：账号管理弹窗及编辑状态；调用存储和主界面登录回调。
- `dy_grab_gui.py`：可选runner生命周期及结果、批量选择/登录/启动接入；不做其他重构。
- `tests/test_batch_accounts.py`、`test_batch_runner_lifecycle.py`、`test_batch_queue.py`、`test_batch_accounts_ui.py`：上述边界测试。
- `tests/test_multi_account_browser.py`、`tests/fixtures/batch_accounts.html`：自有网页/独立profile的真实浏览器验收。
- `build/verify_packaged_source.py`、新独立发布脚本：源码嵌入核对、备份/替换。
- `用户使用说明.md`及dist同名文件：增加简短多账号操作，不扩成冗长手册。

独立任务工作区 `.superpowers/sdd/2026-10-09-sequential-multi-account/`。先创建带system-site-packages的专属venv，再在其中安装`license_server/requirements.txt`与Paramiko供现有全量测试使用；不修改全局Python、不复用或改写其他计划的ledger/私密文件。若venv中缺少现有桌面测试依赖，安装`requirements-python.txt`。

命令中的`$py`指该venv的`Scripts/python.exe`；每个新shell调用显式重新定义。全量命令为`& $py -m unittest discover -s tests -v`，日志留本任务工作区并读取结尾、退出码和失败名称。先运行基线，预计235项通过；若数量变动先记录差异，不能把预计数当结果。

---

### Task 1: 隔离账号注册表

**Files:** Create `batch_accounts.py`; Test `tests/test_batch_accounts.py`。

**Interfaces:**
- `AccountRecord(account_id: str, name: str, selected: bool, buy_times: int, buy_quantity: int, login_saved: bool)`，frozen dataclass。
- `BatchJob(account_id: str, name: str, profile_dir: str, buy_times: int, buy_quantity: int)`，frozen dataclass。
- `AccountStore(data_dir: Path)`；`load() -> tuple[AccountRecord, ...]`、`save(accounts: tuple[AccountRecord, ...]) -> None`、`new_account(name: str) -> AccountRecord`、`jobs(accounts: tuple[AccountRecord, ...], default_profile: Path) -> tuple[BatchJob, ...]`。
- `AccountStoreError(ValueError)`；注册表`batch_accounts.json`，版本1，特殊原有账号ID为`legacy`；新账号ID为UUID十六进制，路径`data_dir/batch-account-profiles/<id>`。注册表不接受用户指定profile路径。

- [ ] 写失败测试：`test_roundtrip_and_order`保存两个选中账号，断言jobs顺序/数量；`test_remove_keeps_profile`移除记录后断言原目录文件仍在。
- [ ] 写边界测试：路径`../outside`/重复ID/链接目录/默认目录与新profile相同均拒绝；损坏JSON和未知版本报错且原文件字节不变；写失败不丢既有文件；legacy使用传入原路径且不复制；非法名称控制字符、非正整数、bool冒充整数拒绝。备注名1–60字符；允许重名但UI显示短ID区分。
- [ ] Run: `& $py -m unittest discover -s tests -p test_batch_accounts.py -v`。Expected: RED，缺少模块/接口。
- [ ] 最小实现上述接口；存储中只保存固定字段，原子replace，同级临时文件，错误明确上报；解析路径验证不创建/清理浏览器资料。
- [ ] 同命令GREEN；全量suite GREEN，ledger写命令/结果。无Git不执行提交。

### Task 2: 批量执行结果与队列浏览器生命周期

**Files:** Create `batch_queue.py`中的结果类型；Modify `dy_grab_gui.py:AutomationRunner`；Test `tests/test_batch_runner_lifecycle.py`。

**Interfaces:**
- `BatchOutcome(kind: str, confirmed_orders: int, reason: str)`，frozen；kind仅`completed/tested/awaiting_payment/stopped/failed`。
- `AutomationRunner(..., license_status=None, queue_lifecycle: bool=False)`；`run() -> str`保持旧返回语义；新增`batch_outcome() -> BatchOutcome`读取快照。
- `run_batch_buy`明确设置结果：测试全部完成为tested且confirmed_orders=0；真实全部确认才completed；auto_pay=False为awaiting_payment；异常/未完成为stopped或failed。

- [ ] 写失败测试：自动支付关闭即使run返回completed，outcome仍awaiting_payment；成功计数少于目标不得completed；tested不计真实订单。
- [ ] 写生命周期测试：queue_lifecycle=True时仅tested/全部confirmed正常结束关闭后切换；库存/价格/验证/结果不明/异常保留可用浏览器；显式stop关闭；queue_lifecycle=False保持旧完成保留浏览器设置。
- [ ] Run: `& $py -m unittest discover -s tests -p test_batch_runner_lifecycle.py -v`。Expected: RED，接口缺失或错误状态。
- [ ] 最小接入结果，不修改现有选择商品/金额/提交/成功确认逻辑。队列异常先记录停止原因，再保持浏览器直到本人关闭/明确停止；关闭期间不宣称已经可切下一账号。保留原异常传播/资源清理语义。
- [ ] 同命令GREEN；全量suite GREEN，ledger记录。

### Task 3: 顺序队列与取消/授权边界

**Files:** Modify `batch_queue.py`; Test `tests/test_batch_queue.py`。

**Interfaces:**
- `BatchRunner` Protocol：`run() -> str`、`batch_outcome() -> BatchOutcome`。
- `AccountResult(account_id: str, name: str, outcome: BatchOutcome)`，frozen。
- `QueueReport(results: tuple[AccountResult,...], unstarted: tuple[BatchJob,...], reason: str)`，frozen。
- `SequentialBatchQueue(runner_factory: Callable[[BatchJob], BatchRunner], log: Callable[[str,str],None], stop_event: threading.Event, license_status=None)`；`run(jobs: tuple[BatchJob,...]) -> QueueReport`。
- 每个job只创建一次runner；仅completed且confirmed_orders==job.buy_times或tested继续；判断同时检查kind而非runner返回文本。factory/runner异常也停止后续，不重试。

- [ ] 写失败测试：两个账号按顺序各执行一次，最大活动runner=1；第二账号状态全新；tested各0真实成功；awaiting_payment/少成功计数/异常/授权拒绝停止且未开始项完整保留。
- [ ] 写交接竞态测试：第一账号返回前设置stop，第二账号factory从未调用；切换前授权从allow变deny同样阻断；不调用HTTP或文件保存；日志每条带账号名和进度，最终一次汇总。
- [ ] Run: `& $py -m unittest discover -s tests -p test_batch_queue.py -v`。Expected: RED，SequentialBatchQueue不存在。
- [ ] 最小实现，入参tuple冻结，前后边界检查取消/授权；输出当前停止账号/未执行账号/确认成功总数。需要fake仅替代外部浏览器，断言生产QueueReport及真实调度行为。
- [ ] 同命令GREEN；全量suite GREEN，ledger记录。

### Task 4: 账号管理窗口、登录与批量任务接入

**Files:** Create `batch_accounts_ui.py`; Modify `dy_grab_gui.py:App`；Test `tests/test_batch_accounts_ui.py`。

**Interfaces:**
- `AccountManagerDialog(master, accounts: tuple[AccountRecord,...], on_save: Callable, on_login: Callable, busy: Callable[[],bool])`，沿用现有ttk/抗锯齿勾选风格。
- 主界面新增`batch_multi_account`默认False、`account_store`、冻结队列/选定登录ID；新增`open_account_manager()`、`start_account_login(account_id: str)`；原start_login原路径不变。
- 账号窗口提供新增/备注编辑/移除/上下调整/勾选/订单数/每单数量/登录，保存失败保留编辑并明确提示，移除确认“不删除登录资料”。

- [ ] 写失败GUI测试：多账号关闭配置和默认profile不变；开启且无勾选拒绝；勾选两个不同数量构造正确冻结jobs；执行中编辑/登录/再启动拒绝；flash忽略批量账号。
- [ ] 写边界测试：登录ID捕获后不受选择变化影响，saved才标记、cancelled/unverified不标记；重复备注可区分；注册表损坏不覆盖并禁用账号功能但原单账号仍可用；切换时修改表单不改变运行快照；页面高度和现有页签基线不跳动。
- [ ] Run: `& $py -m unittest discover -s tests -p test_batch_accounts_ui.py -v`。Expected: RED，控件/接口缺失。
- [ ] 实现GUI与工厂：冻结base_config，每job用dataclasses.replace设置profile_dir/buy_times/buy_quantity；独立诊断/网络记录路径按内部ID隔离。factory构造queue_lifecycle=True的全新runner，持有相同stop_event和内存授权快照。GUI仅主线程更新，队列工作线程不读tk变量。
- [ ] 登录仍复用LoginSession及其现有会话Cookie检查；名称是用户备注，不能声称已验证真实身份。登录与队列互斥，完成/异常通过ui_queue恢复控件。
- [ ] 同命令GREEN；全量suite GREEN，ledger记录。

### Task 5: 自有浏览器环境集成与最终独立审查

**Files:** Create `tests/test_multi_account_browser.py`, `tests/fixtures/batch_accounts.html`；简要更新`用户使用说明.md`。

**Interfaces:** 只消费Task1–4的接口，测试页面用测试专属服务器和Cookie标记账号，不增加生产绕过/测试开关。

- [ ] 先写真实浏览器测试：两个临时独立profile各有不同模拟账号会话，顺序订单归属/数量正确，当前浏览器关闭后下个才打开；模拟支付结果不明/验证页面只处理第一个账号，第二个零请求；DRY_RUN零提交。
- [ ] Run: `& $py -m unittest discover -s tests -p test_multi_account_browser.py -v`。Expected: 首轮RED，缺少测试fixture；实现自有服务/fixture后GREEN。观察真runner/队列行为，不把mock成功当平台确认。
- [ ] 手册在批量章节增加3–4步多账号操作及“异常停止，先核对，不直接重跑”的提醒，保持原简洁分类。
- [ ] 全量suite GREEN；准备独立审查包和ledger。按执行技能安排一次fresh-context全量代码审查，重点使用Review Focus；主实现者处理严重项，每项RED→GREEN并全量回归，无重复逐任务审查。
- [ ] 无审查未处理的重要项后记录本地完成，进入打包；真实账号购买、平台登录身份、真实支付均非本次模拟验收结论。

### Task 6: 发行验收与dist更新

**Files:** Modify `build/verify_packaged_source.py`；Create `build/publish_multi_account_release.ps1`、`build/多账号批量下单交付报告.md`；更新dist发行及手册。

**Interfaces:** 源码嵌入校验新增batch_accounts/batch_queue/batch_accounts_ui；发布脚本使用独立staging和全新完整backup路径，绝不覆盖联网授权阶段备份。

- [ ] Run fullsuite并读退出码和尾部；保留GUI/batch新增及旧秒杀/授权测试结果。
- [ ] 打包：`& $py -m PyInstaller --noconfirm --distpath build/release --workpath build/pyinstaller-multi-account dy_live.spec`，日志留本任务工作区。Expected: exit0。
- [ ] Run: `& $py build/verify_packaged_source.py`。Expected:主GUI、原授权模块及3个新模块代码签名一致；再比对新旧local_license_keys嵌入代码一致，不输出卡密。
- [ ] 在隔离LOCALAPPDATA启动实际EXE检查激活窗口，验证依赖/新模块导入；如需要激活主界面使用真实合法测试凭证或独立测试环境，不能伪造授权，不改用户缓存、不运行真实商品任务。
- [ ] 发布前确认旧EXE未运行；若占用请用户关闭。验证绝对目标均在工作区具体发行子目录，完整备份当前dist目录，保留原非运行库文件；整套新运行库一起替换。不删除用户资料。
- [ ] 替换后核对整套发行文件SHA及手册同步，实际dist启动检查，重新运行全量suite，交付报告记录测试范围/剩余边界/恢复路径，不宣称真实平台成功率。

## 计划自检与交付门槛

所有设计段落对应Task1–6；模型/方法与后续消费名称一致；Review Focus五类各有任务测试。保留runner.run旧返回值，但队列仅消费结构化结果，避免已有completed含义混淆。新队列正常关闭窗口规则仅由queue_lifecycle控制。最终只有模拟/回归/打包/备份/启动验收全部通过才称完成。

**状态：** 用户已确认设计、计划及本会话实施。Task1–6全部完成，独立审查重要项修复，最终281项回归通过，dist已完整备份并更新；一个备注Unicode过滤小项明确延后。证据见本计划的 progress.md 与 build/多账号批量下单交付报告.md。
