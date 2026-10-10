"""Bounded streaming and verified resumable downloads from immutable URLs."""
import http.client
from contextlib import contextmanager
import os
from pathlib import Path
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.request

from update_config import MAX_PACKAGE_SIZE
from update_protocol import UpdateError, assert_plain_path, file_hash, valid_hash


class RetryNetwork(Exception):
    pass


@contextmanager
def bounded_response(response):
    entered=response.__enter__()
    try: yield entered
    finally:
        # Windows may keep recv blocked briefly even after socket shutdown.
        # BufferedReader.close would then wait on its lock on the UI worker.
        if not getattr(entered,'_dy_deferred_close',False): response.__exit__(None,None,None)


def check_deadline(cancel,deadline):
    if cancel.is_set(): raise UpdateError('更新已取消')
    if time.monotonic()>=deadline: raise UpdateError('更新请求超时')


def response_chunks(response,cancel,deadline,idle_timeout=30):
    """Reader owns blocking I/O; consumer can cancel without a 30s UI stall."""
    reader=getattr(response,'read1',None)
    if reader is None: raise UpdateError('更新响应不支持有界流式读取')
    sock=getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
    if sock is not None: sock.settimeout(min(idle_timeout,max(.001,deadline-time.monotonic())))
    items=queue.Queue(maxsize=2); stop=threading.Event()
    def deliver(value):
        while not stop.is_set():
            try: items.put(value,timeout=.1); return
            except queue.Full: pass
    def read():
        try:
            while not stop.is_set():
                chunk=reader(65536)
                deliver(('data',chunk))
                if not chunk: return
        except Exception as exc: deliver(('error',exc))
    worker=threading.Thread(target=read,daemon=True,name='update-bounded-reader'); worker.start()
    last=time.monotonic()
    try:
        while True:
            check_deadline(cancel,deadline)
            if time.monotonic()-last>=idle_timeout: raise RetryNetwork('更新网络空闲超时')
            try: kind,value=items.get(timeout=min(.1,max(.001,deadline-time.monotonic())))
            except queue.Empty: continue
            check_deadline(cancel,deadline)
            if kind=='error':
                if isinstance(value,(OSError,http.client.HTTPException)): raise RetryNetwork('更新连接中断') from value
                raise value
            if not value: return
            last=time.monotonic()
            yield value
    finally:
        stop.set()
        if worker.is_alive() and sock is not None:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        worker.join(timeout=.05)
        if worker.is_alive():
            response._dy_deferred_close=True
            def close_when_read_finishes():
                worker.join()
                try: response.__exit__(None,None,None)
                except Exception: pass
            threading.Thread(target=close_when_read_finishes,daemon=True,name='update-reader-cleanup').start()


def content_length(response):
    value=response.headers.get('Content-Length')
    if value is None: return None
    if not value.isascii() or not value.isdecimal(): raise UpdateError('更新响应大小非法')
    return int(value)


def verified_download(opener,origin,path,size,sha256,output,cancel,progress,*,timeout=600,idle_timeout=30,max_attempts=3):
    if not isinstance(path,str) or not re.fullmatch(r'/updates/releases/\d+\.\d+\.\d+/[A-Za-z0-9._-]+\.zip',path):
        raise UpdateError('更新包路径不合法')
    if type(size) is not int or not 0<size<=MAX_PACKAGE_SIZE or not valid_hash(sha256): raise UpdateError('更新包身份不合法')
    output=assert_plain_path(output); partial=assert_plain_path(output.with_name(output.name+'.part'))
    if output.exists() or partial.exists(): raise UpdateError('更新下载目标已存在，拒绝覆盖')
    url=origin+path; deadline=time.monotonic()+timeout
    with partial.open('xb') as stream:
        for attempt in range(max_attempts):
            check_deadline(cancel,deadline)
            start=stream.tell()
            headers={'Accept-Encoding':'identity','Cache-Control':'no-cache'}
            if start: headers['Range']=f'bytes={start}-'
            request=urllib.request.Request(url,headers=headers)
            try:
                try: response=opener(request,timeout=min(10,max(.001,deadline-time.monotonic())))
                except urllib.error.HTTPError as exc:
                    if exc.code not in (408,429,500,502,503,504): raise UpdateError(f'更新服务 HTTP {exc.code}') from exc
                    raise RetryNetwork('更新服务暂不可用') from exc
                except (OSError,urllib.error.URLError) as exc: raise RetryNetwork('更新连接失败') from exc
                with bounded_response(response):
                    if response.geturl()!=url: raise UpdateError('更新源重定向或地址异常')
                    if response.headers.get('Content-Encoding','identity').lower()!='identity': raise UpdateError('更新响应编码不合法')
                    status=getattr(response,'status',200)
                    if status==200:
                        stream.seek(0); stream.truncate(); start=0
                    elif status==206:
                        expected=f'bytes {start}-{size-1}/{size}'
                        if response.headers.get('Content-Range')!=expected: raise UpdateError('更新续传范围不符')
                    else: raise UpdateError(f'更新服务 HTTP {status}')
                    length=content_length(response)
                    if length is not None and length!=size-start: raise UpdateError('更新响应大小不符')
                    for chunk in response_chunks(response,cancel,deadline,idle_timeout):
                        if stream.tell()+len(chunk)>size: raise UpdateError('更新下载超过大小限制')
                        stream.write(chunk)
                        if progress: progress(stream.tell(),size)
                    if stream.tell()!=size: raise RetryNetwork('更新下载不完整')
                stream.flush(); os.fsync(stream.fileno())
                break
            except RetryNetwork as exc:
                if attempt+1>=max_attempts: raise UpdateError('更新网络请求失败或超时，已尝试续传') from exc
        else: raise UpdateError('更新下载未完成')
    check_deadline(cancel,deadline)
    if file_hash(partial)!=sha256: raise UpdateError('更新包哈希不符，未安装')
    assert_plain_path(output); assert_plain_path(partial)
    if output.exists(): raise UpdateError('更新目标出现新文件，拒绝覆盖')
    os.rename(partial,output)
    return output
