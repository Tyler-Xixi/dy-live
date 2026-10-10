"""Owned loopback checkout: real runner, real persistent profiles, no platform traffic."""
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
import dy_grab_gui as app
from batch_accounts import AccountStore
from batch_queue import SequentialBatchQueue


class BrowserAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.orders=[]; self.visits=[]; self.events=[]; self.runners=[]; self.logs=[]; self.forbidden=[]
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a): pass
            def do_GET(self):
                if self.path.startswith('/product'):
                    outer.visits.append(self.headers.get('Cookie',''))
                    payload=(Path(__file__).parent/'fixtures'/'batch_accounts.html').read_bytes()
                else: payload=b''
                self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                data['cookie']=self.headers.get('Cookie',''); outer.orders.append(data)
                payload=json.dumps({'id':'LOCAL%06d'%len(outer.orders)}).encode()
                self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.origin=f'http://127.0.0.1:{self.server.server_port}'
        store=AccountStore(self.root)
        records=tuple(replace(store.new_account(name),selected=True,buy_times=2 if name=='A' else 1,buy_quantity=1 if name=='A' else 3) for name in ('A','B'))
        self.jobs=store.jobs(records,self.root/'legacy')
        with sync_playwright() as pw:
            for job in self.jobs:
                context=pw.chromium.launch_persistent_context(job.profile_dir,channel='msedge',headless=True)
                context.add_cookies([{'name':'owned_account','value':job.name,'url':self.origin,'expires':time.time()+3600}]); context.close()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def run_queue(self,mode='success',dry=False):
        stop=threading.Event()
        real_launch=app.launch_persistent_browser
        def launch(*args):
            context=real_launch(*args)
            name=self.jobs[len(self.runners)-1].name
            self.events.append(('open',name))
            context.on('close',lambda:self.events.append(('close',name)))
            context.pages[0].on('console',lambda msg:self.forbidden.append(msg.text) if msg.text=='FORBIDDEN' else None)
            return context
        def factory(job):
            config=app.AutomationConfig(mode='batch',live_url=self.origin+'/product?mode='+mode,product_name='测试球',target_price=20,
                profile_dir=job.profile_dir,buy_times=job.buy_times,buy_quantity=job.buy_quantity,headless=True,dry_run=dry,
                diagnostics_dir=str(self.root/'diagnostics'/job.account_id),save_diagnostics=False,batch_interval_ms=0,order_step_delay_ms=0)
            def log(level,msg):
                self.logs.append(msg)
                if '浏览器将保留' in msg:
                    self.events.append(('retained',job.name))
                    runner.context.close() # User closes owned test browser after inspecting it.
            runner=app.AutomationRunner(config,log,stop,queue_lifecycle=True); self.runners.append(runner)
            runner.pending_order_timeout_ms=100
            return runner
        with patch.object(app,'launch_persistent_browser',side_effect=launch):
            return SequentialBatchQueue(factory,lambda *a:None,stop).run(self.jobs)

    def test_profiles_order_quantity_and_browser_handoff(self):
        report=self.run_queue()
        self.assertEqual([(x['cookie'],x['quantity']) for x in self.orders],[('owned_account=A',1),('owned_account=A',1),('owned_account=B',3)])
        self.assertEqual(self.events,[('open','A'),('close','A'),('open','B'),('close','B')])
        self.assertEqual([r.outcome.confirmed_orders for r in report.results],[2,1])
        self.assertEqual(report.unstarted,())
        self.assertIsNot(self.runners[0].state,self.runners[1].state)
        self.assertTrue(any('原详情页继续' in x for x in self.logs))
        self.assertEqual(self.forbidden,[])

    def test_ambiguous_payment_stops_and_retains_current(self):
        report=self.run_queue('ambiguous')
        self.assertEqual(len(self.orders),1)
        self.assertEqual(report.results[0].outcome.confirmed_orders,0)
        self.assertEqual([j.name for j in report.unstarted],['B'])
        self.assertIn(('retained','A'),self.events)
        self.assertNotIn(('open','B'),self.events)
        self.assertTrue(all('owned_account=B' not in cookie for cookie in self.visits))

    def test_dry_run_never_submits(self):
        report=self.run_queue(dry=True)
        self.assertEqual(self.orders,[])
        self.assertEqual([r.outcome.kind for r in report.results],['tested','tested'])
        self.assertEqual(sum(r.outcome.confirmed_orders for r in report.results),0)

    def test_missing_wrong_or_ambiguous_evidence_never_counts_or_retries(self):
        for mode in ('missing','wrong_product','wrong_quantity','wrong_spec'):
            with self.subTest(mode=mode):
                before=len(self.orders)
                self.runners=[]; self.events=[]
                report=self.run_queue(mode)
                self.assertEqual(len(self.orders)-before,1)
                self.assertEqual(report.results[0].outcome.confirmed_orders,0)
                self.assertEqual([j.name for j in report.unstarted],['B'])

    def test_fallback_reopens_and_rechecks_quantity(self):
        report=self.run_queue('fallback')
        self.assertEqual([r.outcome.confirmed_orders for r in report.results],[2,1])
        self.assertEqual([x['quantity'] for x in self.orders],[1,1,3])
        self.assertTrue(any('回直播间' in x for x in self.logs))
        self.assertEqual(self.forbidden,[])
