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
# CONFIGURACION INSTITUCIONAL — ARGON QUANT V5.5 (EARLY-IGNITION & FUEL GAUGE)
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = "portfolio_state.json"
SIGNALS_FILE = "active_signals.json"
SEC_CACHE_FILE = "sec_insider_cache.json"
TZ_MADRID = pytz.timezone("Europe/Madrid")

# Gestion de Capital Armonizada: 1R Constante (~4.20$ - 5.20$ en tramo 2.000$)
DEFAULT_INITIAL_CAPITAL = 2000.0
BASE_RISK_PCT = 0.0021              # 0.21% del poder = 4.20$ de riesgo base uniforme
HIGH_CONVICTION_RISK_PCT = 0.0026   # 0.26% del poder = 5.20$ en alta conviccion
MAX_SINGLE_RISK_CAP = 0.0038        # 0.38% tope maximo por redondeo de 1 accion (7.60$)
MAX_POSITION_ALLOCATION = 0.245     # 24.5% del poder maximo por posicion (~490$)
FREE_POWER_SAFETY_BUFFER = 0.96     # Usa hasta el 96% del poder libre disponible
MAX_OPEN_POSITIONS = 3              # Maximo 3 posiciones simultaneas
MAX_PER_SECTOR = 1                  # 1 posicion por sector (diversificacion real)
MAX_DAILY_DRAWDOWN = -0.0085        # Cortafuegos diario real: -0.85% (-17.00$ sobre 2.000$)

# Geometria Anti-Lateral de 3 Trinquetes en Multiplos de R (1R = Distancia Entrada a Stop Inicial)
TARGET_TP_R_MULT = 2.40             # Take-Profit completo a +2.40R (~+10.10$)
TRAIL_0_TRIGGER_R = 1.15            # Nivel 0 (Anti-Lateral): Al ganar +1.15R (~+4.80$), protege la posicion
TRAIL_0_LOCK_R = 0.25               # Nivel 0 asegura +0.25R (+1.05$ limpios) dejando 0.90R de espacio contra ruido
TRAIL_1_TRIGGER_R = 1.65            # Nivel 1: Al ganar +1.65R (~+6.90$), blinda beneficio fuerte
TRAIL_1_LOCK_R = 0.90               # Nivel 1 asegura +0.90R (~+3.80$ limpios) dejando 0.75R de aire
TRAIL_2_TRIGGER_R = 2.05            # Nivel 2: Al ganar +2.05R (~+8.60$), acorrala el precio
TRAIL_2_LOCK_R = 1.50               # Nivel 2 asegura +1.50R (~+6.30$ limpios)

# Filtros de Ignicion Temprana y Medidor de Gasolina Diaria (Evita comprar acciones ya muy subidas)
MIN_STOP_DISTANCE_PCT = 0.0082      # Stop-Loss minimo 0.82% (mas ceñido al entrar en el inicio del impulso)
MAX_PANIC_ATR_PCT = 0.0135          # Si una accion tiene ATR > 1.35% cada 5m (Crash), bloquea por panico
MAX_CLIMAX_BAR_ATR_MULT = 2.40      # Prohibido comprar velas gigantes ya estiradas (>2.4x ATR en 5m)
MAX_ADR_USED_PCT = 72.0             # PROHIBIDO comprar si la accion ya consumio >72% de su rango diario (ADR)
MAX_VWAP_EXTENSION_ATR = 1.65       # Rebajado de 2.20 a 1.65: prohibe comprar lejos del VWAP (solo cerca de la base)
IGNITION_MAX_VWAP_ATR = 0.65        # Motor 1 (Ignicion Temprana): compra pegado al VWAP (<= 0.65x ATR)
PULLBACK_MAX_EMA9_ATR = 0.70        # Motor 2 (Pullback Temprano): compra pegado a la EMA-9 (<= 0.70x ATR)
BREAKOUT_MAX_EMA9_ATR = 1.20        # Motores 3 y 4: solo permite rupturas frescas que nazcan cerca de la base
MAX_ENTRY_CHASE_R = 0.22            # Banda maxima para no perseguir precio en la app (+0.22R)
SIGNAL_COOLDOWN_SECONDS = 600       # 10 minutos de enfriamiento entre compras
MIN_CANDLE_CLOSE_STRENGTH = 0.60    # Vela cerrando en el 40% superior de su rango (sin mecha de rechazo)
MIN_MARKET_BREADTH_PCT = 42.0       # Amplitud estandar de mercado (% de activos sobre su VWAP)

# Universo Universal de 55 Gigantes Liquidos (Todos los Sectores Institucionales)
SECTOR_UNIVERSE = {
    "Semiconductores_IA": ["NVDA", "AMD", "AVGO", "MU", "ARM", "ANET", "TSM", "VRT", "LRCX", "KLAC", "QCOM", "TXN"],
    "Software_Cloud_Cyber": ["PLTR", "APP", "NOW", "CRWD", "PANW", "NET", "DDOG", "SNOW", "SHOP", "CRM", "ORCL", "ADBE"],
    "Big_Tech_Comunicacion": ["META", "NFLX", "AMZN", "GOOGL", "AAPL", "MSFT", "SPOT", "TTD", "DIS"],
    "Banca_Fintech_Cripto": ["JPM", "GS", "MS", "AXP", "V", "MA", "COIN", "MSTR", "HOOD", "PYPL"],
    "Energia_Industria_Defensa": ["VST", "CEG", "GEV", "GE", "CAT", "ETN", "XOM", "CVX"],
    "Salud_Consumo_Movilidad": ["LLY", "ISRG", "VRTX", "UNH", "ABBV", "COST", "WMT", "HD", "TSLA", "UBER", "BKNG"]
}

ALL_TICKERS = list(dict.fromkeys([ticker for sector in SECTOR_UNIVERSE.values() for ticker in sector]))
TICKER_TO_SECTOR = {ticker: sector for sector, tickers in SECTOR_UNIVERSE.items() for ticker in tickers}
BENCHMARK = "QQQ"

SEC_CIK_MAP = {
    "NVDA": "0001045810", "AMD": "0000002488", "AVGO": "0001730168", "MU": "0000723125",
    "ANET": "0001596532", "VRT": "0001674101", "LRCX": "0000707549", "KLAC": "0000319201",
    "PLTR": "0001321655", "APP": "0001751008", "NOW": "0001373715", "CRWD": "0001535527",
    "PANW": "0001327567", "NET": "0001477333", "DDOG": "0001561550", "CRM": "0001108524",
    "ORCL": "0001341439", "META": "0001326801", "NFLX": "0001065280", "AMZN": "0001018724",
    "GOOGL": "0001652044", "AAPL": "0000320193", "MSFT": "0000789019", "TTD": "0001671933",
    "MSTR": "0001050446", "COIN": "0001679788", "HOOD": "0001783879", "AXP": "0000004962",
    "PYPL": "0001633917", "VST": "0001692819", "CEG": "0001868275", "GEV": "0001996810",
    "XOM": "0000034088", "CAT": "0000018230", "LLY": "0000059478", "ISRG": "0001035267",
    "VRTX": "0000875320", "UNH": "0000731766", "JPM": "0000019617", "GS": "0000886982",
    "COST": "0000909832", "TSLA": "0001318605", "UBER": "0001543151"
}

# ==============================================================================
# UTILIDADES DE TIEMPO Y PERSISTENCIA GIT BLINDADA (CON AUTOSTASH)
# ==============================================================================
def now_madrid():
    return datetime.now(TZ_MADRID)

def today_str():
    return now_madrid().strftime("%Y-%m-%d")

def git_sync_state(commit_msg="Auto-sync Argon Quant v5.5"):
    try:
        subprocess.run(["git", "config", "--global", "user.name", "argon-quant-bot"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "bot@argonquant.local"], check=False)
        subprocess.run(["git", "add", STATE_FILE, SIGNALS_FILE], check=False)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if status.stdout.strip():
            subprocess.run(["git", "commit", "-m", commit_msg], check=False)
            pull_res = subprocess.run(
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                check=False, timeout=15
            )
            if pull_res.returncode != 0:
                subprocess.run(["git", "rebase", "--abort"], check=False)
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
            state.setdefault("consecutive_losses", 0)
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
        "consecutive_losses": 0,
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

def save_state(state, push_to_git=False, msg="Sync portfolio state v5.5"):
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
                sigs = json.load(f)
            # Limpia senales antiguas (>12h) para mantener el archivo ligero
            now_ts = time.time()
            clean_sigs = {k: v for k, v in sigs.items() if (now_ts - float(v.get("signal_ts", now_ts))) < 43200}
            return clean_sigs
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
# COMUNICACION TELEGRAM (CON FALLBACK ANTI-ERROR HTML)
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
        res = r.json()
        if not res.get("ok"):
            clean_text = (
                text.replace("<b>", "").replace("</b>", "")
                .replace("<code>", "").replace("</code>", "")
                .replace("<i>", "").replace("</i>", "")
                .replace("&gt;", ">").replace("&lt;", "<")
            )
            payload["text"] = clean_text
            payload.pop("parse_mode", None)
            r2 = requests.post(url, data=payload, timeout=10)
            return r2.json()
        return res
    except Exception as e:
        print(f"[WARN] Telegram send error: {e}")
        return None

