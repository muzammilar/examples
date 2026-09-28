"""Write seeded random data into ./data: events.csv, users.json (NDJSON), countries.csv.

Stdlib only; the same seed always produces byte-identical files.
Usage: python generate.py [out_dir] [n_events]
"""

import csv
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
N_EVENTS = int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000
N_USERS = 10_000
rng = random.Random(42)

COUNTRIES = [  # code, name, region, weight
    ("US", "United States", "Americas", 30), ("DE", "Germany", "Europe", 12),
    ("GB", "United Kingdom", "Europe", 10), ("IN", "India", "Asia", 10),
    ("BR", "Brazil", "Americas", 8), ("JP", "Japan", "Asia", 8),
    ("FR", "France", "Europe", 7), ("CA", "Canada", "Americas", 6),
    ("AU", "Australia", "Oceania", 5), ("PK", "Pakistan", "Asia", 4),
]
PLANS = (["free"] * 6) + (["pro"] * 3) + ["enterprise"]
TAGS = ["beta", "mobile", "newsletter", "power_user", "referral", "churn_risk", "vip"]
EVENTS = ["page_view"] * 60 + ["click"] * 25 + ["signup"] * 5 + ["purchase"] * 8 + ["refund"] * 2

OUT.mkdir(parents=True, exist_ok=True)

with open(OUT / "countries.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["code", "name", "region"])
    w.writerows(c[:3] for c in COUNTRIES)

codes = [c[0] for c in COUNTRIES]
weights = [c[3] for c in COUNTRIES]
home = {}  # user_id -> country, so events mostly come from the user's country
with open(OUT / "users.json", "w") as f:
    for uid in range(1, N_USERS + 1):
        home[uid] = rng.choices(codes, weights)[0]
        user = {
            "user_id": uid,
            "name": f"user_{uid:05d}",
            "signup_date": (date(2024, 1, 1) + timedelta(days=rng.randrange(730))).isoformat(),
            "plan": rng.choice(PLANS),
            "country": home[uid],
            "tags": rng.sample(TAGS, rng.randint(0, 3)),
            "prefs": {"theme": rng.choice(["dark", "light"]), "emails": rng.random() < 0.4},
        }
        f.write(json.dumps(user) + "\n")

start = datetime(2026, 1, 1)
with open(OUT / "events.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["ts", "user_id", "event_type", "amount", "country"])
    for _ in range(N_EVENTS):
        uid = rng.randint(1, N_USERS)
        et = rng.choice(EVENTS)
        amount = f"{rng.lognormvariate(3, 1):.2f}" if et in ("purchase", "refund") else ""
        country = home[uid] if rng.random() < 0.95 else rng.choice(codes)
        ts = start + timedelta(seconds=rng.randrange(180 * 86400))
        w.writerow([ts.isoformat(sep=" "), uid, et, amount, country])

for p in sorted(OUT.iterdir()):
    if p.is_file():
        print(f"{p.name:15} {p.stat().st_size / 1e6:6.1f} MB")
