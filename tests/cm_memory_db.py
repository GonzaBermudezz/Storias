"""Stateful in-memory fake of the supabase client for the CM tests.

The loop does many reads/writes whose order depends on the model's answers, so an
ordered-response fake is too brittle: this one keeps real tables.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime

from postgrest.exceptions import APIError

_ids = itertools.count(1)


def as_moment(value):
    """Timestamps compare as datetimes (ISO strings with Z or +00:00), never as text."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value


@dataclass
class Result:
    data: object
    count: int | None = None


class MemoryQuery:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload = "select", None
        self.preds, self.single, self.want_count = [], False, False
        self.sort, self.max_rows = None, None

    def select(self, *args, **kwargs):
        self.want_count = kwargs.get("count") is not None
        return self

    def _add(self, fn): self.preds.append(fn); return self
    def eq(self, key, value): return self._add(lambda r: r.get(key) == value)
    def neq(self, key, value): return self._add(lambda r: r.get(key) != value)
    def is_(self, key, value): return self._add(lambda r: r.get(key) is None)
    def lt(self, key, value): return self._add(lambda r: r.get(key) is not None and as_moment(r[key]) < as_moment(value))
    def gte(self, key, value): return self._add(lambda r: r.get(key) is not None and as_moment(r[key]) >= as_moment(value))
    def in_(self, key, values): return self._add(lambda r: r.get(key) in list(values))

    def or_(self, expression):
        self.db.filters.append(expression)
        parts = []
        for part in expression.split(","):
            column, operator, value = part.split(".", 2)
            parts.append((column, operator, value))

        def matches(row):
            for column, operator, value in parts:
                current = row.get(column)
                if operator == "is" and current is None:
                    return True
                if operator == "lt" and current is not None and as_moment(current) < as_moment(value):
                    return True
            return False
        return self._add(matches)

    def order(self, column, desc=False): self.sort = (column, desc); return self
    def limit(self, count): self.max_rows = count; return self
    def range(self, start, end): return self
    def maybe_single(self): self.single = True; return self
    def insert(self, payload): self.op, self.payload = "insert", payload; return self
    def update(self, payload): self.op, self.payload = "update", payload; return self
    def delete(self): self.op = "delete"; return self

    def _matching(self):
        return [row for row in self.db.tables.setdefault(self.name, []) if all(p(row) for p in self.preds)]

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        self.db.log.append((self.name, self.op, self.payload))
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            created = []
            for item in items:
                row = {"id": f"{self.name}-{next(_ids)}", "created_at": self.db.now(), **item}
                for column, key in self.db.unique.get(self.name, []):
                    if any(all(other.get(c) == row.get(c) for c in column) for other in rows):
                        raise APIError({"message": f"duplicate key on {self.name}{column}", "code": "23505"})
                rows.append(row)
                created.append(dict(row))
            return Result(created)
        matched = self._matching()
        if self.op == "update":
            for row in matched:
                row.update(self.payload)
            return Result([dict(row) for row in matched])
        if self.op == "delete":
            for row in matched:
                rows.remove(row)
            return Result([dict(row) for row in matched])
        if self.sort:
            column, desc = self.sort
            matched = sorted(matched, key=lambda r: (r.get(column) is None, r.get(column)), reverse=desc)
        total = len(matched)
        if self.max_rows is not None:
            matched = matched[: self.max_rows]
        out = [dict(row) for row in matched]
        if self.single:
            return Result(out[0]) if out else None
        return Result(out, count=total if self.want_count else None)


class MemoryDB:
    def __init__(self, tables=None, now="2026-11-11T12:00:00+00:00"):
        self.tables = {name: [dict(r) for r in rows] for name, rows in (tables or {}).items()}
        self.log = []
        self.filters = []  # every or_() expression, to assert the exact string sent
        self._now = now
        self.unique = {"cm_messages": [(("conversation_id", "seq"), None)]}

    def now(self): return self._now
    def table(self, name): return MemoryQuery(self, name)
    def rows(self, name): return self.tables.setdefault(name, [])
    def writes(self, name=None):
        return [entry for entry in self.log if entry[1] != "select" and (name is None or entry[0] == name)]
