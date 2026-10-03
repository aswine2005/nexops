"""Steady synthetic customer traffic for the Payment API so production metrics have real requests behind them."""
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request

TARGET = os.getenv("TARGET_URL", "http://payment-api:8000").rstrip("/")
RPS = float(os.getenv("REQUESTS_PER_SECOND", "8"))
WORKERS = int(os.getenv("WORKERS", "4"))
created: list[str] = []
lock = threading.Lock()


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{TARGET}{path}", data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return 0, None


def worker() -> None:
    interval = WORKERS / max(RPS, 0.1)
    while True:
        started = time.time()
        roll = random.random()
        if roll < 0.55 or not created:
            status, payload = call("POST", "/payments", {"amount_cents": random.randint(100, 50_000), "currency": random.choice(["USD", "EUR", "INR"])})
            if status == 201 and payload:
                with lock:
                    created.append(payload["id"])
                    del created[:-500]
        elif roll < 0.97:
            with lock:
                payment_id = random.choice(created)
            call("GET", f"/payments/{payment_id}")
        else:
            call("GET", "/payments/00000000-0000-0000-0000-000000000000")
        time.sleep(max(0.0, interval - (time.time() - started)))


if __name__ == "__main__":
    print(f"Sending ~{RPS} req/s to {TARGET} with {WORKERS} workers", flush=True)
    for _ in range(WORKERS):
        threading.Thread(target=worker, daemon=True).start()
    while True:
        time.sleep(60)
