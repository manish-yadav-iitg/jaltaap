"""One place for every AWS service JalTaap uses.

Each helper tries the real AWS service first and quietly falls back to a
local/free path when there are no AWS credentials, so `python server.py`
always works on a laptop. `status()` tells the UI which services are live.

Services (env var that switches each one on, besides AWS credentials):
  Amazon Bedrock        BEDROCK_MODEL_ID            copilot answers, bulletins, alerts in Indian languages
  Amazon S3             JALTAAP_S3_BUCKET           report photos + daily bulletin archive
  Amazon DynamoDB       JALTAAP_DDB_TABLE           tanker requests, sensor readings, audit trail
  Amazon SNS            JALTAAP_SNS_TOPIC_ARN       SMS / topic alerts, tanker OTPs
  Amazon Location       JALTAAP_PLACE_INDEX,        geocoding and tanker road routes
                        JALTAAP_ROUTE_CALCULATOR
  AWS Lambda + API Gateway (deploy/)                deployed API and a 6-hourly scheduled check
Sentinel-2 imagery is read from the public AWS Open Data bucket on S3 (no key needed).
"""
from __future__ import annotations

import base64
import os
import threading
import time
from functools import lru_cache

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
_lock = threading.Lock()
_errors: dict[str, str] = {}
_calls: dict[str, int] = {}


def _off() -> bool:
    return os.environ.get("JALTAAP_AWS", "auto").lower() in ("off", "0", "false", "no")


@lru_cache(maxsize=1)
def _session():
    import boto3
    return boto3.session.Session(region_name=REGION)


@lru_cache(maxsize=1)
def has_credentials() -> bool:
    if _off():
        return False
    try:
        return _session().get_credentials() is not None
    except Exception:
        return False


@lru_cache(maxsize=32)
def client(name: str, region: str | None = None):
    from botocore.config import Config
    return _session().client(name, region_name=region or REGION,
                             config=Config(connect_timeout=4, read_timeout=40, retries={"max_attempts": 2}))


def _note(service: str, err: Exception | None = None):
    with _lock:
        if err is None:
            _calls[service] = _calls.get(service, 0) + 1
            _errors.pop(service, None)
        else:
            _errors[service] = f"{err.__class__.__name__}: {str(err)[:160]}"


def _try(service: str, fn, *a, **k):
    """Run an AWS call; return None (and remember why) if it fails."""
    if not has_credentials():
        return None
    try:
        out = fn(*a, **k)
        _note(service)
        return out
    except Exception as e:  # missing permission, service not in region, etc.
        _note(service, e)
        return None


# ---------------------------------------------------------------- Bedrock
BEDROCK_MODEL = os.environ.get("BEDROCK_MODEL_ID", "apac.amazon.nova-lite-v1:0")


