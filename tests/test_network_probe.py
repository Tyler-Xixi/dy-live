import importlib.util
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from pathlib import Path
import tempfile

import dy_grab_gui as app


class NetworkProbeTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('network_probe'), '网络检测模块尚未实现')
        import network_probe
        return network_probe

    def test_latency_and_failure_summary_are_not_packet_loss_or_probability(self):
        module = self.module()
        result = module.summarize_samples([20, 30, None, 40], [200, 200, None, 200])
        self.assertEqual(result['median_ms'], 30)
        self.assertEqual(result['jitter_ms'], 10)
        self.assertEqual(result['request_failure_pct'], 25)
        self.assertEqual(result['samples'], 4)
        self.assertEqual(module.assess_network(result)['level'], '较差')
        self.assertNotIn('success_rate', module.assess_network(result))

    def test_blocked_site_and_insufficient_samples_do_not_predict_quality(self):
        module = self.module()
        for samples, statuses in [([10]*6, [403]*6), ([10], [200]), ([], [])]:
            result = module.summarize_samples(samples, statuses)
            self.assertEqual(module.assess_network(result)['level'], '无法评估')

    def test_fast_stable_and_slow_network_get_different_assessments(self):
        module = self.module()
        fast = module.summarize_samples([20, 22, 25, 23, 24, 21], [200]*6)
        slow = module.summarize_samples([400, 600, 450, 500, 700, 400], [200]*6)
        self.assertEqual(module.assess_network(fast)['level'], '较好')
        self.assertEqual(module.assess_network(slow)['level'], '较差')

    def test_http_engine_downloads_bounded_bytes_and_cancel_stops_requests(self):
        module = self.module()
        class Handler(BaseHTTPRequestHandler):
            count = 0
            def log_message(self, *_): pass
            def do_GET(self):
                Handler.count += 1
                data = b'x' * (262144 if self.path.split('?')[0] == '/download' else 0)
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_HEAD(self):
                Handler.count += 1
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            probe = module.NetworkProbe()
            report = probe.run(origin+'/', origin+'/', origin+'/download', download_bytes=262144)
            self.assertEqual(report['site']['request_failure_pct'], 0)
            self.assertEqual(report['download']['bytes'], 262144)
            self.assertGreater(report['download']['mbps'], 0)
            count = Handler.count
            probe.cancel()
            with self.assertRaises(module.ProbeCancelled):
                probe.run(origin+'/', origin+'/', origin+'/download')
            self.assertEqual(Handler.count, count)
        finally:
            server.shutdown()
            server.server_close()

    def test_site_probe_uses_supported_get_without_downloading_entire_page(self):
        module = self.module()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_HEAD(self):
                self.send_error(404)
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            report = module.NetworkProbe().run(origin,origin,origin,download_bytes=0)
            self.assertEqual(report['site']['http_errors'],0)
            self.assertNotEqual(report['assessment']['level'],'无法评估')
        finally:
            server.shutdown()
            server.server_close()

    def test_partial_download_over_time_budget_returns_measured_throughput(self):
        module = self.module()
        class Response:
            status = 200
            def read1(self, _): return b'x'*16384
        probe = module.NetworkProbe()
        # A controlled clock tests the time cap; real HTTP behavior is tested above.
        with patch.object(probe,'connection') as connection, patch.object(probe,'request',return_value=Response()), \
             patch.object(module.time,'perf_counter',side_effect=[0,1,2,3,4,13,13]):
            result = probe.download('https://example.invalid',8388608)
        self.assertEqual(result['bytes'],65536)
        self.assertGreater(result['mbps'],0)
        self.assertTrue(result['partial'])

    def test_cancel_interrupts_a_waiting_http_request(self):
        module = self.module()
        entered, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                entered.set()
                release.wait(5)
                try:
                    self.send_response(200)
                    self.send_header('Content-Length','0')
                    self.end_headers()
                except OSError:
                    pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever,daemon=True).start()
        probe = module.NetworkProbe()
        errors = []
        def run():
            try:
                probe.latency(f'http://127.0.0.1:{server.server_port}', 'test', 'GET')
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            probe.cancel()
            # Windows buffered socket reads may wake only on the 3s timeout.
            # Cancellation must finish before the stalled server replies at 5s.
            worker.join(4)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(errors[0],module.ProbeCancelled)
        finally:
            release.set()
            worker.join(5)
            server.shutdown()
            server.server_close()

    def test_slow_connection_close_stream_respects_download_budget(self):
        module = self.module()
        done = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length','8388608')
                self.send_header('Connection','close')
                self.end_headers()
                try:
                    self.wfile.write(b'x'*65536)
                    self.wfile.flush()
                    while not done.wait(.04):
                        self.wfile.write(b'x')
                        self.wfile.flush()
                except OSError:
                    pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever,daemon=True).start()
        probe = module.NetworkProbe()
        probe.download_time_limit = .15
        results = []
        worker = threading.Thread(target=lambda:results.append(probe.download(
            f'http://127.0.0.1:{server.server_port}',8388608)))
        worker.start()
        try:
            worker.join(1)
            self.assertFalse(worker.is_alive(), '慢速响应绕过了总耗时限制')
            self.assertTrue(results[0]['partial'])
            self.assertGreater(results[0]['bytes'],0)
        finally:
            done.set()
            probe.cancel()
            worker.join(5)
            server.shutdown()
            server.server_close()

    def test_chunked_framing_cannot_bypass_download_deadline(self):
        module = self.module()
        done = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self,*_): pass
            def do_GET(self):
                self.close_connection = True
                self.send_response(200)
                self.send_header('Transfer-Encoding','chunked')
                self.end_headers()
                try:
                    self.wfile.write(b'10000\r\n'+b'x'*65536+b'\r\n1;')
                    self.wfile.flush()
                    while not done.wait(.04):
                        self.wfile.write(b'x')
                        self.wfile.flush()
                except OSError:
                    pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever,daemon=True).start()
        probe = module.NetworkProbe()
        probe.download_time_limit = .15
        results = []
        def run():
            try:
                results.append(probe.download(f'http://127.0.0.1:{server.server_port}',8388608))
            except module.ProbeCancelled:
                pass
        worker = threading.Thread(target=run)
        worker.start()
        try:
            worker.join(1)
            self.assertFalse(worker.is_alive(),'分块头的慢速响应绕过了下载截止时间')
            self.assertTrue(results[0]['partial'])
        finally:
            done.set()
            probe.cancel()
            worker.join(5)
            server.shutdown()
            server.server_close()


class NetworkUITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.setting = patch.object(app, 'APP_DATA_DIR', Path(self.folder.name))
        self.setting.start()
        self.window = app.App()
        self.window.withdraw()

    def tearDown(self):
        self.window.destroy()
        self.setting.stop()
        self.folder.cleanup()

    def test_network_tab_has_controls_and_cannot_run_during_task(self):
        self.assertTrue(hasattr(self.window, 'network_start_button'), '网络检测页签尚未实现')
        self.window.worker = threading.Thread(target=lambda: time.sleep(.3))
        self.window.worker.start()
        with patch.object(app.messagebox, 'showwarning'):
            self.window.start_network_test()
        self.assertIsNone(self.window.network_thread)
        self.window.worker.join()

    def test_network_result_renders_metrics_without_success_percentage(self):
        self.assertTrue(hasattr(self.window, 'show_network_result'), '网络检测结果尚未实现')
        from network_probe import summarize_samples, assess_network
        site = summarize_samples([20, 22, 23, 24, 21, 20], [200]*6)
        report = {'site': site, 'general': site, 'download': {'mbps': 50, 'bytes': 8388608},
                  'assessment': assess_network(site)}
        self.window.show_network_result(report)
        text = self.window.network_result.get('1.0', 'end')
        self.assertIn('50.00 Mbps', text)
        self.assertIn('较好', text)
        self.assertIn('无法单凭网速计算', text)


if __name__ == '__main__':
    unittest.main()
