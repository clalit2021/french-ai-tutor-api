# app/celery_app.py
import os
from celery import Celery

BROKER  = os.getenv("CELERY_BROKER_URL", "")
BACKEND = os.getenv("CELERY_RESULT_BACKEND", BROKER or "")

celery_app = Celery("french_tutor", broker=BROKER, backend=BACKEND)
celery_app.conf.task_ignore_result = False
# quiet future deprecation + friendlier startup
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.task_track_started = True

print("[CELERY CFG] broker:", BROKER, "backend:", BACKEND)