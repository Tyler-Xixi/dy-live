"""Real Tk geometry: account controls must not cover batch order inputs."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import dy_grab_gui as app


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class BatchLayoutTests(unittest.TestCase):
    def test_account_controls_are_below_order_fields_and_fit_small_window(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'APP_DATA_DIR', Path(folder)):
            window = app.App()
            try:
                window.geometry('980x720')
                window.task_tabs.select(1)
                deadline = time.monotonic() + .3
                while time.monotonic() < deadline:
                    window.update()
                    time.sleep(.01)
                page = window.task_tabs.pages[1]
                switch = next(w for w in descendants(page) if isinstance(w, app.ttk.Checkbutton)
                              and str(w.cget('variable')) == str(window.vars['batch_multi_account']))
                fields = [w for w in descendants(page) if isinstance(w, app.ttk.Entry) and w.master is switch.master.master]
                self.assertGreaterEqual(switch.winfo_rooty(),
                                        max(w.winfo_rooty()+w.winfo_height() for w in fields))
                button = window.account_manager_button
                self.assertIs(switch.master, button.master)
                self.assertLessEqual(switch.winfo_rootx()+switch.winfo_width(), button.winfo_rootx())
                self.assertLessEqual(button.winfo_x()+button.winfo_width(), button.master.winfo_width())
                # Moving widgets must keep their existing variable behavior.
                self.assertFalse(window.vars['batch_multi_account'].get())
                switch.invoke()
                self.assertTrue(window.vars['batch_multi_account'].get())
            finally:
                window.destroy()


if __name__ == '__main__':
    unittest.main()
