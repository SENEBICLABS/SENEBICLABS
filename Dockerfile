# The API is a stateless container: it talks to Supabase, Resend and Anthropic over
# HTTPS and keeps nothing locally. It therefore runs anywhere that
# runs a container, which is the point — no host can hold the platform hostage.
#
# The previous image installed build-essential, gfortran and OpenBLAS, then
# numpy, pandas, anthropic and tqdm that nothing under app/ imports.
# requirements-runtime.txt is what the service actually needs, which keeps the
# image and its resident memory inside a free tier. requirements.txt stays as
# the development set; the tests use numpy.
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY app/ ./app/

# Hosts tell the process which port to listen on, and they do not agree on which.
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
