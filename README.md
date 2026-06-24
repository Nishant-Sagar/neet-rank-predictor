# NEET 2026 Predictor (backend)

A FastAPI backend with **two tools**, both powered by real cutoff data scraped
from the MBBS Council tools suite
(<https://mbbscouncil.com/tools/neet-rank-predictor-2026>):

1. **Rank Predictor** — NEET marks (out of 720) → expected All India Rank.
2. **College Predictor** — your rank + category + quota (+ state) → list of MBBS
   colleges you can realistically get, each with an admission probability.

## Data

The source site loads AES-ECB-encrypted, gzipped datasets from its CDN. These
were decrypted and converted to plain JSON in `data/`:

| File | Contents | Records |
|---|---|---|
| `data/rank_score.json` | Official NEET-UG score → rank table (2024, 2025) | 1,179 |
| `data/cutoffs.json` | Closing ranks per college / quota / category / year (2022–2025) | 51,281 |
| `data/colleges.json` | College master data (state, type, seats, fees, slug) | 823 |

`scripts/build_data.py` documents the full scrape → decrypt → aggregate pipeline.

## Setup

```bash
cd neet-rank-predictor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Server runs at `http://127.0.0.1:8000`. Interactive docs at `/docs`.

## API

### Rank Predictor

```
GET /api/predict-rank?marks=620&year=2025
```

```json
{
  "marks": 620.0,
  "year": 2025,
  "predicted_rank": 462,
  "rank_range": "455 - 469",
  "rank_min": 455,
  "rank_max": 469
}
```

The expected rank is the midpoint of the official score band; integer-gap marks
are linearly interpolated, and marks above the top of the table clamp to rank 1.

### College Predictor

```
GET /api/predict-colleges?rank=15000&category=GN&quota=AIQ&state=All&limit=100
```

Parameters: `rank` (required), `category` (default `GN`), `quota` (default
`AIQ`), `state` (default `All`), `limit` (default 100).

```json
{
  "rank": 15000, "category": "GN", "quota": "AIQ", "state": "All", "count": 100,
  "colleges": [
    {
      "college": "Govt Medical College Ambernath", "state": "Maharashtra",
      "type": "Govt-State", "quota": "AIQ", "category": "GN", "year": 2025,
      "closing_rank": 20024, "seats": 5, "closing_score": 536,
      "diff": -5024, "probability": 95, "chance": "Very High",
      "url": "govt-medical-college-ambernath"
    }
  ]
}
```

For each college the most recent year's closing rank is used.
`diff = your_rank − closing_rank` (negative means you rank better). The
admission-probability bands mirror the source tool:

| diff (your_rank − closing) | Probability | Chance |
|---|---|---|
| < −5000 | 95% | Very High |
| < 0 | 80% | High |
| < 3000 | 50% | Moderate |
| < 7000 | 25% | Low |
| ≥ 7000 | 8% | Very Low |

### Dropdown options

```
GET /api/options
```

Returns the available `categories`, `quotas`, and `states` derived from the
dataset (useful for building a UI). Quota codes: `AIQ` (All India), `SQ` (State),
`MQ` (Management), `NRI`, `OPMQ`, `OPQ`, `MNQ`, `AMU`, `AMQ`.
