FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Default command is web; worker will override in Render settings
# Bind Gunicorn to the port provided by Render
CMD ["sh","-c","gunicorn -w 1 -k gthread --threads 8 --timeout 300 --bind 0.0.0.0:$PORT app.main:app"]
