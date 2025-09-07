# app/celery_app.py
import os
from celery import Celery

BROKER = os.getenv("CELERY_BROKER_URL", "")
BACKEND = os.getenv("CELERY_RESULT_BACKEND", BROKER)

celery_app = Celery(
    "french-ai-tutor-api",
    broker=BROKER,
    backend=BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_ignore_result=True,          # set False if you need results
    worker_hijack_root_logger=False,  # let your app control logging
)

# Autodiscover tasks in the "app" package
celery_app.autodiscover_tasks(["app"])
