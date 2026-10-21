"""Extract only a nickname from known current-user responses during login."""
from urllib.parse import urlsplit
import unicodedata


def current_user_from_response(url, document):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not (host == 'douyin.com' or host.endswith('.douyin.com')):
        return None
    if not isinstance(document, dict) or document.get('status_code') != 0:
        return None
    if parsed.path == '/webcast/user/me/':
        user = document.get('data')
    elif parsed.path == '/aweme/v1/web/user/profile/self/':
        user = document.get('user')
    else:
        return None
    return user if isinstance(user,dict) else None


def nickname_from_response(url, document):
    user=current_user_from_response(url,document)
    name = user.get('nickname') if user else None
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        return None
    return ' '.join(name.split())


def user_id_from_response(url, document):
    user=current_user_from_response(url,document)
    if not user: return None
    for key in ('unique_id','short_id'):
        value=user.get(key)
        if type(value) is int and value>0: value=str(value)
        if isinstance(value,str) and value.strip() and len(value)<=100 and not any(ord(c)<32 for c in value):
            return value.strip()
    return None


def user_label(nickname):
    if not nickname:
        return '用户：昵称未识别'
    width = lambda ch: 2 if unicodedata.east_asian_width(ch) in ('W','F') else 1
    if sum(width(ch) for ch in nickname) <= 10:
        return '用户：' + nickname
    result, used = '', 0
    for ch in nickname:
        if used+width(ch) > 8:
            break
        result += ch
        used += width(ch)
    return '用户：' + result + '…'
