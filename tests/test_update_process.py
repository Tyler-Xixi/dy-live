import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from update_process import ProcessIdentity, process_identity, wait_for_installation_exit
from update_protocol import UpdateError


class ProcessTests(unittest.TestCase):
    def test_transitional_unknown_identity_waits_for_proven_exit(self):
        parent=ProcessIdentity(123,456,'C:/test/DYLiveAssistant.exe')
        unknown=UpdateError('软件进程123仍运行但身份不能确认')
        with patch('update_process.process_identity',return_value=None),patch('update_process.installation_processes',side_effect=[unknown,[]]):
            self.assertTrue(wait_for_installation_exit(parent,Path('C:/test'),timeout=1))

    def test_unknown_live_identity_at_deadline_still_blocks(self):
        parent=ProcessIdentity(123,456,'C:/test/DYLiveAssistant.exe')
        with patch('update_process.process_identity',return_value=None),patch('update_process.installation_processes',side_effect=UpdateError('identity unknown')):
            with self.assertRaises(UpdateError): wait_for_installation_exit(parent,Path('C:/test'),timeout=0)

    def test_parent_exit_timeout_keeps_old_files(self):
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'DYLiveAssistant.exe'; marker.write_bytes(b'old')
            child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(0.4)'])
            identity=process_identity(child.pid)
            self.assertIsNotNone(identity)
            self.assertFalse(wait_for_installation_exit(identity,Path(folder),timeout=0.03))
            self.assertEqual(marker.read_bytes(),b'old')
            child.wait(timeout=5)
            self.assertTrue(wait_for_installation_exit(identity,Path(folder),timeout=1))

    def test_other_instance_or_reused_pid_blocks_replace(self):
        parent=ProcessIdentity(123,456,'C:/test/DYLiveAssistant.exe')
        other=ProcessIdentity(123,457,'C:/test/DYLiveAssistant.exe')
        with patch('update_process.process_identity',return_value=other),patch('update_process.installation_processes',return_value=[other]):
            self.assertFalse(wait_for_installation_exit(parent,Path('C:/test'),timeout=0))
        with patch('update_process.process_identity',return_value=None),patch('update_process.installation_processes',return_value=[other]):
            self.assertFalse(wait_for_installation_exit(parent,Path('C:/test'),timeout=0))


if __name__=='__main__': unittest.main()
