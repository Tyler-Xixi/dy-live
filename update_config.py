"""Public update policy only. Server signing private keys never belong here."""
UPDATE_ORIGIN = 'https://license.txblog.cn'
MANIFEST_PATH = '/updates/stable/latest.json'
import base64

# Official public key exported from the independent server update signer.
UPDATE_PUBLIC_KEYS: dict[str, bytes] = {
    'update-primary-v1': base64.b64decode('4K7xHj3n7StXxbiIlyvbEcxiDLqmOfhTjRSgoWxaq60=',validate=True)
}
UPDATE_PROTOCOL = 1
MAX_MANIFEST_SIZE = 2 * 1024 * 1024
MAX_PACKAGE_SIZE = 512 * 1024 * 1024
MAX_UNPACKED_SIZE = 2 * 1024 * 1024 * 1024
MAX_FILES = 15000
CHECK_TIMEOUT = 10
DOWNLOAD_TIMEOUT = 600
PROCESS_TIMEOUT = 30
MANAGED_ROOTS = ('DYLiveAssistant.exe', 'DYLiveUpdater.exe', '_internal',
                 '用户使用说明.md', 'release-info.json')
