import base64,hashlib,io,json,tempfile,threading,unittest
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mac_update import MacUpdateClient,MacUpdateError,parse_manifest,signed_bytes

class Response(io.BytesIO):
 def __init__(self,data,url):super().__init__(data);self.url=url;self.headers={'Content-Length':str(len(data))}
 def geturl(self):return self.url

class MacUpdateTests(unittest.TestCase):
 def fixture(self,p,**changes):
  self.package=b'owned-mac-zip-fixture';self.key=Ed25519PrivateKey.generate();self.keys={'test':self.key.public_key().public_bytes_raw()}
  self.payload=dict(protocol=1,product='DYLiveAssistant',platform='macos-arm64',version='6.2.39',sequence=1,notes='test',package_path='/updates/macos/releases/6.2.39/DYLiveAssistantMac-6.2.39-arm64.zip',package_size=len(self.package),package_sha256=hashlib.sha256(self.package).hexdigest(),minimum_macos='15.0',notarized=False)
  self.payload.update(changes);self.raw=self.sign(self.payload)
  def opener(req,timeout):return Response(self.raw if req.full_url.endswith('.json') else self.package,req.full_url)
  return MacUpdateClient(p/'data','macos-arm64',keys=self.keys,opener=opener)
 def sign(self,payload):return json.dumps(dict(key_id='test',payload=payload,signature=base64.b64encode(self.key.sign(signed_bytes(payload))).decode())).encode()
 def test_check_and_verified_download_do_not_extract_or_change_account_data(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);c=self.fixture(root);c.data_dir.mkdir();sentinel=c.data_dir/'license.json';sentinel.write_bytes(b'keep')
   m=c.check(threading.Event());target=root/'package.zip';c.download(m,target,threading.Event())
   self.assertEqual(target.read_bytes(),self.package);self.assertEqual(sentinel.read_bytes(),b'keep');self.assertFalse(list(root.glob('*.app')))
 def test_reject_wrong_signature_platform_paths_and_size(self):
  with tempfile.TemporaryDirectory() as d:
   c=self.fixture(Path(d))
   for changes in [dict(platform='windows-x64'),dict(platform='macos-x64'),dict(package_path='/updates/stable/latest.json'),dict(package_size=0),dict(package_sha256='0'*63),dict(notarized=1),dict(sequence=0),dict(minimum_macos='12.0')]:
    with self.subTest(changes=changes),self.assertRaises(MacUpdateError):parse_manifest(self.sign(dict(self.payload,**changes)),'macos-arm64',self.keys)
   raw=json.loads(self.raw);raw['payload']['notes']='changed'
   with self.assertRaises(MacUpdateError):parse_manifest(json.dumps(raw).encode(),'macos-arm64',self.keys)
 def test_sequence_rollback_and_unsigned_download_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);c=self.fixture(p);m=parse_manifest(self.raw,'macos-arm64',self.keys)
   with self.assertRaises(MacUpdateError):c.download(m,p/'no.zip',threading.Event())
   c.check(threading.Event());c.state.write_text('{"highest_sequence":2}')
   with self.assertRaises(MacUpdateError):c.check(threading.Event())
 def test_corrupt_or_cancelled_download_never_produces_success_file(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);c=self.fixture(p);m=c.check(threading.Event());self.package=b'corrupt'
   with self.assertRaises(MacUpdateError):c.download(m,p/'bad.zip',threading.Event())
   self.assertFalse((p/'bad.zip').exists());self.assertFalse((p/'bad.zip.part').exists())
   event=threading.Event();event.set()
   with self.assertRaises(MacUpdateError):c.download(m,p/'cancel.zip',event)
   self.assertFalse((p/'cancel.zip').exists())
 def test_existing_files_and_data_directory_protected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);c=self.fixture(p);m=c.check(threading.Event());f=p/'existing.zip';f.write_bytes(b'keep')
   for destination in [f,c.data_dir/'inside.zip']:
    with self.assertRaises(MacUpdateError):c.download(m,destination,threading.Event())
   self.assertEqual(f.read_bytes(),b'keep')

if __name__=='__main__':unittest.main()
