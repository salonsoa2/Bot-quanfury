import os
import json
import time
import math
import subprocess
from datetime import datetime, timedelta
import pytz
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# ==============================================================================
# CONFIGURACION INSTITUCIONAL — ARGON QUANT V4.3 (SNIPER & SURVIVAL EDITION)
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = "portfolio_state.json"
SIGNALS_FILE = "active_signals.json"
SEC_CACHE_FILE = "sec_insider_cache.json"
TZ_MADRID = pytz.timezone("Europe/Madrid")

# Gestion de Capital Blindada para Proteger Colchon de 120$ sobre 2.000$ Poder
DEFAULT_INITIAL_CAPITAL = 2000.0
BASE_RISK_PCT = 0.0060              # 0.60% riesgo base (12.00$ sobre 2.000$ -> Protege tus 120$)
HIGH_CONVICTION_RISK_PCT = 0.0080   # 0.80% alta conviccion (16.00$ sobre 2.000$)
MAX_SINGLE_RISK_CAP = 0.0105        # 1.05% tope maximo absoluto por redondeo (21.00$)
MAX_POSITION_ALLOCATION = 0.35      # Maximo 35% del poder total por posicion (700$)
FREE_POWER_SAFETY_BUFFER = 0.95     # Usa maximo el 95% del poder libre disponible
MAX_OPEN_POSITIONS = 3              # Maximo 3 posiciones simultaneas (concentracion en lideres)
MAX_PER_SECTOR = 1                  # Maximo 1 accion por sector a la vez (diversificacion real)
MAX_DAILY_DRAWDOWN = -0.020         # Cortafuegos diario (-2.0% = -40$ para blindar los 120$)

# Filtros de Precision Quirurgica V4.3
MIN_STOP_DISTANCE_PCT = 0.0085      # Stop-Loss nunca menor al 0.85% del precio
SIGNAL_COOLDOWN_SECONDS = 600       # 10 minutos de enfriamiento entre senales (Cero metralleta)
MIN_CANDLE_CLOSE_STRENGTH = 0.60    # La vela debe cerrar en el 40% superior de su rango (Sin mechas)
MAX_VWAP_EXTENSION_ATR = 2.25       # Prohibido comprar rupturas a mas de 2.25x ATR sobre el VWAP

# Universo Depurado de 36 Activos Institucionales (Fuera SOFI, SMCI, BABA, SQ -> Entran ANET, VST, AXP, COST)
SECTOR_UNIVERSE = {
    "Chips_IA": ["NVDA", "AMD", "AVGO", "MU", "ARM", "ANET", "TSM"],
    "Cripto_Fintech": ["MSTR", "COIN", "HOOD", "AXP", "PYPL"],
    "Software_Cloud": ["PLTR", "ORCL", "PANW", "CRWD", "NOW", "SNOW", "CRM", "SHOP"],
    "Big_Tech": ["META", "NFLX", "AMZN", "GOOGL", "AAPL", "MSFT"],
    "Energia_EV_Lideres": ["TSLA", "UBER", "VST", "ABNB"],
    "Macro_Defensivo": ["LLY", "UNH", "JPM", "GS", "XOM", "CAT", "COST"]
}

ALL_TICKERS = [ticker for sector in SECTOR_UNIVERSE.values() for ticker in sector]
TICKER_TO_SECTOR = {ticker: sector for sector, tickers in SECTOR_UNIVERSE.items() for ticker in tickers}
BENCHMARK = "QQQ"

SEC_CIK_MAP = {
    "NVDA": "0001045810", "AMD": "0000002488", "AVGO": "0001730168", "MU": "0000723125",
    "ANET": "0001596532", "MSTR": "0001050446", "COIN": "0001679788", "HOOD": "0001783879",
    "AXP": "0000004962", "PYPL": "0001633917", "PLTR": "0001321655", "ORCL": "0001341439",
    "PANW": "0001327567", "CRWD": "0001535527", "NOW": "0001373715", "CRM": "0001108524",
    "META": "0001326801", "NFLX": "0001065280", "AMZN": "0001018724", "GOOGL": "0001652044",
    "AAPL": "0000320193", "MSFT": "0000789019", "TSLA": "0001318605", "UBER": "0001543151",
    "VST": "0001692819", "ABNB": "0001559720", "LLY": "0000059478", "UNH": "0000731766",
    "JPM": "0000019617", "GS": "0000886982", "XOM": "0000034088", "CAT": "0000018230",
    "COST": "0000909832"
}

# ==============================================================================
# UTILIDADES DE TIEMPO Y PERSISTENCIA GIT
# ==============================================================================
def now_madrid():
    return datetime.now(TZ_MADRID)

def today_str():
    return now_madrid().strftime("%Y-%m-%d")

def git_sync_state(commit_msg="Auto-sync Argon Quant v4.3"):
    try:
        subprocess.run(["git", "config", "--global", "user.name", "argon-quant-bot"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "bot@argonquant.local"], check=False)
        subprocess.run(["git", "add", STATE_FILE, SIGNALS_FILE], check=False)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if status.stdout.strip():
            subprocess.run(["git", "commit", "-m", commit_msg], check=False)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False, timeout=15)
            subprocess.run(["git", "push", "origin", "main"], check=False, timeout=15)
    except Exception as e:
        print(f"[WARN] Git sync no completado: {e}")

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            state.setdefault("initial_capital", DEFAULT_INITIAL_CAPITAL)
            state.setdefault("equity", DEFAULT_INITIAL_CAPITAL)
            state.setdefault("peak_equity", max(state.get("equity", DEFAULT_INITIAL_CAPITAL), DEFAULT_INITIAL_CAPITAL))
            state.setdefault("realized_pnl", 0.0)
            state.setdefault("total_trades", 0)
            state.setdefault("wins", 0)
            state.setdefault("losses", 0)
            state.setdefault("gross_profit", 0.0)
            state.setdefault("gross_loss", 0.0)
            state.setdefault("max_drawdown_pct", 0.0)
            state.setdefault("daily_pnl", {})
            state.setdefault("open_positions", {})
            state.setdefault("trade_history", [])
            state.setdefault("cooldowns", {})
            state.setdefault("last_global_signal_ts", 0)
            state.setdefault("last_update_id", 0)
            state.setdefault("eod_alert_sent_date", "")
            return state
        except Exception as e:
            print(f"[ERROR] Error leyendo {STATE_FILE}: {e}")

    return {
        "initial_capital": DEFAULT_INITIAL_CAPITAL,
        "equity": DEFAULT_INITIAL_CAPITAL,
        "peak_equity": DEFAULT_INITIAL_CAPITAL,
        "realized_pnl": 0.0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "max_drawdown_pct": 0.0,
        "daily_pnl": {},
        "open_positions": {},
        "trade_history": [],
        "cooldowns": {},
        "last_global_signal_ts": 0,
        "last_update_id": 0,
        "eod_alert_sent_date": ""
    }

def save_state(state, push_to_git=False, msg="Sync portfolio state v4.3"):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        if push_to_git:
            git_sync_state(msg)
    except Exception as e:
        print(f"[ERROR] Error guardando estado: {e}")

