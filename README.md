# French AI Tutor - Backend Advanced v1

Ready for Render (Web + Worker) + Supabase + Redis + OpenAI.

## Web Service
Build:  pip install -r requirements.txt  
Start:  gunicorn -w 1 -k gthread --threads 8 --timeout 600 app.main:app

## Worker
Build:  pip install -r requirements.txt  
Start:  celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2

## Env (both services)
SUPABASE_URL  
SUPABASE_SERVICE_KEY  
CELERY_BROKER_URL  
OPENAI_API_KEY
