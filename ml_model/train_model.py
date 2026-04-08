import argparse
import os
import sys
from typing import Tuple, Optional, List
import glob

# --- NEW: Kaggle support (optional) ---
def _ensure_kagglehub():
    try:
        import kagglehub  # noqa: F401
        return True
    except Exception:
        return False

def _download_from_kaggle(slug: str) -> str:
    """
    Download dataset using kagglehub and return local folder path.
    slug examples:
      - "zaraavagyan/weathercsv"
      - "OWNER/DATASET"
    """
    if not _ensure_kagglehub():
        print("kagglehub missing. Install first: pip install kagglehub", file=sys.stderr)
        sys.exit(1)
    import kagglehub
    print(f"Downloading Kaggle dataset: {slug} ...")
    path = kagglehub.dataset_download(slug)
    print("Downloaded to:", path)
    return path

def _pick_input_from_folder(folder: str, preferred_name: Optional[str] = None) -> str:
    """Pick a CSV/XLSX file from downloaded folder."""
    candidates = []
    if preferred_name:
        cand = os.path.join(folder, preferred_name)
        if os.path.exists(cand):
            return cand
    # otherwise search
    for ext in ("*.csv", "*.xlsx", "*.xls", "*.txt"):
        candidates.extend(glob.glob(os.path.join(folder, "**", ext), recursive=True))
    if not candidates:
        print("No CSV/XLSX found inside downloaded dataset folder.", file=sys.stderr)
        sys.exit(1)
    # small heuristic: prefer files that have 'weather' in name
    candidates.sort(key=lambda p: ( "weather" not in os.path.basename(p).lower(), len(os.path.basename(p)) ))
    pick = candidates[0]
    print("Auto-selected input file:", pick)
    return pick
# --- END NEW ---

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, roc_auc_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
import joblib

RANDOM_STATE = 42
TARGET_CANDIDATES = ["rain", "Rain", "RAIN", "RainToday", "RainTomorrow"]
YES_VALUES = {"yes", "y", "true", "1"}

# Optional rename map if headers differ
RENAME_MAP = {
    # "TempC": "temperature",
    # "Humid": "humidity",
    # "WindSpd": "wind_speed",
}

PREFERRED_NUMERIC = [
    "temperature", "temp", "temp_c", "humidity", "pressure", "wind_speed",
    "cloud_cover", "rainfall_mm", "precip_mm", "visibility_km"
]
PREFERRED_CATEG = ["wind_dir", "location", "station", "weather"]

def read_sheet(path: str, sheet: Optional[str]) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext in [".xlsx", ".xls"]:
        return pd.read_excel(path, sheet_name=sheet) if sheet else pd.read_excel(path)
    elif ext in [".csv", ".txt"]:
        return pd.read_csv(path)
    else:
        raise ValueError(f"Unsupported file type: {ext}. Use CSV or XLSX.")

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=RENAME_MAP) if RENAME_MAP else df
    df.columns = [c.strip() for c in df.columns]
    return df

def detect_target(df: pd.DataFrame) -> Optional[str]:
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in TARGET_CANDIDATES:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None

def coerce_target(y: pd.Series) -> pd.Series:
    if y.dtype == bool:
        return y.astype(int)
    try:
        yn = pd.to_numeric(y, errors="coerce")
        if yn.notna().mean() > 0.8:
            return yn.fillna(0).astype(int)
    except Exception:
        pass
    return y.astype(str).str.strip().str.lower().isin(YES_VALUES).astype(int)

def add_datetime_features(df: pd.DataFrame) -> pd.DataFrame:
    dt_cols = [c for c in df.columns if "date" in c.lower() or "time" in c.lower() or "datetime" in c.lower()]
    for c in dt_cols:
        try:
            d = pd.to_datetime(df[c], errors="coerce")
            df[c + "_year"] = d.dt.year
            df[c + "_month"] = d.dt.month
            df[c + "_day"] = d.dt.day
            df[c + "_hour"] = d.dt.hour
            df[c + "_dow"] = d.dt.weekday
        except Exception:
            continue
    return df

def split_xy(df: pd.DataFrame, target_col: Optional[str]):
    if target_col and target_col in df.columns:
        y = coerce_target(df[target_col])
        X = df.drop(columns=[target_col])
        return X, y
    return df, None

def infer_feature_types(X: pd.DataFrame):
    numeric_cols, categorical_cols = [], []
    for c in X.columns:
        if pd.api.types.is_numeric_dtype(X[c]):
            numeric_cols.append(c)
        else:
            coerced = pd.to_numeric(X[c], errors="coerce")
            if coerced.notna().mean() > 0.7:
                X[c] = coerced
                numeric_cols.append(c)
            else:
                categorical_cols.append(c)
    numeric_cols = sorted(numeric_cols, key=lambda x: (x not in PREFERRED_NUMERIC, x))
    categorical_cols = sorted(categorical_cols, key=lambda x: (x not in PREFERRED_CATEG, x))
    return numeric_cols, categorical_cols

