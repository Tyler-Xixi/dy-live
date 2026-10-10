"""Structured outcomes consumed by the sequential batch scheduler."""
from dataclasses import dataclass
import threading
from typing import Callable, Protocol
from batch_accounts import BatchJob


@dataclass(frozen=True)
class BatchOutcome:
    kind: str
    confirmed_orders: int
    reason: str


@dataclass(frozen=True)
class AccountResult:
    account_id: str
    name: str
    outcome: BatchOutcome


@dataclass(frozen=True)
class QueueReport:
    results: tuple[AccountResult, ...]
    unstarted: tuple[BatchJob, ...]
    reason: str


class BatchRunner(Protocol):
    def run(self) -> str: ...
    def batch_outcome(self) -> BatchOutcome: ...


class SequentialBatchQueue:
    def __init__(self, runner_factory: Callable[[BatchJob], BatchRunner],
                 log: Callable[[str, str], None], stop_event: threading.Event,
                 license_status=None):
        self.runner_factory = runner_factory
        self.log = log
        self.stop_event = stop_event
        self.license_status = license_status

    def boundary_reason(self):
        if self.stop_event.is_set():
            return '用户已停止，后续账号未执行'
        if self.license_status is not None:
            try:
                if not self.license_status().allowed:
                    return '授权不可用，后续账号未执行'
            except Exception:
                return '无法确认授权状态，后续账号未执行'
        return ''

    def run(self, jobs: tuple[BatchJob, ...]) -> QueueReport:
        jobs = tuple(jobs)
        results = []
        reason = '全部账号完成'
        for index, job in enumerate(jobs):
            reason = self.boundary_reason()
            if reason:
                break
            prefix = f'[{job.name} {index + 1}/{len(jobs)}] '
            self.log('info', prefix + f'开始 · {job.buy_times} 单 · 每单 {job.buy_quantity} 件')
            runner = None
            try:
                runner = self.runner_factory(job)
                reason = self.boundary_reason()
                if reason:
                    break
                runner.run()
                outcome = runner.batch_outcome()
                valid_count = type(outcome.confirmed_orders) is int and 0 <= outcome.confirmed_orders <= job.buy_times
                success = valid_count and ((outcome.kind == 'completed' and outcome.confirmed_orders == job.buy_times) or (outcome.kind == 'tested' and outcome.confirmed_orders == 0))
                if not valid_count:
                    outcome = BatchOutcome('failed', 0, '执行结果计数无效，请核对订单')
                elif outcome.kind == 'tested' and outcome.confirmed_orders != 0:
                    outcome = BatchOutcome('failed', 0, '测试结果计数无效，不能计为真实成功')
                elif outcome.kind == 'completed' and not success:
                    outcome = BatchOutcome('failed', outcome.confirmed_orders, '尚未全部收到平台成功确认')
            except Exception as exc:
                # Do not retry; the current browser may still hold a submitted order.
                try:
                    snapshot = runner.batch_outcome() if runner else None
                    count = snapshot.confirmed_orders if isinstance(snapshot, BatchOutcome) and type(snapshot.confirmed_orders) is int and 0 <= snapshot.confirmed_orders <= job.buy_times else 0
                except Exception:
                    count = 0
                outcome = BatchOutcome('failed', count, f'执行异常：{str(exc)[:160]}')
                success = False
            results.append(AccountResult(job.account_id, job.name, outcome))
            self.log('info' if success else 'warn', prefix + f'{outcome.reason} · 平台确认 {outcome.confirmed_orders} 单')
            if not success:
                reason = f'{job.name}：{outcome.reason}'
                break
            reason = self.boundary_reason()
            if reason:
                break
            reason = '全部账号完成'
            del runner
        report = QueueReport(tuple(results), jobs[len(results):], reason)
        completed = sum(r.outcome.kind == 'completed' for r in results)
        tested = sum(r.outcome.kind == 'tested' for r in results)
        confirmed = sum(r.outcome.confirmed_orders for r in results)
        self.log('info', f'队列汇总：成功账号 {completed} · 测试完成 {tested} · 确认成功 {confirmed} 单 · 未执行账号 {len(report.unstarted)} · {reason}')
        return report
