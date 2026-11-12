"""Celery wakes workers; the database remains authoritative for queued jobs."""

from celery import Celery

from app.web_jobs import run_once
from app.web_settings import WebSettings
from app.web_store import Store

settings = WebSettings.from_env()
celery_app = Celery("teassistant", broker=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": 7200},
    beat_schedule={"wake-durable-jobs": {"task": "teassistant.run_pending", "schedule": 5.0}},
)


@celery_app.task(name="teassistant.run_pending", soft_time_limit=7100, time_limit=7200)
def run_pending():
    store = Store(settings.database_url)
    store.init()
    return run_once(store, settings, "celery")
