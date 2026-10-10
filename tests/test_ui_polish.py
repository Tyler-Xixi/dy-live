import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dy_grab_gui as app


def settle(window, seconds=.24):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        window.update()
        time.sleep(.008)


class UIPolishTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.settings = patch.object(app, 'APP_DATA_DIR', Path(self.folder.name))
        self.settings.start()
        self.window = app.App()
        self.window.update()

    def tearDown(self):
        self.window.destroy()
        self.settings.stop()
        self.folder.cleanup()

    def test_task_switch_preserves_content_height_and_common_controls(self):
        tabs = self.window.task_tabs
        self.assertGreater(tabs.pages[0].winfo_height(), 500)
        common = next(child for child in tabs.master.winfo_children() if isinstance(child, app.ttk.LabelFrame))
        common_y = common.winfo_y()
        height = tabs.winfo_height()
        before = self.window.start_button.winfo_rooty()
        tabs.select(1)
        settle(self.window)
        self.assertEqual(tabs.winfo_height(), height)
        self.assertEqual(common.winfo_y(), common_y)
        self.assertEqual(self.window.start_button.winfo_rooty(), before)
        self.assertEqual(self.window.config_from_form().mode, 'batch')
        tabs.select(0)
        settle(self.window)
        self.assertEqual(tabs.winfo_height(), height)
        self.assertEqual(self.window.config_from_form().mode, 'flash')

    def test_both_tab_bars_share_the_same_top_baseline(self):
        self.assertEqual(self.window.task_tabs.winfo_rooty(), self.window.side_tabs.winfo_rooty())

    def test_close_before_first_idle_cancels_widget_owned_callbacks(self):
        pending = app.App()
        pending.destroy()
        self.window.update()

    def test_fast_tab_switch_finishes_on_last_page_without_vertical_offset(self):
        tabs = self.window.side_tabs
        self.assertIsInstance(tabs, app.SmoothNotebook)
        geometry = (tabs.winfo_y(), tabs.winfo_height())
        for index in (1, 2, 0, 2, 1):
            tabs.select(index)
            self.window.update()
        settle(self.window)
        self.assertEqual(tabs.index(tabs.select()), 1)
        self.assertEqual((tabs.winfo_y(), tabs.winfo_height()), geometry)
        page = self.window.nametowidget(tabs.select())
        self.assertTrue(page.winfo_ismapped())
        self.assertEqual(page.winfo_y(), tabs.header.winfo_height())
        self.assertEqual(page.winfo_x(), 0)

    def test_checkbox_edges_have_antialiasing(self):
        image = self.window.checkbox_images[1]
        colors = {image.get(x, y) for y in range(image.height()) for x in range(image.width())}
        # A binary pixel mask has only background, fill and tick colors.
        self.assertGreater(len(colors), 12)

    def test_scrollbar_drag_and_wheel_stay_in_bounds(self):
        panel = app.tk.Toplevel(self.window)
        panel.geometry('200x180')
        canvas = app.tk.Canvas(panel, height=150, width=180, yscrollincrement=1)
        canvas.pack(side='left', fill='both', expand=True)
        canvas.create_rectangle(0, 0, 180, 1500)
        canvas.configure(scrollregion=(0, 0, 180, 1500))
        bar = app.SmoothScrollbar(panel, command=canvas.yview,
                                  on_interact=lambda: self.window.cancel_scroll(canvas))
        bar.pack(side='right', fill='y')
        canvas.configure(yscrollcommand=bar.set)
        self.window.update()
        self.window.smooth_panel_scroll(canvas, -120)
        settle(self.window)
        self.assertGreater(canvas.yview()[0], 0)
        self.assertLess(canvas.yview()[0], .2)
        self.window.smooth_panel_scroll(canvas, -12000)
        settle(self.window, .45)
        self.assertAlmostEqual(canvas.yview()[1], 1, places=2)
        self.window.smooth_panel_scroll(canvas, 12000)
        settle(self.window, .45)
        self.assertAlmostEqual(canvas.yview()[0], 0, places=2)
        # Dragging interrupts a pending wheel animation, so it cannot snap back.
        self.window.smooth_panel_scroll(canvas, -120)
        bar.event_generate('<Button-1>', x=7, y=8)
        bar.event_generate('<B1-Motion>', x=7, y=bar.winfo_height()-5)
        bar.event_generate('<ButtonRelease-1>', x=7, y=bar.winfo_height()-5)
        settle(self.window)
        self.assertAlmostEqual(canvas.yview()[1], 1, places=2)
        self.assertNotIn(canvas, self.window.scroll_motion)
        panel.destroy()


if __name__ == '__main__':
    unittest.main()
