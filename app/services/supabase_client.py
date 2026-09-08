"""
Supabase client — singleton used across all API routes that need database access.

Falls back to None if SUPABASE_URL / SUPABASE_SERVICE_KEY are not set,
so the server still starts in development without a database configured.
Callers check `get_client()` and raise a clear 503 if it is None.
"""

import logging
from supabase import create_client, Client
from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client | None:
    global _client
    if _client is not None:
        return _client
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        logger.warning("Supabase not configured — SUPABASE_URL or SUPABASE_SERVICE_KEY missing")
        return None
    _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    _harden_transport(_client)
    return _client


def _harden_transport(client) -> None:
    """Use HTTP/1.1 with connection retries for PostgREST.

    supabase-py's client pools HTTP/2 connections. Supabase recycles those, and the sync
    h2 path raises RemoteProtocolError(ConnectionTerminated, NO_ERROR) when the server
    closes a pooled connection rather than transparently reopening one. In practice that
    surfaced as sporadic failures mid-sequence — and worst of all in audit writes, which
    are deliberately best-effort and so vanished into a log line, leaving silent holes in
    the provenance trail we tell clients is complete.

    HTTP/1.1 keep-alive handles a server-closed idle connection by reconnecting, and
    `retries` covers the connect itself. Best-effort: if the internals ever move, the
    client still works exactly as before, just with the original transport.
    """
    try:
        import httpx
        transport = httpx.HTTPTransport(retries=3)          # http2 defaults to False
        for api in (getattr(client, "postgrest", None), getattr(client, "storage", None)):
            session = getattr(api, "session", None)
            if session is not None:
                session._transport = transport
    except Exception as exc:                                 # pragma: no cover
        logger.warning("Could not harden Supabase transport (%s) — using the default.", exc)
