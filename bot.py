import os
import json
import math
import time
import subprocess
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ==============================================================================
# ARGON QUANT V3.6 — EDICIÓN DINERO REAL (MODO CONTINUO HASTA 22:00 ESP)
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DEFAULT_TRADING_POWER = float(os.getenv("INITIAL_CAPITAL", "2000.0"))

STATE_FILE = "portfolio_state.json"
SPAIN_TZ = ZoneInfo("Europe/Madrid")
NY_TZ = ZoneInfo("America/New_York")
TIMEFRAME = "5m"

# Universo de Alto Beta en Quantfury (NYSE / NASDAQ Spot 0% Comisiones)
TRADE_WATCHLIST = ["NVDA", "AMD", "META", "TSLA", "PLTR", "COIN"]
SEC_CIK_MAP = {
    "NVDA": "0001045810",
    "AMD":  "0000002488",
    "META": "0001326801",
    "TSLA": "0001318605",
    "PLTR": "0001321655",
    "COIN": "0001679788"
}
REGIME_ORACLE = "QQQ"

# Parámetros Cuantitativos de Riesgo v3.6
BASE_RISK_PER_TRADE = 0.010        # 1.00% riesgo base sobre tu Poder de Trading
CONFLUENCE_RISK_PER_TRADE = 0.0135 # 1.35% en confluencia (ORB-15m, RS Líder o SEC Form 4)
MAX_DAILY_DRAWDOWN = -0.025        # Cortafuegos diario (-2.5%)
MAX_OPEN_POSITIONS = 4             # Máximo 4 posiciones simultáneas
MAX_POSITION_ALLOCATION = 0.50     # Máximo 50% de tu Poder de Trading por operación


# ==============================================================================
# MÓDULO 1: CONTABILIDAD PERSISTENTE E INMUNE A REINICIOS
# ==============================================================================
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("initial_capital", DEFAULT_TRADING_POWER)
                data.setdefault("equity", DEFAULT_TRADING_POWER)
                data.setdefault("peak_equity", data["equity"])
                data.setdefault("max_drawdown_pct", 0.0)
                data.setdefault("last_update_id", 0)
                data.setdefault("pending_signals", {})
                data.setdefault("open_positions", {})
                data.setdefault("closed_trades", [])
                return data
        except Exception as e:
            print(f"Error leyendo {STATE_FILE}: {e}")
    return {
        "initial_capital": DEFAULT_TRADING_POWER,
        "equity": DEFAULT_TRADING_POWER,
        "peak_equity": DEFAULT_TRADING_POWER,
        "max_drawdown_pct": 0.0,
        "last_update_id": 0,
        "pending_signals": {},
        "open_positions": {},
        "closed_trades": []
    }


def save_state(state: dict, push_to_git: bool = False):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error guardando archivo local: {e}")

    if push_to_git:
        try:
            subprocess.run(["git", "config", "--global", "user.name", "Argon Quant Bot"], check=False)
            subprocess.run(["git", "config", "--global", "user.email", "bot@argonquant.local"], check=False)
            subprocess.run(["git", "add", STATE_FILE], check=False)
            subprocess.run(["git", "commit", "-m", "Actualizacion contable Quantfury en vivo"], check=False)
            subprocess.run(["git", "pull", "--rebase", "-X", "ours"], check=False)
            subprocess.run(["git", "push"], check=False)
        except Exception as e:
            print(f"Aviso git push: {e}")


def get_daily_realized_pnl_pct(state: dict) -> float:
    """Calcula el % ganado o perdido hoy para el Cortafuegos Diario (-2.5%)."""
    today_prefix = datetime.now(SPAIN_TZ).strftime("%d/%m")
    today_pnl = sum(
        t.get("pnl", 0.0) for t in state.get("closed_trades", [])
        if str(t.get("exit_time", "")).startswith(today_prefix)
    )
    start_day_eq = state["equity"] - today_pnl
    if start_day_eq <= 0:
        return 0.0
    return today_pnl / start_day_eq