def answer_callback_query(callback_id, text="", show_alert=False):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    try:
        requests.post(
            url,
            data={"callback_query_id": callback_id, "text": text, "show_alert": show_alert},
            timeout=8
        )
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
# CONTABILIDAD ARMONIZADA Y DIMENSIONAMIENTO DE RIESGO CONSTANTE (1R UNIFORME)
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

def get_floating_pnl_usd(state, indicator_cache=None):
    if not indicator_cache:
        return 0.0
    floating = 0.0
    for sym, pos in state.get("open_positions", {}).items():
        df_s = indicator_cache.get(sym)
        if df_s is not None and not df_s.empty:
            curr_p = float(df_s["Close"].iloc[-1])
            floating += (curr_p - float(pos["entry_price"])) * int(pos["shares"])
    return floating

def has_losing_open_position(state, indicator_cache):
    """Cerrojo Anti-Cascada: True si alguna posicion abierta va perdiendo mas de -0.25R."""
    if not indicator_cache:
        return False
    for sym, pos in state.get("open_positions", {}).items():
        df_s = indicator_cache.get(sym)
        if df_s is not None and not df_s.empty:
            curr_p = float(df_s["Close"].iloc[-1])
            entry = float(pos["entry_price"])
            init_sl = float(pos.get("initial_stop_loss", pos["stop_loss"]))
            one_r = max(0.05, abs(entry - init_sl))
            if (curr_p - entry) < (-0.25 * one_r):
                return True
    return False

def get_total_daily_drawdown_pct(state, indicator_cache=None):
    d_str = today_str()
    realized_today = float(state.get("daily_pnl", {}).get(d_str, 0.0))
    floating_now = get_floating_pnl_usd(state, indicator_cache)
    total_today_usd = realized_today + min(0.0, floating_now)
    base_cap = max(float(state.get("initial_capital", DEFAULT_INITIAL_CAPITAL)), 100.0)
    return total_today_usd / base_cap

def recalculate_position_targets(pos, new_entry_price):
    init_sl = float(pos.get("initial_stop_loss", pos["stop_loss"]))
    if new_entry_price <= init_sl:
        old_one_r = max(0.05, abs(float(pos["entry_price"]) - init_sl))
        init_sl = round(new_entry_price - old_one_r, 2)
        pos["initial_stop_loss"] = init_sl

    one_r = max(0.05, abs(new_entry_price - init_sl))
    pos["entry_price"] = round(new_entry_price, 2)
    pos["one_r"] = round(one_r, 4)
    pos["take_profit"] = round(new_entry_price + (TARGET_TP_R_MULT * one_r), 2)
    pos["trail_0_trigger"] = round(new_entry_price + (TRAIL_0_TRIGGER_R * one_r), 2)
    pos["trail_0_stop"] = round(new_entry_price + (TRAIL_0_LOCK_R * one_r), 2)
    pos["trail_1_trigger"] = round(new_entry_price + (TRAIL_1_TRIGGER_R * one_r), 2)
    pos["trail_1_stop"] = round(new_entry_price + (TRAIL_1_LOCK_R * one_r), 2)
    pos["trail_2_trigger"] = round(new_entry_price + (TRAIL_2_TRIGGER_R * one_r), 2)
    pos["trail_2_stop"] = round(new_entry_price + (TRAIL_2_LOCK_R * one_r), 2)

    if pos.get("trail2_triggered", False):
        pos["stop_loss"] = pos["trail_2_stop"]
    elif pos.get("be_triggered", False):
        pos["stop_loss"] = pos["trail_1_stop"]
    elif pos.get("trail0_triggered", False):
        pos["stop_loss"] = pos["trail_0_stop"]
    else:
        pos["stop_loss"] = init_sl

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
        if entry_price <= usable_free_power and entry_price <= (equity * 0.40) and (risk_per_share / equity) <= MAX_SINGLE_RISK_CAP:
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
    rem_pnl_usd = round((exit_price - entry_price) * shares, 2)
    partial_already_banked = float(pos.get("realized_partial_usd", 0.0))
    total_trade_pnl = round(rem_pnl_usd + partial_already_banked, 2)

    pnl_pct_pos = round(((exit_price - entry_price) / entry_price) * 100, 2)

    state["equity"] = round(state["equity"] + rem_pnl_usd, 2)
    state["realized_pnl"] = round(state["realized_pnl"] + rem_pnl_usd, 2)
    state["total_trades"] += 1

    d_str = today_str()
    state["daily_pnl"][d_str] = round(state["daily_pnl"].get(d_str, 0.0) + rem_pnl_usd, 2)

    if total_trade_pnl >= 0:
        state["wins"] += 1
        state["consecutive_losses"] = 0
        if rem_pnl_usd > 0:
            state["gross_profit"] = round(state["gross_profit"] + rem_pnl_usd, 2)
    else:
        state["losses"] += 1
        state["consecutive_losses"] = int(state.get("consecutive_losses", 0)) + 1
        if rem_pnl_usd < 0:
            state["gross_loss"] = round(state["gross_loss"] + abs(rem_pnl_usd), 2)

    if state["equity"] > state["peak_equity"]:
        state["peak_equity"] = state["equity"]

    dd_pct = ((state["equity"] - state["peak_equity"]) / state["peak_equity"]) * 100
    if dd_pct < state["max_drawdown_pct"]:
        state["max_drawdown_pct"] = round(dd_pct, 2)

    record = {
        "symbol": symbol,
        "sector": pos.get("sector", "N/A"),
        "engine": pos.get("engine", "N/A"),
        "shares": int(pos.get("initial_shares", shares)),
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "pnl_usd": total_trade_pnl,
        "pnl_pct": pnl_pct_pos,
        "reason": reason,
        "closed_at": now_madrid().strftime("%Y-%m-%d %H:%M:%S")
    }
    state["trade_history"].append(record)
    if len(state["trade_history"]) > 200:
        state["trade_history"] = state["trade_history"][-200:]

    state["cooldowns"][symbol] = (now_madrid() + timedelta(minutes=35)).isoformat()
    save_state(state, push_to_git=True, msg=f"Close {symbol} Total PnL {total_trade_pnl}$")
    return record

# ==============================================================================
# CATALIZADOR SEC FORM 4
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
# MOTOR DE INDICADORES V5.5 (CON CALCULO DE GASOLINA DIARIA ADR Y COMPRESION)
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
            threads=True,
            progress=False,
            timeout=25
        )
        return raw
    except Exception as e:
        print(f"[ERROR] Fallo en descarga masiva yfinance: {e}")
        return None

