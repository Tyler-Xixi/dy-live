import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from update_fixtures import sample_release
from update_protocol import UpdateError, parse_manifest, validate_archive
from update_client import atomic_json
from tools.update_publish import init_key, publish_release


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.secrets=self.root/'secrets'; self.public=self.root/'public'
        exported=init_key(self.secrets)
        self.keys={exported['key_id']:base64.b64decode(exported['public_key'])}
    def tearDown(self): self.temp.cleanup()
    def release(self,version,sequence):
        folder=self.root/version; folder.mkdir()
        archive,payload,_=sample_release(folder,version=version,sequence=sequence)
        correct=folder/Path(payload['package_path']).name
        archive.rename(correct); atomic_json(folder/'payload.json',payload)
        return folder

    def test_publish_checks_complete_bundle_before_latest_switch(self):
        first=publish_release(self.release('6.1.0',1),self.public,self.secrets)
        old=first.read_bytes()
        second=self.release('6.1.1',2)
        for phase in ('verified','copied','signed','before_latest'):
            with self.subTest(phase=phase):
                def fail(actual):
                    if actual==phase: raise OSError('injected '+phase)
                with patch('tools.update_publish._checkpoint',side_effect=fail),self.assertRaises(OSError):
                    publish_release(second,self.public,self.secrets)
                self.assertEqual(first.read_bytes(),old)
        publish_release(second,self.public,self.secrets)
        with self.assertRaises(UpdateError): publish_release(second,self.public,self.secrets)
        with self.assertRaises(UpdateError): publish_release(self.release('6.0.9',3),self.public,self.secrets)
        with self.assertRaises(UpdateError): publish_release(self.release('6.1.2',1),self.public,self.secrets)

    def test_wrong_key_or_origin_and_inaccessible_file_prevent_publish(self):
        exported=init_key(self.secrets); key_bytes=(self.secrets/'update-ed25519.key').read_bytes()
        self.assertEqual(exported['key_id'],'update-primary-v1')
        self.assertEqual((self.secrets/'update-ed25519.key').read_bytes(),key_bytes)
        source=self.release('6.1.0',1); latest=publish_release(source,self.public,self.secrets)
        manifest=parse_manifest(latest.read_bytes(),self.keys)
        validate_archive(self.public/manifest.package_path.removeprefix('/updates/'),manifest)
        with self.assertRaises(UpdateError): parse_manifest(latest.read_bytes(),{'update-primary-v1':b'x'*32})
        broken=self.release('6.1.1',2); next(broken.glob('*.zip')).write_bytes(b'corrupt')
        before=latest.read_bytes()
        with self.assertRaises(UpdateError): publish_release(broken,self.public,self.secrets)
        self.assertEqual(latest.read_bytes(),before)

    def test_incremental_resources_are_signed_before_latest_switch(self):
        import json
        import zipfile
        from tools.update_publish import _key, KEY_ID
        from tools.update_release import build_incremental
        from update_protocol import signed_bytes
        from update_incremental import parse_incremental_index,parse_incremental_plan,validate_incremental_archive
        first=publish_release(self.release('6.1.2',3),self.public,self.secrets)
        source=self.release('6.1.3',4)
        payload=json.loads((source/'payload.json').read_bytes())
        key=_key(self.secrets)
        raw=json.dumps({'key_id':KEY_ID,'payload':payload,'signature':base64.b64encode(key.sign(signed_bytes(payload))).decode()},ensure_ascii=False,sort_keys=True).encode()
        base=self.root/'base'; target=self.root/'target'; base.mkdir(); target.mkdir()
        with zipfile.ZipFile(next((self.root/'6.1.2').glob('*.zip'))) as archive: archive.extractall(base)
        with zipfile.ZipFile(next(source.glob('*.zip'))) as archive: archive.extractall(target)
        build_incremental(base,target,raw,source,public_keys=self.keys)
        old=first.read_bytes()
        def inspect(phase):
            if phase=='incremental_index':
                self.assertEqual(first.read_bytes(),old)
                self.assertTrue((self.public/'releases/6.1.3/incremental-6.1.2.json').is_file())
        with patch('tools.update_publish._checkpoint',side_effect=inspect):
            latest=publish_release(source,self.public,self.secrets)
        self.assertEqual(latest.read_bytes(),raw)
        index=parse_incremental_index((self.public/'stable/incremental.json').read_bytes(),self.keys,raw,'6.1.2')
        plan_raw=(self.public/index.plan_path.removeprefix('/updates/')).read_bytes()
        plan=parse_incremental_plan(plan_raw,self.keys,raw,'6.1.2')
        validate_incremental_archive(self.public/plan.package_path.removeprefix('/updates/'),plan)


if __name__=='__main__': unittest.main()