def bedrock_text(prompt: str, system: str = "", max_tokens: int = 600, temperature: float = 0.2) -> str | None:
    """Plain text answer from Amazon Bedrock (Converse API works for Nova, Claude, Llama...)."""
    def call():
        r = client("bedrock-runtime").converse(
            modelId=BEDROCK_MODEL,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            system=[{"text": system}] if system else [],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        return "".join(c.get("text", "") for c in r["output"]["message"]["content"]).strip()
    return _try("bedrock", call) or None


# ---------------------------------------------------------------- translation (Bedrock)
LANG_NAMES = {"hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam", "mr": "Marathi",
              "gu": "Gujarati", "kn": "Kannada", "or": "Odia", "pa": "Punjabi", "ur": "Urdu", "as": "Assamese"}


def translate(text: str, target: str, source: str = "en") -> str | None:
    """Translate an alert with Amazon Bedrock. None when Bedrock is not reachable."""
    if target == source or target not in LANG_NAMES:
        return None
    return bedrock_text(text[:6000], system=f"Translate this public-safety alert into {LANG_NAMES[target]}. "
                        "Keep numbers, dates, place names and emoji exactly. Output only the translation.",
                        max_tokens=900, temperature=0)


# ---------------------------------------------------------------- S3
S3_BUCKET = os.environ.get("JALTAAP_S3_BUCKET", "")
LOCAL_FILES = os.environ.get("JALTAAP_FILES") or os.path.join(os.path.dirname(__file__), "..", "data", "files")


def put_file(key: str, body: bytes, content_type: str = "application/octet-stream") -> dict:
    """Save a file to S3 (with a 7-day link) or to data/files/ on this laptop."""
    if S3_BUCKET:
        def call():
            c = client("s3")
            c.put_object(Bucket=S3_BUCKET, Key=key, Body=body, ContentType=content_type)
            url = c.generate_presigned_url("get_object", Params={"Bucket": S3_BUCKET, "Key": key}, ExpiresIn=7 * 86400)
            return {"where": "s3", "key": key, "url": url}
        r = _try("s3", call)
        if r:
            return r
    path = os.path.join(LOCAL_FILES, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(body)
    return {"where": "local", "key": key, "url": "/files/" + key}


# ---------------------------------------------------------------- SNS
SNS_TOPIC = os.environ.get("JALTAAP_SNS_TOPIC_ARN", "")
_outbox: list[dict] = []


def send_sms(text: str, phone: str | None = None, subject: str = "JalTaap") -> dict:
    """SMS to one phone (E.164, +91...) or a broadcast to the SNS topic. Logged locally otherwise."""
    def call():
        c = client("sns")
        if phone:
            r = c.publish(PhoneNumber=phone, Message=text[:1500],
                          MessageAttributes={"AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}})
        elif SNS_TOPIC:
            r = c.publish(TopicArn=SNS_TOPIC, Message=text, Subject=subject[:99])
        else:
            return None
        return {"sent": True, "via": "Amazon SNS", "id": r.get("MessageId")}
    r = _try("sns", call)
    if r:
        return r
    item = {"ts": time.time(), "to": phone or "topic", "text": text, "sent": False, "via": "local outbox"}
    _outbox.append(item)
    del _outbox[:-200]
    return item


def outbox() -> list[dict]:
    return list(reversed(_outbox[-50:]))


# ---------------------------------------------------------------- Location Service
PLACE_INDEX = os.environ.get("JALTAAP_PLACE_INDEX", "")
ROUTE_CALC = os.environ.get("JALTAAP_ROUTE_CALCULATOR", "")
MAPS_KEY = os.environ.get("JALTAAP_MAPS_API_KEY", "")  # Amazon Location API key allowed to call geo-maps:GetTile
MAPS_REGION = os.environ.get("JALTAAP_MAPS_REGION", REGION)


def map_config() -> dict:
    """Tile URLs for the browser. Amazon Location satellite tiles when a Maps API key is set."""
    if not MAPS_KEY or _off():
        return {"satellite": None}
    return {"satellite": f"https://maps.geo.{MAPS_REGION}.amazonaws.com/v2/tiles/raster.satellite/{{z}}/{{x}}/{{y}}?key={MAPS_KEY}",
            "attribution": "© AWS, Maxar, Esri (Amazon Location Service)"}


def location_search(text: str, limit: int = 8) -> list[dict] | None:
    if not PLACE_INDEX:
        return None

    def call():
        r = client("location").search_place_index_for_text(IndexName=PLACE_INDEX, Text=text, MaxResults=limit,
                                                           FilterCountries=["IND"])
        out = []
        for x in r["Results"]:
            p = x["Place"]
            lon, lat = p["Geometry"]["Point"]
            out.append({"name": p.get("Label", "").split(",")[0], "district": p.get("SubRegion", ""),
                        "state": p.get("Region", ""), "lat": lat, "lon": lon})
        return out
    return _try("location", call)


def location_route(points: list[tuple[float, float]]) -> dict | None:
    """Road route through (lat, lon) points with Amazon Location. Returns km, minutes, line."""
    if not ROUTE_CALC or len(points) < 2:
        return None

    def call():
        r = client("location").calculate_route(
            CalculatorName=ROUTE_CALC, DeparturePosition=[points[0][1], points[0][0]],
            DestinationPosition=[points[-1][1], points[-1][0]],
            WaypointPositions=[[p[1], p[0]] for p in points[1:-1]][:23],
            TravelMode="Truck", IncludeLegGeometry=True)
        line = [[y, x] for leg in r["Legs"] for x, y in leg.get("Geometry", {}).get("LineString", [])]
        s = r["Summary"]
        return {"km": round(s["Distance"], 1), "minutes": round(s["DurationSeconds"] / 60), "line": line,
                "by": "Amazon Location Service"}
    return _try("location", call)


# ---------------------------------------------------------------- status
SERVICES = [
    ("bedrock", "Amazon Bedrock", "Copilot answers, district bulletins, Strands agent alerts, alerts in 12 Indian languages",
     lambda: True, "Built-in rules and English/Hindi templates"),
    ("sns", "Amazon SNS", "SMS alerts, tanker delivery codes, groundwater category changes",
     lambda: True, "Local outbox"),
    ("location", "Amazon Location Service", "Place search, tanker road routes, satellite base map",
     lambda: bool(PLACE_INDEX or ROUTE_CALC or MAPS_KEY), "Open-Meteo search, straight-line ETA, OpenStreetMap tiles"),
    ("s3", "Amazon S3", "Report photos and bulletin archive; Sentinel-2 scenes from AWS Open Data", lambda: bool(S3_BUCKET),
     "data/files on this laptop"),
    ("dynamodb", "Amazon DynamoDB", "Tanker requests, sensor readings, alerts and audit trail",
     lambda: bool(os.environ.get("JALTAAP_DDB_TABLE")), "SQLite (data/jaltaap.db)"),
    ("lambda", "AWS Lambda + API Gateway", "Deployed API and a 6-hourly check of river cities (deploy/)",
     # Vercel also sets AWS_LAMBDA_FUNCTION_NAME, so don't count that as our own Lambda
     lambda: bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME")) and not os.environ.get("VERCEL"),
     "Running on Vercel" if os.environ.get("VERCEL") else "Running locally with uvicorn"),
]


def status() -> dict:
    creds = has_credentials()
    rows = []
    for key, name, use, configured, fallback in SERVICES:
        if key == "lambda":
            state = "live" if configured() else "local"
        elif not creds or not configured():
            state = "fallback"
        else:
            state = "error" if key in _errors else "live"
        rows.append({"key": key, "name": name, "use": use, "state": state, "fallback": fallback,
                     "calls": _calls.get(key, 0), "error": _errors.get(key)})
    return {"credentials": creds, "region": REGION, "model": BEDROCK_MODEL, "services": rows,
            "live": sum(r["state"] == "live" for r in rows), "total": len(rows)}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
