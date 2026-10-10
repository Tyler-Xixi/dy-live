"""Owned loopback response fixture; product never accepts an HTTP origin."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import urllib.request


@contextmanager
def update_server(manifest, archive, *, trickle_delay=0, pause_at=None, pause_for=0,
                  interrupt_at=None, resume='range', encoding=None):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            data=manifest if self.path.endswith('latest.json') else archive
            start=0
            requested=self.headers.get('Range')
            ranged=requested and resume!='full'
            if ranged:
                start=int(requested.removeprefix('bytes=').removesuffix('-'))
            self.send_response(206 if ranged else 200)
            if ranged:
                reported=start+1 if resume=='wrong' else start
                self.send_header('Content-Range',f'bytes {reported}-{len(data)-1}/{len(data)}')
                data=data[start:]
            self.send_header('Content-Length',str(len(data)))
            if encoding: self.send_header('Content-Encoding',encoding)
            self.end_headers()
            try:
                if interrupt_at is not None and len(calls)==1:
                    self.wfile.write(data[:interrupt_at]); self.wfile.flush()
                    self.close_connection=True
                elif pause_at is not None:
                    self.wfile.write(data[:pause_at]); self.wfile.flush(); time.sleep(pause_for)
                    self.wfile.write(data[pause_at:]); self.wfile.flush()
                elif trickle_delay:
                    for byte in data:
                        self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(trickle_delay)
                else: self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError): pass
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    class Response:
        def __init__(self,response,url):
            self.response=response; self.url=url; self.headers=response.headers; self.fp=response.fp
            self.status=response.status
        def geturl(self): return self.url
        def read(self,size): return self.response.read(size)
        def read1(self,size): return self.response.read1(size)
        def __enter__(self): return self
        def __exit__(self,*args): self.response.close()
    def opener(request,timeout):
        from urllib.parse import urlsplit
        path=urlsplit(request.full_url).path
        response=urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}',headers=dict(request.header_items())),timeout=timeout)
        return Response(response,request.full_url)
    try: yield opener,calls
    finally: server.shutdown(); server.server_close(); thread.join(timeout=5)
