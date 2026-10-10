"""Protect data without mistaking an empty profile for the install folder."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import dy_grab_gui as gui
import dy_live_updater as helper
from update_client import validate_protected_paths
from update_protocol import UpdateError


class UpdateDiagnosticTests(unittest.TestCase):
    def test_empty_profile_uses_default_not_install_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            install=Path(folder)/'program'; install.mkdir()
            default=Path(folder)/'data'/'profile'
            app=SimpleNamespace(vars={'profile_dir':SimpleNamespace(get=lambda:'')})
            with patch.object(gui,'DEFAULT_PROFILE_DIR',default), patch.object(gui,'APP_DATA_DIR',default.parent):
                paths=gui.App.update_protected_paths(app)
                self.assertEqual(paths[-1],default)
                self.assertEqual(validate_protected_paths(install,paths),install)

    def test_whitespace_profile_uses_same_default(self):
        app=SimpleNamespace(vars={'profile_dir':SimpleNamespace(get=lambda:'  \t ')})
        self.assertEqual(gui.App.update_protected_paths(app)[-1],gui.DEFAULT_PROFILE_DIR)

    def test_real_internal_profile_still_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaises(UpdateError):
                validate_protected_paths(root,(root/'_internal'/'profile',))

    def test_custom_path_strips_outer_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            expected=Path(folder)/'custom profile'
            app=SimpleNamespace(vars={'profile_dir':SimpleNamespace(get=lambda:f' {expected} ')})
            self.assertEqual(gui.App.update_protected_paths(app)[-1],expected)

    def test_no_journal_has_no_recovery_command(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(helper.recovery_details(Path(folder)),'')

    def test_malformed_journal_has_no_recovery_command(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'journal.json').write_text('{}')
            self.assertEqual(helper.recovery_details(root),'')

    def test_valid_journal_offers_existing_recovery_tool(self):
        from test_updater_entry import UpdaterTests
        from update_transaction import UpdateTransaction
        fixture=UpdaterTests()
        with tempfile.TemporaryDirectory() as folder:
            prepared,_,_=fixture.setup_install(Path(folder)/'case')
            fixture.job(prepared)
            (prepared.work_dir/'DYLiveUpdater.exe').write_bytes(b'new-updater')
            transaction=UpdateTransaction(prepared,public_keys=fixture.keys)
            transaction.install()
            with patch.object(helper,'UPDATE_PUBLIC_KEYS',fixture.keys):
                details=helper.recovery_details(prepared.work_dir)
            self.assertIn('--recover',details)
            self.assertIn(str(prepared.work_dir/'journal.json'),details)

    def test_helper_can_offer_verified_full_bundle_before_replacement(self):
        from test_updater_entry import UpdaterTests
        fixture=UpdaterTests()
        with tempfile.TemporaryDirectory() as folder:
            prepared,_,_=fixture.setup_install(Path(folder)/'case'); fixture.job(prepared)
            candidate=helper.portable_candidate(prepared.work_dir,public_keys=fixture.keys)
            self.assertEqual(candidate[0],prepared)
            self.assertEqual(candidate[1].version,'6.1.1')

    def test_helper_does_not_offer_portable_before_unfinished_restore(self):
        from test_updater_entry import UpdaterTests
        from update_transaction import UpdateTransaction
        fixture=UpdaterTests()
        with tempfile.TemporaryDirectory() as folder:
            prepared,_,_=fixture.setup_install(Path(folder)/'case'); fixture.job(prepared)
            transaction=UpdateTransaction(prepared,public_keys=fixture.keys); transaction.install()
            self.assertIsNone(helper.portable_candidate(prepared.work_dir,public_keys=fixture.keys))

    def test_invalid_text_fields_cannot_smuggle_secrets(self):
        from update_diagnostics import UpdateDiagnosticLog
        with tempfile.TemporaryDirectory() as folder:
            log=UpdateDiagnosticLog(Path(folder))
            log.record('download',{'version':'secret','pid':'secret','package_type':'secret'})
            output=log.export(Path(folder)/'diagnostic.json')
            self.assertNotIn('secret',output.read_text(encoding='utf-8'))

    def test_diagnostic_export_does_not_disclose_user_or_credentials(self):
        from update_diagnostics import UpdateDiagnosticLog
        with tempfile.TemporaryDirectory() as folder:
            log=UpdateDiagnosticLog(Path(folder))
            log.record('preflight',{'install_path':r'C:\Users\PrivatePerson\Desktop\app',
                'pid':6968,'error_code':5,'token':'secret-token','cookie':'secret-cookie',
                'message':'license secret-key','version':'6.1.2','target_version':'6.1.3'})
            output=log.export(Path(folder)/'diagnostic.json')
            raw=output.read_text(encoding='utf-8')
            for value in ('PrivatePerson','secret-token','secret-cookie','secret-key'):
                self.assertNotIn(value,raw)
            data=json.loads(raw)
            self.assertEqual(data['records'][0]['pid'],6968)
            self.assertEqual(data['records'][0]['error_code'],5)

    def test_logs_remain_bounded(self):
        from update_diagnostics import UpdateDiagnosticLog
        with tempfile.TemporaryDirectory() as folder:
            log=UpdateDiagnosticLog(Path(folder),max_bytes=512)
            for _ in range(30): log.record('download',{'bytes':100,'package_type':'full'})
            files=list((Path(folder)/'update-diagnostics').glob('*.jsonl'))
            self.assertLessEqual(len(files),3)
            self.assertTrue(all(p.stat().st_size<=512 for p in files))

    def test_client_records_check_and_prepare_stages(self):
        from test_update_client import UpdateClientTests
        fixture=UpdateClientTests(); fixture.setUp()
        try:
            manifest=fixture.client.check('6.1.0',fixture.cancel)
            fixture.client.prepare(manifest,fixture.install,(),fixture.cancel,lambda *_:None)
            output=fixture.client.diagnostics.export(fixture.root/'export.json')
            stages=[r['stage'] for r in json.loads(output.read_text(encoding='utf-8'))['records']]
            self.assertIn('check',stages)
            self.assertIn('preflight',stages)
            self.assertIn('download',stages)
            self.assertIn('verify',stages)
        finally: fixture.tearDown()

    def test_client_failure_records_stage_without_raw_exception(self):
        from test_update_client import UpdateClientTests
        fixture=UpdateClientTests(); fixture.setUp()
        try:
            with patch.object(fixture.transport,'fetch_manifest',side_effect=OSError('secret-token')):
                with self.assertRaises(OSError): fixture.client.check('6.1.0',fixture.cancel)
            output=fixture.client.diagnostics.export(fixture.root/'export.json')
            raw=output.read_text(encoding='utf-8')
            self.assertNotIn('secret-token',raw)
            self.assertEqual(json.loads(raw)['records'][-1]['result'],'failed')
        finally: fixture.tearDown()

    def test_export_includes_helper_phase_logs_not_job_tokens(self):
        from test_update_client import UpdateClientTests
        from update_diagnostics import UpdateDiagnosticLog
        fixture=UpdateClientTests(); fixture.setUp()
        try:
            work=fixture.install/'.dy-update-transactions'/('a'*32); work.mkdir(parents=True)
            (work/'job.json').write_text('{"token":"secret-token"}')
            UpdateDiagnosticLog(work).record('wait_exit',{'pid':6968,'error_code':5,'result':'failed'})
            result=fixture.client.export_diagnostics(fixture.root/'export.json',fixture.install)
            raw=result.read_text(encoding='utf-8')
            self.assertNotIn('secret-token',raw)
            self.assertTrue(any(r['stage']=='wait_exit' and r['pid']==6968 for r in json.loads(raw)['records']))
        finally: fixture.tearDown()


if __name__=='__main__': unittest.main()
