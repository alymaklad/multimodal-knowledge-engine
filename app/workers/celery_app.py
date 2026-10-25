from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "instagram_knowledge_engine",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Fail fast when the broker is unreachable. By default Celery retries the connection with
    # backoff, which blocks the API request thread indefinitely - a dispatch call simply never
    # returned when Redis was not running. A queueing call that cannot reach the queue should
    # report that in seconds, not hang.
    broker_connection_retry_on_startup=False,
    broker_connection_max_retries=0,
    broker_transport_options={"socket_connect_timeout": 2, "socket_timeout": 2, "max_retries": 0},
)