def build_pipeline(numeric_cols: List[str], categorical_cols: List[str]) -> Pipeline:
    numeric_pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler())
    ])
    categorical_pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(handle_unknown="ignore"))
    ])
    pre = ColumnTransformer(
        transformers=[
            ("num", numeric_pipe, numeric_cols),
            ("cat", categorical_pipe, categorical_cols),
        ],
        remainder="drop"
    )
    base = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=RANDOM_STATE)
    clf = CalibratedClassifierCV(base, cv=5, method="isotonic")
    pipe = Pipeline([("prep", pre), ("clf", clf)])
    return pipe

def train_and_eval(df: pd.DataFrame, target_col: str):
    X_raw, y = split_xy(df, target_col)
    X_raw = add_datetime_features(X_raw)

    num_cols, cat_cols = infer_feature_types(X_raw)
    pipe = build_pipeline(num_cols, cat_cols)

    X_train, X_val, y_train, y_val = train_test_split(
        X_raw, y, test_size=0.2, stratify=y, random_state=RANDOM_STATE
    )
    pipe.fit(X_train, y_train)

    y_pred = pipe.predict(X_val)
    try:
        y_prob = pipe.predict_proba(X_val)[:, 1]
    except Exception:
        y_prob = None

    acc = accuracy_score(y_val, y_pred)
    p, r, f1, _ = precision_recall_fscore_support(y_val, y_pred, average="binary", zero_division=0)

    print("\n=== Validation Metrics ===")
    print(f"Accuracy : {acc:.3f}")
    print(f"Precision: {p:.3f}")
    print(f"Recall   : {r:.3f}")
    print(f"F1-score : {f1:.3f}")
    if y_prob is not None:
        try:
            auc = roc_auc_score(y_val, y_prob)
            print(f"ROC-AUC  : {auc:.3f}")
        except Exception:
            pass
    print("\nDetailed report:\n", classification_report(y_val, y_pred, digits=3))

    joblib.dump(pipe, "model.joblib")
    print("\nSaved model -> model.joblib")

    out = X_raw.copy()
    out["prediction"] = pipe.predict(X_raw).astype(int)
    try:
        out["rain_probability"] = pipe.predict_proba(X_raw)[:, 1]
    except Exception:
        out["rain_probability"] = np.nan
    return pipe, out

def predict_only(df: pd.DataFrame, model_path: str) -> pd.DataFrame:
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model not found: {model_path}. Train first.")
    model: Pipeline = joblib.load(model_path)
    df2 = add_datetime_features(df.copy())
    out = df2.copy()
    out["prediction"] = model.predict(df2).astype(int)
    try:
        out["rain_probability"] = model.predict_proba(df2)[:, 1]
    except Exception:
        out["rain_probability"] = np.nan
    return out

def default_output_name(input_path: str) -> str:
    base = os.path.basename(input_path)
    name, _ = os.path.splitext(base)
    return f"predictions_{name}.csv"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="Path to CSV/XLSX sheet")
    ap.add_argument("--sheet", default=None, help="Excel sheet name (if .xlsx)")
    ap.add_argument("--model", default="model.joblib", help="Path to saved model for predict-only mode")
    ap.add_argument("--output", default=None, help="Output CSV path")
    # NEW: Kaggle options
    ap.add_argument("--kaggle", help="Kaggle dataset slug, e.g. zaraavagyan/weathercsv")
    ap.add_argument("--kaggle-file", help="Specific file name inside the Kaggle dataset (optional)")
    args = ap.parse_args()

    # Decide input
    input_path = args.input
    if args.kaggle:
        folder = _download_from_kaggle(args.kaggle)
        input_path = _pick_input_from_folder(folder, args.kaggle_file)

    if not input_path:
        print("ERROR: provide --input <file> or --kaggle <owner/dataset>", file=sys.stderr)
        sys.exit(2)

    df = read_sheet(input_path, args.sheet)
    if df.empty:
        print("Input sheet is empty.", file=sys.stderr)
        sys.exit(1)

    df = normalize_columns(df)

    target_col = detect_target(df)
    if target_col is not None:
        print(f"Detected target column: {target_col}")
        _, pred_df = train_and_eval(df, target_col)
    else:
        print("No target column found. Predict-only mode using saved model.")
        pred_df = predict_only(df, args.model)

    out_path = args.output or default_output_name(input_path)
    pred_df.to_csv(out_path, index=False)
    print(f"\nSaved predictions -> {out_path}")

    if "prediction" in pred_df.columns:
        rate = pred_df["prediction"].mean()
        print(f"Rain predicted in {pred_df['prediction'].sum()} rows ({rate:.1%} of records).")
        overall = int(pred_df["prediction"].max())
        print(f"\nOverall verdict (any row indicates rain?): {'RAIN' if overall==1 else 'NO RAIN'}")

if __name__ == "__main__":
    main()
