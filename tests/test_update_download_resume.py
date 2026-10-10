"""Real loopback faults exercise bounded stream and range handling."""
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest

from fixtures.update_server import update_server
from update_client import UpdateTransport
from update_protocol import UpdateError


class DownloadResumeTests(unittest.TestCase):
    data=b'0123456789abcdef'*1024
    path='/updates/releases/6.1.3/DYLiveAssistant-6.1.3-windows-x64.zip'

    def download(self,opener,root,cancel=None):
        return UpdateTransport('https://license.txblog.cn',opener=opener).download_verified(
            self.path,len(self.data),hashlib.sha256(self.data).hexdigest(),root/'package.zip',
            cancel or threading.Event(),lambda *_:None)

    def test_pause_longer_than_half_second_still_finishes(self):
        with tempfile.TemporaryDirectory() as folder, update_server(b'',self.data,pause_at=256,pause_for=.7) as (opener,calls):
            output=self.download(opener,Path(folder))
            self.assertEqual(output.read_bytes(),self.data)
            self.assertEqual(len(calls),1)

    def test_metadata_pause_is_not_a_half_second_network_failure(self):
        with update_server(b'abc',b'',pause_at=1,pause_for=.7) as (opener,_):
            transport=UpdateTransport('https://license.txblog.cn',opener=opener)
            self.assertEqual(transport._read('/updates/stable/latest.json',20,threading.Event(),2),b'abc')

    def test_disconnection_resumes_without_installing_partial_bytes(self):
        with tempfile.TemporaryDirectory() as folder, update_server(b'',self.data,interrupt_at=256) as (opener,calls):
            output=self.download(opener,Path(folder))
            self.assertEqual(output.read_bytes(),self.data)
            self.assertEqual(len(calls),2)

    def test_range_ignored_200_restarts_from_zero(self):
        with tempfile.TemporaryDirectory() as folder, update_server(b'',self.data,interrupt_at=256,resume='full') as (opener,calls):
            output=self.download(opener,Path(folder))
            self.assertEqual(output.read_bytes(),self.data)
            self.assertEqual(len(calls),2)

    def test_wrong_range_is_rejected_without_completed_archive(self):
        with tempfile.TemporaryDirectory() as folder, update_server(b'',self.data,interrupt_at=256,resume='wrong') as (opener,calls):
            root=Path(folder)
            with self.assertRaises(UpdateError): self.download(opener,root)
            self.assertFalse((root/'package.zip').exists())
            self.assertEqual(len(calls),2)

    def test_encoded_response_is_not_appended(self):
        with tempfile.TemporaryDirectory() as folder, update_server(b'',self.data,encoding='gzip') as (opener,calls):
            root=Path(folder)
            with self.assertRaises(UpdateError): self.download(opener,root)
            self.assertFalse((root/'package.zip').exists())
            self.assertEqual(len(calls),1)

    def test_cancel_while_server_pauses_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder, update_server(b'',self.data,pause_at=256,pause_for=1) as (opener,calls):
            event=threading.Event(); timer=threading.Timer(.1,event.set); timer.start()
            begin=time.monotonic()
            try:
                with self.assertRaises(UpdateError): self.download(opener,Path(folder),event)
                self.assertLess(time.monotonic()-begin,.7)
                self.assertFalse((Path(folder)/'package.zip').exists())
            finally: timer.cancel(); timer.join()

    def test_hash_mismatch_is_not_retried_or_marked_complete(self):
        with tempfile.TemporaryDirectory() as folder, update_server(b'',b'x'*len(self.data)) as (opener,calls):
            with self.assertRaises(UpdateError): self.download(opener,Path(folder))
            self.assertFalse((Path(folder)/'package.zip').exists())
            self.assertEqual(len(calls),1)

    def test_connection_failure_has_three_attempt_limit(self):
        calls=[]
        def failed(request,timeout):
            calls.append(request.full_url)
            raise TimeoutError('offline')
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(UpdateError): self.download(failed,Path(folder))
            self.assertEqual(len(calls),3)

    def test_idle_timeout_reconnects_but_never_marks_partial_complete(self):
        from update_download import verified_download
        with tempfile.TemporaryDirectory() as folder, update_server(b'',self.data,pause_at=256,pause_for=.3) as (opener,calls):
            root=Path(folder)
            with self.assertRaises(UpdateError):
                verified_download(opener,'https://license.txblog.cn',self.path,len(self.data),
                    hashlib.sha256(self.data).hexdigest(),root/'package.zip',threading.Event(),lambda *_:None,
                    idle_timeout=.05,max_attempts=2)
            self.assertFalse((root/'package.zip').exists())
            self.assertEqual(len(calls),2)


if __name__=='__main__': unittest.main()
