FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install CUDA-enabled torch first (CPU wheel is default on aarch64 PyPI)
RUN pip install --no-cache-dir \
    torch torchaudio \
    --extra-index-url https://download.pytorch.org/whl/cu130
RUN pip install --no-cache-dir -r requirements.txt

# coqui-tts uses isin_mps_friendly which was removed from transformers>=4.46.
# Replace the import with torch.isin directly (functionally identical on non-MPS).
RUN sed -i \
    's/from transformers\.pytorch_utils import isin_mps_friendly as isin/from torch import isin/' \
    /usr/local/lib/python3.12/site-packages/TTS/tts/layers/tortoise/autoregressive.py

# coqui-tts raises an ImportError if torchcodec is missing when torch>=2.9.
# torchcodec is not available on aarch64; XTTS v2 inference uses torchaudio instead.
RUN sed -i \
    's/raise ImportError(TORCHCODEC_IMPORT_ERROR)/pass  # torchcodec not needed for XTTS v2 inference/' \
    /usr/local/lib/python3.12/site-packages/TTS/__init__.py

COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
