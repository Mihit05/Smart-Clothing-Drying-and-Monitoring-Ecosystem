import time
import numpy as np
import pandas as pd
import joblib
import gspread
from google.oauth2.service_account import Credentials

# ====== FILL THESE ======
SERVICE_ACCOUNT_FILE = "service_account.json"   # your downloaded key
SPREADSHEET_ID = "YOUR_SHEETS_ID"       # from sheet URL /d/<ID>/
SHEET_NAME = "Sheet1"                           # tab name
MODEL_PATH = "model.joblib"                     # trained pipeline
POLL_SECONDS = 20                               # poll frequency
# ========================

# By your Apps Script append order: [timestamp, moisture_raw, moisture_pct, temp_c, hum_pct]
TEMP_COL_IDX = 3  # 0-based -> 4th column
HUM_COL_IDX  = 4  # 0-based -> 5th column

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def get_ws():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    return sh.worksheet(SHEET_NAME)

def get_last_non_empty_row(values):
    if not values:
        return None, None
    for idx in range(len(values) - 1, -1, -1):
        row = values[idx]
        if any(str(c).strip() != "" for c in row):
            return idx, row
    return None, None

def to_float(x):
    try:
        s = str(x).strip()
        if s == "" or s.lower() == "none":
            return np.nan
        return float(s)
    except Exception:
        return np.nan

def align_for_model(df, model):
    prep = model.named_steps["prep"]
    expected = []
    for name, trans, cols in prep.transformers_:
        if name != "remainder":
            expected += list(cols)
    for c in expected:
        if c not in df.columns:
            df[c] = np.nan
    return df

def main():
    # Load model once
    model = joblib.load(MODEL_PATH)

    # Prepare sheet handle
    ws = get_ws()

    last_seen_signature = None  # to avoid repeating same row
    print(f"Watching sheet every {POLL_SECONDS}s…  (Ctrl+C to stop)\n")

    while True:
        try:
            values = ws.get_all_values()  # 2D list
            idx, row = get_last_non_empty_row(values)
            if row is None:
                print("[No data yet]")
            else:
                # Build a signature to detect same row (timestamp + temp + hum if present)
                signature = tuple(row[:5]) if len(row) >= 5 else tuple(row)
                if signature != last_seen_signature:
                    # Extract by index
                    temp = to_float(row[TEMP_COL_IDX]) if len(row) > TEMP_COL_IDX else np.nan
                    hum  = to_float(row[HUM_COL_IDX])  if len(row) > HUM_COL_IDX  else np.nan

                    # Prepare one-row frame with training column names
                    X = pd.DataFrame([{"MinTemp": temp, "Humidity9am": hum}])
                    X_in = align_for_model(X.copy(), model)

                    pred = int(model.predict(X_in)[0])
                    try:
                        prob = float(model.predict_proba(X_in)[:,1][0])
                    except Exception:
                        prob = float("nan")

                    label = "RAIN" if pred == 1 else "NO RAIN"
                    ts = row[0] if len(row) > 0 else ""
                    print(f"[{ts}] Temp={temp}  Hum={hum}  -> {label}  (p={prob:.2%})")

                    # Append to local log
                    out = X.copy()
                    out["timestamp"] = ts
                    out["RainPrediction"] = pred
                    out["RainProbability"] = prob
                    try:
                        pd.DataFrame(out).to_csv("prediction_watch_log.csv",
                                                 mode="a", header=not pd.io.common.file_exists("prediction_watch_log.csv"),
                                                 index=False)
                    except Exception:
                        pass

                    last_seen_signature = signature
                else:
                    # No change since last poll
                    print("(no change)")

        except KeyboardInterrupt:
            print("\nStopped by user.")
            break
        except Exception as e:
            print(f"[Error] {e}")

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