def compute_intraday_indicators(df):
    if df is None or df.empty or len(df) < 55:
        return None
    df = df.copy().dropna(subset=["Close", "High", "Low", "Volume"])
    if len(df) < 55:
        return None

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(TZ_MADRID)
    else:
        df.index = df.index.tz_convert(TZ_MADRID)

    df["Date"] = df.index.date
    df["Typical_Price"] = (df["High"] + df["Low"] + df["Close"]) / 3.0
    df["TP_Vol"] = df["Typical_Price"] * df["Volume"]

    df["Cum_Vol"] = df.groupby("Date")["Volume"].cumsum().replace(0, np.nan)
    df["Cum_TP_Vol"] = df.groupby("Date")["TP_Vol"].cumsum()
    df["VWAP"] = df["Cum_TP_Vol"] / df["Cum_Vol"]

    df["EMA_9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["EMA_50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["EMA_200"] = df["Close"].ewm(span=200, adjust=False).mean()

    bar_range = (df["High"] - df["Low"]).replace(0, np.nan)
    df["Bar_Range"] = bar_range.fillna(0.0)
    df["Close_Strength"] = ((df["Close"] - df["Low"]) / bar_range).fillna(0.5)

    high_low = df["High"] - df["Low"]
    prev_close = df["Close"].shift(1)
    same_day = df["Date"] == df["Date"].shift(1)
    high_close = np.where(same_day, (df["High"] - prev_close).abs(), high_low * 1.15)
    low_close = np.where(same_day, (df["Low"] - prev_close).abs(), high_low * 1.15)
    tr = pd.concat([high_low, pd.Series(high_close, index=df.index), pd.Series(low_close, index=df.index)], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()

    # Compresion de las 3 velas previas (Detecta cuando la accion descansa estrecha antes de despegar)
    df["Prev_3Bar_Avg_Range"] = df["Bar_Range"].shift(1).rolling(3, min_periods=2).mean()

    delta = df["Close"].diff()
    gain14 = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss14 = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs14 = gain14 / loss14.replace(0, np.nan)
    df["RSI_14"] = (100 - (100 / (1 + rs14))).fillna(50)

    up_move = df["High"].diff()
    down_move = -df["Low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_14 = tr.rolling(14).sum().replace(0, np.nan)
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(14).sum() / tr_14)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(14).sum() / tr_14)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    df["ADX"] = dx.rolling(14).mean().fillna(20)

    bb_mid = df["Close"].rolling(20).mean()
    bb_std = df["Close"].rolling(20).std()
    df["BB_Upper"] = bb_mid + (2.0 * bb_std)
    df["BB_Lower"] = bb_mid - (2.0 * bb_std)
    kc_upper = bb_mid + (1.5 * df["ATR"])
    kc_lower = bb_mid - (1.5 * df["ATR"])
    df["Squeeze_On"] = (df["BB_Upper"] < kc_upper) & (df["BB_Lower"] > kc_lower)
    df["Recent_Squeeze"] = df["Squeeze_On"].rolling(4).max() > 0

    df["Flag_3Bar_High"] = df.groupby("Date")["High"].transform(lambda s: s.shift(1).rolling(3, min_periods=3).max())
    df["Flag_3Bar_Low"] = df.groupby("Date")["Low"].transform(lambda s: s.shift(1).rolling(3, min_periods=3).min())

    vol_median_base = df["Volume"].rolling(55, min_periods=18).median().replace(0, np.nan)
    df["RVOL"] = (df["Volume"] / vol_median_base).fillna(1.0)

    # Calculo del ADR (Rango Diario Medio de los 5 dias) y Gasolina Consumida Hoy
    daily_stats = df.groupby("Date").agg(d_high=("High", "max"), d_low=("Low", "min"))
    daily_ranges = (daily_stats["d_high"] - daily_stats["d_low"]).replace(0, np.nan)
    avg_daily_range = float(daily_ranges.mean()) if not daily_ranges.empty else float(df["Close"].iloc[-1] * 0.025)

    today_date = df["Date"].iloc[-1]
    today_df = df[df["Date"] == today_date]
    if not today_df.empty:
        open_day = float(today_df["Open"].iloc[0])
        today_low = float(today_df["Low"].min())
        curr_c = float(today_df["Close"].iloc[-1])
        adr_safe = max(avg_daily_range, curr_c * 0.018)
        adr_used_pct = ((curr_c - today_low) / adr_safe) * 100.0

        df["Open_Day"] = open_day
        df["Today_Low"] = today_low
        df["ADR_Used_Pct"] = round(min(100.0, max(0.0, adr_used_pct)), 1)
        df["Bars_Today"] = len(today_df)
        df["Day_Return_Pct"] = ((df["Close"] - open_day) / open_day) * 100.0
    else:
        df["Open_Day"] = df["Close"]
        df["Today_Low"] = df["Low"]
        df["ADR_Used_Pct"] = 50.0
        df["Bars_Today"] = 0
        df["Day_Return_Pct"] = 0.0

    return df

def build_indicator_cache_and_breadth(market_data):
    if market_data is None or market_data.empty:
        return {}, 50.0
    cache = {}
    valid = 0
    bullish = 0
    try:
        available_tickers = set(market_data.columns.get_level_values(0))
    except Exception:
        return {}, 50.0

    tickers_list = ALL_TICKERS + [BENCHMARK]
    for sym in tickers_list:
        try:
            if sym not in available_tickers:
                continue
            df_ind = compute_intraday_indicators(market_data[sym])
            if df_ind is not None and len(df_ind) >= 55:
                cache[sym] = df_ind
                if sym != BENCHMARK:
                    valid += 1
                    last_b = df_ind.iloc[-1]
                    closed_b = df_ind.iloc[-2]
                    if float(last_b["Close"]) > float(closed_b["VWAP"]):
                        bullish += 1
        except Exception:
            continue
    breadth = round((bullish / valid) * 100.0, 1) if valid > 0 else 50.0
    return cache, breadth

def is_qqq_truly_bullish(df_qqq):
    if df_qqq is None or len(df_qqq) < 6:
        return False, 0.0
    qqq_last = df_qqq.iloc[-1]
    qqq_closed = df_qqq.iloc[-2]
    qqq_ret = float(qqq_last["Day_Return_Pct"])

    live_c = float(qqq_last["Close"])
    vwap_c = float(qqq_last["VWAP"])
    ema9_c = float(qqq_closed["EMA_9"])
    ema21_c = float(qqq_closed["EMA_21"])
    qqq_atr = float(qqq_closed["ATR"]) if not np.isnan(qqq_closed["ATR"]) else 0.0

    panic_volatility = (qqq_atr / live_c) > 0.0085 if live_c > 0 else True

    bullish = (
        not panic_volatility
        and live_c > vwap_c
        and ema9_c >= (ema21_c * 0.9995)
        and live_c >= (ema9_c * 0.9988)
    )
    return bullish, qqq_ret

# ==============================================================================
# MOTOR DE EVALUACION V5.5 (CAZA EL INICIO DE LA SUBIDA + MEDIDOR DE GASOLINA)
# ==============================================================================
def evaluate_symbol_v5(symbol, df, qqq_ret_pct, qqq_bullish, breadth_pct, current_time):
    if df is None or len(df) < 55:
        return None

    closed_bar = df.iloc[-2]
    prev_closed = df.iloc[-3]
    live_bar = df.iloc[-1]

    live_price = float(live_bar["Close"])
    close_p = float(closed_bar["Close"])
    open_p = float(closed_bar["Open"])
    open_day = float(live_bar["Open_Day"])
    bars_today = int(live_bar["Bars_Today"])
    vwap_p = float(closed_bar["VWAP"])
    close_strength = float(closed_bar["Close_Strength"])
    closed_bar_range = float(closed_bar["Bar_Range"])
    adr_used_pct = float(live_bar["ADR_Used_Pct"])
    fuel_left_pct = round(max(0.0, 100.0 - adr_used_pct), 0)

    if live_price <= open_day or close_p <= open_day or bars_today < 6:
        return None

    # FILTRO DE GASOLINA DIARIA V5.5:
    # Si la accion ya consumio mas del 72% de su rango diario (ya subio demasiado), PROHIBIDO comprar
    if adr_used_pct > MAX_ADR_USED_PCT:
        return None

    raw_atr = float(closed_bar["ATR"]) if not np.isnan(closed_bar["ATR"]) else (live_price * 0.008)
    if (raw_atr / live_price) > MAX_PANIC_ATR_PCT:
        return None
    if raw_atr > 0 and (closed_bar_range / raw_atr) > MAX_CLIMAX_BAR_ATR_MULT:
        return None

    atr_val = max(raw_atr, live_price * 0.0065)

    rvol = max(float(closed_bar["RVOL"]), float(live_bar["RVOL"]))
    rsi14 = float(closed_bar["RSI_14"])
    adx = float(closed_bar["ADX"])
    ema9 = float(closed_bar["EMA_9"])
    ema21 = float(closed_bar["EMA_21"])
    ema50 = float(closed_bar["EMA_50"])
    ema200 = float(closed_bar["EMA_200"])
    prev_ema9 = float(prev_closed["EMA_9"])
    prev_ema50 = float(prev_closed["EMA_50"])
    day_ret = float(live_bar["Day_Return_Pct"])
    alpha_vs_qqq = round(day_ret - qqq_ret_pct, 2)

    # Estructura Mayor Sana (Precio sobre EMA-200 y EMA-50 en ascenso)
    macro_trend_ok = (close_p > ema200) and (close_p > ema50) and (ema50 >= prev_ema50 * 0.9998)
    if not macro_trend_ok or close_p <= vwap_p or ema9 < ema21 * 0.9995 or ema9 < prev_ema9:
        return None

    market_permission = (qqq_bullish and breadth_pct >= MIN_MARKET_BREADTH_PCT) or (
        qqq_bullish and breadth_pct >= 35.0 and alpha_vs_qqq >= 0.80 and rvol >= 1.35
    )
    if not market_permission:
        return None

    vwap_dist_atr = (live_price - vwap_p) / atr_val if atr_val > 0 else 0.0
    ema9_dist_atr = (live_price - ema9) / atr_val if atr_val > 0 else 0.0
    strong_bullish_candle = (close_p > open_p and close_strength >= MIN_CANDLE_CLOSE_STRENGTH)

    # Prohibido comprar lejos del VWAP (garantiza entrar cerca de la base institucional)
    if not strong_bullish_candle or not (0.0 <= vwap_dist_atr <= MAX_VWAP_EXTENSION_ATR):
        return None

    t_val = current_time.hour + (current_time.minute / 60.0)
    is_breakout_window = (16.00 <= t_val < 18.00) or (19.50 <= t_val <= 21.25)
    is_continuous_window = (16.00 <= t_val <= 21.25)

    signal_data = None

    # 1. MOTOR 1 (NUEVO ESTRELLA V5.5): Ignicion Temprana en VWAP / EMA-21 (Caza el Inicio Exacto de la Subida)
    # Detecta cuando la accion apoya justo en el VWAP/EMA-21 y lanza la 1a vela de volumen institucional (con <55% de gasolina gastada)
    if is_continuous_window and vwap_dist_atr <= IGNITION_MAX_VWAP_ATR and adr_used_pct <= 58.0 and 46 <= rsi14 <= 64:
        tested_vwap_or_ema21 = (
            float(prev_closed["Low"]) <= (vwap_p * 1.003)
            or float(closed_bar["Low"]) <= (vwap_p * 1.003)
            or float(closed_bar["Low"]) <= (ema21 * 1.0025)
        )
        fresh_launch = (close_p > ema9 and close_p > vwap_p and live_price >= close_p * 0.999)
        if tested_vwap_or_ema21 and fresh_launch and rvol >= 1.25 and alpha_vs_qqq >= 0.25:
            signal_data = {
                "engine": "Motor 1: Ignición Temprana en VWAP (Inicio de Subida)",
                "sl_mult": 1.28,
                "priority_bonus": 2.0,
                "high_conviction": (rvol >= 1.45 and fuel_left_pct >= 50)
            }

    # 2. MOTOR 2: Pullback Temprano a EMA-9 tras Compresion Estrecha
    if signal_data is None and is_continuous_window and ema9_dist_atr <= PULLBACK_MAX_EMA9_ATR and 48 <= rsi14 <= 66:
        touched_ema9 = (float(prev_closed["Low"]) <= ema9 * 1.002) or (float(closed_bar["Low"]) <= ema9 * 1.002)
        reclaimed = (close_p > ema9 and live_price >= close_p * 0.999 and float(closed_bar["Low"]) >= ema21 * 0.998)
        if touched_ema9 and reclaimed and alpha_vs_qqq >= 0.45 and rvol >= 1.15:
            signal_data = {
                "engine": "Motor 2: Pullback Temprano EMA-9 (Base Fresca)",
                "sl_mult": 1.32,
                "priority_bonus": 1.5,
                "high_conviction": (alpha_vs_qqq >= 0.95 and rvol >= 1.35)
            }

    # 3. MOTOR 3: Ruptura de Micro-Cajita Pegada a la Media (3-Bar Tight Coil Breakout)
    if signal_data is None and is_continuous_window and ema9_dist_atr <= BREAKOUT_MAX_EMA9_ATR:
        flag_high = float(closed_bar["Flag_3Bar_High"])
        prev_3bar_avg = float(closed_bar["Prev_3Bar_Avg_Range"]) if not np.isnan(closed_bar["Prev_3Bar_Avg_Range"]) else atr_val
        is_tight_coil = prev_3bar_avg <= (1.10 * atr_val)  # Las 3 velas previas fueron tranquilas/comprimidas
        if not np.isnan(flag_high) and is_tight_coil and alpha_vs_qqq >= 0.45 and 52 <= rsi14 <= 69:
            if close_p > flag_high and live_price >= close_p * 0.999 and float(prev_closed["Close"]) <= flag_high:
                if rvol >= 1.25:
                    signal_data = {
                        "engine": "Motor 3: Despegue de Compresión (Micro-Coil)",
                        "sl_mult": 1.35,
                        "priority_bonus": 1.2,
                        "high_conviction": (rvol >= 1.50 and fuel_left_pct >= 40)
                    }

    # 4. MOTOR 4: Explosion de Volatilidad (Squeeze Naciente Cerca del VWAP)
    if signal_data is None and is_breakout_window and ema9_dist_atr <= BREAKOUT_MAX_EMA9_ATR:
        if bool(closed_bar["Recent_Squeeze"]) and close_p > float(closed_bar["BB_Upper"]) and live_price >= close_p * 0.999:
            if alpha_vs_qqq >= 0.40 and rvol >= 1.35 and 53 <= rsi14 <= 70:
                signal_data = {
                    "engine": "Motor 4: Explosión Volatilidad Temprana (Squeeze)",
                    "sl_mult": 1.35,
                    "priority_bonus": 1.0,
                    "high_conviction": (rvol >= 1.60 and fuel_left_pct >= 40)
                }

    if not signal_data:
        return None

    has_form4 = check_sec_form4_catalyst(symbol)
    if has_form4:
        signal_data["high_conviction"] = True
        signal_data["engine"] += " + [SEC Form 4]"

    raw_stop_dist = signal_data["sl_mult"] * atr_val
    min_stop_dist = live_price * MIN_STOP_DISTANCE_PCT
    ema21_buffer_dist = max(0.0, (live_price - (ema21 * 0.9980)))
    one_r_dist = max(raw_stop_dist, min_stop_dist, ema21_buffer_dist)

    stop_loss = round(live_price - one_r_dist, 2)
    take_profit = round(live_price + (TARGET_TP_R_MULT * one_r_dist), 2)
    min_valid_entry = round(max(ema9 * 0.9985, live_price - (0.18 * one_r_dist)), 2)
    max_valid_entry = round(live_price + (MAX_ENTRY_CHASE_R * one_r_dist), 2)

    # Puntuacion de Ignicion Fresca: premia tener mucha gasolina restante, estar cerca del VWAP y alto RVOL
    freshness_score = (
        signal_data["priority_bonus"]
        + (fuel_left_pct / 25.0)
        + min(3.0, rvol)
        - vwap_dist_atr
    )

    return {
        "symbol": symbol,
        "sector": TICKER_TO_SECTOR.get(symbol, "General"),
        "engine": signal_data["engine"],
        "price": round(live_price, 2),
        "min_valid_entry": min_valid_entry,
        "max_valid_entry": max_valid_entry,
        "vwap": round(vwap_p, 2),
        "atr": round(atr_val, 2),
        "one_r": round(one_r_dist, 4),
        "rvol": round(rvol, 2),
        "rsi": round(rsi14, 1),
        "alpha": alpha_vs_qqq,
        "fuel_left_pct": int(fuel_left_pct),
        "freshness_score": round(freshness_score, 2),
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "high_conviction": signal_data["high_conviction"],
        "signal_ts": time.time()
    }

# ==============================================================================
# GESTION DE POSICIONES ABIERTAS V5.5 (SIN HUECO CIEGO DE 5M ENTRE VELAS)
# ==============================================================================
def get_position_opened_ts(pos):
    if "opened_ts" in pos and float(pos["opened_ts"]) > 0:
        return float(pos["opened_ts"])
    opened_str = pos.get("opened_at", "")
    if opened_str:
        try:
            dt = TZ_MADRID.localize(datetime.strptime(opened_str, "%Y-%m-%d %H:%M:%S"))
            return dt.timestamp()
        except Exception:
            return 0.0
    return 0.0

def monitor_open_positions(state, indicator_cache):
    open_pos = state.get("open_positions", {})
    if not open_pos or not indicator_cache:
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
            df_sym = indicator_cache.get(symbol)
            if df_sym is None or df_sym.empty:
                continue

            last_bar = df_sym.iloc[-1]
            closed_bar = df_sym.iloc[-2]
            curr_price = float(last_bar["Close"])
            ema9_closed = float(closed_bar["EMA_9"])
            ema21_closed = float(closed_bar["EMA_21"])

            pos = open_pos[symbol]
            entry = float(pos["entry_price"])
            sl = float(pos["stop_loss"])
            init_sl = float(pos.get("initial_stop_loss", sl))
            tp = float(pos["take_profit"])
            one_r = float(pos.get("one_r", max(0.05, abs(entry - init_sl))))

            opened_ts = get_position_opened_ts(pos)
            last_bar_ts = last_bar.name.timestamp() if hasattr(last_bar.name, "timestamp") else 0.0
            closed_bar_ts = closed_bar.name.timestamp() if hasattr(closed_bar.name, "timestamp") else 0.0

            effective_high = curr_price
            effective_low = curr_price

            if opened_ts > 0.0 and last_bar_ts > opened_ts:
                effective_high = max(effective_high, float(last_bar["High"]))
                effective_low = min(effective_low, float(last_bar["Low"]))
            if opened_ts > 0.0 and closed_bar_ts > opened_ts:
                effective_high = max(effective_high, float(closed_bar["High"]))
                effective_low = min(effective_low, float(closed_bar["Low"]))

            # 1. Comprobar Take-Profit Completo (+2.40R)
            if effective_high >= tp:
                exit_p = max(curr_price, tp)
                rec = register_closed_trade(state, symbol, exit_p, "🎯 TAKE-PROFIT COMPLETO (+2.40R)")
                if rec:
                    send_telegram_message(
                        f"🎯 <b>¡TAKE-PROFIT COMPLETO EN {symbol}!</b> 🎯\n\n"
                        f"• <b>Acciones a vender:</b> <code>{rec['shares']} acciones (100%)</code>\n"
                        f"• <b>Entrada:</b> <code>{rec['entry_price']:.2f} $</code> | <b>Salida TP:</b> <code>{rec['exit_price']:.2f} $</code>\n"
                        f"• <b>Beneficio Neto Operación:</b> <b>+{rec['pnl_usd']:.2f} $ (+{rec['pnl_pct']:.2f}%)</b>\n"
                        f"• <b>Nuevo Capital Total:</b> <code>{state['equity']:,.2f} $</code>"
                    )
                continue

            # 2. Comprobar Stop-Loss (Inicial o Trinquetes +0.25R / +0.90R / +1.50R)
            if effective_low <= sl:
                exit_p = min(curr_price, sl)
                if pos.get("trail2_triggered"):
                    reason_label = "🔒 SALIDA EN TRINQUETE NIVEL 2 (+1.50R ASEGURADO)"
                elif pos.get("be_triggered"):
                    reason_label = "🛡️ SALIDA EN TRINQUETE NIVEL 1 (+0.90R ASEGURADO)"
                elif pos.get("trail0_triggered"):
                    reason_label = "🟢 SALIDA EN ESCUDO ANTI-LATERAL (+0.25R ASEGURADO)"
                else:
                    reason_label = "🛑 STOP-LOSS EJECUTADO (-1.00R)"

                rec = register_closed_trade(state, symbol, exit_p, reason_label)
                if rec:
                    sign = "+" if rec["pnl_usd"] >= 0 else ""
                    send_telegram_message(
                        f"🔔 <b>{reason_label}: {symbol}</b>\n\n"
                        f"• <b>Entrada:</b> <code>{rec['entry_price']:.2f} $</code> | <b>Salida Stop:</b> <code>{rec['exit_price']:.2f} $</code>\n"
                        f"• <b>Resultado Neto Operación:</b> <b>{sign}{rec['pnl_usd']:.2f} $ ({sign}{rec['pnl_pct']:.2f}%)</b>\n"
                        f"• <b>Capital Actual:</b> <code>{state['equity']:,.2f} $</code>"
                    )
                continue

            # 3. Trinquete Nivel 2 (+2.05R -> Asegura +1.50R)
            trail2_trigger = float(pos.get("trail_2_trigger", round(entry + (TRAIL_2_TRIGGER_R * one_r), 2)))
            if not pos.get("trail2_triggered", False) and effective_high >= trail2_trigger:
                calc_stop_2 = round(max(entry + (TRAIL_2_LOCK_R * one_r), min(ema9_closed * 0.998, curr_price - (0.60 * one_r))), 2)
                new_stop_2 = max(sl, calc_stop_2)
                pos["trail2_triggered"] = True
                pos["be_triggered"] = True
                pos["trail0_triggered"] = True
                pos["stop_loss"] = new_stop_2
                locked_profit_2 = round((new_stop_2 - entry) * int(pos["shares"]), 2)
                save_state(state, push_to_git=True, msg=f"Trail 2 (+1.50R) active {symbol}")
                send_telegram_message(
                    f"🔒 <b>TRINQUETE NIVEL 2 (+1.50R BLINDADO) EN {symbol}</b> 🔒\n\n"
                    f"Cotizando en <code>{curr_price:.2f} $</code>.\n"
                    f"👉 <b>ACCIÓN EN QUANTFURY:</b> Sube el Stop Order de tus <b><code>{pos['shares']} acciones</code></b> a <b><code>{new_stop_2:.2f} $</code></b>.\n"
                    f"💰 <i>Beneficio mínimo ya asegurado: <b>+{locked_profit_2:.2f} $</b></i>"
                )

            # 4. Trinquete Nivel 1 (+1.65R -> Asegura +0.90R)
            elif not pos.get("be_triggered", False):
                be_trigger = float(pos.get("trail_1_trigger", round(entry + (TRAIL_1_TRIGGER_R * one_r), 2)))
                if effective_high >= be_trigger:
                    calc_stop_1 = float(pos.get("trail_1_stop", round(entry + (TRAIL_1_LOCK_R * one_r), 2)))
                    new_stop_1 = max(sl, calc_stop_1)
                    pos["be_triggered"] = True
                    pos["trail0_triggered"] = True
                    pos["stop_loss"] = new_stop_1
                    locked_profit = round((new_stop_1 - entry) * int(pos["shares"]), 2)
                    save_state(state, push_to_git=True, msg=f"Trail 1 (+0.90R) active {symbol}")
                    send_telegram_message(
                        f"🛡️ <b>TRINQUETE NIVEL 1 (+0.90R BLINDADO) EN {symbol}</b> 🛡️\n\n"
                        f"El precio ya avanza +1.65R (<code>{curr_price:.2f} $</code>).\n"
                        f"👉 <b>ACCIÓN EN QUANTFURY:</b> Sube tu Stop Order de las <b><code>{pos['shares']} acciones</code></b> a <b><code>{new_stop_1:.2f} $</code></b>.\n"
                        f"💰 <i>Beneficio mínimo asegurado: <b>+{locked_profit:.2f} $</b> (Buscando TP de {tp:.2f} $)</i>"
                    )

                # 5. Trinquete Nivel 0 Anti-Lateral (+1.15R -> Asegura +0.25R)
                elif not pos.get("trail0_triggered", False):
                    t0_trigger = float(pos.get("trail_0_trigger", round(entry + (TRAIL_0_TRIGGER_R * one_r), 2)))
                    if effective_high >= t0_trigger:
                        calc_stop_0 = float(pos.get("trail_0_stop", round(entry + (TRAIL_0_LOCK_R * one_r), 2)))
                        new_stop_0 = max(sl, calc_stop_0)
                        pos["trail0_triggered"] = True
                        pos["stop_loss"] = new_stop_0
                        locked_profit_0 = round((new_stop_0 - entry) * int(pos["shares"]), 2)
                        save_state(state, push_to_git=True, msg=f"Trail 0 (+0.25R) active {symbol}")
                        send_telegram_message(
                            f"🟢 <b>ESCUDO ANTI-LATERAL (+0.25R BLINDADO) EN {symbol}</b> 🟢\n\n"
                            f"La operación ya gana +1.15R (<code>{curr_price:.2f} $</code>).\n"
                            f"👉 <b>ACCIÓN EN QUANTFURY:</b> Sube tu Stop Order de <b><code>{pos['shares']} acciones</code></b> a <b><code>{new_stop_0:.2f} $</code></b>.\n"
                            f"🛡️ <i>Riesgo eliminado: pase lo que pase ya ganas mínimo <b>+{locked_profit_0:.2f} $</b></i>"
                        )

            # 6. Detector Time-Stop (60 min sin despegar y perdiendo EMA-21)
            if not pos.get("trail0_triggered", False) and not pos.get("timestop_alerted", False):
                if opened_ts > 0:
                    mins_open = (time.time() - opened_ts) / 60.0
                    if mins_open >= 60 and curr_price < (entry + 0.30 * one_r) and curr_price < ema21_closed:
                        pos["timestop_alerted"] = True
                        save_state(state)
                        send_telegram_message(
                            f"⏳ <b>AVISO TIME-STOP (60m ESTANCADA) EN {symbol}</b>\n\n"
                            f"Lleva {int(mins_open)} min sin impulso y perdió la EMA-21.\n"
                            f"💡 <b>Sugerencia:</b> Ciérrala cerca de Break-Even para liberar <code>{pos['shares']*entry:.0f} $</code> de poder."
                        )

        except Exception as e:
            print(f"[WARN] Error vigilando {symbol}: {e}")

# ==============================================================================
# PANEL DE AUDITORIA CUANTITATIVA COMPLETA EN /HISTORIAL Y /CARTERA
# ==============================================================================
def compute_quant_audit_metrics(state):
    hist = state.get("trade_history", [])
    if not hist:
        return {
            "total": 0, "wins": 0, "losses": 0, "wr": 0.0,
            "avg_win_usd": 0.0, "avg_loss_usd": 0.0,
            "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "rr_ratio": 0.0, "breakeven_wr": 0.0,
            "expectancy_usd": 0.0, "pf": 0.0,
            "net_pnl": 0.0, "verdict": "⏳ Sin muestra suficiente (0 trades)"
        }

    wins_list = [h for h in hist if float(h.get("pnl_usd", 0.0)) >= 0]
    loss_list = [h for h in hist if float(h.get("pnl_usd", 0.0)) < 0]

    total = len(hist)
    w_count = len(wins_list)
    l_count = len(loss_list)
    wr = (w_count / total) * 100.0 if total > 0 else 0.0

    gross_p = sum(float(h.get("pnl_usd", 0.0)) for h in wins_list)
    gross_l = sum(abs(float(h.get("pnl_usd", 0.0))) for h in loss_list)
    net_pnl = gross_p - gross_l

    avg_win_usd = (gross_p / w_count) if w_count > 0 else 0.0
    avg_loss_usd = (gross_l / l_count) if l_count > 0 else 0.0

    avg_win_pct = (sum(float(h.get("pnl_pct", 0.0)) for h in wins_list) / w_count) if w_count > 0 else 0.0
    avg_loss_pct = (sum(abs(float(h.get("pnl_pct", 0.0))) for h in loss_list) / l_count) if l_count > 0 else 0.0

    rr_ratio = (avg_win_usd / avg_loss_usd) if avg_loss_usd > 0 else (avg_win_usd if avg_win_usd > 0 else 0.0)
    breakeven_wr = (1.0 / (1.0 + rr_ratio)) * 100.0 if rr_ratio > 0 else 50.0
    expectancy_usd = ((wr / 100.0) * avg_win_usd) - (((100.0 - wr) / 100.0) * avg_loss_usd)
    pf = (gross_p / gross_l) if gross_l > 0 else gross_p

    if total < 15:
        verdict = f"🟡 EN FASE DE MUESTREO ({total}/30 trades para validación)"
    elif expectancy_usd > 0 and pf >= 1.65:
        verdict = "🟢 BOT SÓLIDO & RENTABLE (Ventaja Estadística Positiva)"
    elif expectancy_usd > 0:
        verdict = "🟡 RENTABLE EN AJUSTE (Subiendo R:R con Ignición Temprana V5.5)"
    else:
        verdict = "🔴 EN RACHA ADVERSA (Escudos Early-Ignition V5.5 Activos)"

    return {
        "total": total, "wins": w_count, "losses": l_count, "wr": wr,
        "avg_win_usd": avg_win_usd, "avg_loss_usd": avg_loss_usd,
        "avg_win_pct": avg_win_pct, "avg_loss_pct": avg_loss_pct,
        "rr_ratio": rr_ratio, "breakeven_wr": breakeven_wr,
        "expectancy_usd": expectancy_usd, "pf": pf,
        "net_pnl": net_pnl, "verdict": verdict
    }

def build_historial_audit_report(state):
    m = compute_quant_audit_metrics(state)
    hist = state.get("trade_history", [])[-10:]

    if m["total"] == 0:
        return "📜 Aún no hay operaciones cerradas en el historial."

    edge_margin = m["wr"] - m["breakeven_wr"]
    edge_sign = "+" if edge_margin >= 0 else ""

    lines = [
        f"🏛️ <b>AUDITORÍA CUANTITATIVA DEL BOT (/historial)</b>",
        f"• <b>Diagnóstico:</b> {m['verdict']}",
        f"• <b>Muestra Auditada:</b> <code>{m['total']} trades ({m['wins']}W / {m['losses']}L)</code>",
        f"• <b>PnL Neto Historial:</b> <b><code>{m['net_pnl']:+.2f} $</code></b>\n",
        f"📐 <b>MÉTRICAS DE RIESGO / BENEFICIO REAL:</b>",
        f"• <b>Porcentaje de Acierto (Win Rate):</b> <b><code>{m['wr']:.1f}%</code></b>",
        f"• <b>Acierto Mínimo p/ Ganar (Breakeven):</b> <code>{m['breakeven_wr']:.1f}%</code> <i>(Margen: {edge_sign}{edge_margin:.1f}%)</i>",
        f"• <b>Ganancia Media (Avg Win):</b> <code>+{m['avg_win_usd']:.2f} $ (+{m['avg_win_pct']:.2f}%)</code>",
        f"• <b>Pérdida Media (Avg Loss):</b> <code>-{m['avg_loss_usd']:.2f} $ (-{m['avg_loss_pct']:.2f}%)</code>",
        f"• <b>Ratio Riesgo/Beneficio Real (R:R):</b> <b><code>1 : {m['rr_ratio']:.2f}</code></b>",
        f"• <b>Esperanza Matemática / Trade:</b> <b><code>{m['expectancy_usd']:+.2f} $ / op</code></b>",
        f"• <b>Profit Factor Real:</b> <b><code>{m['pf']:.2f}</code></b> | <b>Max DD:</b> <code>{state['max_drawdown_pct']:.2f}%</code>\n",
        f"📜 <b>ÚLTIMAS {len(hist)} OPERACIONES CERRADAS:</b>"
    ]

    for h in reversed(hist):
        ic = "🟢" if float(h["pnl_usd"]) >= 0 else "🔴"
        lines.append(
            f"{ic} <b>{h['symbol']}</b> ({h['shares']} acc.): <code>{h['entry_price']:.2f}$ a {h['exit_price']:.2f}$</code> | <b>{h['pnl_usd']:+.2f}$ ({h['pnl_pct']:+.2f}%)</b>"
        )
    return "\n".join(lines)

def build_portfolio_report(state, indicator_cache=None):
    eq = state["equity"]
    init_c = state["initial_capital"]
    net_pnl = eq - init_c
    net_pct = (net_pnl / init_c) * 100 if init_c > 0 else 0.0
    free_bp = get_free_buying_power(state)
    dd_today = get_total_daily_drawdown_pct(state, indicator_cache) * 100
    m = compute_quant_audit_metrics(state)

    open_pos = state.get("open_positions", {})
    lines = [
        f"📊 <b>LIBRO CONTABLE QUANTFURY (EARLY-IGNITION v5.5)</b>",
        f"• <b>Poder Total:</b> <code>{eq:,.2f} $</code> | <b>Libre:</b> <code>{free_bp:,.2f} $</code>",
        f"• <b>PnL Cuenta Actual:</b> <code>{net_pnl:+,.2f} $ ({net_pct:+.2f}%)</code>",
        f"• <b>PnL Día (Cerrado + Flotante):</b> <code>{dd_today:+.2f}%</code> <i>(Tope -0.85%)</i>",
        f"• <b>Operaciones:</b> <code>{m['total']} ({m['wins']}W / {m['losses']}L)</code> | <b>WR:</b> <code>{m['wr']:.1f}%</code>",
        f"• <b>Ratio R:R Real:</b> <code>1 : {m['rr_ratio']:.2f}</code> | <b>Profit Factor:</b> <code>{m['pf']:.2f}</code>",
        f"• <b>Esperanza / Trade:</b> <code>{m['expectancy_usd']:+.2f} $</code>",
        f"• <b>Abiertas ahora:</b> <code>{len(open_pos)}/{MAX_OPEN_POSITIONS}</code> (55 Activos Universales)"
    ]

    if open_pos:
        lines.append("\n📂 <b>POSICIONES ABIERTAS EN CURSO:</b>")
        for sym, p in open_pos.items():
            sh = p["shares"]
            ep = p["entry_price"]
            cur_p = ep
            if indicator_cache and sym in indicator_cache:
                df_s = indicator_cache[sym]
                if not df_s.empty:
                    cur_p = float(df_s["Close"].iloc[-1])
            u_pnl = (cur_p - ep) * sh
            shield_tag = "🟢+0.25R" if p.get("trail0_triggered") else ""
            if p.get("be_triggered"):
                shield_tag = "🛡️+0.90R"
            if p.get("trail2_triggered"):
                shield_tag = "🔒+1.50R"
            lines.append(
                f"  ▸ <b>{sym}</b> {shield_tag}: <code>{sh} acc.</code> | Ent: <code>{ep:.2f}$</code> | Act: <code>{cur_p:.2f}$</code> (<b>{u_pnl:+.2f}$</b>) | Stop: <code>{p['stop_loss']:.2f}$</code>"
            )
    return "\n".join(lines)

def build_radar_report(indicator_cache, breadth_pct):
    if not indicator_cache or BENCHMARK not in indicator_cache:
        return "⏳ Descargando datos de los 55 activos universales (repite /radar en 5s)..."
    try:
        df_qqq = indicator_cache[BENCHMARK]
        qqq_bull, qqq_ret = is_qqq_truly_bullish(df_qqq)

        rankings = []
        for sym in ALL_TICKERS:
            df_s = indicator_cache.get(sym)
            if df_s is None or len(df_s) < 55:
                continue
            closed_b = df_s.iloc[-2]
            live_b = df_s.iloc[-1]
            alpha = float(live_b["Day_Return_Pct"]) - qqq_ret
            rvol = max(float(closed_b["RVOL"]), float(live_b["RVOL"]))
            fuel_left = int(max(0.0, 100.0 - float(live_b["ADR_Used_Pct"])))
            htf_ok = (
                float(closed_b["Close"]) > float(closed_b["EMA_50"])
                and float(closed_b["Close"]) > float(closed_b["EMA_200"])
            )
            rankings.append({
                "sym": sym,
                "price": float(live_b["Close"]),
                "alpha": alpha,
                "rvol": rvol,
                "fuel": fuel_left,
                "htf_ok": htf_ok,
                "above_vwap": float(live_b["Close"]) > float(closed_b["VWAP"])
            })

        # El radar ordena priorizando acciones con tendencia 1H, volumen y gasolina disponible
        rankings.sort(key=lambda x: (1 if (x["above_vwap"] and x["htf_ok"] and x["fuel"] >= 28) else 0, x["rvol"], x["alpha"]), reverse=True)
        top = rankings[:12]
        regime_str = "🟢 MERCADO SANO (Permiso Activo)" if (qqq_bull and breadth_pct >= 35.0) else "🛑 ESCUDO DEFENSIVO ACTIVO (QQQ o Amplitud Débil)"

        msg = [
            f"🔭 <b>RADAR EARLY-IGNITION V5.5 (CAZADOR DE INICIO)</b>",
            f"• <b>QQQ:</b> <code>{qqq_ret:+.2f}%</code> | <b>Amplitud (&gt;VWAP):</b> <code>{breadth_pct:.1f}%</code>",
            f"• <b>Estado:</b> {regime_str}",
            f"\n🏆 <b>TOP 12 CANDIDATOS CON GASOLINA VIRGEN:</b>"
        ]
        for r in top:
            vw_icon = "🟢&gt;VWAP" if r["above_vwap"] else "🔴&lt;VWAP"
            msg.append(
                f"• <b>{r['sym']}</b>: <code>{r['price']:.2f}$</code> | RVOL <code>{r['rvol']:.1f}x</code> | ⛽Gasolina: <code>{r['fuel']}%</code> | Alpha <code>{r['alpha']:+.2f}%</code> | {vw_icon}"
            )
        return "\n".join(msg)
    except Exception as e:
        return f"⚠️ Error generando radar: {str(e)}"

def evaluate_signal_still_valid(sig, indicator_cache):
    sym = sig["symbol"]
    if not indicator_cache or sym not in indicator_cache:
        return "⏳ Datos actualizándose, vuelve a pulsar en 5 segundos."
    df_s = indicator_cache[sym]
    if df_s is None or df_s.empty:
        return "⚠️ Sin cotización en vivo."
    curr_p = float(df_s["Close"].iloc[-1])
    ema9_p = float(df_s["EMA_9"].iloc[-1])
    vwap_p = float(df_s["VWAP"].iloc[-1])
    min_v = float(sig.get("min_valid_entry", sig["price"] - 0.18 * sig["one_r"]))
    max_v = float(sig.get("max_valid_entry", sig["price"] + 0.22 * sig["one_r"]))

    if curr_p > max_v:
        return f"🔴 YA SE ESCAPÓ: {sym} cotiza a {curr_p:.2f}$ (Máximo válido para no comprar caro era {max_v:.2f}$)."
    if curr_p < min_v or curr_p < vwap_p or curr_p < (ema9_p * 0.998):
        return f"🔴 PERDIÓ SOPORTE: {sym} cayó a {curr_p:.2f}$ (bajo su arranque {min_v:.2f}$). Descártala."
    return f"🟢 EN ZONA DE INICIO VÁLIDA: {sym} cotiza a {curr_p:.2f}$ (Dentro de banda {min_v:.2f}$ - {max_v:.2f}$). Puedes entrar."

def process_telegram_updates(state, signals, indicator_cache=None, breadth_pct=50.0):
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
                send_telegram_message(build_portfolio_report(state, indicator_cache))

            elif low.startswith("/escanear") or low.startswith("/radar"):
                send_telegram_message(build_radar_report(indicator_cache, breadth_pct))

            elif low.startswith("/historial"):
                send_telegram_message(build_historial_audit_report(state))

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
                                f"• <b>Stop-Loss Activo:</b> <code>{pos['stop_loss']:.2f} $</code>\n"
                                f"• <b>Escudo Lateral (+1.15R):</b> En <code>{pos['trail_0_trigger']:.2f} $</code> sube Stop a <b><code>{pos['trail_0_stop']:.2f} $ (+0.25R)</code></b>\n"
                                f"• <b>Trinquete 1 (+1.65R):</b> En <code>{pos['trail_1_trigger']:.2f} $</code> sube Stop a <b><code>{pos['trail_1_stop']:.2f} $ (+0.90R)</code></b>\n"
                                f"• <b>Take-Profit (+2.40R):</b> <code>{pos['take_profit']:.2f} $</code>"
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
                                f"• Riesgo 1R Uniforme: <code>{new_cap * BASE_RISK_PCT:.2f} $ - {new_cap * HIGH_CONVICTION_RISK_PCT:.2f} $</code>"
                            )
                    except ValueError:
                        send_telegram_message("⚠️ Formato: <code>/capital 2000</code>")

            elif low.startswith("/ajustar"):
                parts = text.split()
                if len(parts) >= 3:
                    sym = parts[1].upper()
                    try:
                        new_sh = int(float(parts[2]))
                        if sym in state.get("open_positions", {}) and new_sh >= 1:
                            state["open_positions"][sym]["shares"] = new_sh
                            state["open_positions"][sym]["initial_shares"] = new_sh
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
                                f"• <b>Entrada:</b> <code>{rec['entry_price']:.2f}$</code> a <b>Salida:</b> <code>{rec['exit_price']:.2f}$</code>\n"
                                f"• <b>PnL Total Operación:</b> <b>{rec['pnl_usd']:+.2f} $ ({rec['pnl_pct']:+.2f}%)</b>\n"
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

            if cb_data.startswith("CHECK|"):
                sig_id = cb_data.split("|")[1]
                sig = signals.get(sig_id)
                if not sig:
                    answer_callback_query(cb_id, "⚠️ Señal expirada.", show_alert=True)
                    continue
                verdict_txt = evaluate_signal_still_valid(sig, indicator_cache)
                answer_callback_query(cb_id, verdict_txt, show_alert=True)

            elif cb_data.startswith("EXEC|"):
                sig_id = cb_data.split("|")[1]
                sig = signals.get(sig_id)
                if not sig:
                    answer_callback_query(cb_id, "⚠️ Señal expirada.")
                    continue
                sym = sig["symbol"]
                if sym in state.get("open_positions", {}):
                    answer_callback_query(cb_id, f"Ya tienes {sym} abierta.")
                    continue

                ep = float(sig["price"])
                sl_p = float(sig["stop_loss"])
                one_r_v = max(0.05, abs(ep - sl_p))
                sh_count = int(sig["shares"])
                state["open_positions"][sym] = {
                    "symbol": sym,
                    "sector": sig["sector"],
                    "engine": sig["engine"],
                    "shares": sh_count,
                    "initial_shares": sh_count,
                    "realized_partial_usd": 0.0,
                    "entry_price": ep,
                    "stop_loss": sl_p,
                    "initial_stop_loss": sl_p,
                    "take_profit": float(sig["take_profit"]),
                    "atr": float(sig["atr"]),
                    "one_r": round(one_r_v, 4),
                    "trail_0_trigger": round(ep + (TRAIL_0_TRIGGER_R * one_r_v), 2),
                    "trail_0_stop": round(ep + (TRAIL_0_LOCK_R * one_r_v), 2),
                    "trail_1_trigger": round(ep + (TRAIL_1_TRIGGER_R * one_r_v), 2),
                    "trail_1_stop": round(ep + (TRAIL_1_LOCK_R * one_r_v), 2),
                    "trail_2_trigger": round(ep + (TRAIL_2_TRIGGER_R * one_r_v), 2),
                    "trail_2_stop": round(ep + (TRAIL_2_LOCK_R * one_r_v), 2),
                    "trail0_triggered": False,
                    "be_triggered": False,
                    "trail2_triggered": False,
                    "timestop_alerted": False,
                    "opened_ts": time.time(),
                    "opened_at": now_madrid().strftime("%Y-%m-%d %H:%M:%S")
                }
                save_state(state, push_to_git=True, msg=f"Open position {sym} ({sh_count} sh)")
                answer_callback_query(cb_id, f"✅ {sym} registrada ({sh_count} acciones)")

                close_kb = {
                    "inline_keyboard": [[
                        {"text": f"🔴 Cerré {sym} en Quantfury (Precio Actual)", "callback_data": f"CLOSE|{sym}"}
                    ]]
                }
                edit_telegram_message(
                    chat_id, msg_id,
                    msg_obj.get("text", "") + f"\n\n✅ <b>POSICIÓN ABIERTA ({sh_count} ACCIONES)</b>\n<i>💡 Si tu precio en Quantfury varió, escribe:</i> <code>/entrada {sym} PRECIO</code>",
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
                if indicator_cache and sym in indicator_cache:
                    df_s = indicator_cache[sym]
                    if not df_s.empty:
                        exit_p = float(df_s["Close"].iloc[-1])

                rec = register_closed_trade(state, sym, exit_p, "👤 CIERRE MANUAL POR BOTÓN")
                answer_callback_query(cb_id, f"Cerrada {sym}")
                if rec:
                    edit_telegram_message(
                        chat_id, msg_id,
                        f"🏁 <b>POSICIÓN CERRADA EN {sym}</b>\n"
                        f"• <b>Salida:</b> <code>{rec['exit_price']:.2f} $</code>\n"
                        f"• <b>Resultado Neto Total:</b> <b>{rec['pnl_usd']:+.2f} $ ({rec['pnl_pct']:+.2f}%)</b>\n"
                        f"• <b>Capital Actual:</b> <code>{state['equity']:,.2f} $</code>"
                    )

# ==============================================================================
# ESCANER EARLY-IGNITION V5.5 (ORDENA POR FRESCURA DE DESPEGUE Y GASOLINA)
# ==============================================================================
def is_entry_window_open(curr_time):
    t = curr_time.hour + (curr_time.minute / 60.0)
    return 16.00 <= t <= 21.25

def scan_market_and_alert(state, signals, indicator_cache, breadth_pct):
    if not indicator_cache or BENCHMARK not in indicator_cache:
        return
    now_m = now_madrid()

    if not is_entry_window_open(now_m):
        return

    if get_total_daily_drawdown_pct(state, indicator_cache) <= MAX_DAILY_DRAWDOWN:
        return

    open_pos = state.get("open_positions", {})
    if len(open_pos) >= MAX_OPEN_POSITIONS:
        return

    if has_losing_open_position(state, indicator_cache):
        return

    if (time.time() - float(state.get("last_global_signal_ts", 0))) < SIGNAL_COOLDOWN_SECONDS:
        return

    df_qqq = indicator_cache.get(BENCHMARK)
    qqq_bullish, qqq_ret = is_qqq_truly_bullish(df_qqq)
    if not qqq_bullish:
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

        df_sym = indicator_cache.get(symbol)
        if df_sym is None:
            continue

        eval_res = evaluate_symbol_v5(symbol, df_sym, qqq_ret, qqq_bullish, breadth_pct, now_m)
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
        eval_res["breadth_pct"] = breadth_pct
        candidates.append(eval_res)

    if not candidates:
        return

    # V5.5: Prioriza la accion que esta iniciando el movimiento con mas gasolina restante (freshness_score)
    candidates.sort(key=lambda x: (1 if x["high_conviction"] else 0, x["freshness_score"], x["rvol"]), reverse=True)
    best = candidates[0]
    symbol = best["symbol"]

    sig_id = f"{symbol}_{int(time.time())}"
    signals[sig_id] = best
    save_signals(signals)

    state["cooldowns"][symbol] = (now_m + timedelta(minutes=35)).isoformat()
    state["last_global_signal_ts"] = time.time()
    save_state(state)

    conv_tag = f"🔥 <b>ALTA CONVICCIÓN (Riesgo 1R: -{best['risk_usd']:.2f} $)</b>" if best["high_conviction"] else f"✅ <b>ESTÁNDAR PROTEGIDO (Riesgo 1R: -{best['risk_usd']:.2f} $)</b>"
    free_after = max(0.0, get_free_buying_power(state) - best["cap_used"])
    one_r = best["one_r"]
    t0_trigger = round(best["price"] + (TRAIL_0_TRIGGER_R * one_r), 2)
    t0_stop = round(best["price"] + (TRAIL_0_LOCK_R * one_r), 2)
    t1_trigger = round(best["price"] + (TRAIL_1_TRIGGER_R * one_r), 2)
    t1_stop = round(best["price"] + (TRAIL_1_LOCK_R * one_r), 2)
    expected_tp_usd = round((best["take_profit"] - best["price"]) * best["shares"], 2)

    msg_text = (
        f"🎯 <b>SEÑAL EARLY-IGNITION V5.5 — {symbol} ({best['sector']})</b> 🎯\n"
        f"{conv_tag}\n"
        f"• <b>Motor:</b> <code>{best['engine']}</code>\n"
        f"• <b>⛽ Gasolina Diaria Virgen:</b> <b><code>{best['fuel_left_pct']}% de recorrido libre hoy</code></b>\n\n"
        f"🛒 <b>ORDEN EXACTA EN QUANTFURY:</b>\n"
        f"• <b>Acción:</b> <b>COMPRAR (LONG) <code>{best['shares']} acciones</code></b> de <b>{symbol}</b>\n"
        f"• <b>Precio Ref.:</b> <code>{best['price']:.2f} $</code>\n"
        f"• <b>✅ Rango Válido en App:</b> <b><code>{best['min_valid_entry']:.2f} $ a {best['max_valid_entry']:.2f} $</code></b>\n"
        f"• <b>Capital Usado:</b> <code>{best['cap_used']:,.2f} $</code> <i>(Quedarán {free_after:,.0f}$ libres)</i>\n\n"
        f"🛡️ <b>ESCUDO DE 3 NIVELES EN MÚLTIPLOS DE R:</b>\n"
        f"• 🛑 <b>Stop-Loss (-1.00R):</b> <b><code>{best['stop_loss']:.2f} $</code></b> <i>(-{best['risk_usd']:.2f} $)</i>\n"
        f"• 🟢 <b>Escudo Lateral (+1.15R):</b> En <code>{t0_trigger:.2f} $</code> subir Stop a <code>{t0_stop:.2f} $ (+0.25R)</code>\n"
        f"• 🛡️ <b>Trinquete 1 (+1.65R):</b> En <code>{t1_trigger:.2f} $</code> subir Stop a <code>{t1_stop:.2f} $ (+0.90R)</code>\n"
        f"• 🎯 <b>Take-Profit (+2.40R):</b> <b><code>{best['take_profit']:.2f} $</code></b> <i>(Ganancia: +{expected_tp_usd:.2f} $)</i>\n\n"
        f"📊 <b>Métricas de Ignición:</b>\n"
        f"• RVOL: <code>{best['rvol']:.2f}x</code> | Alpha: <code>{best['alpha']:+.2f}%</code> | Amplitud: <code>{best['breadth_pct']:.0f}%</code>"
    )

    kb = {
        "inline_keyboard": [
            [
                {"text": f"🟢 ENTRÉ ({best['shares']} acc.)", "callback_data": f"EXEC|{sig_id}"},
                {"text": "🗑️ Descartar", "callback_data": f"DISCARD|{sig_id}"}
            ],
            [
                {"text": "🔄 ¿Sigue en precio válido ahora?", "callback_data": f"CHECK|{sig_id}"}
            ]
        ]
    }
    send_telegram_message(msg_text, reply_markup=kb)

# ==============================================================================
# BUCLE CONTINUO DE SESION V5.5
# ==============================================================================
def run_continuous_session():
    state = load_state()
    signals = load_signals()

    now_m = now_madrid()
    free_bp = get_free_buying_power(state)
    m = compute_quant_audit_metrics(state)
    send_telegram_message(
        f"🟢 <b>OMNI-SCANNER EARLY-IGNITION V5.5 EN LÍNEA ({now_m.strftime('%H:%M:%S')} ESP)</b>\n"
        f"• <b>Poder Total:</b> <code>{state['equity']:,.2f} $</code> | <b>Libre:</b> <code>{free_bp:,.2f} $</code>\n"
        f"• <b>Mejoras V5.5:</b> <code>Ignición VWAP | ⛽Medidor Gasolina ADR | Cero Compras Tardías</code>\n"
        f"• <b>Auditoría Actual:</b> <code>WR {m['wr']:.1f}% | R:R 1:{m['rr_ratio']:.2f} | PF {m['pf']:.2f}</code>\n"
        f"• <b>Comandos:</b> <code>/historial</code>, <code>/cartera</code>, <code>/radar</code>, <code>/cerrar SIMB PRECIO</code>"
    )

    raw_data = fetch_omni_market_batch()
    indicator_cache, breadth_pct = build_indicator_cache_and_breadth(raw_data)
    last_scan_ts = time.time()

    while True:
        now_m = now_madrid()
        t_val = now_m.hour + (now_m.minute / 60.0)

        if t_val >= 22.04:
            save_state(state, push_to_git=True, msg="EOD Argon Quant v5.5 sync")
            send_telegram_message(
                f"🏁 <b>SESIÓN CERRADA EN WALL STREET (22:02 ESP)</b>\n\n"
                + build_historial_audit_report(state)
            )
            break

        process_telegram_updates(state, signals, indicator_cache, breadth_pct)

        if time.time() - last_scan_ts >= 60:
            last_scan_ts = time.time()
            if 15.45 <= t_val <= 22.02:
                new_raw = fetch_omni_market_batch()
                if new_raw is not None:
                    indicator_cache, breadth_pct = build_indicator_cache_and_breadth(new_raw)
                    monitor_open_positions(state, indicator_cache)
                    scan_market_and_alert(state, signals, indicator_cache, breadth_pct)

        time.sleep(3)

if __name__ == "__main__":
    run_continuous_session()
