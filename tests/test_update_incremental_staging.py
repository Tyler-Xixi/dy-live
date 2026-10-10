"""Assemble a complete target without modifying or linking the old install."""
from dataclasses import replace
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_update_incremental as incremental_fixture
from update_protocol import UpdateError,parse_manifest


class StagingTests(unittest.TestCase):
    setUp=incremental_fixture.IncrementalTests.setUp
    tearDown=incremental_fixture.IncrementalTests.tearDown
    build=incremental_fixture.IncrementalTests.build
    plan=incremental_fixture.IncrementalTests.plan
    def test_complete_stage_uses_old_runtime_and_omits_removed_files(self):
        from update_incremental import parse_incremental_plan,assemble_incremental,validate_target_directory
        package,payload,raw=self.plan()
        plan=parse_incremental_plan(raw,self.keys,self.raw,'6.1.2')
        stage=self.root/'staged'; target=parse_manifest(self.raw,self.keys)
        assemble_incremental(plan,target,self.base,package,stage,threading.Event())
        validate_target_directory(stage,target)
        self.assertFalse((stage/'_internal/removed.dll').exists())
        (self.base/'_internal/runtime.dll').write_bytes(b'changed after staging')
        self.assertEqual((stage/'_internal/runtime.dll').read_bytes(),b'new-runtime')
        self.assertEqual((stage/'DYLiveAssistant.exe').read_bytes(),b'new-main')

    def test_bad_or_missing_reused_file_cannot_silently_fall_back(self):
        from update_incremental import parse_incremental_plan,assemble_incremental
        package,_,raw=self.plan(); plan=parse_incremental_plan(raw,self.keys,self.raw,'6.1.2')
        (self.base/'_internal/runtime.dll').write_bytes(b'changed')
        with self.assertRaises(UpdateError):
            assemble_incremental(plan,parse_manifest(self.raw,self.keys),self.base,package,self.root/'stage',threading.Event())
        self.assertEqual((self.base/'DYLiveAssistant.exe').read_bytes(),b'old-main')

    def test_copy_race_detected_in_staged_hash(self):
        import shutil
        from update_incremental import parse_incremental_plan,assemble_incremental
        package,_,raw=self.plan(); plan=parse_incremental_plan(raw,self.keys,self.raw,'6.1.2')
        real_copy=shutil.copyfile
        def mutate(source,dest,*args,**kwargs):
            if str(source).endswith('runtime.dll'): Path(source).write_bytes(b'raced-runtime')
            return real_copy(source,dest,*args,**kwargs)
        with patch('update_incremental.shutil.copyfile',side_effect=mutate),self.assertRaises(UpdateError):
            assemble_incremental(plan,parse_manifest(self.raw,self.keys),self.base,package,self.root/'stage',threading.Event())

    def test_helper_revalidates_protocol_two_and_restores_without_network(self):
        from update_incremental import parse_incremental_plan,assemble_incremental
        from update_client import PreparedIncrementalUpdate,atomic_json
        from update_transaction import UpdateTransaction
        from update_process import ProcessIdentity
        from dy_live_updater import validate_request
        package,_,raw=self.plan(); plan=parse_incremental_plan(raw,self.keys,self.raw,'6.1.2')
        work=self.base/'.dy-update-transactions'/('c'*32); work.mkdir(parents=True)
        import shutil
        shutil.copyfile(package,work/'package.zip'); (work/'manifest.json').write_bytes(self.raw)
        (work/'incremental.json').write_bytes(raw)
        prepared=PreparedIncrementalUpdate('c'*32,work,work/'package.zip',work/'manifest.json',work/'staged',self.base,work/'incremental.json','6.1.2')
        assemble_incremental(plan,parse_manifest(self.raw,self.keys),self.base,prepared.archive,prepared.staged_dir,threading.Event())
        parent=ProcessIdentity(123,456,str(self.base/'DYLiveAssistant.exe'))
        atomic_json(work/'job.json',{'protocol':2,'token':'a'*64,'parent':vars(parent),
            'prepared':{key:str(value) for key,value in vars(prepared).items()}})
        checked,_,_=validate_request(work/'job.json',public_keys=self.keys,require_parent=False)
        self.assertEqual(checked,prepared)
        transaction=UpdateTransaction(prepared,public_keys=self.keys); transaction.install()
        self.assertEqual((self.base/'DYLiveAssistant.exe').read_bytes(),b'new-main')
        self.assertEqual(transaction.rollback(),'restored')
        self.assertEqual((self.base/'DYLiveAssistant.exe').read_bytes(),b'old-main')
        (work/'incremental.json').write_bytes(b'bad')
        with self.assertRaises(UpdateError): validate_request(work/'job.json',public_keys=self.keys,require_parent=False)

    def test_client_offer_prepares_verified_complete_stage(self):
        import hashlib
        from test_update_client import Response
        from update_client import UpdateClient,UpdateTransport,PreparedIncrementalUpdate
        package,payload,plan_raw=self.plan()
        index={'protocol':2,'kind':'index','product':'DYLiveAssistant','platform':'windows-x64',
            'base_version':'6.1.2','target_version':'6.1.3','sequence':4,
            'target_manifest_sha256':hashlib.sha256(self.raw).hexdigest(),
            'plan_path':'/updates/releases/6.1.3/incremental-6.1.2.json',
            'plan_sha256':hashlib.sha256(plan_raw).hexdigest()}
        data={'/updates/stable/latest.json':self.raw,
            '/updates/stable/incremental.json':incremental_fixture.signed_delta(index,self.key),
            index['plan_path']:plan_raw,payload['package_path']:package.read_bytes()}
        calls=[]
        def opener(request,timeout):
            path=request.full_url.removeprefix('https://license.txblog.cn'); calls.append(path)
            return Response(data[path],request.full_url)
        client=UpdateClient(self.root/'data',self.keys,UpdateTransport('https://license.txblog.cn',opener=opener))
        cancel=threading.Event(); manifest=client.check('6.1.2',cancel)
        offer=client.incremental_offer(manifest,'6.1.2',cancel)
        prepared=client.prepare_incremental(manifest,offer,self.base,(),cancel,lambda *_:None)
        self.assertIsInstance(prepared,PreparedIncrementalUpdate)
        self.assertEqual((prepared.staged_dir/'_internal/runtime.dll').read_bytes(),b'new-runtime')
        self.assertNotIn(self.payload['package_path'],calls)

    def test_changed_baseline_rejected_before_delta_download(self):
        from update_client import UpdateClient,UpdateTransport
        from update_incremental import parse_incremental_plan
        package,_,raw=self.plan()
        client=UpdateClient(self.root/'data',self.keys,UpdateTransport('https://license.txblog.cn'))
        client._verified_raw=self.raw; client._verified_incremental_raw=raw
        manifest=parse_manifest(self.raw,self.keys)
        plan=parse_incremental_plan(raw,self.keys,self.raw,'6.1.2')
        (self.base/'_internal/runtime.dll').write_bytes(b'corrupt')
        def download_archive(path,size,digest,destination,*args):
            destination.write_bytes(package.read_bytes())
        with patch.object(client.transport,'download_verified',side_effect=download_archive) as download,self.assertRaises(UpdateError):
            client.prepare_incremental(manifest,plan,self.base,(),threading.Event(),lambda *_:None)
        download.assert_not_called()

    def test_interrupted_incremental_install_recovers_original_files(self):
        import shutil
        from update_incremental import parse_incremental_plan,assemble_incremental
        from update_client import PreparedIncrementalUpdate,atomic_json
        from update_transaction import UpdateTransaction,recover_transaction
        from update_process import ProcessIdentity
        package,_,raw=self.plan(); plan=parse_incremental_plan(raw,self.keys,self.raw,'6.1.2')
        work=self.base/'.dy-update-transactions'/('d'*32); work.mkdir(parents=True)
        shutil.copyfile(package,work/'package.zip'); (work/'manifest.json').write_bytes(self.raw)
        (work/'incremental.json').write_bytes(raw)
        prepared=PreparedIncrementalUpdate('d'*32,work,work/'package.zip',work/'manifest.json',work/'staged',self.base,work/'incremental.json','6.1.2')
        assemble_incremental(plan,parse_manifest(self.raw,self.keys),self.base,prepared.archive,prepared.staged_dir,threading.Event())
        atomic_json(work/'job.json',{'protocol':2,'token':'a'*64,
            'parent':vars(ProcessIdentity(123,456,str(self.base/'DYLiveAssistant.exe'))),
            'prepared':{key:str(value) for key,value in vars(prepared).items()}})
        transaction=UpdateTransaction(prepared,public_keys=self.keys)
        def power_loss(phase,name,point):
            if phase=='replace' and name=='DYLiveAssistant.exe' and point=='after_move': raise OSError('power loss')
        with patch.object(transaction,'_checkpoint',side_effect=power_loss),self.assertRaises(OSError): transaction.install()
        self.assertEqual(recover_transaction(work/'journal.json',public_keys=self.keys),'restored')
        self.assertEqual((self.base/'DYLiveAssistant.exe').read_bytes(),b'old-main')
        self.assertTrue((self.base/'_internal/removed.dll').exists())
        self.assertFalse((self.base/'_internal/new.dll').exists())


if __name__=='__main__': unittest.main()
