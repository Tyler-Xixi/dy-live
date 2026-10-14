"""macOS-only operating system adapters; purchase logic remains in the runner."""
import hashlib
import platform
import plistlib
import subprocess
from pathlib import Path


def platform_name(machine=None):
    value = machine or platform.machine()
    if value not in ('arm64', 'x86_64'):
        raise RuntimeError('Mac版仅支持 Apple Silicon arm64 和 Intel x86_64')
    return 'macos-arm64' if value == 'arm64' else 'macos-x64'


def application_data_dir(home=None):
    directory = (home or Path.home()) / 'Library' / 'Application Support' / 'DYLiveAssistantMac'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def machine_fingerprint():
    try:
        data = subprocess.check_output(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice', '-a'], timeout=5)
        value = plistlib.loads(data)[0]['IOPlatformUUID']
        if not isinstance(value, str) or not value.strip():
            raise ValueError('missing UUID')
    except Exception as exc:
        raise RuntimeError('无法读取Mac设备标识，请检查系统权限后重试') from exc
    return hashlib.sha256(('DYLiveAssistant|' + value).encode('utf-8')).hexdigest()


def open_directory(path):
    subprocess.Popen(['/usr/bin/open', str(path)])


def set_window_icon(window, asset_dir):
    import tkinter as tk
    window.mac_icon = tk.PhotoImage(master=window, file=str(Path(asset_dir) / 'app.png'))
    window.iconphoto(True, window.mac_icon)
