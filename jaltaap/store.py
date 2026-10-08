"""Tiny record store: Amazon DynamoDB when configured, SQLite otherwise.

One DynamoDB table (JALTAAP_DDB_TABLE) holds every collection:
  pk = collection name ("tanker_request", "gw_reading", "audit", ...)
  sk = record id
The SQLite fallback mirrors that layout so the code above never cares.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from decimal import Decimal

from . import aws

DB = os.environ.get("JALTAAP_DB", os.path.join(os.path.dirname(__file__), "..", "data", "jaltaap.db"))
TABLE = os.environ.get("JALTAAP_DDB_TABLE", "")


def _sql():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS records(pk TEXT, sk TEXT, ts REAL, body TEXT, PRIMARY KEY(pk, sk))")
    return con


def _ddb():
    return aws._session().resource("dynamodb").Table(TABLE)


def backend() -> str:
    return "dynamodb" if TABLE and aws.has_credentials() else "sqlite"


def put(collection: str, item: dict, rid: str | None = None) -> dict:
    rid = rid or item.get("id") or uuid.uuid4().hex[:10]
    item = {**item, "id": rid, "ts": item.get("ts") or time.time()}
    if backend() == "dynamodb":
        ok = aws._try("dynamodb", lambda: _ddb().put_item(
            Item=json.loads(json.dumps({"pk": collection, "sk": rid, **item}, default=str), parse_float=Decimal)))
        if ok is not None:
            return item
    with _sql() as con:
        con.execute("INSERT OR REPLACE INTO records(pk, sk, ts, body) VALUES(?,?,?,?)",
                    (collection, rid, item["ts"], json.dumps(item, default=str)))
    return item


def get(collection: str, rid: str) -> dict | None:
    if backend() == "dynamodb":
        r = aws._try("dynamodb", lambda: _ddb().get_item(Key={"pk": collection, "sk": rid}))
        if r is not None:
            it = r.get("Item")
            return _plain(it) if it else None
    with _sql() as con:
        row = con.execute("SELECT body FROM records WHERE pk=? AND sk=?", (collection, rid)).fetchone()
    return json.loads(row[0]) if row else None


def items(collection: str, since_hours: float | None = None, limit: int = 500) -> list[dict]:
    since = time.time() - since_hours * 3600 if since_hours else 0
    if backend() == "dynamodb":
        from boto3.dynamodb.conditions import Key
        r = aws._try("dynamodb", lambda: _ddb().query(KeyConditionExpression=Key("pk").eq(collection), Limit=limit))
        if r is not None:
            rows = [_plain(x) for x in r.get("Items", [])]
            return sorted([x for x in rows if x.get("ts", 0) >= since], key=lambda x: x.get("ts", 0))
    with _sql() as con:
        rows = con.execute("SELECT body FROM records WHERE pk=? AND ts>=? ORDER BY ts DESC LIMIT ?",
                           (collection, since, limit)).fetchall()
    return [json.loads(r[0]) for r in reversed(rows)]


def delete(collection: str, rid: str):
    if backend() == "dynamodb":
        aws._try("dynamodb", lambda: _ddb().delete_item(Key={"pk": collection, "sk": rid}))
    with _sql() as con:
        con.execute("DELETE FROM records WHERE pk=? AND sk=?", (collection, rid))


def audit(event: str, **detail):
    """Append-only trail (who/what/when) for drains, leaks, tankers and alerts."""
    return put("audit", {"event": event, **detail})


def _plain(x):
    if isinstance(x, list):
        return [_plain(v) for v in x]
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items() if k not in ("pk", "sk")}
    if isinstance(x, Decimal):
        return int(x) if x == int(x) else float(x)
    return x
