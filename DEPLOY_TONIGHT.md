# Deploy checklist (deadline: 28 Oct, 11:59 PM IST)

Everything below needs **your** Catalyst account; that is the part I could not do for you. Work top to bottom, and stop to fix anything that fails before moving on. Rough total: 60 to 90 minutes if the account already exists.

## 0. Before you start (5 min)

- [ ] Unzip the project, run `./scripts/check.sh`. It must end with `all checks passed`.
- [ ] Open the **Catalyst for HackSphere 3.0** onboarding guide linked in the problem statement and create the free account/project if you have not. Note which **data centre** it uses (.in, .com, .eu ...). It decides the accounts domain in step 3.

## 1. Train and publish the model in QuickML (20 to 30 min)

- [ ] Catalyst console > QuickML > Datasets > Import Dataset > File Upload > `quickml_train.csv` (4000 rows, 7 columns).
- [ ] Create an ML pipeline. Target column: `target_demand_kw`. Problem type: regression. Use all six feature columns.
- [ ] Train. Look at the RMSE QuickML reports. A good result is around **11 to 13 kW**. If it is far worse (for example 25+), try another algorithm (tree/boosting models suit this data; linear ones do not) or increase training effort. Accuracy is 35% of the score.
- [ ] Publish the model as an endpoint. Copy the **Deployment URL** and the **Endpoint key**.
- [ ] Use QuickML's "Test the Model" with one row, for example `hour=19, ambient_temp_c=33, humidity_pct=50, solar_irradiance_wm2=10, cooling_degree=15, is_peak_window=1`. Expect roughly 230 to 260.

## 2. Find your Org ID (2 min)

- [ ] Catalyst console > Settings (or the org details page) > copy the **Org ID**. It is sent as the `CATALYST-ORG` header.

## 3. Create OAuth credentials (10 min)

QuickML's external endpoint needs a Zoho OAuth token with scope `QuickML.deployment.READ`.

1. Open the API console for your data centre (api-console.zoho.in for India) > **Add Client** > **Self Client**. Copy the Client ID and Client Secret.
2. On the Self Client's **Generate Code** tab: scope `QuickML.deployment.READ`, time duration 10 minutes, any description. Copy the code right away; it expires.
3. Exchange it for a refresh token (use your data centre's accounts domain):

```bash
curl -X POST "https://accounts.zoho.in/oauth/v2/token" \
  -d grant_type=authorization_code -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET -d code=THE_CODE_FROM_STEP_2
```

4. Keep the `refresh_token` from the response (it does not expire). `predict.py` and the app use it to fetch fresh access tokens automatically.

## 4. Test locally against your real endpoint (10 min)

```bash
cp .env.example .env      # fill in the values; .env is git-ignored
set -a; source .env; set +a
python3 predict.py --input sample_test_input.csv --output predictions.csv
```

- [ ] Output ends with `(source: quickml)`. If it says `local_fallback`, read the warning line above it; it contains the HTTP error.
- [ ] If the warning mentions the request body or response shape, set `QUICKML_MODE=single` (one flat row per call) or `batch` and retry.
- [ ] `python3 test_eval.py` prints `SUCCESS`.
- [ ] `QUICKML_FALLBACK=0 python3 predict.py ...` also works. This proves the score is coming from QuickML and not from the fallback model.
- [ ] Paste your Deployment URL into `QUICKML_ENDPOINT_URL` at the top of `predict.py` (the brief asks for it to be configured there). It is not a secret.

## 5. Deploy to AppSail (20 to 30 min)

```bash
./scripts/build_appsail.sh
npm install -g zcatalyst-cli
catalyst login
catalyst init          # choose your project; select AppSail; stack Python 3.9; source directory: appsail
catalyst deploy
```

- [ ] `appsail/app-config.json` must have: `"command": "python3 -u app.py"`, stack Python 3.9, `"catalyst_auth": false`. If `catalyst init` writes its own copy, edit it to match. The app must be **public**, or the judges cannot open it.
- [ ] In the Catalyst console > AppSail > your service > environment variables, add **the secrets and endpoint settings** from step 4: `QUICKML_ENDPOINT_URL`, `QUICKML_ENDPOINT_KEY`, `CATALYST_ORG_ID`, `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`, `ZOHO_ACCOUNTS_URL`. Do not put secrets in `app-config.json`.
- [ ] Restart/redeploy the service so it picks up the variables.
- [ ] If the CLI flow differs from the above, follow the onboarding guide; the app itself only needs "run `python3 -u app.py` in this folder".

## 6. Verify the live URL (10 min)

- [ ] Open the AppSail URL in a **private/incognito window** (proves it is public). The dashboard should load with data already on screen.
- [ ] The top-right badge says **Forecast by QuickML endpoint**, not "built-in fallback model". The System log at the bottom shows `QuickML` in the Model column.
- [ ] Click the three sample days, switch to the CSV tab and upload `sample_test_input.csv`, empty one cell in the form and re-run (the Data quality report should mention the repair), press Play, click a ribbon cell.
- [ ] Check an hour you can verify by hand: 17:00 with demand 150 should be Main Grid; 18:00 should always be Renewable Storage; any hour at or above 200 kW shows the red Critical overload banner and chip.

## 7. Submit (Google Form, team leader only)

The form's required fields: Email, Team Name, Team Leader name / e-mail / phone / GitHub profile, **Deployed Catalyst Link**, **GitHub Repository Link**, and **a presentation as a PDF (max 100 MB)**.

- [ ] Put the live URL into the README table.
- [ ] **GitHub repo must be public.** A zip is not enough for this form. From the project folder:
  ```bash
  git init && git add . && git commit -m "HackSphere 3.0 submission"
  git branch -M main
  git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git   # create the empty public repo on github.com first
  git push -u origin main
  ```
  Before pushing run `git status` and confirm no `.env` is listed (`.gitignore` already excludes it).
- [ ] **Regenerate the deck with your real details** (takes a second). Take a screenshot of your *live* dashboard first so the deck shows the QuickML badge, not the fallback one:
  ```bash
  pip install reportlab
  python3 tools/make_deck.py --team "YOUR TEAM" --url https://YOUR-APP-URL --repo https://github.com/YOUR_USER/YOUR_REPO \
      --quickml-rmse 11.9 --screenshot live_dashboard.png --out Presentation.pdf
  ```
  Use the RMSE QuickML actually reported (leave `--quickml-rmse` out if you are unsure). Open the PDF and check every page before uploading.
- [ ] Fill in the form and upload `Presentation.pdf`.

## Ask the organisers (important)

The brief says your `predict.py` is run against your published QuickML API on a hidden test set, and also says to keep credentials secure. `predict.py` reads credentials from environment variables, and the grader's machine will not have yours. Message Dipti (8219836424) or Ananya (8700721174) **tonight** and ask exactly how credentials should be supplied to the evaluation run (for example a shared token, a public endpoint, or hard-coded test credentials). If you do not resolve this, the grader may run your script with no credentials, and with the default settings it will silently score the local fallback model instead of your QuickML model.
