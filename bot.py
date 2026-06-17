"""
Multi-Asset M15 - Ichimoku + MACD(5,35,5) Signal Bot
=====================================================
Aktywa: Złoto, Kakao, Ropa WTI, Ropa Brent, US100 (Nasdaq)
Logika: Przecięcie MACD jako trigger + pozycja ceny względem chmury Ichimoku
Deployment: Railway.app (działa 24/7 jako worker)
"""

import os
import json
import time
import logging
import schedule
import requests
import pandas as pd
from datetime import datetime, timezone

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Konfiguracja ─────────────────────────────────────────────────────────────
TWELVEDATA_API_KEY = os.environ["TWELVEDATA_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

SYMBOLS = {
    "Gold (XAU/USD)":  "XAU/USD",
    "Cocoa (Kakao)":   "CC",
    "Crude Oil (WTI)": "CL",
    "Brent Oil":       "BRENT",
    "US100 (Nasdaq)":  "NDX",
}

INTERVAL        = "15min"
BARS_TO_FETCH   = 300
SWING_LOOKBACK  = 50

MACD_FAST       = 5
MACD_SLOW       = 35
MACD_SIGNAL_LEN = 5
RSI_LENGTH      = 14

TENKAN_PERIOD   = 9
KIJUN_PERIOD    = 26
SENKOU_B_PERIOD = 52

STATE_FILE = "state.json"

# ── State (anty-duplikaty) ────────────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Pobieranie danych ─────────────────────────────────────────────────────────
def get_candles(symbol: str) -> pd.DataFrame:
    params = {
        "symbol":     symbol,
        "interval":   INTERVAL,
        "outputsize": BARS_TO_FETCH,
        "apikey":     TWELVEDATA_API_KEY,
        "order":      "ASC",
    }
    resp = requests.get(TWELVEDATA_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") == "error":
        raise RuntimeError(f"Twelve Data error dla {symbol}: {data.get('message')}")

    rows = [
        {
            "time":  v["datetime"],
            "open":  float(v["open"]),
            "high":  float(v["high"]),
            "low":   float(v["low"]),
            "close": float(v["close"]),
        }
        for v in data["values"]
    ]
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values("time").reset_index(drop=True)


# ── Wskaźniki ─────────────────────────────────────────────────────────────────
def add_macd(df: pd.DataFrame) -> pd.DataFrame:
    ema_fast = df["close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = df["close"].ewm(span=MACD_SLOW, adjust=False).mean()
    df["MACD"] = ema_fast - ema_slow
    df["MACD_SIG"] = df["MACD"].ewm(span=MACD_SIGNAL_LEN, adjust=False).mean()
    return df


def add_rsi(df: pd.DataFrame) -> pd.DataFrame:
    delta    = df["close"].diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_LENGTH, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_LENGTH, adjust=False).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))
    return df


def add_ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    """
    Senkou A/B BEZ przesunięcia – porównujemy je z ceną bieżącej świecy,
    a nie z ceną sprzed 26 świec (błąd w klasycznym shift(+26)).
    """
    high = df["high"]
    low  = df["low"]
    tenkan   = (high.rolling(TENKAN_PERIOD).max()   + low.rolling(TENKAN_PERIOD).min())   / 2
    kijun    = (high.rolling(KIJUN_PERIOD).max()    + low.rolling(KIJUN_PERIOD).min())    / 2
    df["SENKOU_A"] = (tenkan + kijun) / 2
    df["SENKOU_B"] = (high.rolling(SENKOU_B_PERIOD).max() + low.rolling(SENKOU_B_PERIOD).min()) / 2
    return df


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = add_macd(df)
    df = add_rsi(df)
    df = add_ichimoku(df)
    return df


# ── Logika sygnału ────────────────────────────────────────────────────────────
def detect_signal(df: pd.DataFrame, asset_name: str) -> dict | None:
    last = df.iloc[-1]
    prev = df.iloc[-2]

    required = ["MACD", "MACD_SIG", "SENKOU_A", "SENKOU_B", "RSI"]
    if any(pd.isna(last[c]) or pd.isna(prev[c]) for c in required):
        return None

    close      = last["close"]
    cloud_top  = max(last["SENKOU_A"], last["SENKOU_B"])
    cloud_bot  = min(last["SENKOU_A"], last["SENKOU_B"])

    above_cloud = close > cloud_top
    below_cloud = close < cloud_bot
    in_cloud    = cloud_bot <= close <= cloud_top

    bullish_cross = prev["MACD"] < prev["MACD_SIG"] and last["MACD"] > last["MACD_SIG"]
    bearish_cross = prev["MACD"] > prev["MACD_SIG"] and last["MACD"] < last["MACD_SIG"]

    direction = None

    if bullish_cross:
        if below_cloud:
            direction = "LONG"
        elif in_cloud:
            log.info(f"[{asset_name}] Bullish cross – cena W CHMURZE → OBSERWUJ")
            return {"status": "OBSERWUJ", "candle_time": last["time"]}
        else:
            log.info(f"[{asset_name}] Bullish cross – cena NAD chmurą → ignoruj")
            return None

    elif bearish_cross:
        if above_cloud:
            direction = "SHORT"
        elif in_cloud:
            log.info(f"[{asset_name}] Bearish cross – cena W CHMURZE → OBSERWUJ")
            return {"status": "OBSERWUJ", "candle_time": last["time"]}
        else:
            log.info(f"[{asset_name}] Bearish cross – cena POD chmurą → ignoruj")
            return None

    else:
        return None

    return {
        "status":      "SIGNAL",
        "direction":   direction,
        "close":       close,
        "rsi":         last["RSI"],
        "candle_time": last["time"],
    }


