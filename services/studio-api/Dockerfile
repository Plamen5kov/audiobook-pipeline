FROM python:3.11-slim

WORKDIR /app

COPY services/studio-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Build context is the repo root: casting lives in core/.
COPY core/ ./core/
COPY services/studio-api/app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
