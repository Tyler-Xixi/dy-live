import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from dy_grab_gui import AutomationConfig, AutomationRunner, GracefulStop


class Page:
    def goto(self, *args, **kwargs): pass


class OutcomeTests(unittest.TestCase):
    def runner(self, **values):
        config = AutomationConfig(mode='batch', product_url='https://example.test/product', product_name='', **values)
        runner = AutomationRunner(config, lambda *a: None, threading.Event(), queue_lifecycle=True)
        runner.install_product_image_viewer_guard = lambda p: None
        runner.wait_ms = lambda ms: None
        runner.dismiss_blocking_overlays = lambda p: None
        runner.safe_exact_action_buttons = lambda *a: [object()]
        runner.advance_order_flow = lambda p: True
        return runner

    def test_missing_target_stops_without_confirmation(self):
        runner = self.runner(dry_run=False)
        runner.config.live_url = ''
        with self.assertRaises(GracefulStop):
            runner.run_batch_buy(Page())
        self.assertEqual(runner.state['batch_completed'], 0)
        self.assertFalse(runner.state['order_submitted'])

    def test_pending_evidence_requires_product_spec_quantity_and_new_id(self):
        runner = self.runner()
        runner.batch_target_name = '测试球'
        runner.config.buy_quantity = 3
        runner.config.multi_option_enabled = True
        runner.config.option_names = '颜色=红色'
        class EvidencePage:
            def evaluate(self, *args): return self.records
        page = EvidencePage()
        good = '商品名称：测试球\n购买数量：3\n颜色：红色'
        page.records = [{'id':'NEW123456','text':good}]
        self.assertEqual(runner.pending_order_snapshot(page)['id'],'NEW123456')
        for text in (good.replace('测试球','其他商品'),good.replace('：3','：1'),good.replace('红色','蓝色'),'商品名称：测试球'):
            page.records = [{'id':'NEW123456','text':text}]
            with self.assertRaises(GracefulStop): runner.pending_order_snapshot(page)
        page.records = [{'id':'NEW123456','text':good},{'id':'OTHER123456','text':good}]
        with self.assertRaises(GracefulStop): runner.pending_order_snapshot(page)
        page.records = [{'id':'NEW123456','text':good}]
        runner.pending_order_snapshot(page,capture_existing=True)
        self.assertIsNone(runner.pending_order_snapshot(page))
        self.assertEqual(runner.state['batch_completed'],0)

    def test_pending_never_dismisses_or_abandons(self):
        runner = AutomationRunner(AutomationConfig(),lambda *a:None,threading.Event())
        runner.state['pending_payment_review'] = True
        class NoInteraction:
            def __getattr__(self,name): raise AssertionError('Unexpected interaction: '+name)
        page=NoInteraction()
        runner.dismiss_blocking_overlays(page)
        self.assertFalse(runner.dismiss_product_image_viewer(page))
        for action in (runner.close_payment_layer,runner.confirm_abandon_payment):
            with self.assertRaises(GracefulStop): action(page)

    def lifecycle(self, queue_mode, action, stop=False, close_failure=False, buy_times=1):
        with tempfile.TemporaryDirectory() as directory:
            runner = AutomationRunner(AutomationConfig(mode='batch', profile_dir=directory, diagnostics_dir=directory,buy_times=buy_times), lambda *a: None, threading.Event(), queue_lifecycle=queue_mode)
            events = []
            class Context:
                pages = [Page()]
                def close(self):
                    events.append('close')
                    if close_failure: raise RuntimeError('owned context still alive')
            context = Context()
            context.pages[0].set_default_timeout = lambda ms: None
            class PW:
                def start(self): return self
                def stop(self): events.append('playwright-stop')
            runner.capture_network_evidence = lambda p: None
            runner.run_batch_buy = lambda p: action(runner)
            def retained_wait(seconds):
                events.append('retained')
                context.pages = []
                return False
            if stop: runner.stop_event.set()
            else: runner.stop_event.wait = retained_wait
            with patch('playwright.sync_api.sync_playwright', return_value=PW()), patch('dy_grab_gui.launch_persistent_browser',return_value=context):
                try: result=runner.run()
                except RuntimeError: result='error'
            return runner, result, events

    def test_queue_test_success_releases_browser(self):
        def action(r):
            from batch_queue import BatchOutcome
            r._batch_outcome = BatchOutcome('tested',0,'测试完成')
        _,result,events=self.lifecycle(True,action)
        self.assertEqual(result,'completed')
        self.assertEqual(events,['close','playwright-stop'])

    def test_pending_retains_even_if_run_returns_completed(self):
        def action(r):
            from batch_queue import BatchOutcome
            r._batch_outcome = BatchOutcome('awaiting_payment',0,'等待付款')
        runner,result,events=self.lifecycle(True,action)
        self.assertEqual(result,'completed')
        self.assertIn('retained',events)
        self.assertEqual(runner.batch_outcome().kind,'awaiting_payment')

    def test_error_and_graceful_stop_retain(self):
        for error in (RuntimeError('页面异常'),GracefulStop('库存不足')):
            def action(r): raise error
            runner,_,events=self.lifecycle(True,action)
            self.assertIn('retained',events)
            self.assertIn(runner.batch_outcome().kind,('failed','stopped'))

    def test_explicit_stop_closes(self):
        def action(r): raise GracefulStop('用户停止')
        _,_,events=self.lifecycle(True,action,stop=True)
        self.assertNotIn('retained',events)

    def test_single_account_lifecycle_unchanged(self):
        _,_,events=self.lifecycle(False,lambda r: None)
        self.assertIn('retained',events)

    def test_cleanup_failure_blocks_next_account(self):
        from batch_accounts import BatchJob
        from batch_queue import BatchOutcome, SequentialBatchQueue
        for failing_close,failing_stop in ((True,False),(False,True),(True,True)):
            with self.subTest(close=failing_close,stop=failing_stop),tempfile.TemporaryDirectory() as directory:
                events=[]; stop=threading.Event()
                page=Page(); page.set_default_timeout=lambda ms:None
                class Context:
                    pages=[page]
                    def close(self):
                        events.append('close')
                        if failing_close: raise RuntimeError('owned context still alive')
                class PW:
                    def start(self): return self
                    def stop(self):
                        events.append('driver-stop')
                        if failing_stop: raise RuntimeError('owned driver still alive')
                def factory(job):
                    events.append('open-'+job.name)
                    runner=AutomationRunner(AutomationConfig(mode='batch',profile_dir=str(Path(directory)/job.name),diagnostics_dir=directory),lambda *a:None,stop,queue_lifecycle=True)
                    runner.capture_network_evidence=lambda p:None
                    runner.run_batch_buy=lambda p:setattr(runner,'_batch_outcome',BatchOutcome('tested',0,'test'))
                    return runner
                jobs=tuple(BatchJob(name,name,str(Path(directory)/name),1,1) for name in ('A','B'))
                with patch('playwright.sync_api.sync_playwright',return_value=PW()),patch('dy_grab_gui.launch_persistent_browser',side_effect=lambda *a:Context()):
                    report=SequentialBatchQueue(factory,lambda *a:None,stop).run(jobs)
                self.assertNotIn('open-B',events)
                self.assertEqual([j.name for j in report.unstarted],['B'])
                self.assertEqual(report.results[0].outcome.kind,'failed')
                self.assertEqual(report.results[0].outcome.confirmed_orders,0)

    def test_cleanup_failure_preserves_confirmed_orders(self):
        from batch_queue import BatchOutcome
        def action(r):
            r._batch_outcome=BatchOutcome('completed',2,'两单已确认')
            r.state['batch_completed']=2
        runner,_,_=self.lifecycle(True,action,close_failure=True,buy_times=2)
        self.assertEqual(runner.batch_outcome().kind,'failed')
        self.assertEqual(runner.batch_outcome().confirmed_orders,2)
