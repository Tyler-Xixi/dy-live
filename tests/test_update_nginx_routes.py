import re,unittest
from pathlib import Path
class NginxRoutes(unittest.TestCase):
    def test_signed_resource_names_and_denied_paths(self):
        config=Path('updates/deploy/nginx.location.conf').read_text()
        patterns=re.findall(r'location ~ (.*?) \{',config)
        accepted=lambda path:any(re.fullmatch(pattern,path) for pattern in patterns)
        for path in ('/updates/releases/6.1.5/DYLiveAssistant-6.1.5-windows-x64.zip','/updates/releases/6.1.5/DYLiveAssistant-6.1.4-to-6.1.5-windows-x64.zip','/updates/releases/6.1.5/incremental-6.1.4.json'):
            self.assertTrue(accepted(path),path)
        for path in ('/updates/releases/6.1.5/incremental-6.1.4.payload.json','/updates/releases/6.1.5/private.pem','/updates/releases/6.1.5/../secret','/updates/releases/6.1.5/test.zip'):
            self.assertFalse(accepted(path),path)
        self.assertIn('return 404;',config)
