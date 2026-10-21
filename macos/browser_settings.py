"""Select the software's browser while keeping each explicit profile separate."""
from pathlib import Path
from browser_discovery import registered_browsers, default_browser


BROWSER_CHOICES = {
    '跟随系统默认浏览器': 'system',
    '自动选择': 'auto',
    'Microsoft Edge': 'msedge',
    'Google Chrome': 'chrome',
}
DETECTED_BROWSERS = registered_browsers()
for token, (label, _) in DETECTED_BROWSERS.items():
    if token not in BROWSER_CHOICES.values():
        BROWSER_CHOICES[label + '（兼容性待验证）'] = token


def browser_profile_dir(base, choice='auto') -> Path:
    if choice not in BROWSER_CHOICES.values():
        raise ValueError(f'无效的浏览器选择：{choice}')
    base = Path(base)
    return base if choice == 'auto' else base / choice


def launch_browser(playwright, base, headless, log, choice='auto'):
    executable = None
    if choice == 'system':
        choice, label, executable = default_browser()
        log('info', f'系统默认浏览器：{label}；使用软件独立资料目录')
    elif choice in DETECTED_BROWSERS and choice not in ('msedge', 'chrome'):
        label, executable = DETECTED_BROWSERS[choice]
    profile_dir = str(Path(base) / choice) if executable else str(browser_profile_dir(base, choice))
    options = {
        'headless': headless,
        'viewport': {'width': 1365, 'height': 900},
        'locale': 'zh-CN',
        'timezone_id': 'Asia/Shanghai',
    }
    candidates = [('Microsoft Edge', 'msedge'), ('Google Chrome', 'chrome'), ('Playwright Chromium', None)]
    if choice != 'auto':
        candidates = [(label, channel) for label, channel in candidates if channel == choice]
    if executable:
        candidates = [(label, choice)]
    errors = []
    for label, channel in candidates:
        try:
            channel_options = ({'executable_path': executable} if executable else {'channel': channel} if channel else {})
            context = playwright.chromium.launch_persistent_context(profile_dir, **channel_options, **options)
        except Exception as exc:
            detail = ' '.join(str(exc).split())[:160]
            errors.append(f'{label}: {detail}')
            if choice != 'auto':
                raise RuntimeError(
                    f'无法启动所选浏览器 {label}。请确认已安装该浏览器，或改为“自动选择”；不会自动切换其他浏览器。\n{detail}'
                ) from exc
            if channel is None:
                raise RuntimeError(
                    '无法启动浏览器。请安装 Microsoft Edge 或 Google Chrome；'
                    '源码运行也可执行 python -m playwright install chromium。\n' + '\n'.join(errors)
                ) from exc
            continue
        log('info', f'已使用系统浏览器：{label}' if channel else '已使用 Playwright Chromium')
        return context
