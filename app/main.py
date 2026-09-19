"""ExactRoom — TeX 数式の厳密自動判定 Web アプリ。

* 解答者: 部屋コード + 名前 (ログイン不要)
* 出題者: 部屋コード + 秘密キー
* 判定  : Python + SymPy による厳密な同値判定 (数値近似なし)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SQLTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .db import init_db
from .mathjudge.runner import configure_runner, reset_runner
from .routers import host, rooms, solve

logger = logging.getLogger("exactroom")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    init_db()
    configure_runner(
        isolation=settings.judge_isolation,
        workers=settings.judge_workers,
        timeout=settings.judge_timeout_sec,
    )
    logger.info(
        "ExactRoom started (env=%s, judge=%s)",
        settings.environment,
        settings.judge_isolation,
    )
    try:
        yield
    finally:
        reset_runner()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ExactRoom API",
        description="TeX 数式の厳密な自動判定 (SymPy)",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    origins = settings.cors_origin_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,  # Bearer トークン方式なので Cookie は不要
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            max_age=600,
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        cdn = " ".join(settings.cdn_host_list)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            f"script-src 'self' {cdn}; "
            f"style-src 'self' 'unsafe-inline' {cdn} https://fonts.googleapis.com; "
            f"font-src 'self' data: {cdn} https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self' " + " ".join(origins) + "; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        else:
            # 毎回 ETag で確認させる。これが無いと、アプリを更新したときに
            # 「古い JS + 新しい HTML」の組み合わせになって画面が壊れる。
            # 変更が無ければ 304 が返るだけなので通信量はほぼ増えない。
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    app.include_router(rooms.router)
    app.include_router(solve.router)
    app.include_router(host.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": "http_error"},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "入力"
        return JSONResponse(
            status_code=422,
            content={
                "detail": f"{field} の値が不正です。",
                "code": "validation_error",
            },
        )

    @app.exception_handler(SQLTimeoutError)
    async def db_busy(_request: Request, exc: SQLTimeoutError):
        """DB の接続待ちが詰まったときは、落ちずに「混雑中」と返す。

        これが無いと、同時アクセスが多いときに待たされた末 500 になる。
        恒常的に出るなら DB_POOL_SIZE / DB_MAX_OVERFLOW を増やす。
        """
        logger.warning("database pool exhausted: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "detail": "いま混み合っています。数秒おいてもう一度お試しください。",
                "code": "busy",
            },
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(OperationalError)
    async def db_unavailable(_request: Request, exc: OperationalError):
        logger.warning("database operational error: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "detail": "データベースに接続できませんでした。"
                "数秒おいてもう一度お試しください。",
                "code": "db_unavailable",
            },
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(Exception)
    async def unhandled_error(_request: Request, exc: Exception):  # pragma: no cover
        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "サーバ内部エラーが発生しました。", "code": "internal"},
        )

    if settings.serve_static and STATIC_DIR.is_dir():
        app.mount(
            "/static", StaticFiles(directory=str(STATIC_DIR)), name="static"
        )

        # フロントを別ホスト (GitHub Pages など) に置いたときと同じ相対リンクで
        # 動くように、"/solve" と "/solve.html" の両方を受ける。
        @app.get("/", include_in_schema=False)
        @app.get("/index.html", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/solve", include_in_schema=False)
        @app.get("/solve.html", include_in_schema=False)
        def solve_page() -> FileResponse:
            return FileResponse(STATIC_DIR / "solve.html")

        @app.get("/host", include_in_schema=False)
        @app.get("/host.html", include_in_schema=False)
        def host_page() -> FileResponse:
            return FileResponse(STATIC_DIR / "host.html")

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

        # ブラウザが自動で取りに来る定番パス (404 をログに残さない)
        @app.get("/apple-touch-icon.png", include_in_schema=False)
        @app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
        def apple_touch_icon() -> FileResponse:
            return FileResponse(
                STATIC_DIR / "apple-touch-icon.png", media_type="image/png"
            )

        @app.get("/robots.txt", include_in_schema=False)
        def robots() -> FileResponse:
            return FileResponse(STATIC_DIR / "robots.txt", media_type="text/plain")

        @app.get("/manifest.json", include_in_schema=False)
        def manifest() -> FileResponse:
            return FileResponse(
                STATIC_DIR / "manifest.json", media_type="application/manifest+json"
            )

    return app


app = create_app()