def get_performance_summary(state: dict) -> str:
    init_cap = state["initial_capital"]
    eq = state["equity"]
    net_pnl = eq - init_cap
    net_pct = (net_pnl / init_cap * 100.0) if init_cap > 0 else 0.0
    closed = state["closed_trades"]
    total_t = len(closed)

    if total_t == 0:
        return (
            f"📊 *ESTADO DE TU CUENTA QUANTFURY*\n"
            f"• *Poder de Trading Base:* `{init_cap:,.2f} $`\n"
            f"• *Balance Actual:* `{eq:,.2f} $` (`+0.00%`)\n"
            f"• *Operaciones Confirmadas:* `0`\n"
            f"• *Posiciones Abiertas:* `{len(state['open_positions'])}`"
        )

    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    win_rate = (len(wins) / total_t) * 100.0
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else 99.9
    sign = "+" if net_pnl >= 0 else ""

    return (
        f"📊 *LIBRO CONTABLE QUANTFURY (APEX v3.6)*\n"
        f"• *Capital Inicial:* `{init_cap:,.2f} $`\n"
        f"• *Capital Actual:* `{eq:,.2f} $`\n"
        f"• *Beneficio/Pérdida Neta:* `{sign}{net_pnl:,.2f} $` (`{sign}{net_pct:.2f}%`)\n"
        f"• *Operaciones Cerradas:* `{total_t}` (`{len(wins)}W` / `{len(losses)}L`)\n"
        f"• *Win Rate Real:* `{win_rate:.1f}%` | *Profit Factor:* `{pf:.2f}`\n"
        f"• *Max Drawdown:* `{state['max_drawdown_pct']:.2f}%`\n"
        f"• *Abiertas ahora mismo:* `{len(state['open_positions'])}`"
    )


# ==============================================================================
# MÓDULO 2: COMUNICACIÓN INTERACTIVA EN TELEGRAM
# ==============================================================================
def tg_api(method: str, payload: dict):
    if not TELEGRAM_BOT_TOKEN:
        return {}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Error Telegram API ({method}): {e}")
        return {}


def send_telegram(text: str, reply_markup: dict = None):
    print(text)
    if not TELEGRAM_CHAT_ID:
        return {}
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_api("sendMessage", payload)


def record_closed_trade(state: dict, sym: str, exit_price: float, reason: str):
    pos = state["open_positions"].get(sym)
    if not pos:
        return
    pnl = (exit_price - pos["entry_price"]) * pos["qty"]
    r_mult = pnl / pos["risk_dollars"] if pos["risk_dollars"] > 0 else 0.0

    state["equity"] = round(state["equity"] + pnl, 2)
    if state["equity"] > state["peak_equity"]:
        state["peak_equity"] = state["equity"]
    dd_pct = ((state["equity"] - state["peak_equity"]) / state["peak_equity"]) * 100.0
    if dd_pct < state["max_drawdown_pct"]:
        state["max_drawdown_pct"] = round(dd_pct, 2)

    now_str = datetime.now(SPAIN_TZ).strftime("%d/%m %H:%M")
    state["closed_trades"].append({
        "symbol": sym,
        "engine": pos["engine"],
        "entry_time": pos["entry_time"],
        "exit_time": now_str,
        "qty": pos["qty"],
        "notional_usd": round(pos["notional_usd"], 2),
        "entry_price": round(pos["entry_price"], 2),
        "exit_price": round(exit_price, 2),
        "pnl": round(pnl, 2),
        "r_multiple": round(r_mult, 2),
        "reason": reason
    })
    del state["open_positions"][sym]
    save_state(state, push_to_git=True)

    icono = "🟢 *OPERACIÓN GANADORA*" if pnl >= 0 else "🔴 *OPERACIÓN PERDEDORA*"
    sign = "+" if pnl >= 0 else ""
    send_telegram(
        f"{icono} — *{sym}* ({reason})\n"
        f"• *Entrada:* `{pos['entry_price']:.2f} $` ➔ *Salida:* `{exit_price:.2f} $`\n"
        f"• *Posición en Quantfury:* `{pos['notional_usd']:,.2f} $` (`{pos['qty']}` acc.)\n"
        f"• *Resultado Neto:* `{sign}{pnl:,.2f} $` (`{sign}{r_mult:.2f} R`)\n\n"
        + get_performance_summary(state)
    )


# ==============================================================================
# MÓDULO 3: INDICADORES CUANTITATIVOS Y MOTORES APEX V3.6
# ==============================================================================
def clean_yf_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.index.tz is not None:
        df.index = df.index.tz_convert(NY_TZ)
    return df


