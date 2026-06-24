"""
NEET 2026 Predictor — FastAPI backend.

Two tools, both driven by real data scraped from the MBBS Council tools suite
(https://mbbscouncil.com/tools/neet-rank-predictor-2026):

1. Rank Predictor   — NEET marks (out of 720)  -> expected All India Rank.
                      Uses the official score->rank table (2025 NEET-UG).

2. College Predictor — your rank + category + quota (+ state) -> list of MBBS
                      colleges you can realistically get, each with an
                      admission probability. Uses 51k+ historical closing-rank
                      records (2022-2025).

Run:  uvicorn main:app --reload   then open http://127.0.0.1:8000
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

def _find_data_dir() -> Path:
    """Locate the data/ dir whether run locally or bundled on Vercel."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "data", here.parent / "data", Path.cwd() / "data"):
        if candidate.is_dir():
            return candidate
    return here / "data"


DATA_DIR = _find_data_dir()
MAX_MARKS = 720
PRIMARY_YEAR = 2025  # latest completed counselling cycle, used as the 2026 baseline

app = FastAPI(
    title="NEET 2026 Predictor",
    description="Rank predictor + MBBS college predictor built on real NEET cutoff data.",
    version="2.0.0",
)


# --------------------------------------------------------------------------- #
# Data loading                                                                #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _rank_score() -> dict[int, dict[int, tuple[int, int]]]:
    """{year: {score: (min_rank, max_rank)}}."""
    rows = json.loads((DATA_DIR / "rank_score.json").read_text())
    table: dict[int, dict[int, tuple[int, int]]] = {}
    for r in rows:
        table.setdefault(r["year"], {})[r["score"]] = (r["minrank"], r["maxrank"])
    return table


@lru_cache(maxsize=1)
def _cutoffs() -> list[dict]:
    return json.loads((DATA_DIR / "cutoffs.json").read_text())


@lru_cache(maxsize=1)
def _colleges() -> dict[str, dict]:
    rows = json.loads((DATA_DIR / "colleges.json").read_text())
    return {c["name"]: c for c in rows if c.get("name")}


# --------------------------------------------------------------------------- #
# Rank predictor                                                              #
# --------------------------------------------------------------------------- #
def predict_rank(marks: float, year: int = PRIMARY_YEAR) -> dict:
    if marks > MAX_MARKS:
        raise ValueError(f"Marks cannot exceed {MAX_MARKS}.")
    if marks < 0:
        raise ValueError("Marks cannot be negative.")

    table = _rank_score().get(year)
    if not table:
        raise ValueError(f"No rank data for year {year}.")

    scores = sorted(table)
    lo_score, hi_score = scores[0], scores[-1]
    m = round(marks)

    if m >= hi_score:
        # Top of the table — best possible ranks.
        mn, mx = table[hi_score]
    elif m <= lo_score:
        mn, mx = table[lo_score]
    elif m in table:
        mn, mx = table[m]
    else:
        # Gap in the table: interpolate between the two nearest scores.
        lower = max(s for s in scores if s < m)
        upper = min(s for s in scores if s > m)
        frac = (m - lower) / (upper - lower)
        lmn, lmx = table[lower]
        umn, umx = table[upper]
        mn = round(lmn + frac * (umn - lmn))
        mx = round(lmx + frac * (umx - lmx))

    predicted = (mn + mx) // 2
    return {
        "marks": marks,
        "year": year,
        "predicted_rank": predicted,
        "rank_range": f"{mn:,} - {mx:,}",
        "rank_min": mn,
        "rank_max": mx,
    }


# --------------------------------------------------------------------------- #
# College predictor                                                           #
# --------------------------------------------------------------------------- #
# Admission-probability model (mirrors the source tool):
#   diff = your_rank - college_closing_rank   (negative => you rank better)
def _probability(diff: int) -> tuple[int, str]:
    if diff < -5000:
        return 95, "Very High"
    if diff < 0:
        return 80, "High"
    if diff < 3000:
        return 50, "Moderate"
    if diff < 7000:
        return 25, "Low"
    return 8, "Very Low"


def predict_colleges(
    rank: int,
    category: str,
    quota: str,
    state: str = "All",
    limit: int = 100,
) -> list[dict]:
    rows = _cutoffs()
    colleges = _colleges()

    # Keep the most recent year available for each (college, quota, category).
    best_by_group: dict[tuple, dict] = {}
    for r in rows:
        if r["cat"] != category or r["quo"] != quota:
            continue
        if state != "All" and r["st"] != state:
            continue
        key = (r["col"], r["quo"], r["cat"])
        prev = best_by_group.get(key)
        if prev is None or r["year"] > prev["year"]:
            best_by_group[key] = r

    results = []
    for r in best_by_group.values():
        closing = r["closing"]
        diff = rank - closing
        prob, label = _probability(diff)
        master = colleges.get(r["col"], {})
        results.append(
            {
                "college": r["col"],
                "state": r["st"],
                "type": r["type"],
                "quota": r["quo"],
                "category": r["cat"],
                "year": r["year"],
                "closing_rank": closing,
                "seats": r["seats"] or master.get("seats"),
                "closing_score": r["score"],
                "diff": diff,
                "probability": prob,
                "chance": label,
                "url": master.get("url"),
            }
        )

    # Best chances first; within the same chance, the colleges whose cutoff is
    # nearest the user's rank (most relevant — best reachable / closest reach).
    results.sort(key=lambda x: (-x["probability"], abs(x["diff"])))
    return results[:limit]


# --------------------------------------------------------------------------- #
# API models                                                                  #
# --------------------------------------------------------------------------- #
class RankResponse(BaseModel):
    marks: float
    year: int
    predicted_rank: int
    rank_range: str
    rank_min: int
    rank_max: int


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #
@app.get("/api/predict-rank", response_model=RankResponse)
def api_predict_rank(
    marks: float = Query(..., ge=0, le=MAX_MARKS),
    year: int = Query(PRIMARY_YEAR),
):
    try:
        return predict_rank(marks, year)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/predict-colleges")
def api_predict_colleges(
    rank: int = Query(..., ge=1),
    category: str = Query("GN"),
    quota: str = Query("AIQ"),
    state: str = Query("All"),
    limit: int = Query(100, ge=1, le=500),
):
    matches = predict_colleges(rank, category, quota, state, limit)
    return {"rank": rank, "category": category, "quota": quota, "state": state, "count": len(matches), "colleges": matches}


@app.get("/api/options")
def api_options():
    """Dropdown values derived from the dataset."""
    rows = _cutoffs()
    cats = sorted({r["cat"] for r in rows})
    quotas = sorted({r["quo"] for r in rows})
    states = sorted({r["st"] for r in rows})
    # Surface the common categories first for a friendlier UI.
    common = ["GN", "EWS", "OBC", "SC", "ST", "GN-PH", "OBC-PH", "SC-PH", "ST-PH", "EWS-PH"]
    cats = [c for c in common if c in cats] + [c for c in cats if c not in common]
    return {"categories": cats, "quotas": quotas, "states": ["All"] + states}


@app.get("/")
def index():
    return {
        "message": "NEET 2026 Predictor API",
        "endpoints": {
            "rank_predictor": "/api/predict-rank?marks=620&year=2025",
            "college_predictor": "/api/predict-colleges?rank=15000&category=GN&quota=AIQ&state=All",
            "dropdown_options": "/api/options",
            "docs": "/docs",
        },
    }
