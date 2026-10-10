"""Public bulletin reads; authenticated, CSRF-protected administration."""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from .admin_api import require_admin, require_write, render

class BulletinInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)

def router():
    api = APIRouter()
    @api.get('/api/v1/announcements')
    def public(request: Request):
        with request.app.state.db.connect() as db:
            return {'items': [dict(row) for row in db.execute('SELECT id,title,content,published_at,updated_at FROM announcements WHERE withdrawn=0 ORDER BY published_at DESC,id DESC LIMIT 100')]}
    @api.get('/admin/announcements')
    def page(request: Request, session=Depends(require_admin)):
        with request.app.state.db.connect() as db:
            items=[dict(row) for row in db.execute('SELECT * FROM announcements ORDER BY published_at DESC,id DESC')]
        return render('announcements.html', title='公告管理', csrf=session.csrf, items=items)
    def save(request, body, identity=None, withdraw=False):
        state=request.app.state
        now=int(state.clock.wall_time())
        with state.db.connect(write=True) as db:
            if identity:
                if not db.execute('SELECT id FROM announcements WHERE id=?',(identity,)).fetchone():
                    raise HTTPException(404,'announcement_not_found')
                if withdraw:
                    db.execute('UPDATE announcements SET withdrawn=1,updated_at=? WHERE id=?',(now,identity))
                else:
                    db.execute('UPDATE announcements SET title=?,content=?,updated_at=? WHERE id=?',(body.title,body.content,now,identity))
            else:
                identity=uuid.uuid4().hex
                db.execute('INSERT INTO announcements VALUES(?,?,?,?,?,0)',(identity,body.title,body.content,now,now))
            db.execute('INSERT INTO audit(actor,action,card_id,at,details) VALUES(?,?,?,?,?)',('admin','announcement_withdraw' if withdraw else 'announcement_save',None,now,identity))
        return {'id':identity}
    @api.post('/admin/announcements')
    def publish(body: BulletinInput, request: Request, session=Depends(require_write)):
        return save(request,body)
    @api.post('/admin/announcements/{identity}/edit')
    def edit(identity: str, body: BulletinInput, request: Request, session=Depends(require_write)):
        return save(request,body,identity)
    @api.post('/admin/announcements/{identity}/withdraw')
    def withdraw(identity: str, request: Request, session=Depends(require_write)):
        return save(request,None,identity,True)
    return api
