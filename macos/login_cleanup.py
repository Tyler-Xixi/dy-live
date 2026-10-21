"""Clear Douyin sessions from existing software browser profiles only."""
import stat
from pathlib import Path

from browser_settings import launch_browser


def is_link(path):
    return path.is_symlink() or bool(path.exists() and getattr(path.stat(follow_symlinks=False),
        'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def login_profiles(roots):
    profiles = set()
    for value in roots:
        root = Path(value).absolute()
        if any(is_link(part) for part in (root, *root.parents)):
            raise ValueError('登录资料目录不能使用符号链接或目录联接')
        if not root.is_dir():
            continue
        candidates = (root, *root.iterdir())
        for candidate in candidates:
            if is_link(candidate):
                raise ValueError('登录资料目录不能使用符号链接或目录联接')
            default = candidate / 'Default'
            if default.is_dir():
                if is_link(default):
                    raise ValueError('登录资料目录不能使用符号链接或目录联接')
                profiles.add(candidate.resolve())
    return tuple(sorted(profiles, key=str))


def clear_profiles(playwright, profiles, clear_login, log, stop):
    for profile in profiles:
        if stop.is_set():
            raise RuntimeError('已取消清理；部分资料可能已清除，请重新检查登录状态')
        # Use the exact discovered profile, without appending another browser folder.
        context = launch_browser(playwright, profile, True, log, 'auto')
        try:
            context.route('**/*', lambda route: route.abort())
            clear_login(context)
            log('info', f'已清除抖音登录资料：{profile}')
        finally:
            context.close()