def compute_rsi(series: pd.Series, period: int = 2) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = df["High"].diff()
    down_move = -df["Low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    atr = compute_atr(df, period)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1/period, adjust=False).mean()


def enrich_intraday_features_v3(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_yf_df(df)
    if df.empty:
        return df
    df["Date"] = df.index.date
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
    df["TP_Vol"] = typical_price * df["Volume"]

    cum_vol = df.groupby("Date")["Volume"].cumsum().replace(0, np.nan)
    cum_tp_vol = df.groupby("Date")["TP_Vol"].cumsum()
    df["VWAP"] = cum_tp_vol / cum_vol

    df["Sq_Diff_Vol"] = ((typical_price - df["VWAP"]) ** 2) * df["Volume"]
    cum_sq_diff = df.groupby("Date")["Sq_Diff_Vol"].cumsum()
    df["VWAP_STD"] = np.sqrt(cum_sq_diff / cum_vol)

    df["VWAP_Upper_2_2"] = df["VWAP"] + (2.2 * df["VWAP_STD"])
    df["VWAP_Lower_2_2"] = df["VWAP"] - (2.2 * df["VWAP_STD"])

    df["EMA_9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["RSI_2"] = compute_rsi(df["Close"], period=2)
    df["RSI_14"] = compute_rsi(df["Close"], period=14)
    df["ATR"] = compute_atr(df, period=14)
    df["ADX"] = compute_adx(df, period=14)

    vol_ma = df["Volume"].rolling(20).mean().replace(0, np.nan)
    df["RVOL"] = df["Volume"] / vol_ma

    sma_20 = df["Close"].rolling(20).mean()
    std_20 = df["Close"].rolling(20).std()
    df["BB_Upper"] = sma_20 + (2.0 * std_20)
    df["BB_Lower"] = sma_20 - (2.0 * std_20)
    df["KC_Upper"] = sma_20 + (1.5 * df["ATR"])
    df["KC_Lower"] = sma_20 - (1.5 * df["ATR"])

    df["Squeeze_On"] = (df["BB_Lower"] > df["KC_Lower"]) & (df["BB_Upper"] < df["KC_Upper"])
    df["Recent_Squeeze"] = df["Squeeze_On"].shift(1).rolling(3).max() > 0

    day_open = df.groupby("Date")["Open"].transform("first")
    df["Intraday_Ret"] = (df["Close"] - day_open) / day_open

    df["Bar_Idx"] = df.groupby("Date").cumcount()
    orb_bars = df[df["Bar_Idx"] < 3]
    orb_high_map = orb_bars.groupby("Date")["High"].max()
    df["ORB_15_High"] = df["Date"].map(orb_high_map)
    return df


def check_sec_form4_catalyst(symbol: str) -> bool:
    cik = SEC_CIK_MAP.get(symbol)
    if not cik:
        return False
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    headers = {"User-Agent": "ArgonQuantCopilot/3.6 (quant@argonbot.org)"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code != 200:
            return False
        recent = r.json().get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        cutoff = (datetime.now(SPAIN_TZ).date() - timedelta(days=3)).isoformat()
        for f, d in zip(forms[:15], dates[:15]):
            if f == "4" and d >= cutoff:
                return True
    except Exception:
        pass
    return False


def get_qqq_oracle_state():
    df_q = yf.Ticker(REGIME_ORACLE).history(period="5d", interval=TIMEFRAME, prepost=False)
    df_q = enrich_intraday_features_v3(df_q)
    if df_q.empty or len(df_q) < 25:
        return False, 0.0
    last_q = df_q.iloc[-1]
    bullish = bool((last_q["Close"] > last_q["VWAP"]) or (last_q["EMA_9"] > last_q["EMA_21"]))
    return bullish, float(last_q["Intraday_Ret"])


def evaluate_bar_pair(symbol: str, last: pd.Series, prev: pd.Series, live_price: float, qqq_bullish: bool, qqq_ret: float, spain_hour_dec: float):
    price = float(live_price)
    bar_close = float(last["Close"])
    vwap = float(last["VWAP"])
    atr = float(last["ATR"])
    adx = float(last["ADX"])
    rvol = float(last["RVOL"])
    rsi_2 = float(last["RSI_2"])
    rsi_14 = float(last["RSI_14"])
    orb_15_high = float(last["ORB_15_High"])
    rs_alpha = float(last["Intraday_Ret"]) - qqq_ret

    if np.isnan(atr) or atr <= 0 or np.isnan(vwap):
        return None

    # MOTOR 4: Ruptura Rango Apertura ORB-15m (15:45 - 16:50 ESP)
    if (
        qqq_bullish
        and (15.75 <= spain_hour_dec <= 16.85)
        and rs_alpha > 0.0015
        and not np.isnan(orb_15_high)
        and prev["Close"] <= orb_15_high
        and bar_close > orb_15_high
        and price > orb_15_high
        and price > vwap
        and rvol >= 1.65
        and 54 <= rsi_14 <= 76
    ):
        return {
            "symbol": symbol,
            "strategy": "Motor 4: Ruptura Rango Apertura (ORB-15m + RS Alpha)",
            "price": price,
            "atr": atr,
            "stop_loss": round(price - (1.35 * atr), 2),
            "take_profit": round(price + (3.20 * atr), 2),
            "high_conviction": True,
            "metrics": f"ORB Break: {orb_15_high:.2f}$ | RS Alpha: +{rs_alpha*100:.2f}% | RVOL: {rvol:.2f}x"
        }

    # MOTOR 2: Explosión Volatilidad Squeeze + RS Alpha
    if (
        qqq_bullish
        and rs_alpha >= -0.0015
        and bool(last["Recent_Squeeze"])
        and bar_close > last["BB_Upper"]
        and price > vwap
        and rvol >= 1.30
    ):
        return {
            "symbol": symbol,
            "strategy": "Motor 2: Explosión Volatilidad (Squeeze + RS Alpha)",
            "price": price,
            "atr": atr,
            "stop_loss": round(price - (1.40 * atr), 2),
            "take_profit": round(price + (3.20 * atr), 2),
            "high_conviction": bool(rs_alpha > 0.002 and rvol >= 1.60),
            "metrics": f"Squeeze Break | RS Alpha: {rs_alpha*100:+.2f}% | RVOL: {rvol:.2f}x"
        }

    # MOTOR 1: Impulso Institucional VWAP + RVOL + RS Alpha
    if (
        qqq_bullish
        and rs_alpha > 0.0005
        and rvol >= 1.60
        and adx >= 24
        and price > vwap
        and last["EMA_9"] > last["EMA_21"]
        and bar_close > prev["High"]
        and price >= prev["High"]
        and 55 <= rsi_14 <= 74
    ):
        return {
            "symbol": symbol,
            "strategy": "Motor 1: Impulso Institucional (VWAP+RVOL+RS)",
            "price": price,
            "atr": atr,
            "stop_loss": round(price - (1.50 * atr), 2),
            "take_profit": round(price + (3.00 * atr), 2),
            "high_conviction": bool(rs_alpha > 0.0035 and rvol >= 1.85),
            "metrics": f"RS Alpha: +{rs_alpha*100:.2f}% | RVOL: {rvol:.2f}x | ADX: {adx:.1f}"
        }

    # MOTOR 3: Reversión Institucional con Capitulación (VWAP -2.2σ)
    if (
        adx < 22
        and prev["Low"] <= prev["VWAP_Lower_2_2"]
        and rsi_2 < 8
        and rvol >= 1.30
        and bar_close > last["Open"]
    ):
        return {
            "symbol": symbol,
            "strategy": "Motor 3: Reversión Institucional (VWAP -2.2σ)",
            "price": price,
            "atr": atr,
            "stop_loss": round(price - (1.20 * atr), 2),
            "take_profit": round(max(vwap, price + (1.75 * atr)), 2),
            "high_conviction": False,
            "metrics": f"RSI(2): {rsi_2:.1f} | Capitulación RVOL: {rvol:.2f}x"
        }

    return None


def evaluate_symbol_v3(symbol: str, df: pd.DataFrame, qqq_bullish: bool, qqq_ret: float, spain_hour_dec: float):
    """
    Audita tanto la vela en curso como la vela de 5m recién cerrada para no perder
    ninguna señal si el escáner corre en los primeros segundos de una vela nueva.
    """
    live_price = float(df.iloc[-1]["Close"])
    sig_live = evaluate_bar_pair(symbol, df.iloc[-1], df.iloc[-2], live_price, qqq_bullish, qqq_ret, spain_hour_dec)
    if sig_live is not None:
        return sig_live
    if len(df) >= 4:
        return evaluate_bar_pair(symbol, df.iloc[-2], df.iloc[-3], live_price, qqq_bullish, qqq_ret, spain_hour_dec)
    return None


def send_live_radar_report():
    """Responde al comando /escanear con la telemetría en vivo de Wall Street."""
    now_str = datetime.now(SPAIN_TZ).strftime("%H:%M:%S")
    qqq_bull, qqq_ret = get_qqq_oracle_state()
    q_tag = "🟢 ALCISTA (>VWAP)" if qqq_bull else "🔴 BAJISTA (<VWAP)"
    lines = [
        f"📡 *RADAR EN VIVO WALL STREET ({now_str} ESP)*",
        f"• *Oráculo Nasdaq (QQQ):* {q_tag} (`{qqq_ret*100:+.2f}%` hoy)\n"
    ]
    for sym in TRADE_WATCHLIST:
        try:
            df_s = yf.Ticker(sym).history(period="5d", interval=TIMEFRAME, prepost=False)
            df_s = enrich_intraday_features_v3(df_s)
            if df_s.empty:
                continue
            last = df_s.iloc[-1]
            prev = df_s.iloc[-2]
            p = float(last["Close"])
            v = float(last["VWAP"])
            # Si la vela nueva acaba de abrir hace segundos, mostrar RVOL de la recién cerrada
            rv = float(last["RVOL"]) if float(last["RVOL"]) >= 0.4 else float(prev["RVOL"])
            rsi = float(last["RSI_14"])
            alpha = (float(last["Intraday_Ret"]) - qqq_ret) * 100
            sq = "🔥Squeeze" if bool(last["Recent_Squeeze"]) else "Normal"
            lines.append(
                f"• *{sym}*: `{p:.2f}$` (VWAP `{v:.2f}$`) | RVOL `{rv:.1f}x` | RSI `{rsi:.0f}` | Alpha `{alpha:+.2f}%` | {sq}"
            )
        except Exception:
            pass
    send_telegram("\n".join(lines))


# ==============================================================================
# MÓDULO 4: ESCUCHA DE BOTONES Y COMANDOS EN TIEMPO REAL
# ==============================================================================
def process_telegram_interactions(state: dict):
    if not TELEGRAM_BOT_TOKEN:
        return
    offset = state.get("last_update_id", 0) + 1
    res = tg_api("getUpdates", {"offset": offset, "timeout": 2})
    updates = res.get("result", [])

    for upd in updates:
        state["last_update_id"] = upd["update_id"]

        if "callback_query" in upd:
            cb = upd["callback_query"]
            cb_id = cb["id"]
            data = cb.get("data", "")
            msg = cb.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            msg_id = msg.get("message_id")

            if data.startswith("EXEC|"):
                sig_id = data.split("|")[1]
                sig = state["pending_signals"].get(sig_id)
                if not sig:
                    tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Señal ya procesada."})
                    continue

                sym = sig["symbol"]
                state["open_positions"][sym] = sig
                del state["pending_signals"][sig_id]
                save_state(state, push_to_git=True)

                tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"✅ {sym} registrada en cartera!"})
                close_kb = {
                    "inline_keyboard": [[
                        {"text": f"🔴 Cerré {sym} Manualmente en Quantfury", "callback_data": f"CLOSE|{sym}"}
                    ]]
                }
                new_text = (
                    msg.get("text", "")
                    + f"\n\n✅ CONFIRMADA EN QUANTFURY ({datetime.now(SPAIN_TZ).strftime('%H:%M:%S')} ESP)\n"
                    f"El bot vigila ahora tu Trinquete de 2 Niveles, Take-Profit y Stop-Loss."
                )
                tg_api("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": new_text,
                    "reply_markup": close_kb
                })

            elif data.startswith("SKIP|"):
                sig_id = data.split("|")[1]
                sig = state["pending_signals"].pop(sig_id, None)
                save_state(state)
                tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Operación descartada."})
                sym_name = sig["symbol"] if sig else ""
                tg_api("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": f"⚪ Señal en {sym_name} descartada. (No afecta a tu contabilidad)."
                })

            elif data.startswith("CLOSE|"):
                sym = data.split("|")[1]
                if sym not in state["open_positions"]:
                    tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"{sym} ya estaba cerrada."})
                    continue
                try:
                    df_now = clean_yf_df(yf.Ticker(sym).history(period="1d", interval="1m"))
                    live_price = float(df_now["Close"].iloc[-1]) if not df_now.empty else state["open_positions"][sym]["entry_price"]
                except Exception:
                    live_price = state["open_positions"][sym]["entry_price"]

                tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"Cerrando {sym} a {live_price:.2f}$..."})
                record_closed_trade(state, sym, live_price, "Cierre Manual en Quantfury")

        elif "message" in upd:
            text = upd["message"].get("text", "").strip()
            if text.startswith("/cartera") or text.startswith("/estado"):
                msg_out = get_performance_summary(state)
                if state["open_positions"]:
                    msg_out += "\n\n📂 *POSICIONES ABIERTAS AHORA MISMO:*"
                    for s, p in state["open_positions"].items():
                        try:
                            df_live = clean_yf_df(yf.Ticker(s).history(period="1d", interval="1m"))
                            cur_p = float(df_live["Close"].iloc[-1])
                            lat_pnl = (cur_p - p["entry_price"]) * p["qty"]
                            sign = "+" if lat_pnl >= 0 else ""
                            msg_out += f"\n• *{s}*: Entrada `{p['entry_price']:.2f}$` | Actual `{cur_p:.2f}$` (`{sign}{lat_pnl:.2f}$`) | Stop: `{p['stop_loss']:.2f}$`"
                        except Exception:
                            msg_out += f"\n• *{s}*: Entrada `{p['entry_price']:.2f}$` | Stop: `{p['stop_loss']:.2f}$`"
                send_telegram(msg_out)

            elif text.startswith("/escanear") or text.startswith("/radar"):
                send_live_radar_report()

            elif text.startswith("/historial"):
                closed = state["closed_trades"][-10:]
                if not closed:
                    send_telegram("📭 Aún no has cerrado ninguna operación confirmada.")
                else:
                    lines = ["📜 *ÚLTIMAS 10 OPERACIONES EN QUANTFURY:*"]
                    for t in closed:
                        s_pnl = f"+{t['pnl']:.2f}$" if t["pnl"] >= 0 else f"{t['pnl']:.2f}$"
                        lines.append(f"• `{t['exit_time']}` | *{t['symbol']}* | `{s_pnl}` (`{t['r_multiple']:+.2f}R`) — _{t['reason']}_")
                    send_telegram("\n".join(lines))

            elif text.startswith("/capital"):
                parts = text.split()
                if len(parts) >= 2:
                    try:
                        new_cap = float(parts[1].replace(",", ""))
                        state["initial_capital"] = new_cap
                        state["equity"] = new_cap
                        state["peak_equity"] = new_cap
                        state["max_drawdown_pct"] = 0.0
                        save_state(state, push_to_git=True)
                        send_telegram(f"✅ *Poder de Trading de Quantfury actualizado a:* `{new_cap:,.2f} $`")
                    except ValueError:
                        send_telegram("⚠️ Formato: `/capital 2000`")

    save_state(state)


