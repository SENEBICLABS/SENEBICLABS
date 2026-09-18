"""
Paged reads (services/paging.py).

The bug this exists to prevent is silent: PostgREST caps a response at max-rows and
returns a normal 200, so a truncated read reaches the client as a wrong number rather
than an error. A 1100-item project produced a scorecard computed on 1000 of them.

These tests use a fake query builder that enforces the cap the way PostgREST does,
so they fail if the paging loop ever stops early again.

Run: PYTHONPATH=. pytest tests/test_paging.py
"""
import pytest

from app.services.paging import TooManyRows, fetch_all


class FakeQuery:
    """Mimics PostgREST: never returns more than `cap` rows for one request."""

    def __init__(self, rows, cap, calls):
        self.rows, self.cap, self.calls = rows, cap, calls
        self.start = 0
        self.end = None

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        window = self.rows[self.start : self.end + 1]
        served = window[: self.cap]
        self.calls.append((self.start, self.end, len(served)))
        return type("R", (), {"data": served})()


def builder(n_rows, cap=1000):
    """(build, calls) for a table holding `n_rows`, capped at `cap` per request."""
    rows = [{"idx": i} for i in range(n_rows)]
    calls = []
    return (lambda: FakeQuery(rows, cap, calls)), calls


def test_reads_past_the_cap():
    # The exact shape of the original bug: 1100 rows behind a 1000-row cap.
    build, calls = builder(1100)
    got = fetch_all(build)
    assert len(got) == 1100
    assert [r["idx"] for r in got] == list(range(1100))
    assert len(calls) == 2


def test_a_small_table_is_one_request():
    build, calls = builder(5)
    assert len(fetch_all(build)) == 5
    assert len(calls) == 1


def test_empty_table():
    build, calls = builder(0)
    assert fetch_all(build) == []
    assert len(calls) == 1


def test_exactly_one_full_page_asks_again():
    """A full page is ambiguous: it could mean "more to come". Stopping there is how
    an off-by-one truncation hides, so the loop must ask once more and get nothing."""
    build, calls = builder(1000)
    assert len(fetch_all(build)) == 1000
    assert len(calls) == 2


def test_each_page_requests_the_next_window():
    build, calls = builder(2500)
    assert len(fetch_all(build)) == 2500
    assert [(s, e) for s, e, _ in calls] == [(0, 999), (1000, 1999), (2000, 2999)]


def test_refuses_rather_than_returning_a_partial_read():
    # Past the ceiling we raise. Returning the first rows would be the very failure
    # this module exists to stop, just at a larger size.
    build, _ = builder(60)
    with pytest.raises(TooManyRows):
        fetch_all(build, page=10, cap=50)


def test_no_rows_are_dropped_or_repeated_across_pages():
    build, _ = builder(3333, cap=100)
    got = fetch_all(build, page=100)
    assert [r["idx"] for r in got] == list(range(3333))
