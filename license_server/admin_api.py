import hashlib
import hmac
import secrets
from dataclasses import asdict
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr
from pathlib import Path
from datetime import datetime, timezone, timedelta
from jinja2 import Environment, FileSystemLoader, select_autoescape
from fastapi.responses import RedirectResponse
from .auth import AuthDenied


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: StrictStr
    password: StrictStr


class GenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt
    duration_days: StrictInt | None = None
    note: StrictStr = ""


templates = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html"]))
HK = timezone(timedelta(hours=8))
templates.filters["hkdate"] = lambda value: datetime.fromtimestamp(value, HK).strftime("%Y-%m-%d %H:%M:%S") if value is not None else "—"
templates.filters["hkinput"] = lambda value: datetime.fromtimestamp(value, HK).strftime("%Y-%m-%dT%H:%M") if value is not None else ""


def render(name, **context):
    return HTMLResponse(templates.get_template(name).render(**context))


def wants_html(request):
    return "text/html" in request.headers.get("accept", "")


def login_token(state):
    value = secrets.token_urlsafe(24) + "." + str(state.clock.wall_time())
    signature = hmac.new(state.digest_secret, value.encode(), hashlib.sha256).hexdigest()
    return value + "." + signature


def validate_login_token(state, token):
    try:
        value, signature = token.rsplit(".", 1)
        issued = int(value.rsplit(".", 1)[1])
        expected = hmac.new(state.digest_secret, value.encode(), hashlib.sha256).hexdigest()
        return 0 <= state.clock.wall_time()-issued < 1800 and hmac.compare_digest(signature, expected)
    except (ValueError, AttributeError): return False


def same_origin(request):
    if request.headers.get("origin") != request.app.state.settings.origin:
        raise HTTPException(403, "invalid_origin")


def require_admin(request: Request):
    try: return request.app.state.auth.require(request.cookies.get("dyl_admin"))
    except AuthDenied as exc:
        if request.method == "GET" and wants_html(request):
            raise HTTPException(303, "login_required", headers={"Location": "/admin/login"})
        raise HTTPException(403, str(exc))


def require_write(request: Request, session=Depends(require_admin)):
    same_origin(request)
    token = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(token, session.csrf): raise HTTPException(403, "invalid_csrf")
    return session


def router():
    admin = APIRouter(prefix="/admin")
    protected = APIRouter(dependencies=[Depends(require_admin)])

    @admin.get("/login")
    def login_page(request: Request):
        state = request.app.state
        token = login_token(state)
        response = render("login.html", csrf=token, title="管理员登录")
        response.headers["x-csrf-token"] = token
        response.set_cookie("dyl_login_csrf", token, secure=not state.settings.test_mode or state.settings.origin.startswith("https:"),
                            httponly=True, samesite="lax", path="/admin", max_age=1800)
        return response

    @admin.post("/login")
    def login(body: LoginInput, request: Request):
        state = request.app.state
        same_origin(request)
        token = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(token, request.cookies.get("dyl_login_csrf", "")) or not validate_login_token(state, token):
            raise HTTPException(403, "invalid_csrf")
        try: session = state.auth.login(body.username, body.password, request.client.host if request.client else "unknown")
        except AuthDenied as exc: raise HTTPException(429 if str(exc) == "rate_limited" else 403, str(exc))
        state.auth.logout(request.cookies.get("dyl_admin"))
        response = JSONResponse({"ok": True, "csrf": session.csrf})
        response.set_cookie("dyl_admin", session.token, secure=not state.settings.test_mode or state.settings.origin.startswith("https:"),
                            httponly=True, samesite="lax", path="/admin", max_age=28800)
        response.delete_cookie("dyl_login_csrf", path="/admin")
        return response

    @protected.post("/logout")
    def logout(request: Request, session=Depends(require_write)):
        request.app.state.auth.logout(session.token)
        response = JSONResponse({"ok": True})
        response.delete_cookie("dyl_admin", path="/admin")
        return response

    @protected.get("/cards")
    def cards(request: Request, page: int = 1, q: str = "", status: str = "", batch_id: str = "",
              expires_before: int | None = None, expires_after: int | None = None,
              expiry_from: str = "", expiry_to: str = "", session=Depends(require_admin)):
        try:
            def local_timestamp(value):
                if len(value) > 19: raise ValueError("invalid_date")
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is not None: raise ValueError("local_date_required")
                return int(parsed.replace(tzinfo=HK).timestamp())
            if expiry_from: expires_after = local_timestamp(expiry_from)
            if expiry_to: expires_before = local_timestamp(expiry_to)
            if expires_after is not None and expires_before is not None and expires_after > expires_before:
                raise ValueError("invalid_range")
            result = request.app.state.cards.list_cards({"q": q, "status": status, "batch_id": batch_id,
                "expires_before": expires_before, "expires_after": expires_after}, page, 50)
            if wants_html(request):
                return render("cards.html", title="卡密管理", csrf=session.csrf, cards=result, q=q, status=status,
                              expiry_from=expiry_from, expiry_to=expiry_to)
            return asdict(result)
        except (ValueError, OverflowError, OSError): raise HTTPException(400, "invalid_filter")

    @protected.get("/cards/{card_id}")
    def detail(card_id: str, request: Request, session=Depends(require_admin)):
        try: card = request.app.state.cards.get(card_id)
        except ValueError: raise HTTPException(404, "card_not_found")
        return render("card_detail.html", title="卡密详情", csrf=session.csrf, card=card) if wants_html(request) else asdict(card)

    @protected.post("/cards/generate")
    def generate(body: GenerateInput, request: Request, session=Depends(require_write)):
        try: return asdict(request.app.state.cards.create_batch(body.count, body.duration_days, body.note))
        except ValueError: raise HTTPException(400, "invalid_card_settings")

    @protected.post("/cards/{card_id}/{action}")
    def update(card_id: str, action: str, values: dict, request: Request, session=Depends(require_write)):
        try: return asdict(request.app.state.cards.update(card_id, action, values, "admin"))
        except ValueError: raise HTTPException(400, "invalid_card_update")

    @protected.get("/audit")
    def audit(request: Request, page: int = 1, session=Depends(require_admin)):
        try:
            items = request.app.state.cards.audit(page, 50)
            return render("audit.html", title="操作记录", csrf=session.csrf, items=items, page=page) if wants_html(request) else {"items": items}
        except ValueError: raise HTTPException(400, "invalid_filter")

    @admin.get("")
    def home(): return RedirectResponse("/admin/cards", status_code=303)

    admin.include_router(protected)
    return admin
