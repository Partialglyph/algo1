# algo1

Predictive shipping price and demand forecasting service.

This repository contains a FastAPI backend for multi‑module forecasting:

- Module 1: Shipping price index Monte Carlo forecaster (8‑week horizon)
- Module 2: Multi‑factor export cost projection (to be layered on top)
- Module 3: Consumer volume forecasting (to be layered on top)

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn shipping_forecast.api:app --reload
```

Then open http://localhost:8000/docs to explore the API.
