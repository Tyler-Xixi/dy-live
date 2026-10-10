import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from update_fixtures import sample_release
from update_protocol import UpdateError
from tools.update_release import build_release


class ReleaseTests(unittest.TestCase):
    def test_bundle_excludes_profiles_license_secrets_old_zip_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); _,_,contents=sample_release(root,version='6.1.0')
            dist=root/'dist'; dist.mkdir()
            for name,data in contents.items():
                target=dist/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
            for name in ('license.key','old.zip','diagnostic.png','accounts.json','test_private_key.pem'):
                (dist/name).write_bytes(b'never publish')
            with patch('tools.update_release.embedded_version',return_value='6.1.0'):
                release=build_release(dist,root/'output','6.1.0',1,'首版')
                with zipfile.ZipFile(next(release.glob('*.zip'))) as archive:
                    self.assertEqual(set(archive.namelist()),set(contents))
                with self.assertRaises(UpdateError): build_release(dist,root/'different','6.2.0',2,'bad')


if __name__=='__main__': unittest.main()
