import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
import warnings
from pathlib import Path
from unittest.mock import patch

import update_protocol as protocol
from update_fixtures import envelope, sample_release


class UpdateProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.folder=Path(self.temp.name)
        self.archive,self.payload,self.contents=sample_release(self.folder)
        self.raw,self.keys=envelope(self.payload)

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_manifest_and_numeric_order(self):
        manifest=protocol.parse_manifest(self.raw,self.keys)
        self.assertEqual(manifest.version,'6.1.1')
        self.assertEqual(manifest.sequence,2)
        self.assertEqual(len(manifest.files),5)
        self.assertEqual(protocol.version_tuple('6.10.0'),(6,10,0))
        self.assertTrue(protocol.is_newer(manifest,'6.1.0'))
        self.assertFalse(protocol.is_newer(manifest,'6.1.1'))
        self.assertFalse(protocol.is_newer(manifest,'6.2.0'))
        self.assertGreater(protocol.version_tuple('6.10.0'),protocol.version_tuple('6.9.9'))
        for value in ('6.1','06.1.0','6.-1.0','6.1.0-beta', True):
            with self.subTest(value=value), self.assertRaises(protocol.UpdateError):
                protocol.version_tuple(value)

    def test_rejects_unsigned_tampered_wrong_product_platform_or_replay(self):
        for change in ({'product':'other'},{'platform':'linux'},{'protocol':2},
                       {'updater_protocol':2},{'sequence':True},{'package_size':0},
                       {'version':'6.1'}, {'published_at':'not-time'},
                       {'package_path':'https://evil.invalid/package.zip'}):
            with self.subTest(change=change):
                payload=copy.deepcopy(self.payload); payload.update(change)
                raw,keys=envelope(payload)
                with self.assertRaises(protocol.UpdateError): protocol.parse_manifest(raw,keys)
        altered=json.loads(self.raw); altered['payload']['notes']='tampered'
        for raw,keys in ((json.dumps(altered).encode(),self.keys),
                         (self.raw,{'test':b'x'*32}),
                         (json.dumps(self.payload).encode(),self.keys),
                         (self.raw,{})):
            with self.assertRaises(protocol.UpdateError): protocol.parse_manifest(raw,keys)
        with self.assertRaises(protocol.UpdateError): protocol.parse_manifest(self.raw,self.keys,3)
        self.assertEqual(protocol.parse_manifest(self.raw,self.keys,2).sequence,2)
        duplicated=self.raw.replace(b'"key_id": "test"',b'"key_id": "test", "key_id": "test"')
        with self.assertRaises(protocol.UpdateError): protocol.parse_manifest(duplicated,self.keys)

    def test_signed_bytes_has_exact_domain_and_canonical_serialization(self):
        self.assertEqual(protocol.signed_bytes({'z':'中文','a':1}),
                         b'DYLiveAssistant:update-manifest:v1\x00'+ '{"a":1,"z":"中文"}'.encode())

    def test_verified_archive_extracts_only_owned_files(self):
        manifest=protocol.parse_manifest(self.raw,self.keys)
        protocol.validate_archive(self.archive,manifest)
        destination=self.folder/'staged'
        protocol.extract_verified(self.archive,destination,manifest)
        for name,data in self.contents.items(): self.assertEqual((destination/name).read_bytes(),data)
        self.assertEqual(len(list(p for p in destination.rglob('*') if p.is_file())),5)

    def test_archive_rejects_traversal_links_duplicates_bombs_and_missing_files(self):
        for name in ('../outside','/absolute','C:/escape','//host/share/file',
                     '_internal/x:stream','_internal/CON.txt','_internal/a/../escape',
                     '_internal/x.','_internal/x ', '_internal\\escape'):
            with self.subTest(name=name):
                archive,payload,_=sample_release(self.folder,extras={name:b'bad'})
                raw,keys=envelope(payload)
                with self.assertRaises(protocol.UpdateError):
                    protocol.extract_verified(archive,self.folder/'bad-output',protocol.parse_manifest(raw,keys))
                self.assertFalse((self.folder/'outside').exists())
        # All legitimate roots exist: refusal must not depend on a missing root.
        archive,payload,_=sample_release(self.folder,extras={'_internal/RUNTIME.dll':b'collision'})
        raw,keys=envelope(payload)
        with self.assertRaises(protocol.UpdateError): protocol.parse_manifest(raw,keys)
        self.archive,self.payload,_=sample_release(self.folder)
        with zipfile.ZipFile(self.archive,'a') as package:
            link=zipfile.ZipInfo('_internal/link'); link.create_system=3
            link.external_attr=0o120777<<16; package.writestr(link,'../../outside')
        self.payload['package_size']=self.archive.stat().st_size
        self.payload['package_sha256']=hashlib.sha256(self.archive.read_bytes()).hexdigest()
        raw,keys=envelope(self.payload)
        with self.assertRaises(protocol.UpdateError):
            protocol.validate_archive(self.archive,protocol.parse_manifest(raw,keys))
        archive,payload,_=sample_release(self.folder)
        raw,keys=envelope(payload); manifest=protocol.parse_manifest(raw,keys)
        with patch.object(protocol,'MAX_UNPACKED_SIZE',1), self.assertRaises(protocol.UpdateError):
            protocol.validate_archive(archive,manifest)
        with patch.object(protocol,'MAX_FILES',1), self.assertRaises(protocol.UpdateError):
            protocol.validate_archive(archive,manifest)
        payload['files'][0]['sha256']='0'*64
        raw,keys=envelope(payload)
        with self.assertRaises(protocol.UpdateError):
            protocol.validate_archive(archive,protocol.parse_manifest(raw,keys))

    def test_tampered_archive_and_linked_destination_are_not_written(self):
        manifest=protocol.parse_manifest(self.raw,self.keys)
        self.archive.write_bytes(self.archive.read_bytes()+b'tampered')
        with self.assertRaises(protocol.UpdateError): protocol.validate_archive(self.archive,manifest)
        # Existing staging must not be reused or overwrite unknown data.
        existing=self.folder/'existing'; existing.mkdir(); (existing/'keep').write_bytes(b'keep')
        with self.assertRaises(protocol.UpdateError): protocol.extract_verified(self.archive,existing,manifest)
        self.assertEqual((existing/'keep').read_bytes(),b'keep')

    def test_signed_archive_missing_extra_and_exact_duplicate(self):
        for kind in ('missing','extra','duplicate'):
            with self.subTest(kind=kind):
                archive,payload,contents=sample_release(self.folder)
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore',UserWarning)
                    with zipfile.ZipFile(archive,'w') as package:
                        for name,data in contents.items():
                            if kind=='missing' and name=='release-info.json': continue
                            package.writestr(name,data)
                        if kind=='extra': package.writestr('_internal/extra',b'extra')
                        if kind=='duplicate': package.writestr('DYLiveAssistant.exe',b'new-main')
                payload['package_size']=archive.stat().st_size
                payload['package_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
                raw,keys=envelope(payload)
                with self.assertRaises(protocol.UpdateError): protocol.validate_archive(archive,protocol.parse_manifest(raw,keys))


if __name__=='__main__': unittest.main()
