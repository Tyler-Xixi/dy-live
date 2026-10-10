import threading
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
from pathlib import Path
from license_protocol import Clock, strict_json_loads
from .config import ServerSettings
from .database import Database
from .cards import CardService
from .signing import Signer
from .auth import AuthService
from . import public_api, admin_api, announcements


def create_app(settings: ServerSettings, clock=None):
    settings.validate_secrets()
    app = FastAPI(debug=False, docs_url=None, redoc_url=None, openapi_url=None)
    state = app.state
    state.settings, state.clock = settings, clock or Clock()
    state.db = Database(settings.database_path)
    state.db.initialize()
    state.signer = Signer.from_file(settings.secret_path / "signing.pem", settings.key_id)
    state.digest_secret = (settings.secret_path / "digest.key").read_bytes()
    state.public_keys = {settings.key_id: state.signer.public_bytes()}
    state.cards = CardService(state.db, state.digest_secret, state.clock, settings.key_id)
    state.auth = AuthService(state.db, state.clock)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.origin).hostname])
    attempts, lock = {}, threading.Lock()

    @app.middleware("http")
    async def protections(request: Request, call_next):
        # Enforce streaming body bounds before parsing (Content-Length can be missing or false).
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536: return JSONResponse({"detail": "request_too_large"}, 413)
        request._body = bytes(body)
        if body and request.headers.get("content-type", "").split(";")[0] == "application/json":
            try: strict_json_loads(bytes(body))
            except ValueError: return JSONResponse({"detail": "invalid_json"}, 400)
        if request.url.path.startswith("/api/v1/") and not settings.proxy_rate_limited:
            # Deliberately do not trust arbitrary client X-Forwarded-For headers.
            source = request.client.host if request.client else "unknown"
            now = state.clock.wall_time()
            with lock:
                expired = [k for k, values in attempts.items() if not values or values[-1] <= now-60]
                for key in expired: attempts.pop(key, None)
                times = [t for t in attempts.get(source, []) if t > now-60]
                if len(times) >= 120 or len(attempts) >= 10000 and source not in attempts:
                    return JSONResponse({"detail": "rate_limited"}, 429)
                attempts[source] = times + [now]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(Exception)
    async def generic_error(request, exc):
        return JSONResponse({"detail": "service_unavailable"}, 500, headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health(): return {"ok": True}

    app.include_router(announcements.router())
    app.include_router(public_api.router())
    app.include_router(admin_api.router())
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    return app


def production_app():
    return create_app(ServerSettings.from_env())
