"""Validated account registry; browser sessions stay in separate profiles."""
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


class AccountStoreError(ValueError):
    pass


@dataclass(frozen=True)
class AccountRecord:
    account_id: str
    name: str
    selected: bool
    buy_times: int
    buy_quantity: int
    login_saved: bool


@dataclass(frozen=True)
class BatchJob:
    account_id: str
    name: str
    profile_dir: str
    buy_times: int
    buy_quantity: int


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AccountStoreError('账号文件存在重复字段')
        result[key] = value
    return result


def _validate(accounts):
    seen = set()
    for account in accounts:
        if not isinstance(account, AccountRecord):
            raise AccountStoreError('账号记录格式不正确')
        ident = account.account_id
        if not isinstance(ident, str) or not re.fullmatch(r'legacy|[0-9a-f]{32}', ident) or ident in seen:
            raise AccountStoreError('账号 ID 无效或重复')
        seen.add(ident)
        if not isinstance(account.name, str) or not 1 <= len(account.name.strip()) <= 60 or any(ord(c) < 32 or ord(c) == 127 for c in account.name):
            raise AccountStoreError('账号备注须为 1–60 字且不能包含控制字符')
        if any(type(v) is not bool for v in (account.selected, account.login_saved)):
            raise AccountStoreError('账号开关格式不正确')
        if any(type(v) is not int or v < 1 for v in (account.buy_times, account.buy_quantity)):
            raise AccountStoreError('订单数和每单数量必须是正整数')


def _reject_links(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise AccountStoreError('账号目录不能使用符号链接或目录联接')


class AccountStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).absolute()
        self.path = self.data_dir / 'batch_accounts.json'

    def load(self) -> tuple[AccountRecord, ...]:
        if not self.path.exists():
            return ()
        try:
            _reject_links(self.path)
            value = json.loads(self.path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
            if type(value) is not dict or set(value) != {'version', 'accounts'} or type(value['version']) is not int or value['version'] != 1 or type(value['accounts']) is not list:
                raise AccountStoreError('账号文件版本或格式不正确')
            fields = set(AccountRecord.__dataclass_fields__)
            if any(type(row) is not dict or set(row) != fields for row in value['accounts']):
                raise AccountStoreError('账号文件含有未知或缺失字段')
            accounts = tuple(AccountRecord(**row) for row in value['accounts'])
            _validate(accounts)
            return accounts
        except (OSError, ValueError, TypeError) as exc:
            raise AccountStoreError(f'读取账号失败：{exc}') from exc

    def save(self, accounts: tuple[AccountRecord, ...]) -> None:
        _validate(accounts)
        temporary = None
        try:
            _reject_links(self.path)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.data_dir, prefix='batch-accounts-', suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                json.dump({'version': 1, 'accounts': [asdict(a) for a in accounts]}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise AccountStoreError(f'保存账号失败：{exc}') from exc
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def new_account(self, name: str) -> AccountRecord:
        account = AccountRecord(uuid.uuid4().hex, name.strip(), False, 1, 1, False)
        _validate((account,))
        return account

    def jobs(self, accounts: tuple[AccountRecord, ...], default_profile: Path) -> tuple[BatchJob, ...]:
        _validate(accounts)
        jobs = []
        root = self.data_dir / 'batch-account-profiles'
        for account in accounts:
            if not account.selected:
                continue
            path = Path(default_profile).absolute() if account.account_id == 'legacy' else root / account.account_id
            _reject_links(path)
            resolved = path.resolve()
            default = Path(default_profile).resolve()
            if account.account_id != 'legacy' and (resolved == default or default in resolved.parents or resolved in default.parents):
                raise AccountStoreError('新账号资料不能与默认登录资料目录重叠')
            if account.account_id != 'legacy' and not resolved.is_relative_to(root.resolve()):
                raise AccountStoreError('账号目录超出范围')
            for existing in jobs:
                previous = Path(existing.profile_dir)
                if previous == resolved or previous in resolved.parents or resolved in previous.parents:
                    raise AccountStoreError('账号资料目录重叠，不能共用登录资料')
            jobs.append(BatchJob(account.account_id, account.name, str(resolved), account.buy_times, account.buy_quantity))
        return tuple(jobs)
