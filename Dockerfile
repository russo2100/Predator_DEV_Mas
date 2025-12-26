# -------- builder: compilers + build deps (НЕ попадает в финальный образ)
FROM python:3.11.9-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /build/requirements.txt

# Собираем wheels для всех зависимостей
RUN pip wheel --no-cache-dir --wheel-dir /build/wheels -r /build/requirements.txt


# -------- runtime: только рантайм + wheels + код
FROM python:3.11.9-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY --from=builder /build/wheels /wheels
COPY --from=builder /build/requirements.txt /app/requirements.txt

# Ставим зависимости из wheels (без компиляторов) и чистим wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r /app/requirements.txt \
    && rm -rf /wheels \
    && find /usr/local/lib/python3.11/site-packages -type d -name "tests" -prune -exec rm -rf {} + \
    && find /usr/local/lib/python3.11/site-packages -type d -name "__pycache__" -prune -exec rm -rf {} +

# ВАЖНО: копируем код в самом конце, чтобы лучше работал кэш
COPY . /app

CMD ["python", "-m", "src.main"]
