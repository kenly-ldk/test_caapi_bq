FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Cloud Run sets $PORT (default 8080). gunicorn serves the Flask app.
ENV PORT=8080
CMD exec gunicorn --bind ":$PORT" --workers 1 --threads 8 --timeout 300 app:app
