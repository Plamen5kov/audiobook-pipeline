# Training image, built on the same base as the serving container so the model
# code and CUDA stack are identical. Only the training-side extras are added.
FROM audiobook-pipeline-qwen3-tts

USER root

# peft supplies the LoRA layers; accelerate drives the loop. Both are absent
# from the serving image, which only ever runs inference.
RUN pip install --no-cache-dir peft accelerate

WORKDIR /train
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/hf \
    PYTHONPATH=/train

ENTRYPOINT ["python"]
