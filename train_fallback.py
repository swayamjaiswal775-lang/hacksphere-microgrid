"""Train the local fallback model + build the QuickML training CSV.

Dev-only script (needs scikit-learn + pandas). The deployed app does NOT need it.

    python tools/train_fallback.py

Outputs
  quickml_train.csv    -> upload this to Catalyst QuickML (same features predict.py sends)
  model_fallback.json  -> tiny gradient-boosted-tree model, evaluated in pure Python
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from predict import FEATURES, build_features  # single source of truth for features

df = pd.read_csv(os.path.join(ROOT, "train_telemetry.csv"))
feat = pd.DataFrame([build_features(r) for r in df.to_dict("records")])[FEATURES]
y = df["target_demand_kw"].to_numpy(float)

out = feat.copy()
out["target_demand_kw"] = y
out.to_csv(os.path.join(ROOT, "quickml_train.csv"), index=False)


def make():
    return GradientBoostingRegressor(n_estimators=300, max_depth=3, learning_rate=0.05,
                                     subsample=0.8, random_state=0)


# time-blocked validation: train on the past, score on the following 600 hours
folds = [(2000, 2600), (2600, 3200), (3200, 3800), (3400, 4000)]
scores = []
for a, b in folds:
    m = make().fit(feat.iloc[:a], y[:a])
    scores.append(float(np.sqrt(np.mean((m.predict(feat.iloc[a:b]) - y[a:b]) ** 2))))
print("blocked-CV RMSE per fold:", np.round(scores, 2), "mean", round(float(np.mean(scores)), 2))

m = make().fit(feat, y)
trees = []
for est in m.estimators_[:, 0]:
    t = est.tree_
    trees.append({"l": t.children_left.tolist(), "r": t.children_right.tolist(),
                  "f": t.feature.tolist(), "t": t.threshold.tolist(),
                  "v": t.value[:, 0, 0].tolist()})
model = {"features": FEATURES, "init": float(m.init_.constant_[0][0]),
         "lr": float(m.learning_rate), "trees": trees,
         "cv_rmse_mean": round(float(np.mean(scores)), 2)}
with open(os.path.join(ROOT, "model_fallback.json"), "w") as fh:
    json.dump(model, fh, separators=(",", ":"))
print("wrote model_fallback.json,", len(trees), "trees; quickml_train.csv", out.shape)