# ==============================================================================
# MÓDULO 5: VIGILANCIA DE POSICIONES Y DISPARO DE TARJETAS INTERACTIVAS
# ==============================================================================
def monitor_confirmed_positions(state: dict, spain_hour_dec: float):
    open_pos = state["open_positions"]
    if not open_pos:
        return

    for sym in list(open_pos.keys()):
        pos = open_pos[sym]
        try:
            df = clean_yf_df(yf.Ticker(sym).history(period="2d", interval=TIMEFRAME, prepost=False))
            if df.empty:
                continue
            last = df.iloc[-1]
            low_p = float(last["Low"])
            high_p = float(last["High"])
            close_p = float(last["Close"])
            atr = pos["atr"]

            if low_p <= pos["stop_loss"]:
                reason = (
                    "Trinquete Nivel 2 (+1.1R)" if pos.get("stage", 0) == 2
                    else ("Escudo Break-Even (+0.2R)" if pos.get("stage", 0) == 1 else "Stop-Loss")
                )
                record_closed_trade(state, sym, pos["stop_loss"], reason)
                continue

            if high_p >= pos["take_profit"]:
                record_closed_trade(state, sym, pos["take_profit"], "Take-Profit Total")
                continue

            if spain_hour_dec >= 21.88:
                send_telegram(
                    f"⏰ *SON LAS 21:55 ESP — CIERRA {sym} EN QUANTFURY*\n"
                    f"Cerrando contabilidad al precio actual (`{close_p:.2f} $`) para dormir con 0% riesgo nocturno."
                )
                record_closed_trade(state, sym, close_p, "Cierre Sesión (21:55 ESP)")
                continue

            stage = pos.get("stage", 0)
            if stage < 2 and high_p >= pos["entry_price"] + (2.35 * atr):
                new_sl = round(pos["entry_price"] + (1.55 * atr), 2)
                pos["stop_loss"] = new_sl
                pos["stage"] = 2
                save_state(state, push_to_git=True)
                send_telegram(
                    f"🔒 *TRINQUETE NIVEL 2 EN {sym} (+1.1R ASEGURADO)*\n"
                    f"• El precio ya alcanzó `{high_p:.2f} $` (+2.35x ATR).\n"
                    f"👉 *Entra en Quantfury y sube tu Stop Order a:* `{new_sl:.2f} $`"
                )
            elif stage < 1 and high_p >= pos["entry_price"] + (1.60 * atr):
                new_sl = round(pos["entry_price"] + (0.30 * atr), 2)
                pos["stop_loss"] = new_sl
                pos["stage"] = 1
                save_state(state, push_to_git=True)
                send_telegram(
                    f"🛡️ *ESCUDO BREAK-EVEN EN {sym} (+0.2R ASEGURADO)*\n"
                    f"• El precio ya alcanzó `{high_p:.2f} $` (+1.60x ATR).\n"
                    f"👉 *Entra en Quantfury y sube tu Stop Order a:* `{new_sl:.2f} $`"
                )
        except Exception as e:
            print(f"Error vigilando {sym}: {e}")


