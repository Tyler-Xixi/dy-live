"""Exit races must not bypass live-process/permission safety checks."""
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock

import update_process as processes
from update_protocol import UpdateError

def owned_executable(folder):
    executable=Path(folder)/'DYLiveAssistant.exe'
    base=Path(sys._base_executable)
    shutil.copy2(base,executable)
    for dll in base.parent.glob('python*.dll'): shutil.copy2(dll,Path(folder)/dll.name)
    return executable


class ExitRaceTests(unittest.TestCase):
    def test_exited_snapshot_entry_does_not_block_installation(self):
        # Real Toolhelp snapshot, injecting only exit timing before identity read.
        with tempfile.TemporaryDirectory() as folder:
            executable=owned_executable(folder)
            environment=dict(os.environ,PYTHONHOME=sys.base_prefix)
            child=subprocess.Popen([str(executable),'-c','import time; time.sleep(30)'],env=environment)
            original=processes.process_identity
            try:
                def after_exit(pid):
                    if pid==child.pid:
                        child.terminate(); child.wait(timeout=5)  # only this owned test Python
                    return original(pid)
                with patch.object(processes,'process_identity',side_effect=after_exit):
                    self.assertEqual(processes.installation_processes(folder),[])
                self.assertIsNotNone(child.returncode)
            finally:
                if child.poll() is None: child.terminate(); child.wait(timeout=5)

    def test_live_unqueryable_snapshot_entry_still_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            executable=owned_executable(folder)
            child=subprocess.Popen([str(executable),'-c','import time; time.sleep(30)'],env=dict(os.environ,PYTHONHOME=sys.base_prefix))
            original=processes.process_identity
            try:
                with patch.object(processes,'process_identity',side_effect=lambda pid:None if pid==child.pid else original(pid)):
                    with self.assertRaises(UpdateError): processes.installation_processes(folder)
                self.assertIsNone(child.poll())
            finally: child.terminate(); child.wait(timeout=5)

    def test_live_then_exited_process_state(self):
        child=subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.buffer.read(1)'],stdin=subprocess.PIPE)
        try:
            self.assertFalse(processes.process_has_exited(child.pid))
            child.communicate(input=b'x',timeout=5)
            self.assertTrue(processes.process_has_exited(child.pid))
        finally:
            if child.poll() is None: child.terminate(); child.wait(timeout=5)

    def test_access_denied_or_wait_error_is_never_treated_as_exit(self):
        kernel=Mock(); kernel.OpenProcess.return_value=0
        with patch.object(processes.ctypes,'WinDLL',return_value=kernel),patch.object(processes.ctypes,'get_last_error',return_value=5):
            with self.assertRaises(UpdateError): processes.process_has_exited(1234)
        kernel.OpenProcess.return_value=42; kernel.WaitForSingleObject.return_value=0xffffffff
        with patch.object(processes.ctypes,'WinDLL',return_value=kernel),patch.object(processes.ctypes,'get_last_error',return_value=6):
            with self.assertRaises(UpdateError): processes.process_has_exited(1234)

    def test_missing_valid_pid_is_confirmed_without_bypassing_permission_errors(self):
        kernel=Mock(); kernel.OpenProcess.return_value=0
        with patch.object(processes.ctypes,'WinDLL',return_value=kernel),patch.object(processes.ctypes,'get_last_error',return_value=87):
            self.assertTrue(processes.process_has_exited(1234))

    def test_reused_live_pid_blocks_and_opened_handle_is_closed(self):
        kernel=Mock(); kernel.OpenProcess.return_value=42; kernel.WaitForSingleObject.return_value=258
        with patch.object(processes.ctypes,'WinDLL',return_value=kernel):
            self.assertFalse(processes.process_has_exited(1234))
        kernel.CloseHandle.assert_called_once_with(42)

    def test_exited_process_with_still_active_exit_code_is_confirmed(self):
        # Exit code 259 is legal; only the signaled handle proves exit.
        child=subprocess.Popen([sys.executable,'-c','raise SystemExit(259)'])
        child.wait(timeout=5)
        self.assertTrue(processes.process_has_exited(child.pid))

    def test_invalid_pid_does_not_bypass_safety(self):
        for pid in (0,-1,True,0x100000000):
            with self.subTest(pid=pid),self.assertRaises(UpdateError): processes.process_has_exited(pid)
