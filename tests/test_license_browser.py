"""Owned loopback server and disposable browser; never uses real user profiles."""
import dataclasses
import socket
import threading
import time
import unittest
from pathlib import Path
import uvicorn
from playwright.sync_api import sync_playwright
from license_server.app import create_app
from license_test_support import server_fixture


class BrowserTests(unittest.TestCase):
    def test_admin_real_browser_flow_and_responsive_layout(self):
        temp, settings, clock, keys = server_fixture()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        settings = dataclasses.replace(settings, origin=origin)
        app = create_app(settings, clock)
        app.state.auth.initialize("admin", "browser-test-password-only")
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(server.started)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(origin + "/admin")
                page.wait_for_url("**/admin/login")
                page.locator('[name="username"]').fill("admin")
                page.locator('[name="password"]').fill("browser-test-password-only")
                page.get_by_role("button", name="安全登录").click()
                page.wait_for_url("**/admin/cards")
                page.locator('[name="count"]').fill("2")
                page.locator('[name="note"]').fill("本地浏览器测试")
                page.get_by_role("button", name="生成卡密", exact=True).click()
                page.locator("#generated").wait_for(state="visible")
                # Plaintext is checked in memory only, never recorded in an artifact.
                cards = page.locator("#generated-cards").input_value().splitlines()
                self.assertEqual(len(cards), 2)
                with page.expect_download() as event:
                    page.locator("#download-cards").click()
                self.assertTrue(event.value.suggested_filename.endswith(".txt"))
                page.reload()
                self.assertFalse(page.locator("#generated").is_visible())
                self.assertEqual(page.get_by_role("link", name="管理", exact=True).count(), 2)
                output = Path("output/playwright")
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / "license-admin-desktop.png"), full_page=True)
                activated = app.state.cards.bind(cards[0], "test-device")
                with app.state.db.connect(write=True) as connection:
                    connection.execute("UPDATE cards SET expires_at=900 WHERE id=?", (activated.license_id,))
                page.get_by_role("link", name="管理", exact=True).first.click()
                # List order is created_at/id, so select the known activated card explicitly.
                page.goto(origin + "/admin/cards/" + activated.license_id)
                page.locator('[name="note"]').fill("expired-note-edited")
                page.get_by_role("button", name="保存修改").click()
                page.wait_for_timeout(300)
                self.assertEqual(app.state.cards.get(activated.license_id).note, "expired-note-edited")
                page.get_by_role("button", name="停用", exact=True).click()
                page.wait_for_load_state("networkidle")
                page.get_by_role("button", name="恢复授权").wait_for()
                page.get_by_role("button", name="恢复授权").click()
                page.wait_for_load_state("networkidle")
                page.get_by_role("button", name="停用", exact=True).wait_for()
                page.goto(origin + "/admin/cards")
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(output / "license-admin-mobile.png"), full_page=True)
                width = page.evaluate("({document:document.documentElement.scrollWidth,viewport:innerWidth})")
                self.assertLessEqual(width["document"], width["viewport"])
                self.assertEqual(errors, [])
                context.close()
                browser.close()
        finally:
            server.should_exit = True
            worker.join(10)
            listener.close()
            temp.cleanup()
