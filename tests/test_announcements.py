import unittest
from fastapi.testclient import TestClient
from license_server.app import create_app
from license_test_support import server_fixture

class AnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.temp,self.settings,self.clock,_=server_fixture()
        self.app=create_app(self.settings,self.clock)
        self.app.state.auth.initialize('admin','test-only-password-long')
        self.client=TestClient(self.app,base_url=self.settings.origin)
        csrf=self.client.get('/admin/login').headers['x-csrf-token']
        login=self.client.post('/admin/login',json={'username':'admin','password':'test-only-password-long'},headers={'Origin':self.settings.origin,'X-CSRF-Token':csrf})
        self.headers={'Origin':self.settings.origin,'X-CSRF-Token':login.json()['csrf']}
    def tearDown(self):
        self.client.close();self.temp.cleanup()
    def test_history_edit_withdraw_escape_and_audit(self):
        ids=[]
        for title in ('first','<script>alert(1)</script>'):
            result=self.client.post('/admin/announcements',json={'title':title,'content':'<b>text</b>'},headers=self.headers)
            self.assertEqual(result.status_code,200);ids.append(result.json()['id']);self.clock.now+=1
        public=self.client.get('/api/v1/announcements').json()['items']
        self.assertEqual(len(public),2);self.assertEqual(public[0]['id'],ids[1])
        self.assertNotIn('<script>alert(1)</script>',self.client.get('/admin/announcements').text)
        self.assertEqual(self.client.post('/admin/announcements/'+ids[0]+'/edit',json={'title':'edited','content':'new'},headers=self.headers).status_code,200)
        self.assertEqual(self.client.post('/admin/announcements/'+ids[1]+'/withdraw',headers=self.headers).status_code,200)
        self.assertEqual([x['title'] for x in self.client.get('/api/v1/announcements').json()['items']],['edited'])
        with self.app.state.db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action LIKE 'announcement_%'").fetchone()[0],4)
    def test_public_without_activation_and_protected_writes(self):
        with TestClient(self.app,base_url=self.settings.origin) as anon:
            self.assertEqual(anon.get('/api/v1/announcements').status_code,200)
            self.assertEqual(anon.post('/admin/announcements',json={'title':'x','content':'y'}).status_code,403)
        for headers in ({},{**self.headers,'Origin':'https://evil.test'},{**self.headers,'X-CSRF-Token':'wrong'}):
            self.assertEqual(self.client.post('/admin/announcements',json={'title':'x','content':'y'},headers=headers).status_code,403)
    def test_lengths_and_old_database_initialization(self):
        self.app.state.db.initialize()
        for body in ({'title':'','content':'x'},{'title':'x'*201,'content':'x'},{'title':'x','content':'x'*20001}):
            self.assertEqual(self.client.post('/admin/announcements',json=body,headers=self.headers).status_code,422)

    def test_old_database_migrates_and_audit_failure_rolls_back(self):
        with self.app.state.db.connect(write=True) as db:db.execute('DROP TABLE announcements')
        self.app.state.db.initialize()
        with self.app.state.db.connect(write=True) as db:
            db.execute("CREATE TRIGGER reject_announcement_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'test rejection'); END")
        with TestClient(self.app,base_url=self.settings.origin,raise_server_exceptions=False) as client:
            client.cookies.update(self.client.cookies)
            self.assertEqual(client.post('/admin/announcements',json={'title':'x','content':'y'},headers=self.headers).status_code,500)
        self.assertEqual(self.client.get('/api/v1/announcements').json()['items'],[])
    def test_invalid_session_rejected(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/admin/announcements',json={'title':'x','content':'y'},headers=self.headers).status_code,403)
