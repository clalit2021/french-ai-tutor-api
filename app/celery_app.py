import os
from celery import Celery

BROKER = os.getenv("CELERY_BROKER_URL", "")
BACKEND = os.getenv("CELERY_RESULT_BACKEND", BROKER)

celery_app = Celery("french_tutor", broker=BROKER, backend=BACKEND)
celery_app.conf.task_ignore_result = False

# Optional: autodiscover tasks in the app module (for future scalability)
celery_app.autodiscover_tasks(['app'])
