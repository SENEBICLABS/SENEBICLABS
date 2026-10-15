# The API is a stateless container: it talks to Supabase, Resend, Label Studio and
# Anthropic over HTTPS and keeps nothing locally. It therefore runs anywhere that
# runs a container, which is the point — no host can hold the platform hostage.
#
# The previous image installed build-essential, gfortran and OpenBLAS for the
# genomics stack. That stack is gone; numpy and pandas ship wheels. Slim keeps the
# image small enough for a free tier to build and run comfortably.
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Hosts tell the process which port to listen on, and they do not agree on which.
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