def scan_market_and_alert(state: dict):
    now_spain = datetime.now(SPAIN_TZ)
    spain_hour_dec = now_spain.hour + (now_spain.minute / 60.0)
    hora_str = now_spain.strftime("%H:%M")

    if now_spain.weekday() >= 5 or spain_hour_dec < 15.5 or spain_hour_dec > 22.05:
        return

    monitor_confirmed_positions(state, spain_hour_dec)

    # Cortafuegos Diario (-2.5%) para proteger dinero real
    if get_daily_realized_pnl_pct(state) <= MAX_DAILY_DRAWDOWN:
        return

    # Ventanas Prime en Hora Española (15:40-18:00 y 19:45-21:15)
    if spain_hour_dec < 15.66 or (18.0 <= spain_hour_dec < 19.75) or spain_hour_dec >= 21.25:
        return

    if len(state["open_positions"]) >= MAX_OPEN_POSITIONS:
        return

    qqq_bullish, qqq_ret = get_qqq_oracle_state()
    ranked_candidates = []

    for sym in TRADE_WATCHLIST:
        if sym in state["open_positions"]:
            continue
        try:
            df_sym = yf.Ticker(sym).history(period="5d", interval=TIMEFRAME, prepost=False)
            df_sym = enrich_intraday_features_v3(df_sym)
            if df_sym.empty or len(df_sym) < 40:
                continue
            rs_alpha = float(df_sym.iloc[-1]["Intraday_Ret"]) - qqq_ret
            ranked_candidates.append((rs_alpha, sym, df_sym))
        except Exception as e:
            print(f"Error descargando {sym}: {e}")

    ranked_candidates.sort(key=lambda x: x[0], reverse=True)

    for rs_alpha, sym, df_sym in ranked_candidates:
        signal = evaluate_symbol_v3(sym, df_sym, qqq_bullish, qqq_ret, spain_hour_dec)
        if not signal:
            continue

        # Evitar duplicar la misma alerta en los últimos 15 minutos
        already_alerted = False
        for old_id, old_sig in state["pending_signals"].items():
            if old_sig.get("symbol") == sym:
                already_alerted = True
                break
        if already_alerted:
            continue

        sec_catalyst = check_sec_form4_catalyst(sym)
        is_high_conf = signal["high_conviction"] or sec_catalyst
        trade_risk_pct = CONFLUENCE_RISK_PER_TRADE if is_high_conf else BASE_RISK_PER_TRADE

        equity = state["equity"]
        risk_dollars = equity * trade_risk_pct
        stop_dist = abs(signal["price"] - signal["stop_loss"])
        if stop_dist <= 0:
            continue

        raw_shares = risk_dollars / stop_dist
        max_shares = (equity * MAX_POSITION_ALLOCATION) / signal["price"]
        qty = round(min(raw_shares, max_shares), 2)
        notional_usd = round(qty * signal["price"], 2)

        if notional_usd < 10:
            continue

        sig_id = f"{sym}_{now_spain.strftime('%H%M')}"
        state["pending_signals"][sig_id] = {
            "symbol": sym,
            "engine": signal["strategy"],
            "entry_time": now_spain.strftime("%d/%m %H:%M"),
            "entry_price": round(signal["price"], 2),
            "qty": qty,
            "notional_usd": notional_usd,
            "atr": round(signal["atr"], 4),
            "stop_loss": signal["stop_loss"],
            "take_profit": signal["take_profit"],
            "risk_dollars": round(qty * stop_dist, 2),
            "stage": 0
        }
        save_state(state)

        conv_label = "🔥 *ALTA CONVICCIÓN (1.35% Riesgo)*" if is_high_conf else "✅ *ESTÁNDAR (1.00% Riesgo)*"
        sec_label = " | 🏛️ *SEC Form 4*" if sec_catalyst else ""

        inline_kb = {
            "inline_keyboard": [[
                {"text": f"🟢 ENTRÉ EN QUANTFURY ({sym})", "callback_data": f"EXEC|{sig_id}"},
                {"text": "⚪ Ignorar", "callback_data": f"SKIP|{sig_id}"}
            ]]
        }

        send_telegram(
            f"🎯 *SEÑAL QUANTFURY ({sym}) — {hora_str} ESP*\n"
            f"• *Motor:* {signal['strategy']}\n"
            f"• *Nivel:* {conv_label}{sec_label}\n"
            f"────────────────────────\n"
            f"💵 *DATOS PARA METER EN QUANTFURY:*\n"
            f"• *Acción (LONG):* `{sym}` a `~{signal['price']:.2f} $`\n"
            f"• *Tamaño a Comprar:* **`{notional_usd:,.2f} $`** (`{qty}` acciones)\n"
            f"• *Stop Order (SL):* **`{signal['stop_loss']:.2f} $`** _(Riesgo: -{qty*stop_dist:.2f}$)_\n"
            f"• *Target Order (TP):* **`{signal['take_profit']:.2f} $`** _(Beneficio: +{(signal['take_profit']-signal['price'])*qty:.2f}$)_\n"
            f"────────────────────────\n"
            f"📡 `{signal['metrics']}`\n\n"
            f"👇 *Pulsa el botón verde si has abierto la operación:*",
            reply_markup=inline_kb
        )


