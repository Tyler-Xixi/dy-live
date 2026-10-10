"""Signed delta must bind exactly one signed target and baseline."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from update_fixtures import sample_release, envelope
from update_protocol import UpdateError, parse_manifest


def signed_delta(payload,key,key_id='test'):
    message=b'DYLiveAssistant:update-incremental:v2\x00'+json.dumps(payload,
        sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    return json.dumps({'key_id':key_id,'payload':payload,
        'signature':base64.b64encode(key.sign(message)).decode()},ensure_ascii=False,sort_keys=True).encode()


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.key=Ed25519PrivateKey.generate()
        self.archive,self.payload,self.contents=sample_release(self.root,'6.1.3',4,{'_internal/new.dll':b'added'})
        self.raw,self.keys=envelope(self.payload,self.key)
        self.base=self.root/'base'; self.target=self.root/'target'; self.output=self.root/'output'
        self.base.mkdir(); self.target.mkdir(); self.output.mkdir()
        for name,data in self.contents.items():
            for root in (self.base,self.target):
                path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        (self.base/'DYLiveAssistant.exe').write_bytes(b'old-main')
        (self.base/'release-info.json').write_text('{"version":"6.1.2"}')
        (self.base/'_internal/new.dll').unlink()
        (self.base/'_internal/removed.dll').write_bytes(b'removed')

    def tearDown(self): self.temp.cleanup()

    def build(self):
        from tools.update_release import build_incremental
        return build_incremental(self.base,self.target,self.raw,self.output,public_keys=self.keys)

    def test_valid_old_index_does_not_block_new_full_release(self):
        from update_incremental import parse_incremental_index
        old={'protocol':2,'kind':'index','product':'DYLiveAssistant','platform':'windows-x64',
             'base_version':'6.1.1','target_version':'6.1.2','sequence':3,
             'target_manifest_sha256':'a'*64,
             'plan_path':'/updates/releases/6.1.2/incremental-6.1.1.json','plan_sha256':'b'*64}
        self.assertIsNone(parse_incremental_index(signed_delta(old,self.key),self.keys,self.raw,'6.1.2'))

    def plan(self):
        package=self.build()
        payload=json.loads((self.output/'incremental-6.1.2.payload.json').read_bytes())
        return package,payload,signed_delta(payload,self.key)

    def test_delta_contains_only_added_and_changed_files(self):
        package,payload,raw=self.plan()
        self.assertEqual({f['path'] for f in payload['changed_files']},
            {'DYLiveAssistant.exe','release-info.json','_internal/new.dll'})
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(set(archive.namelist()),{'DYLiveAssistant.exe','release-info.json','_internal/new.dll'})
        from update_incremental import parse_incremental_plan, validate_incremental_archive
        plan=parse_incremental_plan(raw,self.keys,self.raw,'6.1.2')
        validate_incremental_archive(package,plan)
        self.assertEqual(plan.target_version,'6.1.3')

    def test_binding_and_unknown_fields_are_rejected_even_if_signed(self):
        from update_incremental import parse_incremental_plan
        _,payload,raw=self.plan()
        for name,value in (('base_version','6.1.1'),('target_version','6.1.4'),('sequence',3),
                           ('target_manifest_sha256','0'*64),('product','Other'),('platform','linux'),('extra',1)):
            with self.subTest(field=name):
                altered=deepcopy(payload); altered[name]=value
                with self.assertRaises(UpdateError):
                    parse_incremental_plan(signed_delta(altered,self.key),self.keys,self.raw,'6.1.2')
        with self.assertRaises(UpdateError): parse_incremental_plan(raw,self.keys,self.raw+b' ','6.1.2')

    def test_changed_file_must_match_full_target(self):
        from update_incremental import parse_incremental_plan
        _,payload,_=self.plan()
        for path in ('../oops','_internal/New.dll','unknown.txt'):
            altered=deepcopy(payload); altered['changed_files'][0]['path']=path
            with self.subTest(path=path),self.assertRaises(UpdateError):
                parse_incremental_plan(signed_delta(altered,self.key),self.keys,self.raw,'6.1.2')
        altered=deepcopy(payload); altered['changed_files'].append(altered['changed_files'][0])
        with self.assertRaises(UpdateError): parse_incremental_plan(signed_delta(altered,self.key),self.keys,self.raw,'6.1.2')

    def test_delta_archive_extra_file_rejected(self):
        from update_incremental import parse_incremental_plan, validate_incremental_archive
        package,payload,_=self.plan()
        with zipfile.ZipFile(package,'a') as archive: archive.writestr('_internal/unlisted.dll',b'bad')
        payload['package_size']=package.stat().st_size
        payload['package_sha256']=hashlib.sha256(package.read_bytes()).hexdigest()
        plan=parse_incremental_plan(signed_delta(payload,self.key),self.keys,self.raw,'6.1.2')
        with self.assertRaises(UpdateError): validate_incremental_archive(package,plan)

    def test_builder_rejects_target_modified_after_manifest(self):
        (self.target/'DYLiveAssistant.exe').write_bytes(b'bad')
        with self.assertRaises(UpdateError): self.build()

    def test_signature_and_index_hash_binding_cannot_be_bypassed(self):
        from update_incremental import parse_incremental_index,parse_incremental_plan
        _,payload,raw=self.plan()
        damaged=json.loads(raw); damaged['payload']['sequence']=9
        with self.assertRaises(UpdateError):
            parse_incremental_plan(json.dumps(damaged).encode(),self.keys,self.raw,'6.1.2')
        index={'protocol':2,'kind':'index','product':'DYLiveAssistant','platform':'windows-x64',
            'base_version':'6.1.2','target_version':'6.1.3','sequence':4,
            'target_manifest_sha256':hashlib.sha256(self.raw).hexdigest(),
            'plan_path':'/updates/releases/6.1.3/incremental-6.1.2.json',
            'plan_sha256':hashlib.sha256(raw).hexdigest()}
        parsed=parse_incremental_index(signed_delta(index,self.key),self.keys,self.raw,'6.1.2')
        self.assertEqual(parsed.plan_path,'/updates/releases/6.1.3/incremental-6.1.2.json')
        self.assertIsNone(parse_incremental_index(signed_delta(index,self.key),self.keys,self.raw,'6.1.0'))
        index['plan_path']='/updates/secrets/private.key'
        with self.assertRaises(UpdateError): parse_incremental_index(signed_delta(index,self.key),self.keys,self.raw,'6.1.2')

    def test_signed_archive_cannot_replace_a_file_with_symlink(self):
        import stat
        from update_incremental import parse_incremental_plan,validate_incremental_archive
        package,payload,_=self.plan()
        with zipfile.ZipFile(package,'w') as archive:
            for entry in payload['changed_files']:
                info=zipfile.ZipInfo(entry['path']); info.create_system=3
                info.external_attr=((stat.S_IFLNK if entry['path']=='DYLiveAssistant.exe' else stat.S_IFREG)|0o644)<<16
                archive.writestr(info,self.contents[entry['path']])
        payload['package_size']=package.stat().st_size; payload['package_sha256']=hashlib.sha256(package.read_bytes()).hexdigest()
        plan=parse_incremental_plan(signed_delta(payload,self.key),self.keys,self.raw,'6.1.2')
        with self.assertRaises(UpdateError): validate_incremental_archive(package,plan)


if __name__=='__main__': unittest.main()
