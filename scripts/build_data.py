"""
Scrape + decrypt + aggregate pipeline for the NEET 2026 Predictor datasets.

The MBBS Council tools site is a React SPA that loads AES-ECB-encrypted, gzipped
JSON datasets from its CDN. This script reproduces how the browser bundle
(`/tools/static/js/main.*.js`) decodes them, then aggregates the raw cutoff
records into the compact files under `data/`.

Decryption parameters (extracted from the JS bundle):
    cipher : AES-256-ECB, PKCS7 padding
    key    : "fykNEuUfyX1ykX1AzafyNGEuUykX1kX1"  (32 ASCII bytes)
    then   : gunzip -> UTF-8 -> JSON   (some files are AES-only, no gzip layer)

Requires:  pip install pycryptodome   (only to re-run the scrape; the app itself
needs nothing extra — the decoded JSON already ships in data/.)

Usage:  python scripts/build_data.py
"""

from __future__ import annotations

import gzip
import json
import urllib.request
from pathlib import Path

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
except ImportError:  # pragma: no cover
    raise SystemExit("Install pycryptodome to run the scrape: pip install pycryptodome")

CDN = "https://mbbscouncilcdn.s3.ap-south-1.amazonaws.com/data"
VERSION = "v2025.02-gz"
KEY = b"fykNEuUfyX1ykX1AzafyNGEuUykX1kX1"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def fetch_dataset(name: str) -> list[dict]:
    """Download `<name>.js`, AES-decrypt, optionally gunzip, and parse JSON."""
    url = f"{CDN}/{name}.js?v={VERSION}"
    with urllib.request.urlopen(url) as resp:
        blob = resp.read()
    plain = unpad(AES.new(KEY, AES.MODE_ECB).decrypt(blob), AES.block_size)
    if plain[:2] == b"\x1f\x8b":  # gzip magic
        plain = gzip.decompress(plain)
    return json.loads(plain.decode("utf-8"))


def build() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    # 1. Rank <-> score table (keep the two most recent NEET-UG cycles).
    rank_score = fetch_dataset("rank_score_tools")
    rank_score = sorted(
        (r for r in rank_score if r["year"] in (2024, 2025)),
        key=lambda r: (r["year"], r["score"]),
    )
    _dump("rank_score.json", rank_score)

    # 2. Historical closing ranks -> aggregate to one row per
    #    (college, state, type, quota, category, year) using the final-round
    #    (loosest) closing rank.
    raw = fetch_dataset("cut_tools_all")
    agg: dict[tuple, dict] = {}
    for r in raw:
        air = r.get("air")
        if not isinstance(air, int) or air <= 0:
            continue
        key = (r["col"], r["st"], r["type"], r["quo"], r["cat"], int(r["year"]))
        sco = r["sco"] if isinstance(r.get("sco"), int) else None
        sat = r["sat"] if isinstance(r.get("sat"), int) else 0
        cur = agg.get(key)
        if cur is None:
            agg[key] = {"closing": air, "seats": sat, "score": sco}
        else:
            cur["closing"] = max(cur["closing"], air)
            cur["seats"] = max(cur["seats"], sat)
            if sco is not None:
                cur["score"] = min(cur["score"], sco) if cur["score"] else sco
    cutoffs = [
        {"col": c, "st": s, "type": t, "quo": q, "cat": cat, "year": y,
         "closing": v["closing"], "seats": v["seats"], "score": v["score"]}
        for (c, s, t, q, cat, y), v in agg.items()
    ]
    _dump("cutoffs.json", cutoffs)

    # 3. College master data (display fields only).
    master = fetch_dataset("college_mbbs_data")
    colleges = [
        {"name": c.get("name") or c.get("affl"), "st": c.get("st"), "dt": c.get("dt"),
         "type": c.get("ctype"), "seats": c.get("seats"), "estd": c.get("estd"),
         "gfees": c.get("gfees"), "mfees": c.get("mfees"), "nfees": c.get("nfees"),
         "url": c.get("ourl")}
        for c in master
    ]
    _dump("colleges.json", colleges)


def _dump(name: str, obj: object) -> None:
    path = DATA_DIR / name
    path.write_text(json.dumps(obj, separators=(",", ":")))
    print(f"wrote {path}  ({len(obj):,} records, {path.stat().st_size/1e6:.2f} MB)")


if __name__ == "__main__":
    build()
