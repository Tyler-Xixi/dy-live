"""Discover supported Chromium browsers without opening or reusing user profiles."""
import plistlib
import subprocess
from pathlib import Path

KNOWN = {
    'chrome': ('Google Chrome', 'Google Chrome.app/Contents/MacOS/Google Chrome', 'com.google.Chrome'),
    'msedge': ('Microsoft Edge', 'Microsoft Edge.app/Contents/MacOS/Microsoft Edge', 'com.microsoft.edgemac'),
    'brave': ('Brave', 'Brave Browser.app/Contents/MacOS/Brave Browser', 'com.brave.Browser'),
}

def registered_browsers():
    found = {}
    for token, (name, relative, _) in KNOWN.items():
        for root in (Path('/Applications'), Path.home() / 'Applications'):
            executable = root / relative
            if executable.is_file():
                found[token] = (name, str(executable)); break
    return found

def default_browser():
    try:
        raw = subprocess.check_output(['/usr/bin/defaults', 'export', 'com.apple.LaunchServices/com.apple.launchservices.secure', '-'], stderr=subprocess.DEVNULL, timeout=5)
        handlers = plistlib.loads(raw).get('LSHandlers', [])
        bundle = next(x['LSHandlerRoleAll'] for x in reversed(handlers) if x.get('LSHandlerURLScheme') == 'https' and 'LSHandlerRoleAll' in x)
        for token, (name, _, identifier) in KNOWN.items():
            if identifier.casefold() == bundle.casefold():
                browser = registered_browsers().get(token)
                if browser: return token, name, browser[1]
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration, KeyError):
        pass
    raise ValueError('默认浏览器不受支持或无法确认，请选择已安装的 Microsoft Edge 或 Google Chrome。')
