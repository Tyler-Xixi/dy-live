"""Authorization latency comparison on the existing owned inventory simulator."""
import json
import statistics
import threading
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import dy_grab_gui as desktop
from license_server.app import create_app
from license_test_support import server_fixture
from online_license import OnlineLicenseClient
from test_online_license import LocalTransport
from tests.flash_sandbox import SandboxServer, Scenario, InventoryRoom, isolated_context


class HotpathTests(unittest.TestCase):
    def test_blocked_license_network_does_not_block_inventory_purchase(self):
        temp, settings, clock, keys = server_fixture()
        app = create_app(settings, clock)
        http = TestClient(app, base_url=settings.origin)
        transport = LocalTransport(http)
        client = OnlineLicenseClient(Path(temp.name)/"client", "simulated-device", keys, transport, clock)
        card = app.state.cards.create_batch(1, None, "local benchmark").cards[0]
        self.assertTrue(client.activate(card).allowed)
        entered, release = threading.Event(), threading.Event()
        def blocked_post(*_):
            entered.set()
            release.wait(60)
            raise OSError("simulated timeout")
        transport.post = blocked_post
        client.start_background(lambda _: None)
        self.assertTrue(entered.wait(5))
        server = SandboxServer()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        rows = []
        try:
            samples = []
            for _ in range(10000):
                start = time.perf_counter_ns()
                self.assertTrue(client.snapshot().allowed)
                samples.append((time.perf_counter_ns()-start)/1e6)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge", headless=True, args=["--disable-background-networking"])
                for trial in range(3):
                    variants = ("no_license_guard", "signed_cached_guard") if trial % 2 == 0 else ("signed_cached_guard", "no_license_guard")
                    for variant in variants:
                        key = f"{variant}-{trial}"
                        room = InventoryRoom(Scenario(name="license_comparison", window_ms=1000, competitor_offsets_ms=(500, 1000)))
                        server.rooms[key] = room
                        context = isolated_context(browser, origin, [])
                        page = context.new_page()
                        runner = desktop.AutomationRunner(desktop.AutomationConfig(
                            live_url=f"{origin}/room?run={key}&detailLock=1", product_name=desktop.DEFAULT_PRODUCT_NAME,
                            product_id="1", target_price=20, dry_run=False, auto_pay=False,
                            monitor_duration_ms=6000, save_diagnostics=False), lambda *_: None, threading.Event(),
                            license_status=client.snapshot if variant=="signed_cached_guard" else None)
                        runner.config.detail_lock_mode = True
                        try: runner.run_flash_sale(page, 0)
                        except desktop.GracefulStop: pass
                        result = page.evaluate("window.result")
                        confirmed = bool(room.orders) and runner.state.get("order_lock_confirmed", False)
                        rows.append({"variant":variant, "trial":trial, "confirmed":confirmed,
                            "server_release_to_accept_ms":room.accepted_after_release_ms,
                            "display_to_submit_ms":result["submitAt"]-result["readyAt"] if result["submitAt"] is not None else None})
                        self.assertTrue(confirmed, rows[-1])
                        self.assertFalse(release.is_set())
                        self.assertLessEqual(room.order_attempts, 1)
                        context.close()
                browser.close()
            report = {"scope":"Local simulation only; not real Douyin latency/success rate", "license_request":"blocked throughout all inventory trials",
                "snapshot_median_ms":statistics.median(samples), "snapshot_p95_ms":sorted(samples)[9499], "rows":rows}
            Path("output/playwright/license-hotpath.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        finally:
            client.close()
            release.set()
            server.stop_event.set()
            server.shutdown()
            server.server_close()
            http.close()
            temp.cleanup()
