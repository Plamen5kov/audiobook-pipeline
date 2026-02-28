FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    build-essential \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install CUDA-enabled torch first. PyPI default aarch64 wheel is CPU-only.
# cu130 matches CUDA 13.0 on the DGX Spark GB10.
RUN pip install --no-cache-dir \
    torch torchaudio \
    --extra-index-url https://download.pytorch.org/whl/cu130

# flash-attn has no aarch64 wheel; qwen-tts falls back to standard attention automatically.
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Model (~3-4GB) is downloaded from HuggingFace on first startup.
# A named Docker volume is mounted here to cache it across restarts.
ENV HF_HOME=/root/.cache/huggingface

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8007"]
