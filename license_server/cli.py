"""Server-local administration. No password arguments and no automatic secret replacement."""
import argparse
import base64
import getpass
import json
import os
import secrets
import sqlite3
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from license_protocol import Clock
from .config import ServerSettings
from .database import Database
from .auth import AuthService
from .signing import Signer


def initialize_server(settings, username, password):
    db = Database(settings.database_path)
    db.initialize()
    with db.connect() as connection:
        if connection.execute("SELECT id FROM admin").fetchone(): raise ValueError("server_already_initialized")
    if len(password) < 12 or len(password) > 256: raise ValueError("password_length_12_to_256")
    directory = settings.secret_path
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    signing_path, digest_path = directory / "signing.pem", directory / "digest.key"
    if signing_path.exists() != digest_path.exists(): raise ValueError("partial_secrets_restore_backup_do_not_regenerate")
    if not signing_path.exists():
        key = Ed25519PrivateKey.generate()
        data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        for path, content in ((signing_path, data), (digest_path, secrets.token_bytes(32))):
            with path.open("xb") as stream: stream.write(content)
            path.chmod(0o600)
    settings.validate_secrets()
    Signer.from_file(signing_path, settings.key_id)
    AuthService(db, Clock()).initialize(username, password)
    # The bootstrap container runs as root; service files become readable only by its non-root UID.
    if os.name != "nt" and os.geteuid() == 0:
        for path in (settings.data_dir, settings.database_path, directory, signing_path, digest_path):
            os.chown(path, 10001, 10001)


def export_public_key(settings):
    signer = Signer.from_file(settings.secret_path / "signing.pem", settings.key_id)
    return {"key_id": settings.key_id, "public_key": base64.b64encode(signer.public_bytes()).decode("ascii")}


def restore_database(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.exists() or source.resolve() == destination.resolve(): raise ValueError("restore_to_new_path_only")
    if not source.is_file(): raise ValueError("backup_missing")
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    target = None
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise ValueError("invalid_backup")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1: raise ValueError("invalid_backup")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb"): pass
        target = sqlite3.connect(str(destination))
        connection.backup(target)
        target.execute("DELETE FROM sessions")
        target.commit()
    finally:
        connection.close()
        if target: target.close()


def main():
    parser = argparse.ArgumentParser(description="DY 授权服务本地管理")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init").add_argument("--username", default="admin")
    sub.add_parser("reset-admin")
    sub.add_parser("export-public-key")
    sub.add_parser("backup").add_argument("destination", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("destination", type=Path)
    args = parser.parse_args()
    settings = ServerSettings.from_env()
    try:
        if args.action in ("init", "reset-admin"):
            password = getpass.getpass("管理员新密码（至少 12 字符，不显示）：")
            if password != getpass.getpass("再次输入密码："): raise ValueError("passwords_do_not_match")
            if args.action == "init": initialize_server(settings, args.username, password)
            else: AuthService(Database(settings.database_path), Clock()).reset_password(password)
            print("管理员设置成功。密码不输出、不保存为明文。")
        elif args.action == "export-public-key": print(json.dumps(export_public_key(settings)))
        elif args.action == "backup":
            Database(settings.database_path).backup(args.destination)
            print("一致性数据库备份完成；签名密钥和摘要秘密须另外安全备份。")
        else:
            restore_database(args.source, args.destination)
            print("数据库已恢复到新路径，旧管理员会话已撤销；没有覆盖运行数据库。")
    except (ValueError, OSError, sqlite3.Error):
        raise SystemExit("操作未完成：检查初始化状态、路径、权限或备份；未输出敏感数据。")


if __name__ == "__main__": main()
