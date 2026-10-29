# Smart Energy Microgrid & Demand Forecasting Engine

HackSphere 3.0 submission. A web app on **Zoho Catalyst AppSail** that forecasts hourly demand for the next 24 hours with a **Catalyst QuickML** regression model, then applies the grid-switching rules to produce dispatch signals, overload alerts and a battery state-of-charge simulation.

| Deliverable | Where |
|---|---|
| Live app | _paste your AppSail URL here_ |
| Dashboard + API | `app.py`, `engine.py`, `static/` |
| QuickML training data | `quickml_train.csv` |
| Required script | `predict.py` (passes `python test_eval.py`) |
| Deployable folder | `appsail/` (built by `scripts/build_appsail.sh`) |
| Presentation PDF | `python3 tools/make_deck.py ...` (see `DEPLOY_TONIGHT.md`) |

## What it does

1. **Ingest**: type or edit 24 hourly weather rows in the form, or upload a CSV (up to 168 rows).
2. **Clean and validate**: parses timestamps in common formats, sorts, removes duplicates, rejects physically impossible values, fills gaps by time interpolation, and reports every repair on the dashboard (Data quality report).
3. **Forecast**: builds the feature vector and calls the published QuickML endpoint. One prediction per row.
4. **Switch**: applies the rules to each predicted hour.
5. **Show**: load curve with the 160 kW / 200 kW thresholds and the 18:00-22:00 band, a dispatch ribbon (mode per hour), active-state badge with a time scrubber and play button, battery state of charge, alert banners, signals list, hour-by-hour table, `predictions.csv` download and a system log.

### Transition rules (problem statement, section 6)

| Condition | Result |
|---|---|
| demand **> 160 kW** or hour **18..22 inclusive** | Renewable Storage Mode |
| otherwise | Main Grid Mode |
| demand **>= 200 kW** | Critical Overload Alert, in either mode |

Rules run on the predicted demand rounded to 2 decimals, the same number shown on screen. All boundaries (160.00 vs 160.01, hour 17/18/22/23, 199.99 vs 200.00) are covered in `tests/test_engine.py`. A transition signal (`SWITCH_TO_RENEWABLE_STORAGE` / `SWITCH_TO_MAIN_GRID`) is emitted whenever the mode changes.

## Architecture

```
Browser (static/index.html, Chart.js vendored)
   |  JSON
   v
AppSail  app.py  (stdlib HTTP server, no pip dependencies)
   |-- engine.py   clean/validate -> forecast -> controller -> battery sim -> signals/alerts
   |-- predict.py  features -> QuickML REST call (OAuth refresh, retries) -> predictions
   v
Zoho Catalyst QuickML endpoint  (regression model trained on quickml_train.csv)
```

| Module in the brief | Implemented by |
|---|---|
| Telemetry ingestion | Form and CSV upload in the dashboard, validated in `engine.clean_rows` |
| Preprocessing and API | AppSail (`app.py`, `engine.py`) |
| Forecasting engine | QuickML endpoint, called from `predict.py` |
| Transition controller | `engine.classify` / `run_controller` / `build_signals` |
| Live dashboard | `static/index.html` served by AppSail |

Slate, Stratus and Data Store are listed as recommendations in the brief and are **not used**: AppSail serves both the UI and the API. Run history is kept in memory and mirrored to a temp file (AppSail does not allow writes to the app directory), so it resets when the app restarts.

## The model

**Features** (identical for training and inference, defined once in `predict.build_features`):
`hour`, `ambient_temp_c`, `humidity_pct`, `solar_irradiance_wm2`, `cooling_degree` (= max(0, temp - 18)), `is_peak_window` (hour 18..22).

Design choices backed by experiments on `train_telemetry.csv`:

