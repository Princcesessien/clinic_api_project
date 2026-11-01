import sys
import threading
import uuid

import requests

BASE = sys.argv[1].rstrip("/")
KEY = sys.argv[2]


def call(method, path, headers=None, **kw):
    h = {"X-API-Key": KEY, **(headers or {})}
    return requests.request(method, BASE + path, headers=h, timeout=90, **kw)


def check(name, ok):
    print("PASS" if ok else "FAIL", "-", name)


def email():
    return f"{uuid.uuid4().hex[:10]}@example.com"


def mk(id, provider="Dr Ade", hour=9, year=2030):
    return {"id": id, "provider": provider,
            "start": f"{year}-01-01T{hour:02d}:00:00Z",
            "end": f"{year}-01-01T{hour:02d}:30:00Z"}


SLOTS = ([mk("s1"), mk("s2", hour=10), mk("s3", "Dr Bola", 11),
          mk("past", year=2020), mk("race", hour=12), mk("idem", hour=18)]
         + [mk(f"p{i}", hour=13 + i) for i in range(5)])

print("--- Level 1")
check("reset 200", call("POST", "/reset", json={"slots": SLOTS}).status_code == 200)
r = call("GET", "/slots")
starts = [s["start"] for s in r.json()["slots"]]
check("slots listed, earliest first", r.status_code == 200 and starts == sorted(starts))
bola = call("GET", "/slots", params={"provider": "Dr Bola"}).json()["slots"]
check("provider filter", len(bola) == 1 and bola[0]["provider"] == "Dr Bola")
r = call("POST", "/bookings", json={"slot_id": "s1", "name": "Ada Obi", "email": email()})
check("book -> 201 confirmed", r.status_code == 201 and r.json()["status"] == "confirmed")
b = r.json()
check("get booking", call("GET", f"/bookings/{b['id']}").json()["id"] == b["id"])
check("unknown booking 404", call("GET", "/bookings/nope").status_code == 404)
s1 = [s for s in call("GET", "/slots").json()["slots"] if s["id"] == "s1"][0]
check("booked slot unavailable", s1["available"] is False)

print("--- Level 2")
check("no key -> 401", requests.get(BASE + "/slots", timeout=90).status_code == 401)
check("wrong key -> 401", requests.get(BASE + "/slots", headers={"X-API-Key": "x"}, timeout=90).status_code == 401)
for label, body in [
    ("missing name", {"slot_id": "s2", "email": email()}),
    ("blank name", {"slot_id": "s2", "name": "  ", "email": email()}),
    ("bad email", {"slot_id": "s2", "name": "A", "email": "nope"}),
    ("missing slot_id", {"name": "A", "email": email()}),
]:
    check(f"400 {label}", call("POST", "/bookings", json=body).status_code == 400)
check("unknown slot -> 404", call("POST", "/bookings", json={"slot_id": "zzz", "name": "A", "email": email()}).status_code == 404)
check("past slot -> 400", call("POST", "/bookings", json={"slot_id": "past", "name": "A", "email": email()}).status_code == 400)
r = call("POST", "/bookings", json={"slot_id": "s2", "name": "  Ada  ", "email": "  MiXed@Example.COM "})
b2 = r.json()
check("trim name + lowercase email", r.status_code == 201 and b2["name"] == "Ada" and b2["email"] == "mixed@example.com")

print("--- Level 3")
results = []


def attempt():
    results.append(call("POST", "/bookings", json={"slot_id": "race", "name": "R", "email": email()}).status_code)


threads = [threading.Thread(target=attempt) for _ in range(20)]
[t.start() for t in threads]
[t.join() for t in threads]
check("20 simultaneous -> one 201, nineteen 409", results.count(201) == 1 and results.count(409) == 19)
r = call("DELETE", f"/bookings/{b2['id']}")
check("cancel -> 200 cancelled", r.status_code == 200 and r.json()["status"] == "cancelled")
check("cancel again -> 409", call("DELETE", f"/bookings/{b2['id']}").status_code == 409)
check("cancel unknown -> 404", call("DELETE", "/bookings/nope").status_code == 404)
check("rebook freed slot", call("POST", "/bookings", json={"slot_id": "s2", "name": "N", "email": email()}).status_code == 201)

print("--- Level 4")
em, k = email(), uuid.uuid4().hex
body = {"slot_id": "idem", "name": "Ida", "email": em}
r1 = call("POST", "/bookings", json=body, headers={"Idempotency-Key": k})
r2 = call("POST", "/bookings", json=body, headers={"Idempotency-Key": k})
check("same key + body -> same response", r1.status_code == 201 and r2.status_code == 201 and r1.json()["id"] == r2.json()["id"])
r3 = call("POST", "/bookings", json={**body, "name": "Other"}, headers={"Idempotency-Key": k})
check("same key, different body -> 422", r3.status_code == 422)
em, ids = email(), []
for i in range(5):
    ids.append(call("POST", "/bookings", json={"slot_id": f"p{i}", "name": "P", "email": em}).json()["id"])
seen, cursor, pages = [], None, 0
while True:
    params = {"email": em.upper(), "limit": 2}
    if cursor:
        params["cursor"] = cursor
    d = call("GET", "/bookings", params=params).json()
    seen += [x["id"] for x in d["bookings"]]
    pages += 1
    cursor = d["next_cursor"]
    if not cursor:
        break
check("pagination 2+2+1, oldest first, no repeats", pages == 3 and seen == ids)
for bad in ("0", "51", "abc"):
    check(f"limit={bad} -> 400", call("GET", "/bookings", params={"email": em, "limit": bad}).status_code == 400)

print("--- Level 5")
em = email()
codes = [call("POST", "/bookings", json={"slot_id": "nope", "name": "S", "email": em}).status_code for _ in range(6)]
check("5 allowed, 6th -> 429", codes[:5] == [404] * 5 and codes[5] == 429)
r = call("POST", "/bookings", json={"slot_id": "nope", "name": "S", "email": em})
check("Retry-After is whole seconds", r.status_code == 429 and r.headers.get("Retry-After", "").isdigit())
check("other email unaffected", call("POST", "/bookings", json={"slot_id": "nope", "name": "S", "email": email()}).status_code == 404)