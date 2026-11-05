# Kavrelle on a free Hugging Face Space (CPU basic: 2 vCPU, 16 GB RAM).
# One container runs the support model (Ollama + llama3.2:3b) and the website.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates zstd \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://ollama.com/install.sh | sh

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Spaces run as user 1000; the model is baked into the image so a restart is quick.
RUN useradd -m -u 1000 user && chown -R user /app
USER user
ENV HOME=/home/user \
    OLLAMA_MODELS=/home/user/.ollama/models \
    OLLAMA_HOST=127.0.0.1:11434 \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1
RUN sh -c 'ollama serve >/tmp/ollama.log 2>&1 & pid=$!; \
    for i in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done; \
    ollama pull llama3.2:3b; kill $pid'

COPY --chown=user start.sh /app/start.sh
EXPOSE 7860
CMD ["sh", "/app/start.sh"]
