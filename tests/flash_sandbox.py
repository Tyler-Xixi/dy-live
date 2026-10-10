"""Isolated, server-authoritative stock race using the real desktop runner.

This is a model of observable shopping behavior, not Douyin's private protocol.
Run: python tests/flash_sandbox.py --compare --trials 5 --report build/sandbox.json
"""
from dataclasses import asdict, dataclass, replace
from pathlib import Path
import argparse
import hashlib
import json
import marshal
import random
import statistics
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class Scenario:
    name: str = 'normal'
    release_after_ms: int = 2400
    window_ms: int = 500
    stock: int = 2
    competitor_offsets_ms: tuple = (180, 500)
    push_ms: int = 25
    checkout_ms: int = 60
    render_ms: int = 15
    hydrate_ms: int = 0
    order_ms: int = 15
    response_ms: int = 25
    replace_card: bool = False
    overlay_ms: int = 0
    price: float = 20
    drop_response: bool = False
    verification: bool = False
    require_trusted_purchase: bool = False
    detail_auto_update: bool = True


class InventoryRoom:
    def __init__(self, config, now=time.monotonic):
        self.config, self.now = config, now
        self.lock = threading.RLock()
        self.started_at = None
        self.released = False
        self.remaining = 0
        self.competitors_applied = 0
        self.orders = {}
        self.order_attempts = 0
        self.accepted_after_release_ms = None

    def start(self):
        with self.lock:
            if self.started_at is None:
                self.started_at = self.now()

    def _advance(self):
        age_ms = (self.now()-self.started_at)*1000-self.config.release_after_ms
        if age_ms >= 0 and not self.released:
            self.released, self.remaining = True, self.config.stock
        for index, offset in enumerate(self.config.competitor_offsets_ms):
            if age_ms >= offset and index >= self.competitors_applied:
                self.remaining = max(0, self.remaining-1)
                self.competitors_applied = index+1
        if age_ms >= self.config.window_ms:
            self.remaining = 0
        return age_ms

    def snapshot(self):
        with self.lock:
            age = self._advance()
            return {'stock': self.remaining, 'released': self.released,
                    'elapsed_ms': round(age, 3), 'price': self.config.price}

    def submit(self, buyer, product, price, quantity):
        with self.lock:
            self.order_attempts += 1
            age = self._advance()
            valid = (self.remaining > 0 and buyer not in self.orders and product == 'toy'
                     and price == self.config.price and quantity == 1)
            if valid and not self.config.verification:
                self.remaining -= 1
                self.orders[buyer] = {'id': 'SIM-'+buyer, 'accepted_after_release_ms': round(age, 3)}
                self.accepted_after_release_ms = round(age, 3)
                return {'success': True, **self.orders[buyer]}
            return {'success': False, 'verification': valid and self.config.verification}


