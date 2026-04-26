FROM python:3.12-slim

RUN pip install --no-cache-dir pytest ruff httpx pydantic python-dotenv && \
    apt-get update && apt-get install -y --no-install-recommends git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
