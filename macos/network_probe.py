"""Small, cancellable network checks. No account data or order requests."""
from __future__ import annotations

import http.client
import socket
import statistics
import threading
import time
import uuid
from urllib.parse import urlsplit

GENERAL_URL = 'https://speed.cloudflare.com/__down?bytes=0'
SITE_URL = 'https://live.douyin.com/'
DOWNLOAD_BYTES = 8 * 1024 * 1024
DOWNLOAD_URL = f'https://speed.cloudflare.com/__down?bytes={DOWNLOAD_BYTES}'


class ProbeCancelled(Exception):
    pass


def summarize_samples(samples, statuses):
    valid = [value for value in samples if value is not None]
    count = len(samples)
    return {'samples': count, 'responses': len(valid),
            'median_ms': round(statistics.median(valid), 2) if valid else None,
            'min_ms': round(min(valid), 2) if valid else None,
            'max_ms': round(max(valid), 2) if valid else None,
            'jitter_ms': round(statistics.mean(abs(b-a) for a, b in zip(valid, valid[1:])), 2) if len(valid)>1 else None,
            'request_failure_pct': round((count-len(valid))*100/count, 1) if count else None,
            'http_errors': sum(status is not None and not 200 <= status < 400 for status in statuses),
            'statuses': statuses}


def assess_network(site):
    # Heuristic network suitability, never a modeled probability of winning stock.
    if site['responses'] < 3 or site['http_errors']:
        return {'level': '无法评估', 'detail': '站点有效样本不足或返回 HTTP 异常，请重测并核对连接；不能据此断定本机网速差。'}
    delay, jitter, failures = site['median_ms'], site['jitter_ms'] or 0, site['request_failure_pct']
    if failures > 0 or delay > 300 or jitter > 80:
        return {'level': '较差', 'detail': '连接失败、延迟或波动较明显，可能增加页面更新和提交等待。建议检查 Wi-Fi、代理及后台下载后重测。'}
    if delay <= 100 and jitter <= 20:
        return {'level': '较好', 'detail': '本次站点响应较快且稳定，网络侧条件较好。库存竞争和平台处理仍会影响能否抢到。'}
    return {'level': '一般', 'detail': '存在一定延迟或波动，短库存窗口可能受到影响。建议使用稳定连接，并在开售前再次检测。'}


class NetworkProbe:
    def __init__(self, progress=lambda _: None):
        self.progress = progress
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.active = None
        self.active_socket = None
        self.download_time_limit = 12

    @staticmethod
    def abort_socket(active_socket):
        if active_socket:
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            active_socket.close()

    def cancel(self):
        self.stop.set()
        with self.lock:
            connection = self.active
            active_socket = self.active_socket or (connection.sock if connection else None)
            self.abort_socket(active_socket)
            if connection:
                connection.close()

    def check(self):
        if self.stop.is_set():
            raise ProbeCancelled()

    def connection(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname:
            raise ValueError('无效检测地址')
        cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        connection = cls(parsed.hostname, parsed.port, timeout=3)
        with self.lock:
            self.check()
            self.active = connection
            self.active_socket = None
        return connection

    def request(self, connection, url, method='GET'):
        self.check()
        parsed = urlsplit(url)
        path = parsed.path or '/'
        query = (parsed.query+'&' if parsed.query else '')+'probe='+uuid.uuid4().hex
        connection.request(method, path+'?'+query, headers={
            'User-Agent': 'DYLiveAssistant-NetworkCheck/1.0', 'Accept-Encoding': 'identity',
            'Cache-Control': 'no-cache'})
        with self.lock:
            self.check()
            self.active_socket = connection.sock
            active_socket = self.active_socket
        # Header reads also need a total deadline: inactivity timeouts alone
        # do not stop servers that send a byte before each timeout expires.
        finished = threading.Event()
        def expire():
            if not finished.is_set() and active_socket:
                self.abort_socket(active_socket)
        timer = threading.Timer(3,expire)
        timer.daemon = True
        timer.start()
        try:
            return connection.getresponse()
        finally:
            finished.set()
            timer.cancel()

    def latency(self, url, label, method, count=6):
        connection = self.connection(url)
        samples, statuses, errors = [], [], []
        try:
            for index in range(count):
                self.check()
                self.progress(f'{label} · 样本 {index+1}/{count}')
                started = time.perf_counter()
                try:
                    response = self.request(connection, url, method)
                    elapsed = (time.perf_counter()-started)*1000
                    status = response.status
                    # Only first-byte/header timing is needed. Close without
                    # draining a potentially slow or chunked page body.
                    response.close()
                    connection.close()
                    samples.append(elapsed)
                    statuses.append(status)
                except (OSError, http.client.HTTPException) as exc:
                    self.check()
                    samples.append(None)
                    statuses.append(None)
                    errors.append(type(exc).__name__+': '+str(exc)[:160])
                    connection.close()
                if self.stop.wait(.15):
                    self.check()
        finally:
            connection.close()
        summary = summarize_samples(samples, statuses)
        summary['errors'] = errors
        return summary

    def download(self, url, maximum):
        self.progress('检测下载吞吐 · 单连接，最多 8 MiB')
        connection = self.connection(url)
        received = 0
        partial = False
        timer = None
        body_timed_out = threading.Event()
        try:
            response = self.request(connection, url)
            if response.status != 200:
                raise ValueError(f'测速服务返回 HTTP {response.status}')
            started = time.perf_counter()
            active_socket = self.active_socket
            def expire_body():
                body_timed_out.set()
                self.abort_socket(active_socket)
            timer = threading.Timer(self.download_time_limit,expire_body)
            timer.daemon = True
            timer.start()
            while received < maximum:
                self.check()
                if time.perf_counter()-started > self.download_time_limit:
                    partial = True
                    break
                chunk = response.read1(min(16384, maximum-received))
                if not chunk:
                    break
                received += len(chunk)
            elapsed = time.perf_counter()-started
            if received != maximum and (not partial or received < 65536):
                raise ValueError(f'下载样本不完整（{received}/{maximum} 字节）')
            return {'mbps': round(received*8/max(elapsed, .000001)/1_000_000, 2), 'bytes': received, 'partial': partial}
        except (OSError, http.client.HTTPException, ValueError) as exc:
            self.check()
            if body_timed_out.is_set() and received >= 65536:
                elapsed = time.perf_counter()-started
                return {'mbps': round(received*8/max(elapsed,.000001)/1_000_000,2),
                        'bytes': received, 'partial': True}
            return {'mbps': None, 'bytes': received, 'error': str(exc)[:180]}
        finally:
            if timer:
                timer.cancel()
            connection.close()

    def run(self, site_url=SITE_URL, general_url=GENERAL_URL, download_url=DOWNLOAD_URL,
            download_bytes=DOWNLOAD_BYTES):
        self.check()
        started = time.perf_counter()
        site = self.latency(site_url, '抖音直播站点', 'GET')
        general = self.latency(general_url, '通用网络', 'GET')
        download = self.download(download_url, download_bytes)
        self.check()
        return {'site': site, 'general': general, 'download': download,
                'assessment': assess_network(site), 'elapsed_s': round(time.perf_counter()-started, 1)}
