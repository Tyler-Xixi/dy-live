import io,json,time,tkinter as tk,unittest
from tkinter import ttk
from unittest.mock import patch
import announcement_ui as ui
class BulletinUITests(unittest.TestCase):
    def setUp(self):self.root=tk.Tk();self.root.withdraw();self.errors=[];self.root.report_callback_exception=lambda *a:self.errors.append(a)
    def tearDown(self):self.root.destroy()
    def pump(self):
        end=time.monotonic()+.35
        while time.monotonic()<end:self.root.update();time.sleep(.01)
    def open(self,payload):
        with patch.object(ui.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
            ui.show_announcements(self.root);self.pump()
        return next(x for x in self.root.winfo_children() if isinstance(x,tk.Toplevel))
    def test_public_text_and_time_without_license(self):
        w=self.open({'items':[{'title':'<script>x</script>','content':'plain','published_at':0}]})
        text=next(x for x in w.winfo_children() if isinstance(x,tk.Text)).get('1.0','end')
        self.assertIn('<script>x</script>',text);self.assertIn('1970-01-01 08:00:00',text);self.assertFalse(self.errors)
    def test_bad_timestamp_retry(self):
        w=self.open({'items':[{'title':'x','content':'x','published_at':10**100}]})
        button=next(x for x in w.winfo_children() if isinstance(x,ttk.Button))
        self.assertEqual(str(button.cget('state')),'normal')
        with patch.object(ui.urllib.request,'urlopen',return_value=io.BytesIO(b'{"items":[]}')):button.invoke();self.pump()
        self.assertFalse(self.errors)
    def test_close_during_fetch_does_not_callback_into_destroyed_ui(self):
        def delayed(*a,**k):time.sleep(.1);return io.BytesIO(b'{"items":[]}')
        with patch.object(ui.urllib.request,'urlopen',side_effect=delayed):
            ui.show_announcements(self.root);next(x for x in self.root.winfo_children() if isinstance(x,tk.Toplevel)).destroy();self.pump()
        self.assertFalse(self.errors)
