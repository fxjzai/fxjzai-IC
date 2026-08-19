FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --gid 10001 ic && useradd --uid 10001 --gid ic --no-create-home ic

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/container-entrypoint.sh /usr/local/bin/ic-entrypoint
RUN python -m pip install --no-cache-dir .
RUN chmod 0755 /usr/local/bin/ic-entrypoint

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["ic-entrypoint"]
CMD ["uvicorn", "ic.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
