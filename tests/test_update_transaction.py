import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from update_fixtures import sample_release, envelope
from update_client import UpdateClient, UpdateTransport
from test_update_client import Response
from update_protocol import UpdateError
from update_transaction import UpdateTransaction, InstallationLock, recover_transaction


class TransactionTests(unittest.TestCase):
    def setup_install(self,root,extras=None):
        root.mkdir(parents=True)
        archive,payload,contents=sample_release(root,extras=extras)
        raw,keys=envelope(payload)
        self.keys=keys
        install=root/'安装'; install.mkdir()
        old={name:b'old-'+data for name,data in contents.items()}
        old['_internal/old-only.dll']=b'old-only'
        for name,data in old.items():
            target=install/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
        (install/'unknown.txt').write_bytes(b'keep')
        transport=UpdateTransport('https://license.txblog.cn',opener=lambda request,timeout:Response(raw if request.full_url.endswith('latest.json') else archive.read_bytes(),request.full_url))
        client=UpdateClient(root/'data',keys,transport); cancel=threading.Event()
        manifest=client.check('6.1.0',cancel)
        prepared=client.prepare(manifest,install,(),cancel,lambda *_:None)
        return prepared,old,contents

    def assert_old(self,prepared,old):
        for name,data in old.items(): self.assertEqual((prepared.install_dir/name).read_bytes(),data,name)
        self.assertEqual((prepared.install_dir/'unknown.txt').read_bytes(),b'keep')

    def test_replace_program_files_preserves_unknown_and_user_data(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared,old,new=self.setup_install(Path(folder)/'case')
            outside=Path(folder)/'profile'; outside.write_bytes(b'profile')
            transaction=UpdateTransaction(prepared,public_keys=self.keys); transaction.preflight((outside,))
            backup=transaction.install()
            for name,data in new.items(): self.assertEqual((prepared.install_dir/name).read_bytes(),data)
            self.assertFalse((prepared.install_dir/'_internal/old-only.dll').exists())
            self.assertEqual((backup/'_internal/old-only.dll').read_bytes(),b'old-only')
            self.assertEqual(outside.read_bytes(),b'profile')
            transaction.rollback(); self.assert_old(prepared,old)

    def test_fault_at_every_move_restores_complete_old_version(self):
        with tempfile.TemporaryDirectory() as folder:
            for phase in ('backup','replace'):
                for name in ('DYLiveAssistant.exe','DYLiveUpdater.exe','_internal','用户使用说明.md','release-info.json'):
                    for point in ('intent','before_move','after_move','completed'):
                        with self.subTest(phase=phase,name=name,point=point):
                            root=Path(folder)/f'case-{len(list(Path(folder).iterdir()))}'
                            prepared,old,_=self.setup_install(root)
                            transaction=UpdateTransaction(prepared,public_keys=self.keys)
                            def fail(actual_phase,actual_name,actual_point):
                                if (actual_phase,actual_name,actual_point)==(phase,name,point): raise OSError('injected failure')
                            with patch.object(transaction,'_checkpoint',side_effect=fail),self.assertRaises(OSError): transaction.install()
                            self.assertEqual(recover_transaction(prepared.work_dir/'journal.json',public_keys=self.keys),'restored')
                            self.assert_old(prepared,old)

    def test_recovery_is_idempotent_after_interrupted_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared,old,_=self.setup_install(Path(folder)/'case')
            transaction=UpdateTransaction(prepared,public_keys=self.keys); transaction.install()
            import update_transaction
            original=update_transaction.os.replace; calls=[]
            def fail(source,target):
                original(source,target)
                if Path(source).parent.name=='backup':
                    calls.append(source)
                    if len(calls)==1: raise OSError('interrupted restore')
            with patch('update_transaction.os.replace',side_effect=fail),self.assertRaises(OSError): transaction.rollback()
            self.assertEqual(recover_transaction(prepared.work_dir/'journal.json',public_keys=self.keys),'restored')
            self.assertEqual(recover_transaction(prepared.work_dir/'journal.json',public_keys=self.keys),'already-restored')
            self.assert_old(prepared,old)
            prepared,_,_=self.setup_install(Path(folder)/'committed')
            transaction=UpdateTransaction(prepared,public_keys=self.keys); transaction.install(); transaction.commit()
            self.assertEqual(recover_transaction(prepared.work_dir/'journal.json',public_keys=self.keys),'committed')

    def test_linked_directory_invalid_journal_or_second_installer_cannot_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared,old,_=self.setup_install(Path(folder)/'case')
            with InstallationLock(prepared.install_dir):
                with self.assertRaises(UpdateError):
                    with InstallationLock(prepared.install_dir): pass
            transaction=UpdateTransaction(prepared,public_keys=self.keys); transaction.install()
            journal=prepared.work_dir/'journal.json'
            value=json.loads(journal.read_bytes()); value['install_dir']=folder
            journal.write_text(json.dumps(value))
            with self.assertRaises(UpdateError): recover_transaction(journal,public_keys=self.keys)
            self.assertEqual((prepared.install_dir/'unknown.txt').read_bytes(),b'keep')

    def test_cross_process_lock_and_directory_junction(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared,old,_=self.setup_install(Path(folder)/'case')
            code='import sys; from pathlib import Path; from update_transaction import InstallationLock;\nwith InstallationLock(Path(sys.argv[1])): pass'
            with InstallationLock(prepared.install_dir):
                result=subprocess.run([sys.executable,'-c',code,str(prepared.install_dir)],capture_output=True,timeout=15)
                self.assertNotEqual(result.returncode,0)
                self.assertIn(b'UpdateError',result.stderr)
            outside=Path(folder)/'outside'; outside.mkdir(); (outside/'sentinel').write_bytes(b'keep')
            link=prepared.staged_dir/'_internal'/'linked'
            if os.name=='nt':
                command="New-Item -ItemType Junction -Path $env:DY_UPDATE_TEST_LINK -Target $env:DY_UPDATE_TEST_TARGET | Out-Null"
                # Paths passed through dedicated env vars, not interpolated shell commands.
                environment=dict(os.environ,DY_UPDATE_TEST_LINK=str(link),DY_UPDATE_TEST_TARGET=str(outside))
                result=subprocess.run(['powershell','-NoProfile','-Command',command],env=environment,capture_output=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
            else: link.symlink_to(outside,target_is_directory=True)
            with self.assertRaises(UpdateError): UpdateTransaction(prepared,public_keys=self.keys).preflight(())
            self.assertEqual((outside/'sentinel').read_bytes(),b'keep')

    def test_mixed_case_dependency_directories_compare_by_content(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared,_,_=self.setup_install(Path(folder)/'case',extras={'_internal/Z/file.dll':b'Z','_internal/a/file.dll':b'a'})
            UpdateTransaction(prepared,public_keys=self.keys).preflight(())


if __name__=='__main__': unittest.main()
