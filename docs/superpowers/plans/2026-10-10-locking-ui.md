# 6.1.3 锁单入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 普通锁单和脚本锁单一次提交并确认新待付款订单，详情页锁单标注推荐。

**Architecture:** 复用现有详情页提交的安全约束及订单确认，保留三种进入商品的方式。秒杀与批量自动支付分开，不重构整个 GUI。

**Tech Stack:** Python、Tkinter、Playwright、unittest。

**Spec:** `docs/superpowers/specs/2026-10-10-locking-incremental-update-design.md` 第 2、5 节。

## Global Constraints

- 三模式优先级：详情页锁单 > 脚本锁单 > 普通锁单。
- 最终提交必须原生点击一次，确认新订单号及待付款状态后停止并保留浏览器。
- 测试模式零购买/支付点击；不绕过验证码，不保证首次提交绝不扣款。
- 批量下单、多账号及旧离线/联网激活保持原行为。
- 无 Git；每任务保存修改前快照、测试记录和完成清单，不能伪称提交。

## Review Focus

- 旧偏好恢复后不能悄悄增加提交能力：任务 2 覆盖取消确认。
- 商品详情被替换成另一个商品：任务 1 覆盖最终控件身份变化。
- 已有待付款单残留：任务 1 要求旧订单不能确认成功。
- 支付点击超时但服务端已经收单：任务 1 要求不重复提交。
- 同时勾选三开关：任务 2 明确优先级且只执行一个模式。

### Task 1: 共用秒杀一次提交合同

**Files:** Modify `dy_grab_gui.py` 的 `advance_order_flow`、`scan_detail_lock_and_order`、`run_flash_sale`；Test `tests/test_detail_lock.py`、Create `tests/test_lock_modes.py`；复用 `tests/flash_sandbox.py`。

**Interfaces:** 新增 `AutomationRunner.submit_flash_lock(page) -> bool`，返回 True 仅表示确认新的待付款订单；模式入口调用它，不调用批量付款后续。消费现有 `safe_click` 和 `wait_for_pending_order`，提交前保存本轮已有订单基线。

- [ ] 添加失败测试：三个模式库存变化后 `pay_clicks == 1`、确认新待付款订单；旧订单、金额 0/不符、数量非 1、多按钮、截止、详情被替换时 `pay_clicks == 0`；脚本事件被拒绝和提交超时均不补点击。
- [ ] 运行 `venv/Scripts/python.exe -m unittest tests.test_lock_modes tests.test_detail_lock -v`，确认因缺少共用行为而失败；本计划所有 venv 命令使用 `.superpowers/sdd/2026-10-09-manual-confirm-update/venv` 的真实绝对/工作区相对路径。
- [ ] 提取现有详情提交验证到 `submit_flash_lock`；保留提交状态、防重复、验证暂停和截止约束，让普通/脚本入口进入详情后调用；批量流程不走新合同。
- [ ] 重跑上述测试及 `tests.test_flash_fast_path tests.test_flash_windows tests.test_flash_sandbox`，要求全部通过；记录短库存窗口阶段耗时，不能解释为真实平台成功率。
- [ ] 保存本任务快照和红/绿测试证据。

### Task 2: 标签、迁移确认和手册

**Files:** Modify `dy_grab_gui.py` 的界面、偏好恢复、启动确认；Modify `用户使用说明.md`；Create `tests/test_lock_mode_ui.py`。

**Interfaces:** 新增 `effective_flash_lock_mode(config: AutomationConfig) -> str`，返回 `detail`、`script`、`normal`、`none`；UI 与运行器使用同一个结果。迁移确认由界面记录非秘密偏好，不改变任务真实执行确认。

- [ ] 添加测试：准确标签“普通锁单”“脚本锁单”“详情页锁单（推荐）”；三勾选结果 detail；旧真实任务拒绝新行为确认后 worker 未启动；测试模式零点击；批量仍独立显示并读取 auto_pay。
- [ ] 运行 `-m unittest tests.test_lock_mode_ui -v`，确认预期行为失败。
- [ ] 实现统一模式选择、标签及秒杀移除重复 auto_pay 控件；首次确认解释一次提交及潜在付款风险，不默认为用户同意。
- [ ] 重跑本计划所有测试和现有批量/偏好测试；手册用短句列出三模式、规格示例、人工验证和待付款停止。
- [ ] 保存快照、界面验证截图及测试证据；待可靠更新计划验收后联合发布 6.1.3。
