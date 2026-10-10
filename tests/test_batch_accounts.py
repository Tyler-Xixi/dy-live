import json
import tempfile
import subprocess
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from batch_accounts import AccountRecord, AccountStore, AccountStoreError


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = AccountStore(self.root)

    def tearDown(self): self.temp.cleanup()

    def pair(self):
        return (replace(self.store.new_account('账号A'), selected=True, buy_times=2),
                replace(self.store.new_account('账号B'), selected=True, buy_quantity=3))

    def test_roundtrip_and_order(self):
        accounts = self.pair()
        self.store.save(accounts)
        restored = self.store.load()
        jobs = self.store.jobs(restored[::-1], self.root/'legacy-profile')
        self.assertEqual([(j.name,j.buy_times,j.buy_quantity) for j in jobs], [('账号B',1,3),('账号A',2,1)])
        self.assertNotEqual(jobs[0].profile_dir, jobs[1].profile_dir)
        self.assertEqual(restored, accounts)

    def test_remove_keeps_profile(self):
        accounts = self.pair()
        self.store.save(accounts)
        path = Path(self.store.jobs(accounts,self.root/'legacy')[0].profile_dir)
        path.mkdir(parents=True)
        marker = path/'user-data.txt'
        marker.write_text('owned-test-session')
        self.store.save(accounts[1:])
        self.assertEqual(marker.read_text(), 'owned-test-session')
        self.assertEqual(len(self.store.load()), 1)

    def test_invalid_identity_and_duplicate_are_rejected(self):
        account = self.pair()[0]
        for records in ((replace(account,account_id='../outside'),),(account,account)):
            with self.subTest(records=records):
                with self.assertRaises(AccountStoreError): self.store.save(records)
        self.assertFalse((self.root/'batch_accounts.json').exists())

    def test_invalid_fields_cannot_be_saved(self):
        account = self.pair()[0]
        for values in ({'name':'a\nb'},{'name':''},{'name':'x'*61},{'buy_times':True},
                       {'buy_quantity':0},{'selected':1},{'login_saved':'yes'}):
            with self.subTest(values=values):
                with self.assertRaises(AccountStoreError): self.store.save((replace(account,**values),))

    def test_corrupt_or_unknown_version_keeps_bytes(self):
        path = self.root/'batch_accounts.json'
        for content in ('broken JSON','{"version":9,"accounts":[]}','{"version":1,"accounts":[],"accounts":[]}'):
            path.write_text(content)
            with self.assertRaises(AccountStoreError): self.store.load()
            self.assertEqual(path.read_text(),content)

    def test_save_failure_keeps_original(self):
        accounts=self.pair()
        self.store.save(accounts)
        original=(self.root/'batch_accounts.json').read_bytes()
        with patch('batch_accounts.os.replace',side_effect=OSError('read only')):
            with self.assertRaises(AccountStoreError): self.store.save(accounts[::-1])
        self.assertEqual((self.root/'batch_accounts.json').read_bytes(),original)

    def test_default_alias_does_not_copy_or_change_profile(self):
        legacy=AccountRecord('legacy','原有账号',True,2,1,False)
        path=self.root/'custom-original-profile'
        jobs=self.store.jobs((legacy,),path)
        self.assertEqual(Path(jobs[0].profile_dir),path.resolve())
        self.assertFalse(path.exists())

    def test_legacy_profile_collision_is_rejected(self):
        account=self.pair()[0]
        legacy=AccountRecord('legacy','原有账号',True,1,1,False)
        path=Path(self.store.jobs((account,),self.root/'old')[0].profile_dir)
        with self.assertRaises(AccountStoreError): self.store.jobs((legacy,account),path)

    def test_new_profile_cannot_alias_default_without_legacy_record(self):
        account=self.pair()[0]
        path=Path(self.store.jobs((account,),self.root/'old')[0].profile_dir)
        with self.assertRaises(AccountStoreError): self.store.jobs((account,),path)

    def test_profile_link_outside_root_is_rejected(self):
        account=self.pair()[0]
        target=self.root/'elsewhere'
        target.mkdir()
        profile=self.root/'batch-account-profiles'/account.account_id
        profile.parent.mkdir()
        try: profile.symlink_to(target,target_is_directory=True)
        except OSError:
            if os.name != 'nt': raise
            created = subprocess.run(['cmd','/c','mklink','/J',str(profile),str(target)],capture_output=True)
            self.assertEqual(created.returncode,0,created.stderr.decode(errors='replace'))
        with self.assertRaises(AccountStoreError): self.store.jobs((account,),self.root/'old')

    def test_unknown_fields_or_plaintext_secrets_rejected(self):
        account=self.pair()[0]
        self.store.save((account,))
        path=self.root/'batch_accounts.json'
        value=json.loads(path.read_text())
        value['accounts'][0]['password']='not-allowed'
        path.write_text(json.dumps(value))
        with self.assertRaises(AccountStoreError): self.store.load()

    def test_missing_store_is_empty_without_creation(self):
        self.assertEqual(self.store.load(),())
        self.assertFalse((self.root/'batch_accounts.json').exists())
