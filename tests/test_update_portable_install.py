"""A verified fresh install must never overwrite the old installation/data."""
import tempfile
from pathlib import Path
import unittest
import threading
from unittest.mock import patch

from update_fixtures import sample_release,envelope
from update_protocol import parse_manifest,UpdateError


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.archive,payload,self.files=sample_release(self.root,'6.1.3',4)
        self.payload=payload
        raw,keys=envelope(payload); self.manifest=parse_manifest(raw,keys)
        self.old=self.root/'old'; self.old.mkdir(); (self.old/'data.txt').write_bytes(b'keep')
    def tearDown(self): self.temp.cleanup()

    def test_fresh_complete_install_preserves_old_files(self):
        from update_portable_install import install_full_to_new_directory
        dest=self.root/'new'
        result=install_full_to_new_directory(self.manifest,self.archive,dest,(self.old,))
        self.assertEqual(result,dest)
        self.assertEqual((dest/'DYLiveAssistant.exe').read_bytes(),b'new-main')
        self.assertEqual((dest/'_internal/runtime.dll').read_bytes(),b'new-runtime')
        self.assertEqual((self.old/'data.txt').read_bytes(),b'keep')

    def test_nested_ancestor_and_nonempty_destinations_rejected(self):
        from update_portable_install import install_full_to_new_directory
        for dest in (self.old,self.old/'new',self.root):
            with self.subTest(dest=dest),self.assertRaises(UpdateError):
                install_full_to_new_directory(self.manifest,self.archive,dest,(self.old,))
        self.assertEqual((self.old/'data.txt').read_bytes(),b'keep')

    def test_bad_archive_never_creates_completed_install(self):
        from update_portable_install import install_full_to_new_directory
        self.archive.write_bytes(b'bad')
        dest=self.root/'new'
        with self.assertRaises(UpdateError): install_full_to_new_directory(self.manifest,self.archive,dest,(self.old,))
        self.assertFalse(dest.exists())

    def test_explicit_archive_preview_path_blocked_not_all_temp_paths(self):
        from update_client import preflight_install_dir
        temp=self.root/'360zip$Temp'/'360$'/'1'; temp.mkdir(parents=True)
        with self.assertRaises(UpdateError): preflight_install_dir(temp,(),0)
        self.assertEqual(preflight_install_dir(self.old,(),0),self.old)

    def test_unfinished_transaction_blocks_before_download(self):
        from update_client import preflight_install_dir
        work=self.old/'.dy-update-transactions'/('a'*32); work.mkdir(parents=True)
        (work/'journal.json').write_text('{"phase":"replacing"}')
        with self.assertRaises(UpdateError): preflight_install_dir(self.old,(),0)

    def test_fresh_install_space_rejected_before_download(self):
        from update_client import UpdateClient,UpdateTransport
        raw,keys=envelope(self.payload)
        client=UpdateClient(self.root/'data',keys,UpdateTransport('https://license.txblog.cn'))
        client._verified_raw=raw
        manifest=parse_manifest(raw,keys)
        class Space:
            free=0
        class CacheSpace:
            free=10**12
        def space(path): return Space() if Path(path)==self.root else CacheSpace()
        with patch('update_portable_install.shutil.disk_usage',side_effect=space),patch.object(client.transport,'download') as download:
            with self.assertRaises(UpdateError):
                client.prepare_new_install(manifest,self.root/'new',(self.old,),threading.Event(),lambda *_:None)
        download.assert_not_called()

    def test_fresh_install_archive_preview_rejected(self):
        from update_portable_install import install_full_to_new_directory
        parent=self.root/'360zip$Temp'/'360$'/'1'; parent.mkdir(parents=True)
        with self.assertRaises(UpdateError):
            install_full_to_new_directory(self.manifest,self.archive,parent/'new',(self.old,))

    def test_fake_terminal_journal_is_not_a_completed_transaction(self):
        from update_client import preflight_install_dir
        work=self.old/'.dy-update-transactions'/('a'*32);work.mkdir(parents=True)
        (work/'journal.json').write_text('{"phase":"committed"}')
        with self.assertRaises(UpdateError): preflight_install_dir(self.old,(),0)

    def test_helper_does_not_resolve_relative_profile_against_its_cwd(self):
        from types import SimpleNamespace
        import json
        import dy_live_updater as helper
        data=self.root/'data';data.mkdir()
        (data/'preferences.json').write_text(json.dumps({'profile_dir':'../profiles'}))
        with self.assertRaises(UpdateError):
            helper.portable_protected_paths(SimpleNamespace(install_dir=self.old),data)

    def test_genuine_signed_completed_journal_allows_later_preflight(self):
        from test_updater_entry import UpdaterTests
        from update_transaction import UpdateTransaction
        from update_client import preflight_install_dir
        fixture=UpdaterTests()
        prepared,_,_=fixture.setup_install(self.root/'case')
        fixture.job(prepared)
        transaction=UpdateTransaction(prepared,public_keys=fixture.keys)
        transaction.install(); transaction.commit()
        with patch('update_transaction.UPDATE_PUBLIC_KEYS',fixture.keys):
            self.assertEqual(preflight_install_dir(prepared.install_dir,(),0),prepared.install_dir)


if __name__=='__main__': unittest.main()
