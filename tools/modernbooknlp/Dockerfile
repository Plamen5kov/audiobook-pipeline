FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

# CPU wheels: this is a batch pre-pass, and the GPU box is busy synthesising.
RUN pip install --no-cache-dir torch==2.6.0 \
        --index-url https://download.pytorch.org/whl/cpu

WORKDIR /opt
RUN git clone --depth 1 https://github.com/gasmichel/ModernBookNLP_QA.git

# torch is already installed from the CPU index; installing it again from the
# requirements file would pull the CUDA build and bloat the image.
RUN grep -v '^torch==' /opt/ModernBookNLP_QA/requirements.txt > /tmp/req.txt \
    && pip install --no-cache-dir -r /tmp/req.txt \
    && python -m spacy download en_core_web_sm

RUN cd /opt/ModernBookNLP_QA/ModernBookNLP && pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/booknlp_models/hf

WORKDIR /work
ENTRYPOINT ["python"]
