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

    def in_(self, col, vals):
        vals = list(vals)
        self.filters.append(lambda r, c=col, v=vals: r.get(c) in v)
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


class FakeDB:
    def __init__(self, rows=None):
        self.rows = copy.deepcopy(rows or {})

    def table(self, name):
        return _Query(self, name)