def cases_for(seed, names):
    rng = random.Random(seed)
    for name in names:
        c = Scenario(name=name, release_after_ms=rng.randint(2300, 2450))
        if name == 'detail_stale':
            c = replace(c, detail_auto_update=False)
        elif name == 'tight':
            window = rng.randint(85, 115)
            c = replace(c, window_ms=window, competitor_offsets_ms=(window//2, window),
                        push_ms=rng.randint(0, 8), checkout_ms=rng.randint(0, 8), render_ms=0,
                        order_ms=rng.randint(3, 8), response_ms=8)
        elif name in ('window_50', 'window_80', 'window_100', 'window_150'):
            window = int(name.split('_')[1])
            c = replace(c, window_ms=window, competitor_offsets_ms=(window//2, window),
                        push_ms=3, checkout_ms=3, render_ms=0, order_ms=5, response_ms=8)
        elif name == 'trusted_only':
            c = replace(c, require_trusted_purchase=True, window_ms=600,
                        competitor_offsets_ms=(300, 600), push_ms=3, checkout_ms=3,
                        render_ms=0, order_ms=5, response_ms=8)
        elif name == 'normal':
            c = replace(c, push_ms=rng.randint(15, 55), checkout_ms=rng.randint(35, 110),
                        render_ms=rng.randint(0, 25), order_ms=rng.randint(10, 30),
                        response_ms=rng.randint(15, 60))
        elif name == 'hydration':
            c = replace(c, push_ms=8, checkout_ms=5, render_ms=0, hydrate_ms=rng.randint(20, 75),
                        window_ms=220, competitor_offsets_ms=(100, 220), order_ms=8)
        elif name == 'replaced_card':
            c = replace(c, replace_card=True, checkout_ms=10, render_ms=0,
                        window_ms=300, competitor_offsets_ms=(120, 300))
        elif name == 'late_push':
            c = replace(c, push_ms=180, window_ms=100, competitor_offsets_ms=(40, 100))
        elif name == 'slow_checkout':
            c = replace(c, checkout_ms=400, window_ms=250, competitor_offsets_ms=(80, 250))
        elif name == 'overlay':
            c = replace(c, overlay_ms=90, checkout_ms=20)
        elif name == 'price_changed':
            c = replace(c, price=21, window_ms=3000, competitor_offsets_ms=(1500, 3000))
        elif name == 'uncertain':
            c = replace(c, drop_response=True)
        elif name == 'verification':
            c = replace(c, verification=True)
        else:
            raise ValueError('Unknown scenario: '+name)
        yield c


class SandboxServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self):
        super().__init__(('127.0.0.1', 0), ShopHandler)
        self.rooms = {}
        self.stop_event = threading.Event()


class ShopHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_bytes(self, data, content_type='application/json'):
        try:
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        url = urlparse(self.path)
        key = parse_qs(url.query).get('run', [''])[0]
        room = self.server.rooms.get(key)
        if room is None:
            self.send_error(404)
            return
        if url.path == '/room':
            room.start()  # Merchant clock starts on navigation, never on runner readiness.
            raw = (ROOT/'tests/fixtures/flash_room.html').read_text(encoding='utf-8')
            raw = raw.replace('__SCENARIO__', json.dumps(asdict(room.config)))
            self.send_bytes(raw.encode(), 'text/html; charset=utf-8')
        elif url.path == '/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            previous = None
            try:
                while not self.server.stop_event.is_set():
                    snapshot = room.snapshot()
                    version = snapshot['stock'], snapshot['released']
                    if version != previous:
                        previous = version
                        # Push latency can leave the UI temporarily showing stale stock.
                        time.sleep(room.config.push_ms/1000)
                        self.wfile.write(('data: '+json.dumps(snapshot)+'\n\n').encode())
                        self.wfile.flush()
                    self.server.stop_event.wait(.005)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        elif url.path == '/checkout':
            time.sleep(room.config.checkout_ms/1000)
            self.send_bytes(json.dumps(room.snapshot()).encode())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != '/order':
            self.send_error(404)
            return
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        room = self.server.rooms[payload['run']]
        time.sleep(room.config.order_ms/1000)
        answer = room.submit(payload['run'], payload.get('product'), payload.get('price'), payload.get('quantity'))
        time.sleep(room.config.response_ms/1000)
        if room.config.drop_response and answer['success']:
            self.close_connection = True  # Accepted server-side; client cannot prove it.
            return
        self.send_bytes(json.dumps(answer).encode())


def load_baseline(path):
    from PyInstaller.archive.readers import CArchiveReader
    module = types.ModuleType('_sandbox_baseline')
    module.__file__ = str(ROOT/'dy_grab_gui.py')
    sys.modules[module.__name__] = module
    exec(marshal.loads(CArchiveReader(str(path)).extract('dy_grab_gui')), module.__dict__)
    return module


def distribution(samples):
    values = sorted(value for value in samples if value is not None)
    if not values:
        return None
    return {'median': round(statistics.median(values), 2),
            'p95': round(values[max(0, __import__('math').ceil(.95*len(values))-1)], 2),
            'max': round(max(values), 2)}


def summarize(rows):
    output = {}
    for row in rows:
        output.setdefault(row['variant']+'/'+row['scenario'], []).append(row)
    return {key: {'trials': len(group), 'accepted': sum(r['accepted'] for r in group),
                  'confirmed': sum(r['confirmed'] for r in group),
                  'server_release_to_accept_ms': distribution(r['server_release_to_accept_ms'] for r in group),
                  'display_to_submit_ms': distribution(r['display_to_submit_ms'] for r in group),
                  'display_to_buy_ms': distribution(r.get('display_to_buy_ms') for r in group),
                  'display_to_confirm_ms': distribution(r['display_to_confirm_ms'] for r in group),
                  'open_to_confirm_ms': distribution(r['open_to_confirm_ms'] for r in group)}
            for key, group in output.items()}


def isolated_context(browser, origin, blocked):
    context = browser.new_context(viewport={'width': 1365, 'height': 900}, service_workers='block')
    def guard(route):
        address = urlparse(route.request.url)
        if f'{address.scheme}://{address.netloc}' == origin:
            route.continue_()
        else:
            blocked.append(route.request.url)
            route.abort()
    context.route('**/*', guard)
    context.route_web_socket('**/*', lambda socket: socket.close())
    return context


def main():
    from playwright.sync_api import sync_playwright
    import dy_grab_gui as current
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trials', type=int, default=5)
    parser.add_argument('--scenarios', default='tight,normal,hydration,replaced_card,late_push,slow_checkout,overlay')
    parser.add_argument('--seed', type=int, default=71007)
    parser.add_argument('--compare', action='store_true')
    parser.add_argument('--experimental', action='store_true', help='Enable experimental purchase only for after variant')
    parser.add_argument('--detail-lock', action='store_true', help='Enable detail lock only for after variant; local pending-order acknowledgment')
    parser.add_argument('--baseline', default='build/before-realistic-sandbox.exe')
    parser.add_argument('--report', default='build/realistic-sandbox.json')
    args = parser.parse_args()
    versions = [('after', current)]
    if args.compare:
        versions.insert(0, ('before', load_baseline(ROOT/args.baseline)))
    server = SandboxServer()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    rows = []
    report_path = ROOT/args.report
    metadata = {'scope': 'Local sandbox only; not the Douyin backend or a real success rate',
                'experimental_after': args.experimental,
                'seed': args.seed, 'source_sha256': hashlib.sha256((ROOT/'dy_grab_gui.py').read_bytes()).hexdigest(),
                'baseline_sha256': hashlib.sha256((ROOT/args.baseline).read_bytes()).hexdigest() if args.compare else None}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True, args=['--disable-background-networking'])
            metadata['browser_version'] = browser.version
            for trial in range(args.trials):
                for config in cases_for(args.seed+trial, args.scenarios.split(',')):
                    for variant, module in (versions if trial%2 == 0 else list(reversed(versions))):
                        key = f'{variant}-{config.name}-{trial}'
                        room = InventoryRoom(config)
                        server.rooms[key] = room
                        external = []
                        context = isolated_context(browser, origin, external)
                        page = context.new_page()
                        logs, stages = [], []
                        runner = module.AutomationRunner(module.AutomationConfig(
                            live_url=f'{origin}/room?run={key}'+('&detailLock=1' if args.detail_lock and variant == 'after' else ''), product_name=module.DEFAULT_PRODUCT_NAME,
                            product_id='1', target_price=20, dry_run=False, auto_pay=True,
                            strict_price_match=True, strict_product_match=True, monitor_duration_ms=6000,
                            save_diagnostics=False), lambda level, message: logs.append([level, message]), threading.Event())
                        if args.experimental and variant == 'after':
                            runner.config.experimental_purchase_click = True
                        if args.detail_lock and variant == 'after':
                            runner.config.detail_lock_mode = True
                            runner.config.auto_pay = False
                        for method in ('find_and_click_buy_action', 'safe_click', 'advance_order_flow',
                                       'flash_payment_snapshot', 'wait_for_payment_success'):
                            original = getattr(runner, method)
                            def timed(*a, _method=method, _original=original, **kw):
                                start = time.monotonic()
                                try:
                                    return _original(*a, **kw)
                                finally:
                                    stages.append({'name': _method, 'ms': round((time.monotonic()-start)*1000, 3)})
                            setattr(runner, method, timed)
                        stopped = None
                        try:
                            runner.run_flash_sale(page, 0)
                        except module.GracefulStop as exc:
                            stopped = str(exc)
                        result = page.evaluate('window.result')
                        accepted = len(room.orders) == 1
                        confirmed = accepted and (runner.state.get('order_lock_confirmed', False)
                                                  if args.detail_lock and variant == 'after' else runner.state['payment_confirmed'])
                        def delta(end, start):
                            return round(result[end]-result[start], 3) if result[end] is not None and result[start] is not None else None
                        row = {'variant': variant, 'scenario': config.name, 'trial': trial,
                               'config': asdict(config), 'accepted': accepted, 'confirmed': confirmed,
                               'server_release_to_accept_ms': room.accepted_after_release_ms,
                               'display_to_submit_ms': delta('submitAt', 'readyAt'),
                               'display_to_buy_ms': delta('buyAt', 'readyAt'),
                               'display_to_confirm_ms': delta('confirmAt', 'readyAt') if confirmed else None,
                               'open_to_confirm_ms': result['confirmAt'] if confirmed else None,
                               'buy_clicks': result['buyClicks'], 'payment_clicks': result['payClicks'],
                               'server_order_attempts': room.order_attempts,
                               'stop_reason': stopped, 'external_requests_blocked': len(external),
                               'confirmation_kind': 'pending_order' if args.detail_lock and variant == 'after' else 'simulated_payment_success',
                               'log': logs, 'stages': stages}
                        assert len(room.orders) <= 1 and room.order_attempts <= 1 and result['payClicks'] <= 1 and result['buyClicks'] <= 1, row
                        assert not (runner.state['payment_confirmed'] and not accepted), 'False success'
                        rows.append(row)
                        report = {'metadata': metadata, 'rows': rows, 'summary': summarize(rows)}
                        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                        print(json.dumps({k: v for k, v in row.items() if k not in ('config', 'log', 'stages')}, ensure_ascii=True), flush=True)
                        if trial == 0:
                            page.screenshot(path=str(ROOT/f'output/playwright/sandbox-{variant}-{config.name}.png'))
                        context.close()
            browser.close()
    finally:
        server.stop_event.set()
        server.shutdown()
        server.server_close()
    print(json.dumps(summarize(rows), ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