def run_continuous_session():
    state = load_state()
    now_str = datetime.now(SPAIN_TZ).strftime("%H:%M:%S")
    send_telegram(
        f"🟢 *COPILOTO QUANTFURY V3.6 EN LÍNEA ({now_str} ESP)*\n"
        f"• *Capital Activo:* `{state['equity']:,.2f} $`\n"
        f"• *Modo Continuo:* Encendido sin interrupciones hasta el cierre de sesión (21:55 ESP).\n"
        f"• *Comandos en vivo:* `/cartera`, `/escanear`, `/historial`, `/capital`"
    )

    last_scan_time = 0.0
    start_wall = time.time()

    # Permanece activo de forma ininterrumpida hasta las 22:00 ESP (máximo 5h 45m por turno)
    while time.time() - start_wall < 20700:
        now_spain = datetime.now(SPAIN_TZ)
        spain_hour_dec = now_spain.hour + (now_spain.minute / 60.0)

        # 1. Escuchar tus botones y comandos en Telegram cada 2 segundos
        process_telegram_interactions(state)

        # 2. Escanear el mercado y vigilar posiciones cada 60 segundos
        if time.time() - last_scan_time >= 60:
            last_scan_time = time.time()
            scan_market_and_alert(state)

        # 3. Apagado limpio tras el cierre de Wall Street (22:02 ESP)
        if spain_hour_dec >= 22.03 and now_spain.hour >= 22:
            save_state(state, push_to_git=True)
            send_telegram("🔔 *Sesión de Wall Street finalizada (22:00 ESP).* Libro contable guardado.")
            break

        time.sleep(2)


if __name__ == "__main__":
    run_continuous_session()