- Demand is dominated by the hour of day (about 92 kW overnight vs about 215 kW in the evening peak) and its interaction with temperature (about 0.2 kW per degree at 03:00 but 3.6 kW per degree at 13:00), so a linear model fails and tree ensembles work.
- Lag and rolling weather features did not help (blocked-CV RMSE 11.7 vs 11.4 kW), so they are not used. This also keeps inference row-independent: any batch size gives the same features.
- Validation is **time-blocked** (train on the past, score the next 600 hours), because the test set is a later period than training.

Reference numbers from `tools/train_fallback.py` (local gradient boosting, used as the fallback model): mean blocked-CV RMSE **11.2 kW** over four folds, versus about 49 kW for predicting the average of each hour of day. Your QuickML RMSE is whatever QuickML reports for your pipeline; use 11 kW as a sanity benchmark.

### Fallback model

If the endpoint is not configured or a call fails, `predict.py` prints a warning and answers with `model_fallback.json` (pure-Python gradient-boosted trees, verified identical to scikit-learn). The dashboard always shows which model produced the forecast. Set `QUICKML_FALLBACK=0` to make failures raise instead.

## Environment variables

Secrets belong in the Catalyst AppSail console (or a local `.env`), never in the repository. See `.env.example`.

| Variable | Purpose |
|---|---|
| `QUICKML_ENDPOINT_URL` | Deployment URL of the published endpoint (not secret; can also be pasted into `predict.py`) |
| `QUICKML_ENDPOINT_KEY` | Sent as `X-QUICKML-ENDPOINT-KEY` |
| `CATALYST_ORG_ID` | Sent as `CATALYST-ORG` |
| `CATALYST_ENVIRONMENT` | `Development` (default) or `Production` |
| `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN` | OAuth refresh flow, scope `QuickML.deployment.READ`; access tokens renew automatically |
| `QUICKML_ACCESS_TOKEN` | Alternative: a short-lived access token (expires in about an hour) |
| `ZOHO_ACCOUNTS_URL` | Accounts domain of your data centre, default `https://accounts.zoho.in` |
| `QUICKML_MODE` | `auto` (default), `batch` (`{"data":[rows]}`), or `single` (one flat row per call) |
| `QUICKML_FALLBACK` | `1` (default) or `0` |
| `QUICKML_TIMEOUT` | Seconds per HTTP call, default 30 |

## Setup

```bash
# local run (Python 3.9+, nothing to install)
python3 app.py                         # http://localhost:9000
python3 predict.py --input sample_test_input.csv --output predictions.csv
python3 test_eval.py                   # must print SUCCESS
./scripts/check.sh                     # all automated tests
```

1. **Train**: in QuickML upload `quickml_train.csv`, create a regression ML pipeline with target `target_demand_kw`, train, and publish an endpoint.
2. **Configure**: set the variables above (see `DEPLOY_TONIGHT.md` for how to obtain them).
3. **Deploy**: `./scripts/build_appsail.sh`, then deploy the `appsail/` folder as an AppSail service (stack Python 3.9, start command `python3 -u app.py`, authentication off so the URL is public).

## Tests

- `tests/test_engine.py`: rule boundaries, independence of overload from mode, signal sequence, battery bounds and energy balance, ingestion repairs.
- `tests/test_quickml_client.py`: against a mock OAuth + endpoint server: batch and per-row API shapes, token refresh, 401 retry, fallback and loud-failure modes, response parsing.

## Assumptions and limitations

- **Battery model is an assumption.** The brief gives no battery specification. Defaults (4000 kWh, 300 kW discharge, 200 kW charge, 10% reserve, 90% start) are editable in the UI. The battery supplies the load in Storage Mode and recharges from the grid in Main Grid Mode. It never changes the grid mode; it only reports state of charge and warns when the reserve is reached.
- The QuickML request/response format for batch input is not specified in the public docs I found, so the client supports both a batch body and one-row-per-call, and tolerates several response shapes. Confirm with the "Test the Model" tab on your endpoint.
- Forecasts use only weather and the clock. No past demand is available at inference time, so none is used.