def load_signals():
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_signals(signals):
    try:
        with open(SIGNALS_FILE, "w", encoding="utf-8") as f:
            json.dump(signals, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Error guardando senales: {e}")

# ==============================================================================
# COMUNICACION TELEGRAM E INTERFAZ DE COMANDOS
# ==============================================================================
def send_telegram_message(text, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, data=payload, timeout=10)
        return r.json()
    except Exception as e:
        print(f"[WARN] Telegram send error: {e}")
        return None

def answer_callback_query(callback_id, text=""):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, data={"callback_query_id": callback_id, "text": text}, timeout=8)
    except Exception:
        pass

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception:
        pass

# ==============================================================================
# CALCULO CONTABLE Y DIMENSIONAMIENTO CON PODER DE TRADING LIBRE REAL
# ==============================================================================
def get_used_buying_power(state):
    used = 0.0
    for pos in state.get("open_positions", {}).values():
        used += float(pos["shares"]) * float(pos["entry_price"])
    return used

def get_free_buying_power(state):
    equity = float(state.get("equity", DEFAULT_INITIAL_CAPITAL))
    used = get_used_buying_power(state)
    return max(0.0, equity - used)

def get_daily_realized_pnl_pct(state):
    d_str = today_str()
    daily_usd = state.get("daily_pnl", {}).get(d_str, 0.0)
    base_cap = max(state.get("initial_capital", DEFAULT_INITIAL_CAPITAL), 100.0)
    return daily_usd / base_cap

def recalculate_position_targets(pos, new_entry_price):
    """Recalcula Stop, TP y Trinquetes cuando el usuario introduce su precio real de Quantfury."""
    old_entry = float(pos["entry_price"])
    diff = new_entry_price - old_entry
    atr = float(pos.get("atr", abs(old_entry - float(pos["stop_loss"])) / 1.4))

    pos["entry_price"] = round(new_entry_price, 2)
    if not pos.get("be_triggered", False):
        pos["stop_loss"] = round(float(pos["stop_loss"]) + diff, 2)
        pos["initial_stop_loss"] = pos["stop_loss"]
    pos["take_profit"] = round(float(pos["take_profit"]) + diff, 2)
    pos["trail_1_trigger"] = round(new_entry_price + (2.00 * atr), 2)
    pos["trail_1_stop"] = round(new_entry_price + (0.40 * atr), 2)
    pos["trail_2_trigger"] = round(new_entry_price + (2.60 * atr), 2)
    pos["trail_2_stop"] = round(new_entry_price + (1.65 * atr), 2)
    return pos

def calculate_whole_share_sizing(state, entry_price, stop_price, high_conviction=False):
    equity = float(state.get("equity", DEFAULT_INITIAL_CAPITAL))
    free_power = get_free_buying_power(state)
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share <= 0.01 or entry_price <= 0:
        return 0, 0.0, 0.0, 0.0

    usable_free_power = free_power * FREE_POWER_SAFETY_BUFFER
    if entry_price > usable_free_power:
        return 0, 0.0, 0.0, 0.0

    target_risk_pct = HIGH_CONVICTION_RISK_PCT if high_conviction else BASE_RISK_PCT
    target_risk_usd = equity * target_risk_pct
    max_position_usd = min(equity * MAX_POSITION_ALLOCATION, usable_free_power)

    ideal_shares_by_risk = target_risk_usd / risk_per_share
    max_shares_by_capital = int(math.floor(max_position_usd / entry_price))

    if max_shares_by_capital < 1:
        if entry_price <= usable_free_power and entry_price <= (equity * 0.55):
            max_shares_by_capital = 1
        else:
            return 0, 0.0, 0.0, 0.0

    shares_floor = max(1, int(math.floor(ideal_shares_by_risk)))
    shares_ceil = max(1, int(math.ceil(ideal_shares_by_risk)))

    err_floor = abs((shares_floor * risk_per_share) - target_risk_usd)
    err_ceil = abs((shares_ceil * risk_per_share) - target_risk_usd)

    if err_ceil < err_floor and (shares_ceil * risk_per_share) <= (equity * MAX_SINGLE_RISK_CAP):
        chosen_shares = shares_ceil
    else:
        chosen_shares = shares_floor

    final_shares = min(chosen_shares, max_shares_by_capital)
    if final_shares < 1:
        return 0, 0.0, 0.0, 0.0

    actual_risk_usd = final_shares * risk_per_share
    actual_risk_pct = actual_risk_usd / equity

    if actual_risk_pct > MAX_SINGLE_RISK_CAP:
        if final_shares > 1:
            final_shares -= 1
            actual_risk_usd = final_shares * risk_per_share
            actual_risk_pct = actual_risk_usd / equity
        else:
            return 0, 0.0, 0.0, 0.0

    capital_used = final_shares * entry_price
    return int(final_shares), round(capital_used, 2), round(actual_risk_usd, 2), round(actual_risk_pct * 100, 2)

def register_closed_trade(state, symbol, exit_price, reason):
    open_pos = state.get("open_positions", {})
    if symbol not in open_pos:
        return None

    pos = open_pos.pop(symbol)
    shares = int(pos["shares"])
    entry_price = float(pos["entry_price"])
    pnl_usd = round((exit_price - entry_price) * shares, 2)
    pnl_pct_pos = round(((exit_price - entry_price) / entry_price) * 100, 2)

    state["equity"] = round(state["equity"] + pnl_usd, 2)
    state["realized_pnl"] = round(state["realized_pnl"] + pnl_usd, 2)
    state["total_trades"] += 1

    d_str = today_str()
    state["daily_pnl"][d_str] = round(state["daily_pnl"].get(d_str, 0.0) + pnl_usd, 2)

    if pnl_usd >= 0:
        state["wins"] += 1
        state["gross_profit"] = round(state["gross_profit"] + pnl_usd, 2)
    else:
        state["losses"] += 1
        state["gross_loss"] = round(state["gross_loss"] + abs(pnl_usd), 2)

    if state["equity"] > state["peak_equity"]:
        state["peak_equity"] = state["equity"]

    dd_pct = ((state["equity"] - state["peak_equity"]) / state["peak_equity"]) * 100
    if dd_pct < state["max_drawdown_pct"]:
        state["max_drawdown_pct"] = round(dd_pct, 2)

    record = {
        "symbol": symbol,
        "sector": pos.get("sector", "N/A"),
        "engine": pos.get("engine", "N/A"),
        "shares": shares,
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct_pos,
        "reason": reason,
        "closed_at": now_madrid().strftime("%Y-%m-%d %H:%M:%S")
    }
    state["trade_history"].append(record)
    if len(state["trade_history"]) > 100:
        state["trade_history"] = state["trade_history"][-100:]

    state["cooldowns"][symbol] = (now_madrid() + timedelta(minutes=35)).isoformat()
    save_state(state, push_to_git=True, msg=f"Close {symbol} PnL {pnl_usd}$")
    return record

