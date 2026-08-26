FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/
RUN chmod +x /app/entrypoint.sh

EXPOSE 8090
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
