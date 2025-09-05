FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Default command is web; worker will override in Render settings
CMD ["waitress-serve","--listen=0.0.0.0:5000","app.main:app"]
