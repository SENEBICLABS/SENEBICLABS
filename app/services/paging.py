"""
Reading every row a query matches, rather than the first page of them.

PostgREST caps a single response at its configured max-rows (1000 by default on
Supabase) and says nothing about having done so: a truncated read is a normal 200
with a short body. That is worse than an error, because it surfaces as a wrong
number rather than a failure. A project of 1100 items produced a scorecard computed
on 1000 of them, with no warning anywhere.

Raising the cap in the dashboard only moves the cliff. This pages explicitly, so
correctness stops depending on a setting we do not control from here.

    rows = fetch_all(lambda: db.table("project_items")
                     .select("idx,content,label,status")
                     .eq("project_id", pid).order("idx"))

`build` returns a FRESH query each call: a PostgREST builder carries its own range
once set, so reusing one would request the same window every time and loop forever
on the first page.
"""
import logging

logger = logging.getLogger(__name__)

# One request per page. Matches Supabase's default cap, so the first page is a
# single round trip for the common case of a small project.
PAGE = 1000

# Refuse rather than return a partial view. Far above any real project — the ingest
# cap is 5000 per call and the largest sample we take is 10000 — so reaching this
# means something is wrong, and a silent half-answer would be the worst outcome.
MAX_ROWS = 200_000


class TooManyRows(RuntimeError):
    """A query matched more rows than we will assemble in memory."""


def fetch_all(build, *, page: int = PAGE, cap: int = MAX_ROWS) -> list[dict]:
    """Every row the query matches, read a page at a time.

    A short page means the rows ran out, which is the only reliable end-of-data
    signal PostgREST gives: an exactly-full page is ambiguous, so we always ask
    again after one.
    """
    out: list[dict] = []
    for start in range(0, cap, page):
        batch = (build().range(start, start + page - 1).execute()).data or []
        out.extend(batch)
        if len(batch) < page:
            return out
    logger.error("fetch_all hit the %d-row ceiling; refusing to return a partial read", cap)
    raise TooManyRows(
        f"Query matched more than {cap} rows. Narrow it, or page it explicitly — "
        "returning the first rows would deliver a wrong answer that looks right."
    )
