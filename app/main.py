import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.collections import router as collections_router
from app.api.analysis import router as analysis_router
from app.api.jobs import router as jobs_router
from app.api.sources import router as sources_router
from app.api.search import router as search_router
from app.api.system import router as system_router
from app.api.metadata import router as metadata_router
from app.api.evaluations import router as evaluations_router
from app.api.benchmarks import router as benchmarks_router
from app.api.corrections import router as corrections_router
from app.api.facets import router as facets_router
from app.api.saved_searches import router as saved_searches_router
from app.api.knowledge import router as knowledge_router
from app.api.knowledge import source_router as knowledge_source_router
from app.api.media import router as media_router
from app.api.auth import router as auth_router
from app.api.chat import collection_router as chat_collection_router
from app.api.chat import router as chat_router
from app.db.database import SessionLocal, engine
from app.db.migrate import migrate_database
import app.models  # noqa: F401 - ensures all ORM tables are registered
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var
from app.core.rate_limit import RateLimiter

configure_logging(get_settings().log_level)
logger = logging.getLogger(__name__)
rate_limiter = RateLimiter(get_settings().rate_limit_per_minute)

if get_settings().auto_migrate:
    migrate_database(engine)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Pick up work a previous run left in flight.

    In-process work does not survive a restart, so without this a source uploaded moments before one
    would sit queued with nothing coming to collect it. Celery has its own durable queue and needs
    no equivalent, so this is a no-op unless TASK_RUNNER is local.
    """
    if get_settings().task_runner.lower() in {"local", "thread", "inprocess"}:
        try:
            from app.services.local_runner import resume_unfinished

            resumed = resume_unfinished()
            if resumed:
                logger.info("Resumed unfinished jobs at startup", extra={"count": resumed})
        except Exception:
            # Never prevent the API from starting because a resume failed.
            logger.exception("Could not resume unfinished jobs")
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Multimodal Knowledge Engine",
    version="0.1.0",
    description="Build collections from a library of multimodal sources and ask questions answered from them, with citations.",
)
cors_origins = [origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()]
if cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=True)
app.include_router(collections_router)
app.include_router(analysis_router)
app.include_router(jobs_router)
app.include_router(sources_router)
app.include_router(search_router)
app.include_router(system_router)
app.include_router(metadata_router)
app.include_router(benchmarks_router)
app.include_router(evaluations_router)
app.include_router(corrections_router)
app.include_router(facets_router)
app.include_router(saved_searches_router)
app.include_router(knowledge_router)
app.include_router(knowledge_source_router)
app.include_router(media_router)
app.include_router(auth_router)
app.include_router(chat_collection_router)
app.include_router(chat_router)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if rate_limiter.enabled:
        # Keyed by bearer token when present, else client address: an unauthenticated flood and a
        # single misbehaving account are different problems, and both need a ceiling.
        authorization = request.headers.get("Authorization", "")
        key = authorization or (request.client.host if request.client else "unknown")
        allowed, remaining, retry_after = rate_limiter.check(key)
        if not allowed:
            logger.warning("Rate limit exceeded", extra={"path": request.url.path})
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": str(retry_after)},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
    return await call_next(request)


@app.middleware("http")
async def request_correlation(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id", uuid4().hex)
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Request failed",
            extra={"method": request.method, "path": request.url.path},
        )
        raise
    finally:
        request_id_var.reset(token)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    response.headers["X-Request-Id"] = request_id
    logger.info(
        "Request completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query or None,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "request_id": request_id,
        },
    )
    return response


STATIC_ROOT = Path(__file__).resolve().parent / "web" / "static"
app.mount("/app/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.get("/app", include_in_schema=False)
def web_app() -> FileResponse:
    """The single-page client. It talks to the same public API as any other consumer."""
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
def readiness_check() -> dict[str, str]:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "unavailable"})
    return {"status": "ready", "database": "ok"}
