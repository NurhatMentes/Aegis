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

# OKX API Endpoints
if OKX_IS_SIMULATED:
    # Demo/Simulated Trading URL
    # Note: Demotrading REST requests go to the standard domain, but require the `x-simulated-trading: 1` header
    OKX_REST_URL = "https://www.okx.com"
    OKX_WS_PUBLIC = "wss://wspap.okx.com:8443/ws/v5/public"
    OKX_WS_PRIVATE = "wss://wspap.okx.com:8443/ws/v5/private"
else:
    # Live/Production URL
    OKX_REST_URL = "https://www.okx.com"
    OKX_WS_PUBLIC = "wss://ws.okx.com:8443/ws/v5/public"
    OKX_WS_PRIVATE = "wss://ws.okx.com:8443/ws/v5/private"

# Coin Profiles calibrated by log files
COIN_PROFILES = {
    "PEPE-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.30,  # 30% of the target tp ratio triggers partial kar al (TP1)
        "trailing_gap_atr": 0.5,         # Trailing gap multiplier * atr
        "limit_tolerance": 0.02,         # 2% slippage buffer for limit orders
        "avg_volume": 500000             # Average 1-minute volume in USDT (calibrated)
    },
    "BTC-USDT-SWAP": {
        "initial_tp_trigger_pct": 0.40,  # 40% of the target tp ratio triggers partial kar al (TP1)
        "trailing_gap_atr": 1.5,         # Trailing gap multiplier * atr
        "limit_tolerance": 0.005,        # 0.5% slippage buffer for limit orders
        "avg_volume": 12000000           # Average 1-minute volume in USDT (calibrated)
    },
    "DEFAULT": {
        "initial_tp_trigger_pct": 0.35,  # 35% of the target tp ratio triggers partial kar al (TP1)
        "trailing_gap_atr": 1.0,         # Trailing gap multiplier * atr
        "limit_tolerance": 0.01,         # 1% slippage buffer for limit orders
        "avg_volume": 1000000            # Default average 1-minute volume in USDT
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

