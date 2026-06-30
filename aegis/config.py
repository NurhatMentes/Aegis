import os
from dotenv import load_dotenv

# Load variables from .env file
load_dotenv()

# API Configuration
OKX_API_KEY = os.getenv("OKX_API_KEY", "")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
OKX_IS_SIMULATED = os.getenv("OKX_IS_SIMULATED", "True").lower() in ("true", "1", "yes")

# State File Path
STATE_FILE_PATH = os.getenv("STATE_FILE_PATH", "aegis_state.json")

# n8n Post-Close Reentry Webhook URL
N8N_REENTRY_WEBHOOK_URL = os.getenv("N8N_REENTRY_WEBHOOK_URL", "")

# OKX API Endpoints
if OKX_IS_SIMULATED:
    OKX_REST_URL = "https://www.okx.com"
    OKX_WS_PUBLIC = "wss://wspap.okx.com:8443/ws/v5/public"
    OKX_WS_PRIVATE = "wss://wspap.okx.com:8443/ws/v5/private"
else:
    OKX_REST_URL = "https://www.okx.com"
    OKX_WS_PUBLIC = "wss://ws.okx.com:8443/ws/v5/public"
    OKX_WS_PRIVATE = "wss://ws.okx.com:8443/ws/v5/private"

# ============================================================
# COIN PROFILES — V2 (ATR Bazlı Trailing Gap Kalibrasyonu)
# ============================================================
# trailing_gap_atr: min_trailing_gap = trailing_gap_atr × ATR
# Formül: target_gap / median_ATR
#
# Log analizi (V4.x dönemi, 105 trade):
#   BCH  ATR=0.53% → target=%0.60 → çarpan=1.13x (kazananların %90'ı %0.60+ gidiyor)
#   SOL  ATR=0.51% → target=%0.55 → çarpan=1.08x
#   AVAX ATR=0.43% → target=%0.50 → çarpan=1.16x (kazananların %80'i %0.50+ gidiyor)
#   AAVE ATR=0.40% → target=%0.45 → çarpan=1.12x
#   DOGE ATR=0.41% → target=%0.45 → çarpan=1.10x
#   SHIB ATR=0.35% → target=%0.35 → çarpan=1.00x (kazananların %21'i %0.60+ — genişletme)
#   ETH  ATR=0.27% → target=%0.35 → çarpan=1.32x (mevcut yeterli)
#   BTC  ATR=0.26% → target=%0.35 → çarpan=1.35x (BTC zaten özel TP)
#
# initial_tp_trigger_pct: Skynet TP'sinin kaçta kaçında Eşik1 tetiklensin
#   Panel'den ayarlanan %90 değeri bu değerle çarpılır (Aegis paneli)
#   Buradaki değer coin bazlı ince ayar — DEFAULT 0.35 (panel ayarını destekler)
# ============================================================

COIN_PROFILES = {
    # ── BCH: ATR yüksek, trend güçlü → geniş trailing ──
    "BCH-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.35,
        "trailing_gap_atr":       1.13,   # target %0.60 gap (ATR=0.53)
        "limit_tolerance":        0.01,
        "avg_volume":             800000
    },

    # ── SOL: ATR yüksek, trend sürer → geniş trailing ──
    "SOL-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.35,
        "trailing_gap_atr":       1.08,   # target %0.55 gap (ATR=0.51)
        "limit_tolerance":        0.01,
        "avg_volume":             2000000
    },

    # ── AVAX: ATR orta-yüksek, momentum uzun → geniş trailing ──
    "AVAX-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.35,
        "trailing_gap_atr":       1.16,   # target %0.50 gap (ATR=0.43)
        "limit_tolerance":        0.01,
        "avg_volume":             1500000
    },

    # ── AAVE: ATR orta → biraz geniş trailing ──
    "AAVE-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.35,
        "trailing_gap_atr":       1.12,   # target %0.45 gap (ATR=0.40)
        "limit_tolerance":        0.01,
        "avg_volume":             500000
    },

    # ── DOGE: ATR orta, iğneli → biraz geniş trailing ──
    "DOGE-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.35,
        "trailing_gap_atr":       1.10,   # target %0.45 gap (ATR=0.41)
        "limit_tolerance":        0.015,
        "avg_volume":             3000000
    },

    # ── PEPE: küçük birim, iğneli → dar trailing, tolerans geniş ──
    "PEPE-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.30,   # mevcut değer korunuyor
        "trailing_gap_atr":       1.06,   # target %0.45 gap (ATR=0.43)
        "limit_tolerance":        0.02,
        "avg_volume":             500000
    },

    # ── SHIB: ATR düşük, kazananlar az gidiyor → mevcut iyi ──
    "SHIB-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.35,
        "trailing_gap_atr":       1.00,   # target %0.35 gap (ATR=0.35) — genişletme yok
        "limit_tolerance":        0.02,   # küçük birim fiyatı → geniş tolerans
        "avg_volume":             2000000
    },

    # ── ETH: ATR düşük, mevcut yeterli ──
    "ETH-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.35,
        "trailing_gap_atr":       1.32,   # target %0.35 gap (ATR=0.27)
        "limit_tolerance":        0.005,
        "avg_volume":             5000000
    },

    # ── BTC: özel parametreli, mevcut korunuyor ──
    "BTC-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.40,   # mevcut değer korunuyor
        "trailing_gap_atr":       1.35,   # target %0.35 gap (ATR=0.26)
        "limit_tolerance":        0.005,
        "avg_volume":             12000000
    },

    # ── DEFAULT: tanımsız coinler için güvenli fallback ──
    "DEFAULT": {
        "initial_tp_trigger_pct": 0.35,
        "trailing_gap_atr":       1.0,
        "limit_tolerance":        0.01,
        "avg_volume":             1000000
    }
}


def get_coin_profile(inst_id: str) -> dict:
    """
    Returns the coin profile for the specified instrument ID.
    If not explicitly listed, returns the DEFAULT fallback profile.
    """
    return COIN_PROFILES.get(inst_id, COIN_PROFILES["DEFAULT"])


def safe_float(val, default=0.0) -> float:
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default