# ── Fibonacci + SL/TP ─────────────────────────────────────────────────────────
def calculate_fibo(df: pd.DataFrame, direction: str):
    swing = df.iloc[-SWING_LOOKBACK:]
    sw_low  = swing["low"].min()
    sw_high = swing["high"].max()
    diff    = sw_high - sw_low

    ratios = {"23.6%": 0.236, "38.2%": 0.382, "50.0%": 0.500, "61.8%": 0.618, "78.6%": 0.786}

    if direction == "LONG":
        levels = {k: sw_high - r * diff for k, r in ratios.items()}
    else:
        levels = {k: sw_low  + r * diff for k, r in ratios.items()}

    return levels, sw_low, sw_high


def calculate_sl_tp(direction: str, entry: float, sw_low: float, sw_high: float, fibo: dict):
    if direction == "LONG":
        sl   = min(sw_low, fibo["78.6%"])
        risk = entry - sl
        tp1, tp2, tp3 = entry + risk, entry + 2*risk, entry + 3*risk
    else:
        sl   = max(sw_high, fibo["78.6%"])
        risk = sl - entry
        tp1, tp2, tp3 = entry - risk, entry - 2*risk, entry - 3*risk
    return sl, tp1, tp2, tp3


# ── Discord ───────────────────────────────────────────────────────────────────
def send_discord_signal(asset_name: str, signal: dict, fibo: dict, sl, tp1, tp2, tp3) -> None:
    direction = signal["direction"]
    entry     = signal["close"]
    rsi       = signal["rsi"]
    ts        = signal["candle_time"]

    color = 3066993 if direction == "LONG" else 15158332
    emoji = "🚀" if direction == "LONG" else "🔻"
    cloud_ctx = "Cena **POD** chmurą (kontra-trend ↑)" if direction == "LONG" else "Cena **NAD** chmurą (kontra-trend ↓)"

    fibo_text = "\n".join(f"`{k}` = {v:.4f}" for k, v in fibo.items())

    embed = {
        "title":  f"{emoji} {asset_name} — {direction}",
        "color":  color,
        "fields": [
            {"name": "Timeframe", "value": "M15",            "inline": True},
            {"name": "Entry",     "value": f"{entry:.4f}",   "inline": True},
            {"name": "\u200b",    "value": "\u200b",          "inline": True},
            {"name": "SL",        "value": f"{sl:.4f}",      "inline": True},
            {"name": "TP1 (1R)",  "value": f"{tp1:.4f}",     "inline": True},
            {"name": "TP2 (2R)",  "value": f"{tp2:.4f}",     "inline": True},
            {"name": "TP3 (3R)",  "value": f"{tp3:.4f}",     "inline": True},
            {"name": f"RSI ({RSI_LENGTH})", "value": f"{rsi:.1f}", "inline": True},
            {"name": "\u200b",    "value": "\u200b",          "inline": True},
            {"name": "Ichimoku",  "value": cloud_ctx,         "inline": False},
            {"name": "Fibonacci", "value": fibo_text,         "inline": False},
        ],
        "footer": {"text": f"Świeca: {ts} UTC"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
    if resp.status_code not in (200, 204):
        log.warning(f"Discord error dla {asset_name}: {resp.status_code} – {resp.text}")
    else:
        log.info(f"✅ Wysłano {direction} dla {asset_name}")


def send_discord_error(asset_name: str, error: str) -> None:
    embed = {
        "title": f"⚠️ Błąd – {asset_name}",
        "color": 16776960,
        "description": f"```{error}```",
        "footer": {"text": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")},
    }
    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)


# ── Główna pętla ──────────────────────────────────────────────────────────────
def run_scan() -> None:
    log.info("═" * 50)
    log.info(f"Skanowanie START – {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    state = load_state()

    for asset_name, symbol in SYMBOLS.items():
        log.info(f"--- {asset_name} ({symbol}) ---")
        try:
            df     = get_candles(symbol)
            df     = calculate_indicators(df)
            last_t = str(df.iloc[-1]["time"])

            if state.get(symbol) == last_t:
                log.info(f"Świeca {last_t} już przetworzona → pomijam")
                continue

            result = detect_signal(df, asset_name)

            if result and result["status"] == "SIGNAL":
                fibo, sw_low, sw_high = calculate_fibo(df, result["direction"])
                sl, tp1, tp2, tp3     = calculate_sl_tp(result["direction"], result["close"], sw_low, sw_high, fibo)
                send_discord_signal(asset_name, result, fibo, sl, tp1, tp2, tp3)
            elif result and result["status"] == "OBSERWUJ":
                log.info(f"OBSERWUJ – {asset_name}, brak wysyłki")
            else:
                log.info(f"Brak sygnału – {asset_name}")

            state[symbol] = last_t
            time.sleep(1)  # anti-rate-limit między symbolami

        except Exception as exc:
            log.error(f"Błąd {asset_name}: {exc}")
            try:
                send_discord_error(asset_name, str(exc))
            except Exception:
                pass

    save_state(state)
    log.info("Skanowanie DONE")


def main() -> None:
    log.info("Bot startuje...")
    run_scan()                              # od razu przy starcie
    schedule.every(15).minutes.do(run_scan)
    while True:
        schedule.run_pending()
        time.sleep(20)


if __name__ == "__main__":
    main()
