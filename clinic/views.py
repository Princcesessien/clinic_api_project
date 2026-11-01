import base64
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Booking, BookingAttempt, IdempotencyRecord, Slot

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RATE_LIMIT = 5
RATE_WINDOW = 60  # seconds


def err(message, status):
    return JsonResponse({"error": message}, status=status)


def fmt(dt, ms=False):
    dt = dt.astimezone(dt_timezone.utc)
    if ms:
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(value):
    dt = parse_datetime(value)
    if dt is None:
        raise ValueError("bad datetime")
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def booking_json(b):
    return {
        "id": b.id,
        "slot_id": b.slot_id,
        "name": b.name,
        "email": b.email,
        "status": b.status,
        "created_at": fmt(b.created_at, ms=True),
    }


def advisory_lock(text):
    """Makes concurrent requests with the same text wait for each other."""
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [text])


# ---------- Level 1: reset + slots ----------

def reset(request):
    if request.method != "POST":
        return err("method not allowed", 405)
    try:
        data = json.loads(request.body or b"{}")
        objs = [
            Slot(
                id=str(s["id"]),
                provider=s["provider"],
                start=parse_dt(s["start"]),
                end=parse_dt(s["end"]),
            )
            for s in data["slots"]
        ]
    except (ValueError, KeyError, TypeError):
        return err("invalid slots", 400)
    try:
        with transaction.atomic():
            Booking.objects.all().delete()
            Slot.objects.all().delete()
            IdempotencyRecord.objects.all().delete()
            BookingAttempt.objects.all().delete()
            Slot.objects.bulk_create(objs)
    except IntegrityError:
        return err("duplicate slot ids", 400)
    return JsonResponse({"ok": True})


def slots(request):
    if request.method != "GET":
        return err("method not allowed", 405)
    qs = Slot.objects.all().order_by("start", "id")
    provider = request.GET.get("provider")
    if provider is not None:
        qs = qs.filter(provider=provider)
    booked = set(
        Booking.objects.filter(status="confirmed").values_list("slot_id", flat=True)
    )
    return JsonResponse({"slots": [
        {
            "id": s.id,
            "provider": s.provider,
            "start": fmt(s.start),
            "end": fmt(s.end),
            "available": s.id not in booked,
        }
        for s in qs
    ]})


# ---------- Bookings ----------

def bookings(request):
    if request.method == "POST":
        return create_booking(request)
    if request.method == "GET":
        return list_bookings(request)
    return err("method not allowed", 405)


def check_rate_limit(email):
    """Level 5. Returns None if allowed, else seconds to wait."""
    advisory_lock("rate:" + email)
    now = timezone.now()
    times = list(
        BookingAttempt.objects.filter(
            email=email, at__gt=now - timedelta(seconds=RATE_WINDOW)
        ).order_by("at").values_list("at", flat=True)
    )
    if len(times) >= RATE_LIMIT:
        wait = (times[0] + timedelta(seconds=RATE_WINDOW) - now).total_seconds()
        return max(1, math.ceil(wait))
    BookingAttempt.objects.create(email=email, at=now)
    return None


def process_booking(slot_id, name, email):
    """Levels 1-3. Returns (status_code, payload)."""
    if not slot_id or not name or not email:
        return 400, {"error": "slot_id, name and email are required"}
    if not EMAIL_RE.match(email):
        return 400, {"error": "invalid email"}
    slot = Slot.objects.filter(id=slot_id).first()
    if slot is None:
        return 404, {"error": "slot not found"}
    if slot.start <= timezone.now():
        return 400, {"error": "slot has already started"}
    try:
        with transaction.atomic():  # savepoint, so a clash doesn't break the outer transaction
            booking = Booking.objects.create(slot=slot, name=name, email=email)
    except IntegrityError:
        return 409, {"error": "slot already booked"}
    return 201, booking_json(booking)


def create_booking(request):
    raw = request.body
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    fields = parsed if isinstance(parsed, dict) else {}

    def text(value):
        return value.strip() if isinstance(value, str) else ""

    slot_id = text(fields.get("slot_id"))
    name = text(fields.get("name"))
    email = text(fields.get("email")).lower()

    canonical = (
        json.dumps(parsed, sort_keys=True)
        if parsed is not None
        else raw.decode("utf-8", "ignore")
    )
    body_hash = hashlib.sha256(canonical.encode()).hexdigest()
    idem_key = request.headers.get("Idempotency-Key")

    with transaction.atomic():
        # Level 4: a retried request gets the saved response back
        if idem_key:
            advisory_lock("idem:" + idem_key)
            rec = IdempotencyRecord.objects.filter(key=idem_key).first()
            if rec:
                if rec.body_hash != body_hash:
                    return err("Idempotency-Key reused with a different body", 422)
                return JsonResponse(rec.response, status=rec.status_code)

        # Level 5: failed attempts count too
        if email:
            wait = check_rate_limit(email)
            if wait is not None:
                resp = err("too many attempts, try again later", 429)
                resp["Retry-After"] = str(wait)
                return resp

        status, payload = process_booking(slot_id, name, email)
        if idem_key:
            IdempotencyRecord.objects.create(
                key=idem_key, body_hash=body_hash,
                status_code=status, response=payload,
            )
        return JsonResponse(payload, status=status)


def list_bookings(request):
    email = request.GET.get("email", "").strip().lower()
    if not email:
        return err("email is required", 400)
    try:
        limit = int(request.GET.get("limit", "10"))
    except ValueError:
        return err("limit must be an integer from 1 to 50", 400)
    if not 1 <= limit <= 50:
        return err("limit must be an integer from 1 to 50", 400)

    qs = Booking.objects.filter(email__iexact=email).order_by("created_at", "id")
    cursor = request.GET.get("cursor")
    if cursor:
        try:
            ts, last_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return err("invalid cursor", 400)
        qs = qs.filter(Q(created_at__gt=ts) | Q(created_at=ts, id__gt=last_id))

    rows = list(qs[: limit + 1])
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = base64.urlsafe_b64encode(
            f"{last.created_at.isoformat()}|{last.id}".encode()
        ).decode()
    return JsonResponse({
        "bookings": [booking_json(b) for b in page],
        "next_cursor": next_cursor,
    })


def booking_detail(request, booking_id):
    if request.method == "GET":
        b = Booking.objects.filter(id=booking_id).first()
        if b is None:
            return err("booking not found", 404)
        return JsonResponse(booking_json(b))

    if request.method == "DELETE":  # Level 3: cancel
        with transaction.atomic():
            b = Booking.objects.select_for_update().filter(id=booking_id).first()
            if b is None:
                return err("booking not found", 404)
            if b.status == "cancelled":
                return err("booking already cancelled", 409)
            b.status = "cancelled"
            b.save(update_fields=["status"])
        return JsonResponse(booking_json(b))

    return err("method not allowed", 405)