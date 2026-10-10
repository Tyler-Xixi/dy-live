import threading
import unittest
from batch_accounts import BatchJob
from batch_queue import BatchOutcome, SequentialBatchQueue
from license_protocol import LicenseStatus


class QueueTests(unittest.TestCase):
    def run_queue(self, outcomes, hook=None, authorization=None):
        self.events=[]
        self.logs=[]
        self.stop=threading.Event()
        jobs=tuple(BatchJob(str(i),f'账号{i}',f'profile{i}',2, i+1) for i in range(2))
        def factory(job):
            self.events.append(('open',job.account_id))
            outer=self
            class Runner:
                def run(self):
                    outer.events.append(('run',job.account_id))
                    if isinstance(outcomes[int(job.account_id)],Exception): raise outcomes[int(job.account_id)]
                    outer.events.append(('close',job.account_id))
                    if hook: hook(outer,job)
                    return 'completed'
                def batch_outcome(self): return outcomes[int(job.account_id)]
            return Runner()
        queue=SequentialBatchQueue(factory, lambda level,msg:self.logs.append(msg), self.stop, authorization)
        self.report=queue.run(jobs)
        return self.report

    def test_sequential_close_before_next_open(self):
        report=self.run_queue([BatchOutcome('completed',2,'成功')]*2)
        self.assertEqual(self.events,[('open','0'),('run','0'),('close','0'),('open','1'),('run','1'),('close','1')])
        self.assertEqual([r.outcome.confirmed_orders for r in report.results],[2,2])
        self.assertEqual(report.unstarted,())
        self.assertEqual(sum('队列汇总' in line for line in self.logs),1)
        self.assertTrue(all('账号' in line or '队列汇总' in line for line in self.logs))

    def test_dry_run_has_zero_confirmed(self):
        report=self.run_queue([BatchOutcome('tested',0,'测试')]*2)
        self.assertEqual(len(report.results),2)
        self.assertEqual(sum(r.outcome.confirmed_orders for r in report.results),0)

    def test_invalid_test_result_never_counts_real_success(self):
        report=self.run_queue([BatchOutcome('tested',1,'无效模拟计数')]*2)
        self.assertEqual(report.results[0].outcome.kind,'failed')
        self.assertEqual(report.results[0].outcome.confirmed_orders,0)

    def test_incomplete_pending_errors_block_next(self):
        for outcome in (BatchOutcome('completed',1,'少一单'),BatchOutcome('awaiting_payment',0,'待支付'),BatchOutcome('failed',0,'验证码'), RuntimeError('页面错误'),BatchOutcome('tested',1,'错误模拟计数')):
            with self.subTest(outcome=outcome):
                report=self.run_queue([outcome,BatchOutcome('completed',2,'成功')])
                self.assertEqual([j.account_id for j in report.unstarted],['1'])
                self.assertEqual(self.events.count(('open','1')),0)

    def test_stop_at_handoff_blocks_factory(self):
        report=self.run_queue([BatchOutcome('completed',2,'成功')]*2,hook=lambda outer,job:outer.stop.set())
        self.assertEqual(len(report.results),1)
        self.assertEqual(len(report.unstarted),1)
        self.assertNotIn(('open','1'),self.events)

    def test_license_revoked_at_handoff(self):
        allowed=[True]
        def hook(outer,job): allowed[0]=False
        report=self.run_queue([BatchOutcome('completed',2,'成功')]*2,hook,lambda:LicenseStatus(allowed[0],'online','active' if allowed[0] else 'disabled'))
        self.assertEqual(len(report.results),1)
        self.assertEqual(len(report.unstarted),1)

    def test_initial_license_deny_opens_nothing(self):
        report=self.run_queue([],authorization=lambda:LicenseStatus(False,'online','disabled'))
        self.assertEqual(self.events,[])
        self.assertEqual(len(report.unstarted),2)

    def test_factory_failure_and_pre_cancel_are_safe(self):
        stop=threading.Event()
        jobs=(BatchJob('a','A','p',1,1),)
        def failing(job): raise OSError('浏览器无法启动')
        report=SequentialBatchQueue(failing,lambda *a:None,stop).run(jobs)
        self.assertEqual(report.results[0].outcome.kind,'failed')
        stop.set()
        report=SequentialBatchQueue(failing,lambda *a:None,stop).run(jobs)
        self.assertEqual(report.results,())
        self.assertEqual(report.unstarted,jobs)