# ==============================================================================
# CATALIZADOR SEC FORM 4 (INSIDER TRADING)
# ==============================================================================
def check_sec_form4_catalyst(symbol):
    if symbol not in SEC_CIK_MAP:
        return False
    cache = {}
    if os.path.exists(SEC_CACHE_FILE):
        try:
            with open(SEC_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    now_ts = time.time()
    if symbol in cache and (now_ts - cache[symbol].get("ts", 0)) < 14400:
        return cache[symbol].get("has_form4", False)

    cik = SEC_CIK_MAP[symbol]
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    headers = {"User-Agent": "ArgonQuantResearch bot@argonquant.org"}
    has_recent_form4 = False
    try:
        r = requests.get(url, headers=headers, timeout=6)
        if r.status_code == 200:
            data = r.json()
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            cutoff = (now_madrid() - timedelta(days=3)).strftime("%Y-%m-%d")
            for form_type, f_date in zip(forms[:35], dates[:35]):
                if form_type == "4" and f_date >= cutoff:
                    has_recent_form4 = True
                    break
    except Exception:
        pass

    cache[symbol] = {"ts": now_ts, "has_form4": has_recent_form4}
    try:
        with open(SEC_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass
    return has_recent_form4

# ==============================================================================
# DESCARGA DE DATOS Y MOTOR DE INDICADORES INSTITUCIONALES (V4.3)
# ==============================================================================
def fetch_omni_market_batch():
    tickers_to_fetch = list(set(ALL_TICKERS + [BENCHMARK]))
    try:
        raw = yf.download(
            tickers=tickers_to_fetch,
            period="5d",
            interval="5m",
            group_by="ticker",
            auto_adjust=True,
            prepost=False,
            threads=False,
            progress=False,
            timeout=15
        )
        return raw
    except Exception as e:
        print(f"[ERROR] Fallo en descarga masiva yfinance: {e}")
        return None

def compute_intraday_indicators(df):
    if df is None or df.empty or len(df) < 35:
        return None
    df = df.copy().dropna(subset=["Close", "High", "Low", "Volume"])
    if len(df) < 35:
        return None

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(TZ_MADRID)
    else:
        df.index = df.index.tz_convert(TZ_MADRID)

    df["Date"] = df.index.date
    df["Typical_Price"] = (df["High"] + df["Low"] + df["Close"]) / 3.0
    df["TP_Vol"] = df["Typical_Price"] * df["Volume"]

    # VWAP Intradia
    df["Cum_Vol"] = df.groupby("Date")["Volume"].cumsum().replace(0, np.nan)
    df["Cum_TP_Vol"] = df.groupby("Date")["TP_Vol"].cumsum()
    df["VWAP"] = df["Cum_TP_Vol"] / df["Cum_Vol"]

    # EMAs
    df["EMA_9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()

    # Fuerza de Cierre de la Vela (0.0 = cerro en el minimo, 1.0 = cerro en el maximo sin mecha)
    bar_range = (df["High"] - df["Low"]).replace(0, np.nan)
    df["Close_Strength"] = ((df["Close"] - df["Low"]) / bar_range).fillna(0.5)

    # ATR(14) Suavizado contra huecos nocturnos
    high_low = df["High"] - df["Low"]
    prev_close = df["Close"].shift(1)
    same_day = df["Date"] == df["Date"].shift(1)
    high_close = np.where(same_day, (df["High"] - prev_close).abs(), high_low * 1.15)
    low_close = np.where(same_day, (df["Low"] - prev_close).abs(), high_low * 1.15)
    tr = pd.concat([high_low, pd.Series(high_close, index=df.index), pd.Series(low_close, index=df.index)], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()

    # RSI(14)
    delta = df["Close"].diff()
    gain14 = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss14 = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs14 = gain14 / loss14.replace(0, np.nan)
    df["RSI_14"] = (100 - (100 / (1 + rs14))).fillna(50)

    # ADX(14)
    up_move = df["High"].diff()
    down_move = -df["Low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_14 = tr.rolling(14).sum().replace(0, np.nan)
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(14).sum() / tr_14)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(14).sum() / tr_14)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    df["ADX"] = dx.rolling(14).mean().fillna(20)

    # TTM Squeeze
    bb_mid = df["Close"].rolling(20).mean()
    bb_std = df["Close"].rolling(20).std()
    df["BB_Upper"] = bb_mid + (2.0 * bb_std)
    df["BB_Lower"] = bb_mid - (2.0 * bb_std)
    kc_upper = bb_mid + (1.5 * df["ATR"])
    kc_lower = bb_mid - (1.5 * df["ATR"])
    df["Squeeze_On"] = (df["BB_Upper"] < kc_upper) & (df["BB_Lower"] > kc_lower)
    df["Recent_Squeeze"] = df["Squeeze_On"].rolling(4).max() > 0

    # RVOL Normalizado de 78 velas (Sin sesgo de campana de apertura)
    vol_cap = df["Volume"].rolling(78, min_periods=20).quantile(0.85)
    capped_vol = np.minimum(df["Volume"], vol_cap)
    vol_baseline = pd.Series(capped_vol, index=df.index).rolling(50, min_periods=15).mean().replace(0, np.nan)
    df["RVOL"] = (df["Volume"] / vol_baseline).fillna(1.0)

    # Rango ORB de los primeros 15 minutos y Retorno Intradia
    today_date = df["Date"].iloc[-1]
    today_df = df[df["Date"] == today_date]
    if not today_df.empty:
        open_day = float(today_df["Open"].iloc[0])
        df["Day_Return_Pct"] = ((df["Close"] - open_day) / open_day) * 100.0
        orb_bars = today_df.iloc[:3]
        df["ORB_15_High"] = float(orb_bars["High"].max())
        df["ORB_15_Low"] = float(orb_bars["Low"].min())
    else:
        df["Day_Return_Pct"] = 0.0
        df["ORB_15_High"] = np.nan
        df["ORB_15_Low"] = np.nan

    return df

# ==============================================================================
# LOS 4 MOTORES CUANTITATIVOS V4.3 (ANTI-MECHA + PULLBACK EN LIDERES ALPHA)
# ==============================================================================
def evaluate_symbol_v4(symbol, df, qqq_ret_pct, qqq_bullish, current_time):
    if df is None or len(df) < 35:
        return None

    closed_bar = df.iloc[-2]
    prev_closed = df.iloc[-3]
    live_bar = df.iloc[-1]

    live_price = float(live_bar["Close"])
    close_p = float(closed_bar["Close"])
    open_p = float(closed_bar["Open"])
    vwap_p = float(closed_bar["VWAP"])
    close_strength = float(closed_bar["Close_Strength"])

    raw_atr = float(closed_bar["ATR"]) if not np.isnan(closed_bar["ATR"]) else (live_price * 0.008)
    atr_val = max(raw_atr, live_price * 0.0065)

    rvol = max(float(closed_bar["RVOL"]), float(live_bar["RVOL"]))
    rsi14 = float(closed_bar["RSI_14"])
    adx = float(closed_bar["ADX"])
    ema9 = float(closed_bar["EMA_9"])
    ema21 = float(closed_bar["EMA_21"])
    day_ret = float(live_bar["Day_Return_Pct"])
    alpha_vs_qqq = round(day_ret - qqq_ret_pct, 2)

    # Distancia al VWAP medida en ATRs (Evita comprar rupturas ya agotadas lejos del VWAP)
    vwap_dist_atr = (live_price - vwap_p) / atr_val if atr_val > 0 else 0.0
    not_overextended = (0.0 <= vwap_dist_atr <= MAX_VWAP_EXTENSION_ATR)
    strong_bullish_candle = (close_p > open_p and close_strength >= MIN_CANDLE_CLOSE_STRENGTH)

    alpha_decouple_ok = (alpha_vs_qqq >= 0.85 and rvol >= 1.50 and live_price > vwap_p and ema9 > ema21)
    market_regime_permission = qqq_bullish or alpha_decouple_ok

    t_val = current_time.hour + (current_time.minute / 60.0)
    signal_data = None

    # 1. MOTOR 4: Ruptura de Apertura Confirmada (ORB-15m Sin Mecha) -> 15:45 a 16:45 ESP
    if 15.75 <= t_val <= 16.75 and market_regime_permission and strong_bullish_candle and not_overextended:
        orb_high = float(closed_bar["ORB_15_High"])
        if not np.isnan(orb_high) and close_p > orb_high and live_price >= close_p * 0.999 and float(prev_closed["Close"]) <= orb_high:
            if close_p > vwap_p and alpha_vs_qqq >= 0.35 and rvol >= 1.45 and 54 <= rsi14 <= 74:
                signal_data = {
                    "engine": "Motor 4: Ruptura ORB-15m (Sin Mecha)",
                    "sl_mult": 1.40,
                    "tp_mult": 3.20,
                    "high_conviction": True
                }

    # 2. NUEVO MOTOR 3 (ESTRELLA V4.3): Pullback Institucional a EMA-9 / VWAP en Lider Alpha
    # Caza acciones lideres (Alpha >= +0.85%) que retroceden a tocar la EMA-9 y rebotan con vela fuerte
    if signal_data is None and market_regime_permission and strong_bullish_candle:
        if alpha_vs_qqq >= 0.85 and ema9 > ema21 and ema21 > vwap_p and 48 <= rsi14 <= 68:
            touched_support = (float(prev_closed["Low"]) <= ema9 * 1.002) or (float(closed_bar["Low"]) <= ema9 * 1.002)
            reclaimed_ema9 = (close_p > ema9 and live_price >= close_p * 0.999 and float(closed_bar["Low"]) > ema21 * 0.997)
            if touched_support and reclaimed_ema9 and rvol >= 1.15:
                signal_data = {
                    "engine": "Motor 3: Pullback Institucional EMA-9 (Líder Alpha)",
                    "sl_mult": 1.35,
                    "tp_mult": 3.10,
                    "high_conviction": (alpha_vs_qqq >= 1.40)
                }

    # 3. MOTOR 2: Explosion de Volatilidad (TTM Squeeze Breakout Sin Sobre-extension)
    if signal_data is None and market_regime_permission and strong_bullish_candle and not_overextended:
        if bool(closed_bar["Recent_Squeeze"]) and close_p > float(closed_bar["BB_Upper"]) and live_price >= close_p * 0.999:
            if close_p > vwap_p and alpha_vs_qqq >= 0.25 and rvol >= 1.30 and 53 <= rsi14 <= 73:
                signal_data = {
                    "engine": "Motor 2: Explosión Volatilidad (Squeeze)",
                    "sl_mult": 1.45,
                    "tp_mult": 3.20,
                    "high_conviction": (alpha_vs_qqq > 0.60 and rvol >= 1.60)
                }

    # 4. MOTOR 1: Impulso Institucional de Lider Sectorial
    if signal_data is None and market_regime_permission and strong_bullish_candle and not_overextended:
        if alpha_vs_qqq >= 0.40 and rvol >= 1.45 and adx >= 24:
            if close_p > vwap_p and ema9 > ema21 and close_p > float(prev_closed["High"]) and live_price >= close_p * 0.999 and 55 <= rsi14 <= 72:
                signal_data = {
                    "engine": "Motor 1: Impulso Líder Sectorial",
                    "sl_mult": 1.45,
                    "tp_mult": 3.10,
                    "high_conviction": (alpha_vs_qqq > 0.80 and rvol >= 1.75)
                }

    if not signal_data:
        return None

    has_form4 = check_sec_form4_catalyst(symbol)
    if has_form4:
        signal_data["high_conviction"] = True
        signal_data["engine"] += " + [SEC Form 4]"

    raw_stop_dist = signal_data["sl_mult"] * atr_val
    min_stop_dist = live_price * MIN_STOP_DISTANCE_PCT
    final_stop_dist = max(raw_stop_dist, min_stop_dist)

    stop_loss = round(live_price - final_stop_dist, 2)
    take_profit = round(live_price + max(signal_data["tp_mult"] * atr_val, final_stop_dist * 2.3), 2)

    return {
        "symbol": symbol,
        "sector": TICKER_TO_SECTOR.get(symbol, "General"),
        "engine": signal_data["engine"],
        "price": round(live_price, 2),
        "vwap": round(vwap_p, 2),
        "atr": round(atr_val, 2),
        "rvol": round(rvol, 2),
        "rsi": round(rsi14, 1),
        "alpha": alpha_vs_qqq,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "high_conviction": signal_data["high_conviction"]
    }

# ==============================================================================
# GESTION DE POSICIONES ABIERTAS (CON TRINQUETES ESTRUCTURALES EMA-9)
# ==============================================================================
def monitor_open_positions(state, market_data):
    open_pos = state.get("open_positions", {})
    if not open_pos or market_data is None:
        return

    now_m = now_madrid()
    t_val = now_m.hour + (now_m.minute / 60.0)

    if t_val >= 21.916 and state.get("eod_alert_sent_date") != today_str():
        tickers_list = ", ".join(open_pos.keys())
        send_telegram_message(
            f"⚠️ <b>ALERTA DE CIERRE INTRADÍA (21:55 ESP)</b> ⚠️\n\n"
            f"Quedan 5 minutos para el cierre de Wall Street.\n"
            f"Posiciones abiertas a cerrar en Quantfury para <b>0% riesgo nocturno</b>:\n"
            f"👉 <code>{tickers_list}</code>\n\n"
            f"Usa el botón de cierre o escribe <code>/cerrar SIMBOLO PRECIO</code>."
        )
        state["eod_alert_sent_date"] = today_str()
        save_state(state)

    for symbol in list(open_pos.keys()):
        try:
            if symbol not in market_data.columns.levels[0]:
                continue
            df_sym = compute_intraday_indicators(market_data[symbol])
            if df_sym is None or df_sym.empty:
                continue

            last_bar = df_sym.iloc[-1]
            closed_bar = df_sym.iloc[-2]
            curr_price = float(last_bar["Close"])
            bar_high = float(last_bar["High"])
            bar_low = float(last_bar["Low"])
            ema9_closed = float(closed_bar["EMA_9"])

            pos = open_pos[symbol]
            entry = float(pos["entry_price"])
            sl = float(pos["stop_loss"])
            tp = float(pos["take_profit"])
            atr = float(pos.get("atr", abs(entry - sl) / 1.4))

            # 1. Comprobar Take-Profit
            if curr_price >= tp or bar_high >= tp:
                exit_p = max(curr_price, tp)
                rec = register_closed_trade(state, symbol, exit_p, "🎯 TAKE-PROFIT ALCANZADO")
                if rec:
                    send_telegram_message(
                        f"🎯 <b>¡TAKE-PROFIT ALCANZADO EN {symbol}!</b> 🎯\n\n"
                        f"• <b>Acciones a vender en Quantfury:</b> <code>{rec['shares']} acciones</code>\n"
                        f"• <b>Entrada:</b> <code>{rec['entry_price']:.2f} $</code> | <b>Salida:</b> <code>{rec['exit_price']:.2f} $</code>\n"
                        f"• <b>Beneficio Neto:</b> <b>+{rec['pnl_usd']:.2f} $ (+{rec['pnl_pct']:.2f}%)</b>\n"
                        f"• <b>Nuevo Capital Total:</b> <code>{state['equity']:,.2f} $</code>"
                    )
                continue

            # 2. Comprobar Stop-Loss (Inicial o Trinquetes 1 y 2)
            if curr_price <= sl or bar_low <= sl:
                exit_p = min(curr_price, sl)
                if pos.get("trail2_triggered"):
                    reason_label = "🔒 SALIDA EN TRINQUETE NIVEL 2 (BENEFICIO ASEGURADO)"
                elif pos.get("be_triggered"):
                    reason_label = "🛡️ SALIDA EN ESCUDO BREAK-EVEN (TRINQUETE 1)"
                else:
                    reason_label = "🛑 STOP-LOSS EJECUTADO"

                rec = register_closed_trade(state, symbol, exit_p, reason_label)
                if rec:
                    sign = "+" if rec["pnl_usd"] >= 0 else ""
                    send_telegram_message(
                        f"🔔 <b>{reason_label}: {symbol}</b>\n\n"
                        f"• <b>Acciones cerradas:</b> <code>{rec['shares']} acciones</code>\n"
                        f"• <b>Entrada:</b> <code>{rec['entry_price']:.2f} $</code> | <b>Salida Stop:</b> <code>{rec['exit_price']:.2f} $</code>\n"
                        f"• <b>Resultado Neto:</b> <b>{sign}{rec['pnl_usd']:.2f} $ ({sign}{rec['pnl_pct']:.2f}%)</b>\n"
                        f"• <b>Capital Actual:</b> <code>{state['equity']:,.2f} $</code>"
                    )
                continue

            # 3. Trinquete Nivel 1: Espera +2.00x ATR de avance real para no ahogar el trade (Stop a +0.40x ATR)
            be_trigger = float(pos.get("trail_1_trigger", entry + (2.00 * atr)))
            new_be_stop = float(pos.get("trail_1_stop", round(entry + (0.40 * atr), 2)))
            if not pos.get("be_triggered", False) and (curr_price >= be_trigger or bar_high >= be_trigger):
                pos["be_triggered"] = True
                pos["stop_loss"] = new_be_stop
                save_state(state, push_to_git=True, msg=f"Trail 1 active {symbol}")
                send_telegram_message(
                    f"🛡️ <b>ESCUDO BREAK-EVEN ACTIVADO EN {symbol}</b> 🛡️\n\n"
                    f"El precio ya avanza +2.0x ATR (<code>{curr_price:.2f} $</code>).\n"
                    f"👉 <b>ACCIÓN EN QUANTFURY:</b> Sube tu Stop Order de <code>{symbol}</code> a <b><code>{new_be_stop:.2f} $</code></b>.\n"
                    f"<i>(Si compraste más caro que {entry:.2f} $, pon primero <code>/entrada {symbol} PRECIO</code>)</i>"
                )

            # 4. Trinquete Nivel 2 Estructural (+2.60x ATR -> Stop a +1.65x ATR o bajo EMA-9)
            trail2_trigger = float(pos.get("trail_2_trigger", entry + (2.60 * atr)))
            structural_stop = round(max(entry + (1.65 * atr), min(ema9_closed * 0.998, curr_price - (0.8 * atr))), 2)
            if not pos.get("trail2_triggered", False) and (curr_price >= trail2_trigger or bar_high >= trail2_trigger):
                pos["trail2_triggered"] = True
                pos["stop_loss"] = structural_stop
                save_state(state, push_to_git=True, msg=f"Trail 2 active {symbol}")
                send_telegram_message(
                    f"🔒 <b>TRINQUETE ESTRUCTURAL (NIVEL 2) EN {symbol}</b> 🔒\n\n"
                    f"Cotizando en <code>{curr_price:.2f} $</code>.\n"
                    f"👉 <b>ACCIÓN EN QUANTFURY:</b> Sube tu Stop Order a <b><code>{structural_stop:.2f} $</code></b> para amarrar el grueso de la subida."
                )

        except Exception as e:
            print(f"[WARN] Error vigilando {symbol}: {e}")

# ==============================================================================
# PROCESADOR DE COMANDOS Y BOTONES INTERACTIVOS DE TELEGRAM
# ==============================================================================
def build_portfolio_report(state, market_data=None):
    eq = state["equity"]
    init_c = state["initial_capital"]
    net_pnl = eq - init_c
    net_pct = (net_pnl / init_c) * 100 if init_c > 0 else 0.0
    total_t = state["total_trades"]
    wins = state["wins"]
    losses = state["losses"]
    wr = (wins / total_t * 100) if total_t > 0 else 0.0
    pf = (state["gross_profit"] / state["gross_loss"]) if state["gross_loss"] > 0 else state["gross_profit"]
    free_bp = get_free_buying_power(state)

    open_pos = state.get("open_positions", {})
    lines = [
        f"📊 <b>LIBRO CONTABLE QUANTFURY (SNIPER v4.3)</b>",
        f"• <b>Poder Total:</b> <code>{eq:,.2f} $</code> | <b>Libre:</b> <code>{free_bp:,.2f} $</code>",
        f"• <b>Beneficio/Pérdida Neta:</b> <code>{net_pnl:+,.2f} $ ({net_pct:+.2f}%)</code>",
        f"• <b>Operaciones Cerradas:</b> <code>{total_t} ({wins}W / {losses}L)</code>",
        f"• <b>Win Rate Real:</b> <code>{wr:.1f}%</code> | <b>Profit Factor:</b> <code>{pf:.2f}</code>",
        f"• <b>Max Drawdown:</b> <code>{state['max_drawdown_pct']:.2f}%</code>",
        f"• <b>Abiertas ahora mismo:</b> <code>{len(open_pos)}/{MAX_OPEN_POSITIONS}</code>"
    ]

    if open_pos:
        lines.append("\n📂 <b>POSICIONES ABIERTAS EN CURSO:</b>")
        for sym, p in open_pos.items():
            sh = p["shares"]
            ep = p["entry_price"]
            cur_p = ep
            if market_data is not None and sym in market_data.columns.levels[0]:
                df_s = market_data[sym].dropna(subset=["Close"])
                if not df_s.empty:
                    cur_p = float(df_s["Close"].iloc[-1])
            u_pnl = (cur_p - ep) * sh
            shield_tag = "🛡️T1" if p.get("be_triggered") else ""
            if p.get("trail2_triggered"):
                shield_tag = "🔒T2"
            lines.append(
                f"  ▸ <b>{sym}</b> {shield_tag}: <code>{sh} acc.</code> | Ent: <code>{ep:.2f}$</code> | Act: <code>{cur_p:.2f}$</code> (<b>{u_pnl:+.2f}$</b>) | Stop: <code>{p['stop_loss']:.2f}$</code>"
            )
    return "\n".join(lines)

def build_radar_report(market_data):
    if market_data is None:
        return "⏳ Descargando datos de mercado..."
    try:
        df_qqq = compute_intraday_indicators(market_data[BENCHMARK])
        if df_qqq is None:
            return "⚠️ Sin datos de QQQ."
        qqq_last = df_qqq.iloc[-1]
        qqq_ret = float(qqq_last["Day_Return_Pct"])
        qqq_bull = float(qqq_last["Close"]) > float(qqq_last["VWAP"])

        rankings = []
        for sym in ALL_TICKERS:
            if sym not in market_data.columns.levels[0]:
                continue
            df_s = compute_intraday_indicators(market_data[sym])
            if df_s is None or len(df_s) < 3:
                continue
            closed_b = df_s.iloc[-2]
            live_b = df_s.iloc[-1]
            alpha = float(live_b["Day_Return_Pct"]) - qqq_ret
            rvol = max(float(closed_b["RVOL"]), float(live_b["RVOL"]))
            rankings.append({
                "sym": sym,
                "sector": TICKER_TO_SECTOR.get(sym, ""),
                "price": float(live_b["Close"]),
                "alpha": alpha,
                "rvol": rvol,
                "rsi": float(closed_b["RSI_14"]),
                "sqz": bool(closed_b["Recent_Squeeze"]),
                "above_vwap": float(live_b["Close"]) > float(closed_b["VWAP"])
            })

        rankings.sort(key=lambda x: x["alpha"], reverse=True)
        top = rankings[:10]
        regime_str = "🟢 ALCISTA (>VWAP)" if qqq_bull else "🟡 DESACOPLE ALPHA (QQQ < VWAP)"

        msg = [
            f"🔭 <b>RADAR SNIPER V4.3 EN VIVO</b>",
            f"• <b>Oráculo QQQ:</b> <code>{qqq_ret:+.2f}% hoy</code> | {regime_str}",
            f"\n🏆 <b>TOP 10 LÍDERES DE FUERZA RELATIVA:</b>"
        ]
        for r in top:
            vw_icon = "🟢>VWAP" if r["above_vwap"] else "🔴<VWAP"
            sq_icon = "🔥Sqz" if r["sqz"] else ""
            msg.append(
                f"• <b>{r['sym']}</b> ({r['sector']}): <code>{r['price']:.2f}$</code> | Alpha <code>{r['alpha']:+.2f}%</code> | RVOL <code>{r['rvol']:.1f}x</code> | RSI <code>{r['rsi']:.0f}</code> | {vw_icon} {sq_icon}"
            )
        return "\n".join(msg)
    except Exception as e:
        return f"⚠️ Error generando radar: {e}"

def process_telegram_updates(state, signals, market_data=None):
    if not TELEGRAM_BOT_TOKEN:
        return
    offset = state.get("last_update_id", 0) + 1
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": 2}, timeout=6)
        data = r.json()
        if not data.get("ok"):
            return
    except Exception:
        return

    for upd in data.get("result", []):
        upd_id = upd["update_id"]
        if upd_id > state.get("last_update_id", 0):
            state["last_update_id"] = upd_id

        if "message" in upd and "text" in upd["message"]:
            text = upd["message"]["text"].strip()
            low = text.lower()

            if low.startswith("/cartera") or low.startswith("/estado") or low.startswith("/start"):
                send_telegram_message(build_portfolio_report(state, market_data))

            elif low.startswith("/escanear") or low.startswith("/radar"):
                send_telegram_message(build_radar_report(market_data))

            elif low.startswith("/historial"):
                hist = state.get("trade_history", [])[-10:]
                if not hist:
                    send_telegram_message("📜 Aún no hay operaciones cerradas en el historial.")
                else:
                    lines = ["📜 <b>ÚLTIMAS 10 OPERACIONES CERRADAS:</b>"]
                    for h in reversed(hist):
                        ic = "🟢" if h["pnl_usd"] >= 0 else "🔴"
                        lines.append(
                            f"{ic} <b>{h['symbol']}</b> ({h['shares']} acc.): <code>{h['entry_price']:.2f}$ -> {h['exit_price']:.2f}$</code> | <b>{h['pnl_usd']:+.2f}$ ({h['pnl_pct']:+.2f}%)</b> [{h['reason']}]"
                        )
                    send_telegram_message("\n".join(lines))

            elif low.startswith("/entrada") or low.startswith("/precio"):
                parts = text.split()
                if len(parts) >= 3:
                    sym = parts[1].upper()
                    if sym in state.get("open_positions", {}):
                        try:
                            real_entry = float(parts[2].replace(",", "."))
                            pos = recalculate_position_targets(state["open_positions"][sym], real_entry)
                            save_state(state, push_to_git=True, msg=f"Update real entry {sym} to {real_entry}")
                            send_telegram_message(
                                f"✅ <b>PRECIO DE ENTRADA REAL ACTUALIZADO EN {sym}</b>\n\n"
                                f"• <b>Tu Entrada Real:</b> <code>{pos['entry_price']:.2f} $</code>\n"
                                f"• <b>Stop-Loss Ajustado:</b> <code>{pos['stop_loss']:.2f} $</code>\n"
                                f"• <b>Take-Profit Ajustado:</b> <code>{pos['take_profit']:.2f} $</code>\n"
                                f"• <b>Escudo Break-Even (+2.0 ATR):</b> Saltará en <code>{pos['trail_1_trigger']:.2f} $</code> -> Stop a <b><code>{pos['trail_1_stop']:.2f} $</code></b>"
                            )
                        except ValueError:
                            send_telegram_message("⚠️ Formato: <code>/entrada SIMBOLO PRECIO</code>")
                    else:
                        send_telegram_message(f"⚠️ No tienes posición abierta en <code>{sym}</code>.")
                else:
                    send_telegram_message("ℹ️ Uso: <code>/entrada SIMBOLO PRECIO_REAL</code>")

            elif low.startswith("/capital"):
                parts = text.split()
                if len(parts) >= 2:
                    try:
                        new_cap = float(parts[1].replace(",", "."))
                        if new_cap >= 100:
                            state["initial_capital"] = round(new_cap, 2)
                            state["equity"] = round(new_cap, 2)
                            state["peak_equity"] = round(new_cap, 2)
                            save_state(state, push_to_git=True, msg=f"Update capital to {new_cap}$")
                            send_telegram_message(
                                f"✅ <b>PODER DE TRADING ACTUALIZADO</b>\n"
                                f"• Nuevo Capital: <code>{new_cap:,.2f} $</code>\n"
                                f"• Riesgo Base Protegido (0.60%): <code>{new_cap * BASE_RISK_PCT:.2f} $</code>"
                            )
                    except ValueError:
                        send_telegram_message("⚠️ Formato: <code>/capital 1988.46</code>")

            elif low.startswith("/ajustar"):
                parts = text.split()
                if len(parts) >= 3:
                    sym = parts[1].upper()
                    try:
                        new_sh = int(float(parts[2]))
                        if sym in state.get("open_positions", {}) and new_sh >= 1:
                            state["open_positions"][sym]["shares"] = new_sh
                            save_state(state, push_to_git=True, msg=f"Adjust {sym} shares to {new_sh}")
                            send_telegram_message(f"✅ <b>{sym}</b> ajustada a <b><code>{new_sh} acciones</code></b>.")
                    except ValueError:
                        pass

            elif low.startswith("/cerrar"):
                parts = text.split()
                if len(parts) >= 2:
                    sym = parts[1].upper()
                    if sym in state.get("open_positions", {}):
                        pos = state["open_positions"][sym]
                        exit_p = float(pos["stop_loss"])
                        if len(parts) >= 3:
                            try:
                                exit_p = float(parts[2].replace(",", "."))
                            except ValueError:
                                pass
                        rec = register_closed_trade(state, sym, exit_p, "👤 CIERRE MANUAL / STOP EN QUANTFURY")
                        if rec:
                            ic = "🟢" if rec["pnl_usd"] >= 0 else "🔴"
                            send_telegram_message(
                                f"{ic} <b>CIERRE REGISTRADO EN {sym}</b>\n"
                                f"• <b>Acciones:</b> <code>{rec['shares']}</code> | <b>Entrada:</b> <code>{rec['entry_price']:.2f}$</code> -> <b>Salida:</b> <code>{rec['exit_price']:.2f}$</code>\n"
                                f"• <b>PnL Real:</b> <b>{rec['pnl_usd']:+.2f} $ ({rec['pnl_pct']:+.2f}%)</b>\n"
                                f"• <b>Capital Actualizado:</b> <code>{state['equity']:,.2f} $</code>"
                            )
                    else:
                        send_telegram_message(f"⚠️ No hay posición abierta en <code>{sym}</code>.")

        if "callback_query" in upd:
            cb = upd["callback_query"]
            cb_id = cb["id"]
            cb_data = cb.get("data", "")
            msg_obj = cb.get("message", {})
            chat_id = msg_obj.get("chat", {}).get("id")
            msg_id = msg_obj.get("message_id")

            if cb_data.startswith("EXEC|"):
                sig_id = cb_data.split("|")[1]
                sig = signals.get(sig_id)
                if not sig:
                    answer_callback_query(cb_id, "⚠️ Señal expirada.")
                    continue
                sym = sig["symbol"]
                if sym in state.get("open_positions", {}):
                    answer_callback_query(cb_id, f"Ya tienes {sym} abierta.")
                    continue

                atr_v = float(sig["atr"])
                ep = float(sig["price"])
                state["open_positions"][sym] = {
                    "symbol": sym,
                    "sector": sig["sector"],
                    "engine": sig["engine"],
                    "shares": int(sig["shares"]),
                    "entry_price": ep,
                    "stop_loss": float(sig["stop_loss"]),
                    "initial_stop_loss": float(sig["stop_loss"]),
                    "take_profit": float(sig["take_profit"]),
                    "atr": atr_v,
                    "trail_1_trigger": round(ep + (2.00 * atr_v), 2),
                    "trail_1_stop": round(ep + (0.40 * atr_v), 2),
                    "trail_2_trigger": round(ep + (2.60 * atr_v), 2),
                    "trail_2_stop": round(ep + (1.65 * atr_v), 2),
                    "be_triggered": False,
                    "trail2_triggered": False,
                    "opened_at": now_madrid().strftime("%Y-%m-%d %H:%M:%S")
                }
                save_state(state, push_to_git=True, msg=f"Open position {sym} ({sig['shares']} sh)")
                answer_callback_query(cb_id, f"✅ {sym} registrada ({sig['shares']} acciones)")

                close_kb = {
                    "inline_keyboard": [[
                        {"text": f"🔴 Cerré {sym} en Quantfury (Precio Actual)", "callback_data": f"CLOSE|{sym}"}
                    ]]
                }
                edit_telegram_message(
                    chat_id, msg_id,
                    msg_obj.get("text", "") + f"\n\n✅ <b>POSICIÓN ABIERTA ({sig['shares']} ACCIONES)</b>\n<i>💡 Si tu precio en Quantfury varió, escribe:</i> <code>/entrada {sym} PRECIO</code>",
                    reply_markup=close_kb
                )

            elif cb_data.startswith("DISCARD|"):
                sig_id = cb_data.split("|")[1]
                signals.pop(sig_id, None)
                save_signals(signals)
                answer_callback_query(cb_id, "Señal descartada.")
                edit_telegram_message(chat_id, msg_id, "🗑️ <i>Señal descartada manualmente.</i>")

            elif cb_data.startswith("CLOSE|"):
                sym = cb_data.split("|")[1]
                if sym not in state.get("open_positions", {}):
                    answer_callback_query(cb_id, f"{sym} ya está cerrada.")
                    continue
                pos = state["open_positions"][sym]
                exit_p = float(pos["entry_price"])
                if market_data is not None and sym in market_data.columns.levels[0]:
                    df_s = market_data[sym].dropna(subset=["Close"])
                    if not df_s.empty:
                        exit_p = float(df_s["Close"].iloc[-1])

                rec = register_closed_trade(state, sym, exit_p, "👤 CIERRE MANUAL POR BOTÓN")
                answer_callback_query(cb_id, f"Cerrada {sym}")
                if rec:
                    edit_telegram_message(
                        chat_id, msg_id,
                        f"🏁 <b>POSICIÓN CERRADA EN {sym}</b>\n"
                        f"• <b>Acciones:</b> <code>{rec['shares']}</code> | <b>Salida:</b> <code>{rec['exit_price']:.2f} $</code>\n"
                        f"• <b>Resultado Neto:</b> <b>{rec['pnl_usd']:+.2f} $ ({rec['pnl_pct']:+.2f}%)</b>\n"
                        f"• <b>Capital Actual:</b> <code>{state['equity']:,.2f} $</code>"
                    )

# ==============================================================================
# ESCANER FRANCOTIRADOR V4.3 (1 DISPARO CADA 10 MIN | SIN MECHAS | LIDER #1)
# ==============================================================================
def is_entry_window_open(curr_time):
    t = curr_time.hour + (curr_time.minute / 60.0)
    morning_ok = (15.75 <= t < 18.00)
    afternoon_ok = (19.75 <= t <= 21.25)
    return morning_ok or afternoon_ok

def scan_market_and_alert(state, signals, market_data):
    if market_data is None:
        return
    now_m = now_madrid()

    if not is_entry_window_open(now_m):
        return

    if get_daily_realized_pnl_pct(state) <= MAX_DAILY_DRAWDOWN:
        return

    open_pos = state.get("open_positions", {})
    if len(open_pos) >= MAX_OPEN_POSITIONS:
        return

    if (time.time() - float(state.get("last_global_signal_ts", 0))) < SIGNAL_COOLDOWN_SECONDS:
        return

    try:
        df_qqq = compute_intraday_indicators(market_data[BENCHMARK])
        if df_qqq is None:
            return
        qqq_last = df_qqq.iloc[-1]
        qqq_ret = float(qqq_last["Day_Return_Pct"])
        qqq_bullish = (float(qqq_last["Close"]) > float(qqq_last["VWAP"])) or (float(qqq_last["EMA_9"]) > float(qqq_last["EMA_21"]))
    except Exception:
        return

    candidates = []

    for symbol in ALL_TICKERS:
        if symbol in open_pos:
            continue

        cd_iso = state.get("cooldowns", {}).get(symbol)
        if cd_iso:
            try:
                if now_m < datetime.fromisoformat(cd_iso):
                    continue
            except Exception:
                pass

        sector = TICKER_TO_SECTOR.get(symbol, "General")
        sector_count = sum(1 for p in open_pos.values() if p.get("sector") == sector)
        if sector_count >= MAX_PER_SECTOR:
            continue

        if symbol not in market_data.columns.levels[0]:
            continue

        df_sym = compute_intraday_indicators(market_data[symbol])
        eval_res = evaluate_symbol_v4(symbol, df_sym, qqq_ret, qqq_bullish, now_m)
        if not eval_res:
            continue

        shares, cap_used, risk_usd, risk_pct = calculate_whole_share_sizing(
            state,
            eval_res["price"],
            eval_res["stop_loss"],
            high_conviction=eval_res["high_conviction"]
        )
        if shares < 1:
            continue

        eval_res["shares"] = shares
        eval_res["cap_used"] = cap_used
        eval_res["risk_usd"] = risk_usd
        eval_res["risk_pct"] = risk_pct
        candidates.append(eval_res)

    if not candidates:
        return

    candidates.sort(key=lambda x: (1 if x["high_conviction"] else 0, x["alpha"], x["rvol"]), reverse=True)
    best = candidates[0]
    symbol = best["symbol"]

    sig_id = f"{symbol}_{int(time.time())}"
    signals[sig_id] = best
    save_signals(signals)

    state["cooldowns"][symbol] = (now_m + timedelta(minutes=30)).isoformat()
    state["last_global_signal_ts"] = time.time()
    save_state(state)

    conv_tag = "🔥 <b>ALTA CONVICCIÓN (0.80% RIESGO)</b>" if best["high_conviction"] else "✅ <b>ESTÁNDAR PROTEGIDO (0.60% RIESGO)</b>"
    free_after = max(0.0, get_free_buying_power(state) - best["cap_used"])

    msg_text = (
        f"🎯 <b>SEÑAL SNIPER V4.3 — {symbol} ({best['sector']})</b> 🎯\n"
        f"{conv_tag}\n"
        f"• <b>Motor:</b> <code>{best['engine']}</code>\n\n"
        f"🛒 <b>ORDEN EXACTA EN QUANTFURY:</b>\n"
        f"• <b>Acción:</b> <b>COMPRAR (LONG) <code>{best['shares']} acciones</code></b> de <b>{symbol}</b>\n"
        f"• <b>Precio Entrada Ref.:</b> <code>{best['price']:.2f} $</code>\n"
        f"• <b>Capital Usado:</b> <code>{best['cap_used']:,.2f} $</code> <i>(Quedarán {free_after:,.0f}$ libres)</i>\n\n"
        f"🛡️ <b>PARÁMETROS DE PROTECCIÓN (Colchón 120$ Blindado):</b>\n"
        f"• 🛑 <b>Stop Order (Stop-Loss):</b> <b><code>{best['stop_loss']:.2f} $</code></b> <i>(-{best['risk_usd']:.2f} $ | {best['risk_pct']:.2f}%)</i>\n"
        f"• 🎯 <b>Target Order (Take-Profit):</b> <b><code>{best['take_profit']:.2f} $</code></b>\n\n"
        f"📊 <b>Métricas Institucionales:</b>\n"
        f"• Alpha vs QQQ: <code>{best['alpha']:+.2f}%</code> | RVOL: <code>{best['rvol']:.2f}x</code> | RSI: <code>{best['rsi']:.1f}</code>"
    )

    kb = {
        "inline_keyboard": [[
            {"text": f"🟢 ENTRÉ ({best['shares']} acc.)", "callback_data": f"EXEC|{sig_id}"},
            {"text": "🗑️ Descartar", "callback_data": f"DISCARD|{sig_id}"}
        ]]
    }
    send_telegram_message(msg_text, reply_markup=kb)

# ==============================================================================
# BUCLE CONTINUO DE SESION
# ==============================================================================
def run_continuous_session():
    state = load_state()
    signals = load_signals()

    now_m = now_madrid()
    free_bp = get_free_buying_power(state)
    send_telegram_message(
        f"🟢 <b>OMNI-SCANNER SNIPER V4.3 EN LÍNEA ({now_m.strftime('%H:%M:%S')} ESP)</b>\n"
        f"• <b>Poder Total:</b> <code>{state['equity']:,.2f} $</code> | <b>Libre:</b> <code>{free_bp:,.2f} $</code>\n"
        f"• <b>Escudo Activo:</b> <code>Pullback EMA-9 | Sin Mechas | Riesgo 12$-16$</code>\n"
        f"• <b>Comandos:</b> <code>/cartera</code>, <code>/radar</code>, <code>/entrada SIMB PRECIO</code>, <code>/cerrar SIMB PRECIO</code>"
    )

    last_scan_ts = 0
    cached_market_data = None

    while True:
        now_m = now_madrid()
        t_val = now_m.hour + (now_m.minute / 60.0)

        if t_val >= 22.04:
            save_state(state, push_to_git=True, msg="EOD Argon Quant v4.3 sync")
            send_telegram_message(
                f"🏁 <b>SESIÓN CERRADA EN WALL STREET (22:02 ESP)</b>\n\n"
                + build_portfolio_report(state, cached_market_data)
            )
            break

        process_telegram_updates(state, signals, cached_market_data)

        if time.time() - last_scan_ts >= 60:
            last_scan_ts = time.time()
            if 15.45 <= t_val <= 22.02:
                cached_market_data = fetch_omni_market_batch()
                if cached_market_data is not None:
                    monitor_open_positions(state, cached_market_data)
                    scan_market_and_alert(state, signals, cached_market_data)

        time.sleep(3)

if __name__ == "__main__":
    run_continuous_session()
