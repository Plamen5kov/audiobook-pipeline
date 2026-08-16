FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# CPU wheels: this is a batch pre-pass over text, and the GPU box is busy
# generating audio.
# transformers is pinned below 4.31: BookNLP's published checkpoints contain a
# `bert.embeddings.position_ids` buffer that later versions stopped registering,
# so loading them raises "Unexpected key(s) in state_dict".
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir booknlp spacy \
        "transformers==4.30.2" "tokenizers<0.14" "huggingface_hub<0.20" \
    && python -m spacy download en_core_web_sm

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/hf \
    BOOKNLP_HOME=/cache/booknlp

WORKDIR /work
ENTRYPOINT ["python"]
