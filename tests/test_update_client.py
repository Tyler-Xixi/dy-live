import hashlib
import io
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from update_fixtures import envelope, sample_release
from update_protocol import UpdateError
from update_client import UpdateClient, UpdateTransport, UpdateStateStore
from fixtures.update_server import update_server


class Response(io.BytesIO):
    def __init__(self, data, url, length=None):
        super().__init__(data)
        self.url=url
        self.headers={'Content-Length':str(len(data) if length is None else length)}
    def geturl(self): return self.url


class UpdateClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.archive,self.payload,_=sample_release(self.root)
        self.raw,self.keys=envelope(self.payload); self.calls=[]
        self.cancel=threading.Event()
        def opener(request,timeout):
            self.calls.append(request.full_url)
            if request.full_url.endswith('incremental.json'):
                import urllib.error
                raise urllib.error.HTTPError(request.full_url,404,'No delta',{},None)
            return Response(self.raw if request.full_url.endswith('latest.json') else self.archive.read_bytes(),request.full_url)
        self.transport=UpdateTransport('https://license.txblog.cn',opener=opener)
        self.client=UpdateClient(self.root/'data',self.keys,self.transport)
        self.install=self.root/'中文 空格'; self.install.mkdir()
        (self.install/'unknown.txt').write_bytes(b'preserve')

    def tearDown(self): self.temp.cleanup()

    def test_trickle_response_respects_total_deadline(self):
        with update_server(b'x'*60,b'',trickle_delay=.03) as (opener,_):
            transport=UpdateTransport('https://license.txblog.cn',opener=opener)
            start=time.monotonic()
            with self.assertRaises(UpdateError): transport._read('/updates/stable/latest.json',100,threading.Event(),.15)
            self.assertLess(time.monotonic()-start,.7)

    def test_trickle_response_cancel_does_not_wait_for_whole_chunk(self):
        with update_server(b'x'*60,b'',trickle_delay=.03) as (opener,_):
            transport=UpdateTransport('https://license.txblog.cn',opener=opener)
            cancel=threading.Event(); timer=threading.Timer(.15,cancel.set); timer.start()
            start=time.monotonic()
            try:
                with self.assertRaises(UpdateError): transport._read('/updates/stable/latest.json',100,cancel,5)
                self.assertLess(time.monotonic()-start,.7)
            finally: timer.cancel(); timer.join()

    def test_sequence_never_regresses_between_processes(self):
        import subprocess
        import sys
        data=self.root/'race'; data.mkdir()
        low_code='''
import sys,time
from pathlib import Path
import update_client as client
root=Path(sys.argv[1]); original=client.atomic_json
def delayed(path,value):
    (root/'ready').write_text('ready')
    deadline=time.monotonic()+5
    while not (root/'release').exists():
        if time.monotonic()>deadline: raise RuntimeError('barrier timeout')
        time.sleep(.01)
    original(path,value)
client.atomic_json=delayed
client.UpdateStateStore(root).save(4)
'''
        low=subprocess.Popen([sys.executable,'-c',low_code,str(data)])
        high=None
        try:
            deadline=time.monotonic()+5
            while not (data/'ready').exists() and time.monotonic()<deadline: time.sleep(.01)
            self.assertTrue((data/'ready').exists())
            high=subprocess.Popen([sys.executable,'-c','import sys; from update_client import UpdateStateStore; UpdateStateStore(sys.argv[1]).save(5)',str(data)])
            deadline=time.monotonic()+.5
            while high.poll() is None and time.monotonic()<deadline: time.sleep(.01)
        finally:
            (data/'release').write_text('release')
            self.assertEqual(low.wait(timeout=7),0)
            if high is not None: self.assertEqual(high.wait(timeout=7),0)
        self.assertEqual(UpdateStateStore(data).load()['highest_sequence'],5)

    def test_check_never_downloads_package(self):
        manifest=self.client.check('6.1.0',self.cancel)
        self.assertEqual(manifest.version,'6.1.1')
        self.assertIsNone(self.client.check('6.1.1',self.cancel))
        self.assertTrue(all(url.endswith('latest.json') for url in self.calls))
        self.assertEqual((self.install/'unknown.txt').read_bytes(),b'preserve')
        self.assertEqual(UpdateStateStore(self.root/'data').load()['highest_sequence'],2)

    def test_confirmed_download_cancel_timeout_truncation_and_hash_failure(self):
        manifest=self.client.check('6.1.0',self.cancel)
        for kind in ('cancel','timeout','truncated','length','hash','oversized'):
            with self.subTest(kind=kind):
                cancel=threading.Event()
                if kind=='cancel': cancel.set()
                def opener(request,timeout):
                    if kind=='timeout': raise TimeoutError('timeout')
                    data=self.archive.read_bytes()
                    if kind=='truncated': data=data[:-1]
                    if kind=='hash': data=b'X'+data[1:]
                    if kind=='oversized': data+=b'X'
                    return Response(data,request.full_url,1 if kind=='length' else manifest.package_size)
                client=UpdateClient(self.root/('data-'+kind),self.keys,UpdateTransport('https://license.txblog.cn',opener=opener))
                client._verified_raw=self.raw
                progress=[]
                with self.assertRaises(UpdateError): client.prepare(manifest,self.install,(),cancel,lambda a,b:progress.append((a,b)))
                self.assertTrue(all(0<=a<=b==manifest.package_size for a,b in progress))
                self.assertEqual((self.install/'unknown.txt').read_bytes(),b'preserve')

    def test_redirect_and_credentials_rejected(self):
        for url in ('http://license.txblog.cn','https://evil.invalid','https://u:p@license.txblog.cn','https://license.txblog.cn?token=x'):
            with self.subTest(url=url),self.assertRaises(UpdateError): UpdateTransport(url)
        for url in ('https://evil.invalid/updates/stable/latest.json','http://license.txblog.cn/updates/stable/latest.json','https://license.txblog.cn/updates/stable/latest.json?token=x'):
            transport=UpdateTransport('https://license.txblog.cn',opener=lambda request,timeout:Response(self.raw,url))
            with self.assertRaises(UpdateError): transport.fetch_manifest(self.cancel)

    def test_unicode_space_path_and_protected_profile(self):
        manifest=self.client.check('6.1.0',self.cancel)
        prepared=self.client.prepare(manifest,self.install,(self.root/'profiles',),self.cancel,lambda *_:None)
        self.assertEqual((prepared.staged_dir/'DYLiveAssistant.exe').read_bytes(),b'new-main')
        self.assertTrue(prepared.work_dir.is_absolute())
        with self.assertRaises(UpdateError): self.client.prepare(manifest,self.install,(self.install/'_internal'/'profiles',),self.cancel,lambda *_:None)
        with patch('update_client.shutil.disk_usage',return_value=SimpleNamespace(free=1)),self.assertRaises(UpdateError):
            self.client.prepare(manifest,self.install,(),self.cancel,lambda *_:None)
        self.assertEqual((self.install/'unknown.txt').read_bytes(),b'preserve')

    def test_corrupt_state_does_not_reset_replay_counter(self):
        store=UpdateStateStore(self.root/'state'); store.save(10)
        with self.assertRaises(UpdateError): store.save(9)
        store.path.write_bytes(b'broken')
        with self.assertRaises(UpdateError): store.load()

    def test_loopback_stream_and_cancel_after_progress(self):
        with update_server(self.raw,self.archive.read_bytes()) as (opener,calls):
            client=UpdateClient(self.root/'live-data',self.keys,UpdateTransport('https://license.txblog.cn',opener=opener))
            manifest=client.check('6.1.0',self.cancel)
            self.assertEqual(calls,['/updates/stable/latest.json'])
            def progress(*args): self.cancel.set()
            with self.assertRaises(UpdateError): client.prepare(manifest,self.install,(),self.cancel,progress)
            self.assertEqual(len(calls),2)
            self.assertFalse(any(path.name=='staged' for path in self.install.rglob('*')))


if __name__=='__main__': unittest.main()
