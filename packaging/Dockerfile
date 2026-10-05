# Web deployment of the cbapick4me GUI.
#   docker build -f packaging/Dockerfile -t cbapick4me .
#   docker run -p 8080:8080 --env-file .env cbapick4me
# Put it behind a TLS reverse proxy that forwards websockets (see README).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CBAPICK_HOST=0.0.0.0 \
    CBAPICK_PORT=8080 \
    CBAPICK_CACHE_DIR=/data/cache

RUN useradd --create-home --uid 10001 app && mkdir -p /data/cache && chown -R app /data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY cbapick4me ./cbapick4me
RUN pip install .

USER app
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"CBAPICK_PORT\"]}/healthz')"
CMD ["cbapick4me", "--serve"]
