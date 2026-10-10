"""Verified full-package installation to a user-selected fresh directory."""
import os
from pathlib import Path
import shutil
import uuid

from update_protocol import UpdateError,assert_plain_path,validate_archive,extract_verified
from update_incremental import validate_target_directory


def preflight_new_directory(manifest,destination: Path,protected_paths: tuple[Path,...]) -> Path:
    destination=assert_plain_path(destination)
    if any(part.lower() in ('360zip$temp','360$') for part in destination.parts):
        raise UpdateError('请先选择持久软件目录，不要安装到压缩包预览目录')
    if destination==Path.home().absolute(): raise UpdateError('请选择独立软件文件夹，不要直接安装到用户根目录')
    for protected in protected_paths:
        protected=assert_plain_path(protected)
        if destination==protected or destination in protected.parents or protected in destination.parents:
            raise UpdateError('新安装目录与旧程序或用户资料重叠')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise UpdateError('新安装目录必须为空，不能覆盖已有文件')
    if not destination.parent.is_dir(): raise UpdateError('新安装目录的上级目录不存在')
    required=sum(f.size for f in manifest.files)+16*1024*1024
    if shutil.disk_usage(destination.parent).free<required: raise UpdateError('新安装空间不足')
    return destination


def install_full_to_new_directory(manifest,archive: Path,destination: Path,protected_paths: tuple[Path,...],*,cancel=None) -> Path:
    destination=preflight_new_directory(manifest,destination,protected_paths)
    archive=assert_plain_path(archive)
    validate_archive(archive,manifest)
    if cancel is not None and cancel.is_set(): raise UpdateError('更新已取消')
    staged=assert_plain_path(destination.parent/('.dy-new-install-'+uuid.uuid4().hex))
    extract_verified(archive,staged,manifest)
    validate_target_directory(staged,manifest)
    if cancel is not None and cancel.is_set(): raise UpdateError('更新已取消，原软件未更改')
    # Only an empty directory explicitly selected for this installation may be
    # removed; no recursive deletion and no manipulation of the old install.
    assert_plain_path(destination)
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()): raise UpdateError('目标目录出现新文件，未覆盖')
        destination.rmdir()
    os.rename(staged,destination)
    return destination
