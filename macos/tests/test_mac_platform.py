import hashlib,plistlib,tempfile,unittest
from pathlib import Path,PurePosixPath
from unittest.mock import patch
import mac_platform as m

class MacPlatformTests(unittest.TestCase):
 def test_both_architectures_and_unknown_rejected(self):
  self.assertEqual(m.platform_name('arm64'),'macos-arm64')
  self.assertEqual(m.platform_name('x86_64'),'macos-x64')
  with self.assertRaises(RuntimeError):m.platform_name('amd64')
 def test_data_dir_is_outside_app_and_windows(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=m.application_data_dir(Path(tmp))
   self.assertEqual(p,Path(tmp)/'Library/Application Support/DYLiveAssistantMac')
   self.assertTrue(p.is_dir())
 def test_uuid_not_hostname_and_never_returned_raw(self):
  raw=plistlib.dumps([{'IOPlatformUUID':'test-device-uuid'}])
  with patch.object(m.subprocess,'check_output',return_value=raw):
   self.assertEqual(m.machine_fingerprint(),hashlib.sha256(b'DYLiveAssistant|test-device-uuid').hexdigest())
 def test_device_failure_stops_instead_of_unstable_fallback(self):
  with patch.object(m.subprocess,'check_output',return_value=plistlib.dumps([{}])):
   with self.assertRaises(RuntimeError):m.machine_fingerprint()
 def test_open_folder_passes_path_without_shell(self):
  with patch.object(m.subprocess,'Popen') as popen:
   m.open_directory(PurePosixPath('/tmp/space folder'))
   self.assertEqual(popen.call_args.args[0],['/usr/bin/open','/tmp/space folder'])
 def test_windows_update_never_networks(self):
  from mac_update_ui import UpdateController
  c=UpdateController(None)
  self.assertFalse(c.busy());c.check();c.close();self.assertFalse(c.busy())

if __name__=='__main__':unittest.main()
