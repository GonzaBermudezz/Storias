"""Ordered-response fake of the supabase client, shared by the service tests."""
from __future__ import annotations


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.filters, self.single = "select", None, [], False

    def select(self, *args, **kwargs): return self
    def eq(self, key, value): self.filters.append(("eq", key, value)); return self
    def gte(self, key, value): self.filters.append(("gte", key, value)); return self
    def order(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self
    def range(self, start, end): self.filters.append(("range", start, end)); return self
    def maybe_single(self): self.single = True; return self
    def update(self, payload): self.op, self.payload = "update", payload; return self

    def execute(self):
        self.db.calls.append((self.name, self.op, self.payload, self.filters))
        value = self.db.responses.pop(0)
        if self.single and value is None:
            return None
        return Result(value)


class Rpc:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        self.db.calls.append((self.name, "rpc", self.params, []))
        return Result(self.db.responses.pop(0))


class FakeDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def table(self, name): return Query(self, name)
    def rpc(self, name, params): return Rpc(self, name, params)
    def ops(self, op): return [call for call in self.calls if call[1] == op]
