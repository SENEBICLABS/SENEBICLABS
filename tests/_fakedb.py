"""
An in-memory stand-in for the Supabase client, for the query shapes the app uses:
select / eq / in_ / order / limit / range, update / insert / delete, then execute().

Rows are plain dicts held per table. `update` applies to every row matching the filters,
as PostgREST does. Tests read `db.rows[table]` to see what the code wrote.
"""
import copy
import uuid


class _Res:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.window, self.lim = [], None, None
        self.op, self.payload, self.order_key = "select", None, None

    # ── building ──
    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def neq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) != v)
        return self

    def in_(self, col, vals):
        vals = list(vals)
        self.filters.append(lambda r, c=col, v=vals: r.get(c) in v)
        return self

    def is_(self, col, val):
        # PostgREST's IS: only the null test is used.
        assert val in ("null", None), "only is_(col, 'null') is supported"
        self.filters.append(lambda r, c=col: r.get(c) is None)
        return self

    def order(self, col, desc=False):
        self.order_key = (col, desc)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, s, e):
        self.window = (s, e)
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    # ── running ──
    def _match(self):
        return [r for r in self.db.rows.setdefault(self.table, [])
                if all(f(r) for f in self.filters)]

    def execute(self):
        if self.op == "insert":
            rows = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in rows:
                r = copy.deepcopy(r)
                r.setdefault("id", str(uuid.uuid4()))
                self.db.rows.setdefault(self.table, []).append(r)
                out.append(copy.deepcopy(r))
            return _Res(out)
        if self.op == "update":
            hit = self._match()
            for r in hit:
                r.update(copy.deepcopy(self.payload))
            return _Res(copy.deepcopy(hit))
        if self.op == "delete":
            hit = self._match()
            self.db.rows[self.table] = [r for r in self.db.rows[self.table] if r not in hit]
            return _Res(hit)
        rows = self._match()
        if self.order_key:
            col, desc = self.order_key
            rows = sorted(rows, key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self.window:
            rows = rows[self.window[0]: self.window[1] + 1]
        if self.lim is not None:
            rows = rows[: self.lim]
        return _Res(copy.deepcopy(rows), count=len(rows))


class _Rpc:
    def __init__(self, fn, params):
        self.fn, self.params = fn, params

    def execute(self):
        return _Res(self.fn(self.params))


class FakeDB:
    """`rpcs` maps a database function's name to a Python stand-in taking the params dict.
    The real functions are tested against Postgres in tests/sql; here they only need to
    return the shape the code reads. Calling one that is not given raises, as PostgREST
    does for a function that does not exist."""

    def __init__(self, rows=None, rpcs=None):
        self.rows = copy.deepcopy(rows or {})
        self.rpcs = dict(rpcs or {})
        self.rpc_calls: list[tuple[str, dict]] = []

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        self.rpc_calls.append((name, dict(params or {})))
        if name not in self.rpcs:
            raise RuntimeError(f"function {name} does not exist")
        return _Rpc(self.rpcs[name], dict(params or {}))
