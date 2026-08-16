FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY services/qa-verifier/requirements.txt .

# Install CUDA-enabled torch first. PyPI default aarch64 wheel is CPU-only.
# cu130 matches CUDA 13.0 on the DGX Spark GB10.
RUN pip install --no-cache-dir \
    torch torchaudio \
    --extra-index-url https://download.pytorch.org/whl/cu130

RUN pip install --no-cache-dir -r requirements.txt

# Build context is the repo root: the scoring lives in core/.
COPY core/ ./core/
COPY services/qa-verifier/app/ ./app/

# Whisper weights are downloaded from HuggingFace on first startup and cached
# in a named volume.
ENV HF_HOME=/root/.cache/huggingface

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8006"]
