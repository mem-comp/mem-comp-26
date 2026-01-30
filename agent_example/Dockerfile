FROM ubuntu:24.04
WORKDIR /root

RUN apt update && apt install -y python3 python3-pip

COPY src/requirements.txt src/
RUN pip install --break-system-packages --no-cache-dir -r src/requirements.txt
COPY src/ src/

WORKDIR /root/src

# workaround for https://github.com/BerriAI/litellm/issues/19852
ENV TIKTOKEN_CACHE_DIR=/usr/local/lib/python3.12/dist-packages/litellm/litellm_core_utils/tokenizers

# for quicker debugging
ENV MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT=0

# suppress v2.0 deprecation warning
ENV MSWEA_SILENT_STARTUP=1

ENTRYPOINT [ "python3", "-u", "main.py" ]