import ast,hashlib,json,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WINDOWS=Path(r'D:\SoftWare-Work\CodexProject\dy-live-main')

class IsolationTests(unittest.TestCase):
 @unittest.skipUnless(WINDOWS.is_dir(),'Windows baseline exists only on development host')
 def test_windows_source_and_assets_untouched(self):
  for name,digest in json.loads((ROOT/'windows-baseline.json').read_text(encoding='utf-8')).items():
   self.assertEqual(hashlib.sha256((WINDOWS/name).read_bytes()).hexdigest(),digest,name)
 @unittest.skipUnless(WINDOWS.is_dir(),'Windows baseline exists only on development host')
 def test_entire_runner_and_order_modules_unchanged(self):
  def runner(path):
   t=ast.parse(path.read_text(encoding='utf-8'))
   return ast.dump(next(n for n in t.body if isinstance(n,ast.ClassDef) and n.name=='AutomationRunner'),include_attributes=False)
  self.assertEqual(runner(WINDOWS/'dy_grab_gui.py'),runner(ROOT/'dy_grab_gui.py'))
  for name in ('batch_receipt.py','batch_queue.py','batch_accounts.py','browser_settings.py','license_protocol.py','online_license.py'):
   self.assertEqual((WINDOWS/name).read_bytes(),(ROOT/name).read_bytes(),name)
 def test_no_windows_updater_or_executable_in_fork(self):
  self.assertFalse((ROOT/'update_process.py').exists())
  self.assertFalse(list(ROOT.rglob('*.exe')))
  imports=ast.parse((ROOT/'dy_grab_gui.py').read_text(encoding='utf-8'))
  self.assertFalse([n for n in ast.walk(imports) if isinstance(n,ast.ImportFrom) and n.module in ('update_client','update_ui','update_process')])

if __name__=='__main__':unittest.main()
