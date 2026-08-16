FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# CPU wheels: alignment is batch work and runs on the workstation, not the GPU box.
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir soundfile

ENV PYTHONUNBUFFERED=1 \
    TORCH_HOME=/cache/torch \
    OMP_NUM_THREADS=8

WORKDIR /app
ENTRYPOINT ["python", "-m", "builder"]
