import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

import test_update_transaction as transaction_fixtures
from update_protocol import UpdateError
from update_process import ProcessIdentity
from dy_live_updater import validate_request, finish_startup, main, run_request
from update_client import atomic_json
from update_transaction import UpdateTransaction, recover_transaction


class UpdaterTests(unittest.TestCase):
    setup_install=transaction_fixtures.TransactionTests.setup_install
    assert_old=transaction_fixtures.TransactionTests.assert_old
    def job(self,prepared):
        token='a'*64
        parent=ProcessIdentity(123,456,str(prepared.install_dir/'DYLiveAssistant.exe'))
        value={'protocol':1,'token':token,'parent':vars(parent),'prepared':{key:str(value) for key,value in vars(prepared).items()}}
        path=prepared.work_dir/'job.json'; atomic_json(path,value)
        return path,parent,token

    def test_ready_handshake_precedes_app_close_and_helper_rechecks_signature(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared,old,_=self.setup_install(Path(folder)/'case'); job,parent,_=self.job(prepared)
            with patch('dy_live_updater.process_identity',return_value=parent):
                validated=validate_request(job,public_keys=self.keys)
                self.assertEqual(validated[0],prepared)
                prepared.archive.write_bytes(prepared.archive.read_bytes()+b'bad')
                with self.assertRaises(UpdateError): validate_request(job,public_keys=self.keys)
            self.assert_old(prepared,old)
            self.assertFalse((prepared.work_dir/'ready.json').exists())

    def test_new_process_ack_failure_restores_or_waits_for_manual_close(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared,old,_=self.setup_install(Path(folder)/'case'); _,_,token=self.job(prepared)
            transaction=UpdateTransaction(prepared,public_keys=self.keys); transaction.install()
            child=ProcessIdentity(100,200,str(prepared.install_dir/'DYLiveAssistant.exe'))
            with patch('dy_live_updater.process_identity',return_value=child):
                self.assertEqual(finish_startup(transaction,child,token,timeout=0),'awaiting_manual_close')
            self.assertEqual((prepared.install_dir/'DYLiveAssistant.exe').read_bytes(),b'new-main')
            with patch('dy_live_updater.process_identity',return_value=None),patch('dy_live_updater.installation_processes',return_value=[]):
                self.assertEqual(finish_startup(transaction,child,token,timeout=0),'restored')
            self.assert_old(prepared,old)

    def test_recovery_runs_when_main_exe_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared,old,_=self.setup_install(Path(folder)/'case')
            transaction=UpdateTransaction(prepared,public_keys=self.keys)
            def fail(phase,name,point):
                if phase=='backup' and name=='DYLiveAssistant.exe' and point=='after_move': raise OSError('power loss')
            with patch.object(transaction,'_checkpoint',side_effect=fail),self.assertRaises(OSError): transaction.install()
            self.assertFalse((prepared.install_dir/'DYLiveAssistant.exe').exists())
            self.assertEqual(recover_transaction(prepared.work_dir/'journal.json',public_keys=self.keys),'restored')
            self.assert_old(prepared,old)

    def test_main_ack_can_validate_job_without_being_the_helper(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared,_,_=self.setup_install(Path(folder)/'case'); job,_,_=self.job(prepared)
            with patch('dy_live_updater.sys.frozen',True,create=True),patch('dy_live_updater.sys.executable',str(prepared.install_dir/'DYLiveAssistant.exe')):
                actual,_,_=validate_request(job,public_keys=self.keys,require_parent=False)
                self.assertEqual(actual,prepared)

    def test_invalid_cli_request_does_not_write_arbitrary_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'not-a-job.json'
            with patch('dy_live_updater.sys.argv',['updater','--request',str(path)]):
                self.assertEqual(main(),1)
            self.assertFalse((Path(folder)/'result.json').exists())

    def test_ready_is_not_install_authority_without_final_consent(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared,old,_=self.setup_install(Path(folder)/'case'); job,parent,token=self.job(prepared)
            transaction=Mock(); transaction.state={}
            with patch('dy_live_updater.validate_request',return_value=(prepared,parent,token)),patch('dy_live_updater.UpdateTransaction',return_value=transaction),patch('dy_live_updater.verify_helper'),patch('dy_live_updater.wait_for_installation_exit',return_value=True):
                self.assertEqual(run_request(job),2)
            transaction.install.assert_not_called()
            self.assert_old(prepared,old)

    def test_second_handoff_cannot_be_ready_while_first_owns_session(self):
        import shutil
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as folder:
            prepared,old,_=self.setup_install(Path(folder)/'case'); job,parent,token=self.job(prepared)
            work=prepared.work_dir.parent/('b'*32)
            shutil.copytree(prepared.work_dir,work)
            second=replace(prepared,transaction_id='b'*32,work_dir=work,archive=work/'package.zip',manifest_file=work/'manifest.json',staged_dir=work/'staged')
            second_job,_,_=self.job(second)
            def validate(path): return (prepared if path==job else second),parent,token
            entered=[]
            def waiting(*args):
                if entered: return False
                entered.append(True)
                with self.assertRaises(UpdateError): run_request(second_job)
                self.assertFalse((work/'ready.json').exists())
                return False
            with patch('dy_live_updater.validate_request',side_effect=validate),patch('dy_live_updater.UpdateTransaction',return_value=Mock()),patch('dy_live_updater.verify_helper'),patch('dy_live_updater.wait_for_installation_exit',side_effect=waiting):
                self.assertEqual(run_request(job),2)
            self.assert_old(prepared,old)

    def test_restored_old_version_restarts_without_task_arguments(self):
        import dy_live_updater as updater
        with tempfile.TemporaryDirectory() as folder:
            prepared,old,_=self.setup_install(Path(folder)/'case')
            transaction=UpdateTransaction(prepared,public_keys=self.keys); transaction.install()
            result=transaction.rollback()
            commands=[]
            def start(command,**kwargs): commands.append((command,kwargs)); return Mock(pid=123)
            with patch('dy_live_updater.installation_processes',return_value=[]),patch('dy_live_updater.subprocess.Popen',side_effect=start):
                updater.complete_result(transaction,result)
                updater.complete_result(transaction,'already-restored')
            self.assertEqual(commands,[([str(prepared.install_dir/'DYLiveAssistant.exe')],{'cwd':str(prepared.install_dir)})])
            self.assert_old(prepared,old)

    def test_manual_close_result_is_visible_with_recovery_path(self):
        import tkinter as tk
        import dy_live_updater as updater
        window=tk.Tk(); observed=[]
        def inspect():
            def texts(widget):
                result=[]
                for child in widget.winfo_children():
                    if 'text' in child.keys(): result.append(str(child['text']))
                    result.extend(texts(child))
                return result
            observed.append((window.winfo_viewable(),' '.join(texts(window))))
            window.destroy()
        window.after(100,inspect)
        try:
            with patch('tkinter.Tk',return_value=window):
                updater.show_result('awaiting_manual_close',Path('C:/owned/transaction'))
        finally:
            try: window.destroy()
            except tk.TclError: pass
        self.assertEqual(observed[0][0],1)
        self.assertIn('手动关闭',observed[0][1])
        # A made-up/nonexistent transaction is not a usable recovery record.
        self.assertNotIn('--recover',observed[0][1])


if __name__=='__main__': unittest.main()
