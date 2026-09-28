FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SEARCH_CONFIG=/data/search.toml

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . \
    && useradd --create-home --uid 1000 app \
    && mkdir /data \
    && chown app /data

USER app
WORKDIR /data
VOLUME ["/data"]

ENTRYPOINT ["wohnkompass-oss"]
CMD ["run"]
