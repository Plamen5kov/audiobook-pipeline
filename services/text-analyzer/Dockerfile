FROM python:3.11-slim

WORKDIR /srv
COPY services/text-analyzer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The build context is the repo root, because the analysis lives in core/ and
# this service is a thin HTTP wrapper around it.
COPY core/ ./core/
COPY services/text-analyzer/app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
