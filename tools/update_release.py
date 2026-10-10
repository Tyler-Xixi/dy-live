"""White-list a complete local build. Does not sign, upload or publish."""
import argparse
import dis
from datetime import datetime, timezone
import json
from pathlib import Path
import zipfile

from app_version import PRODUCT_ID, PLATFORM
from update_config import MANAGED_ROOTS
from update_protocol import UpdateError, assert_plain_path, file_hash, safe_relative, version_tuple
from update_client import atomic_json
from update_config import UPDATE_PUBLIC_KEYS
from update_protocol import parse_manifest


def embedded_version(executable):
    from PyInstaller.archive.readers import CArchiveReader
    code=CArchiveReader(str(executable)).open_embedded_archive('PYZ.pyz').extract('app_version')
    instructions=list(dis.get_instructions(code))
    for index,instruction in enumerate(instructions):
        if instruction.opname=='STORE_NAME' and instruction.argval=='APP_VERSION':
            previous=instructions[index-1]
            if previous.opname=='LOAD_CONST' and isinstance(previous.argval,str): return previous.argval
    raise UpdateError('发行版没有内嵌版本号')


def build_release(dist_dir,output_dir,version,sequence,notes):
    version_tuple(version)
    if type(sequence) is not int or sequence<1: raise UpdateError('发布序号不合法')
    dist=assert_plain_path(dist_dir); output=assert_plain_path(Path(output_dir)/version)
    if output.exists(): raise UpdateError('发行目录已存在，拒绝覆盖')
    if embedded_version(dist/'DYLiveAssistant.exe')!=version: raise UpdateError('EXE 内嵌版本与发布版本不一致')
    metadata=json.loads((dist/'release-info.json').read_text(encoding='utf-8'))
    if metadata.get('version')!=version: raise UpdateError('发行元数据版本不一致')
    entries=[]
    for name in MANAGED_ROOTS:
        root=assert_plain_path(dist/name)
        if not root.exists() or (name=='_internal')!=root.is_dir(): raise UpdateError('发行版缺少完整程序根')
        for path in sorted(root.rglob('*')) if root.is_dir() else [root]:
            assert_plain_path(path)
            if path.is_dir(): continue
            relative=safe_relative(path.relative_to(dist).as_posix())
            entries.append({'path':relative,'size':path.stat().st_size,'sha256':file_hash(path)})
    output.mkdir(parents=True,exist_ok=False)
    package=output/f'DYLiveAssistant-{version}-windows-x64.zip'
    with zipfile.ZipFile(package,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for entry in entries: archive.write(dist/entry['path'],entry['path'])
    payload={'protocol':1,'product':PRODUCT_ID,'platform':PLATFORM,'version':version,'sequence':sequence,
             'published_at':datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z'),
             'notes':notes,'package_path':f'/updates/releases/{version}/{package.name}',
             'package_size':package.stat().st_size,'package_sha256':file_hash(package),'files':entries,'updater_protocol':1}
    atomic_json(output/'payload.json',payload)
    return output


def build_incremental(base_dist: Path,target_dist: Path,target_manifest_raw: bytes,output_dir: Path,*,public_keys=None) -> Path:
    target=parse_manifest(target_manifest_raw,UPDATE_PUBLIC_KEYS if public_keys is None else public_keys)
    base=assert_plain_path(base_dist); dist=assert_plain_path(target_dist); output=assert_plain_path(output_dir)
    metadata=assert_plain_path(base/'release-info.json')
    if metadata.stat().st_size>4096: raise UpdateError('增量基线版本记录过大')
    base_version=json.loads(metadata.read_text(encoding='utf-8')).get('version')
    if version_tuple(base_version)>=version_tuple(target.version): raise UpdateError('增量基线版本不合法')
    changed=[]
    for entry in target.files:
        current=assert_plain_path(dist/entry.path)
        if not current.is_file() or current.stat().st_size!=entry.size or file_hash(current)!=entry.sha256:
            raise UpdateError('增量目标文件与签名清单不符')
        previous=assert_plain_path(base/entry.path)
        if not previous.is_file() or previous.stat().st_size!=entry.size or file_hash(previous)!=entry.sha256:
            changed.append(entry)
    if not changed: raise UpdateError('增量发行没有变化文件')
    output.mkdir(parents=True,exist_ok=True)
    package=assert_plain_path(output/f'DYLiveAssistant-{base_version}-to-{target.version}-windows-x64.zip')
    payload_path=assert_plain_path(output/f'incremental-{base_version}.payload.json')
    if package.exists() or payload_path.exists(): raise UpdateError('增量发行已存在，拒绝覆盖')
    with zipfile.ZipFile(package,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for entry in changed: archive.write(assert_plain_path(dist/entry.path),entry.path)
    import hashlib
    payload={'protocol':2,'kind':'plan','product':PRODUCT_ID,'platform':PLATFORM,
        'base_version':base_version,'target_version':target.version,'sequence':target.sequence,
        'target_manifest_sha256':hashlib.sha256(target_manifest_raw).hexdigest(),
        'package_path':f'/updates/releases/{target.version}/{package.name}',
        'package_size':package.stat().st_size,'package_sha256':file_hash(package),
        'changed_files':[vars(entry) for entry in changed]}
    # Validation after ZIP construction catches a source changed during packing.
    from update_incremental import IncrementalPlan, validate_incremental_archive
    values=dict(payload); values['changed_files']=tuple(changed)
    validate_incremental_archive(package,IncrementalPlan(**values))
    atomic_json(payload_path,payload)
    return package


def main():
    parser=argparse.ArgumentParser(description='仅打包，不自动上传发布')
    parser.add_argument('--dist',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--version',required=True); parser.add_argument('--sequence',type=int,required=True)
    parser.add_argument('--notes',required=True)
    args=parser.parse_args(); print(build_release(args.dist,args.output,args.version,args.sequence,args.notes))


if __name__=='__main__': main()
