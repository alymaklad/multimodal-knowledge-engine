"""Handing a new job to the worker.

The HTTP request must never block on an unreachable broker. Celery's own retry settings did not
bound this reliably — with Redis stopped, `apply_async` still hung well past a minute — so the
broker is probed with a plain socket first. A queueing call that cannot reach the queue should say
so in a second, not leave the request hanging with no explanation.
"""

from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

from app.core.config import get_settings
from app.workers.tasks import start_source_task

logger = logging.getLogger(__name__)

BROKER_PROBE_TIMEOUT_SECONDS = 1.0


class JobDispatchError(RuntimeError):
    pass


def dispatch_source_job(job_id: int) -> str:
    if get_settings().task_runner.lower() in {"local", "thread", "inprocess"}:
        from app.services.local_runner import submit

        return submit(job_id)

    _require_reachable_broker()
    try:
        # retry=False so a broker that drops out between the probe and the publish surfaces here
        # rather than being retried behind the scenes while the request waits.
        result = start_source_task.apply_async(args=[job_id], retry=False)
    except Exception as error:
        raise JobDispatchError(
            f"Unable to enqueue the processing job ({type(error).__name__})"
        ) from error
    return result.id


def _require_reachable_broker() -> None:
    host, port = _broker_address()
    try:
        with socket.create_connection((host, port), timeout=BROKER_PROBE_TIMEOUT_SECONDS):
            return
    except OSError as error:
        logger.warning("Task broker unreachable at %s:%s (%s)", host, port, error)
        raise JobDispatchError(
            f"The task queue at {host}:{port} is not reachable. Start Redis and the Celery worker "
            "(docker compose up redis worker), then try again."
        ) from error


def _broker_address() -> tuple[str, int]:
    parsed = urlparse(get_settings().celery_broker_url)
    return parsed.hostname or "localhost", parsed.port or 6379
