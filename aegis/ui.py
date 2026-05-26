import json
import time
import os
import textwrap
from datetime import datetime
import streamlit as st
import pandas as pd

# Streamlit Page Config
st.set_page_config(
    page_title="Aegis HFT Trading Desk",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Custom Sleek CSS for HFT Visuals (Glassmorphism, Neon Glow, Dark Theme)
st.markdown("""
<style>
    /* Dark Slate Background */
    .stApp {
        background-color: #0b0f19;
        color: #e2e8f0;
        font-family: 'Inter', -apple-system, sans-serif;
    }
    
    /* Header styling */
    .header-container {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 1rem 2rem;
        background: linear-gradient(90deg, #1e293b 0%, #0f172a 100%);
        border-radius: 12px;
        margin-bottom: 1.5rem;
        border: 1px solid #334155;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
    }
    
    .header-title {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #38bdf8 0%, #3b82f6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    
    /* Status Badge styling */
    .status-badge {
        padding: 0.5rem 1rem;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.85rem;
        display: flex;
        align-items: center;
        gap: 8px;
        border: 1px solid;
    }
    
    .status-online {
        background-color: rgba(16, 185, 129, 0.1);
        color: #10b981;
        border-color: rgba(16, 185, 129, 0.3);
        box-shadow: 0 0 10px rgba(16, 185, 129, 0.2);
    }
    
    .status-offline {
        background-color: rgba(239, 68, 68, 0.1);
        color: #ef4444;
        border-color: rgba(239, 68, 68, 0.3);
        box-shadow: 0 0 10px rgba(239, 68, 68, 0.2);
    }
    
    /* Glassmorphic Position Cards */
    .position-card {
        background: rgba(30, 41, 59, 0.6);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 16px;
        padding: 1.5rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        transition: transform 0.2s ease, border-color 0.2s ease;
    }
    
    .position-card:hover {
        border-color: rgba(56, 189, 248, 0.3);
        transform: translateY(-2px);
    }
    
    .card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        padding-bottom: 0.75rem;
        margin-bottom: 1rem;
    }
    
    .card-symbol {
        font-size: 1.4rem;
        font-weight: 700;
        color: #f8fafc;
    }
    
    /* Trade Side Badges */
    .side-badge {
        padding: 0.25rem 0.6rem;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 700;
        text-transform: uppercase;
    }
    
    .side-long {
        background-color: #059669;
        color: #ecfdf5;
    }
    
    .side-short {
        background-color: #dc2626;
        color: #fef2f2;
    }
    
    /* FSM State Badges */
    .state-badge {
        padding: 0.3rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.8rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    
    .state-init {
        background-color: rgba(245, 158, 11, 0.15);
        color: #f59e0b;
        border: 1px solid rgba(245, 158, 11, 0.3);
    }
    
    .state-risk_zero {
        background-color: rgba(59, 130, 246, 0.15);
        color: #3b82f6;
        border: 1px solid rgba(59, 130, 246, 0.3);
    }
    
    .state-trailing {
        background-color: rgba(139, 92, 246, 0.15);
        color: #8b5cf6;
        border: 1px solid rgba(139, 92, 246, 0.3);
        animation: pulse 2s infinite;
    }
    
    .state-closed {
        background-color: rgba(100, 116, 139, 0.15);
        color: #64748b;
        border: 1px solid rgba(100, 116, 139, 0.3);
    }
    
    /* Stats Grid */
    .metric-box {
        background: rgba(15, 23, 42, 0.5);
        border-radius: 8px;
        padding: 0.75rem;
        border: 1px solid rgba(255, 255, 255, 0.02);
        text-align: center;
    }
    
    .metric-label {
        font-size: 0.7rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.25rem;
    }
    
    .metric-value {
        font-size: 1.1rem;
        font-weight: 700;
        color: #f1f5f9;
    }
    
    /* Logs Panel styling */
    .log-panel {
        background-color: #020617 !important;
        border: 1px solid #1e293b !important;
        border-radius: 12px !important;
        padding: 1rem !important;
        font-family: 'Fira Code', monospace !important;
        font-size: 0.85rem !important;
        color: #38bdf8 !important;
        height: 250px;
        overflow-y: scroll;
    }
    
    /* Utility grids */
    .card-grid-3 {
        display: grid;
        grid-template-columns: 1fr 1fr 1fr;
        gap: 8px;
        margin-bottom: 12px;
    }
    
    .target-highlight {
        background: rgba(56, 189, 248, 0.05);
        border-left: 3px solid #38bdf8;
        padding: 0.5rem 0.75rem;
        border-radius: 0 8px 8px 0;
        margin-bottom: 8px;
    }
    
    .target-achieved {
        background: rgba(16, 185, 129, 0.05);
        border-left: 3px solid #10b981;
        padding: 0.5rem 0.75rem;
        border-radius: 0 8px 8px 0;
        margin-bottom: 8px;
    }
    
    @keyframes pulse {
        0% { opacity: 0.8; }
        50% { opacity: 1; box-shadow: 0 0 10px rgba(139, 92, 246, 0.2); }
        100% { opacity: 0.8; }
    }
</style>
""", unsafe_allow_html=True)

# Helper to collapse html into a single line to prevent markdown parser from converting it to code block
def clean_html(html_str: str) -> str:
    import re
    # Replace newlines with spaces
    s = html_str.replace("\n", " ")
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s)
    return s.strip()

# Helper function to read the state JSON file safely on Windows
def load_aegis_state(filepath="aegis_state.json"):
    if not os.path.exists(filepath):
        return None
    retries = 3
    for i in range(retries):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            if i < retries - 1:
                time.sleep(0.05)
            else:
                return None
    return None

# State Storage between UI loops
if "last_state" not in st.session_state:
    st.session_state.last_state = {
        "system": {
            "ws_connected": False,
            "pub_ws_connected": False,
            "priv_ws_connected": False,
            "last_ping_latency_ms": 0.0,
            "last_update_ts": datetime.now().isoformat(),
            "active_trackers_count": 0
        },
        "trackers": {},
        "logs": []
    }

# Fallback for Streamlit versions supporting st.fragment or st.experimental_fragment
if hasattr(st, "fragment"):
    fragment_decorator = st.fragment
else:
    fragment_decorator = st.experimental_fragment

# Sidebar inputs (remains in global scope to be completely static and focus-safe)
is_muted = st.sidebar.checkbox("🔊 Sesi Kapat (Mute)", value=False)
edit_mode = st.sidebar.checkbox("⚙️ API Ayarlarını Düzenle", value=False)

if edit_mode:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    current_api, current_secret, current_pass, current_sim = "", "", "", "True"
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("OKX_API_KEY="): current_api = line.split("=", 1)[1].strip()
                elif line.startswith("OKX_SECRET_KEY="): current_secret = line.split("=", 1)[1].strip()
                elif line.startswith("OKX_PASSPHRASE="): current_pass = line.split("=", 1)[1].strip()
                elif line.startswith("OKX_IS_SIMULATED="): current_sim = line.split("=", 1)[1].strip()

    with st.sidebar.form("api_settings_form"):
        new_api = st.text_input("OKX API Key", value=current_api, type="password")
        new_secret = st.text_input("OKX Secret Key", value=current_secret, type="password")
        new_pass = st.text_input("OKX Passphrase", value=current_pass, type="password")
        is_sim = st.checkbox("Demo/Paper Trading", value=(current_sim.lower() in ["true", "1", "yes"]))
        
        if st.form_submit_button("Ayarları Kaydet"):
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(f"OKX_API_KEY={new_api}\n")
                f.write(f"OKX_SECRET_KEY={new_secret}\n")
                f.write(f"OKX_PASSPHRASE={new_pass}\n")
                f.write(f"OKX_IS_SIMULATED={'True' if is_sim else 'False'}\n")
            st.sidebar.success("Kaydedildi! Değişikliklerin etkili olması için arka plan sistemini yeniden başlatın.")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⚙️ Motor Ayarları")
    settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aegis_settings.json")
    current_esik1 = 50.0
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r") as f:
                stg = json.load(f)
                current_esik1 = float(stg.get("esik1_ratio_pct", 50.0))
        except: pass
        
    with st.sidebar.form("motor_settings_form"):
        new_esik1 = st.slider("Eşik 1 Kar Al Oranı (%)", min_value=10.0, max_value=90.0, value=current_esik1, step=5.0)
        if st.form_submit_button("Motor Ayarlarını Kaydet"):
            with open(settings_path, "w") as f:
                json.dump({"esik1_ratio_pct": new_esik1}, f)
            st.sidebar.success("Motor ayarları güncellendi! Anında aktif olacaktır (Yeniden başlatma gerektirmez).")

@fragment_decorator(run_every=1.0)
def render_live_view(is_muted_val):
    # Load current state
    state = load_aegis_state("aegis_state.json")
    
    # Fallback to last known state if reading fails or file is empty
    if state is None:
        state = st.session_state.last_state
    else:
        st.session_state.last_state = state
        
    # Parse variables
    sys_state = state.get("system", {})
    trackers = state.get("trackers", {})
    market_data = state.get("market_data", {})
    action_logs = state.get("action_logs", [])
    
    ws_connected = sys_state.get("ws_connected", False)
    pub_connected = sys_state.get("pub_ws_connected", False)
    priv_connected = sys_state.get("priv_ws_connected", False)
    latency = sys_state.get("last_ping_latency_ms", 0.0)
    tracker_count = sys_state.get("active_trackers_count", 0)
    last_update = sys_state.get("last_update_ts", "")
    
    # Audio Pipeline & State Transition Detection
    play_sound = None
    last_trackers = st.session_state.last_state.get("trackers", {})
    curr_trackers = trackers
    
    if not is_muted_val:
        for inst_id, curr_t in curr_trackers.items():
            last_t = last_trackers.get(inst_id)
            if not last_t:
                play_sound = "intercept"
            else:
                ls = last_t.get("state")
                cs = curr_t.get("state")
                if ls == "INIT" and cs == "RISK_ZERO":
                    play_sound = "tp1"
                    
        for inst_id, last_t in last_trackers.items():
            if inst_id not in curr_trackers:
                ls = last_t.get("state")
                if ls in ["RISK_ZERO", "TRAILING"]:
                    play_sound = "siren"
                    
    if play_sound:
        audio_js = f"""
        <script>
        function playTone(freq, type, duration, vol=0.1) {{
            let ctx = new (window.AudioContext || window.webkitAudioContext)();
            let osc = ctx.createOscillator();
            let gain = ctx.createGain();
            osc.type = type;
            osc.frequency.setValueAtTime(freq, ctx.currentTime);
            gain.gain.setValueAtTime(vol, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + duration);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + duration);
        }}
        try {{
            if ('{play_sound}' === 'intercept') {{
                playTone(800, 'sine', 0.5);
                setTimeout(() => playTone(1200, 'sine', 0.5), 150);
            }} else if ('{play_sound}' === 'tp1') {{
                playTone(1500, 'square', 0.1, 0.05);
                setTimeout(() => playTone(2000, 'square', 0.2, 0.05), 100);
            }} else if ('{play_sound}' === 'siren') {{
                playTone(400, 'sawtooth', 0.5, 0.15);
                setTimeout(() => playTone(600, 'sawtooth', 0.5, 0.15), 300);
                setTimeout(() => playTone(400, 'sawtooth', 0.5, 0.15), 600);
            }}
        }} catch(e) {{
            console.warn("Audio autoplay blocked or failed", e);
        }}
        </script>
        """
        import streamlit.components.v1 as components
        components.html(audio_js, height=0, width=0)
        
    # 1. Sleek Dashboard Header
    ws_status_html = ""
    if pub_connected and priv_connected:
        ws_status_html = '<div class="status-badge status-online">🟢 WEBSOCKETS ONLINE</div>'
    elif pub_connected or priv_connected:
        ws_status_html = f'<div class="status-badge" style="background: rgba(245,158,11,0.1); color:#f59e0b; border-color:rgba(245,158,11,0.3);">🟡 PUBLIC: {"ON" if pub_connected else "OFF"} | PRIV: {"ON" if priv_connected else "OFF"}</div>'
    else:
        ws_status_html = '<div class="status-badge status-offline">🔴 WEBSOCKETS OFFLINE</div>'
        
    st.markdown(clean_html(f"""
    <div class="header-container">
        <div class="header-title">
            🛡️ AEGIS <span style="font-size: 1.1rem; color: #94a3b8; font-weight:400; padding-left:10px;">Position Management & Smart Exit Engine</span>
        </div>
        <div style="display: flex; align-items: center; gap: 15px;">
            <div style="color: #64748b; font-size: 0.85rem; font-weight: 500;">
                Gecikme: <span style="color: #38bdf8; font-weight: 700;">{latency} ms</span>
            </div>
            <div style="color: #64748b; font-size: 0.85rem; font-weight: 500; padding-right: 15px;">
                Aktif Takipçi: <span style="color: #f59e0b; font-weight: 700;">{tracker_count}</span>
            </div>
            {ws_status_html}
        </div>
    </div>
    """), unsafe_allow_html=True)
    
    # 2. Split Screen Layout
    left_col, right_col = st.columns([1, 1])
    
    with left_col:
        st.markdown('<div style="font-weight: 700; font-size: 1.2rem; color: #f8fafc; margin-bottom: 1rem; display: flex; align-items:center; gap: 8px;">📜 Aegis Action Log (Savaş Günlüğü)</div>', unsafe_allow_html=True)
        
        log_content = ""
        for l in reversed(action_logs):
            log_content += f"{l}\n"
            
        if not log_content:
            log_content = "📡 Radar Taraması... Henüz herhangi bir işlem kararı kaydedilmedi."
            
        st.text_area(
            label="Action Logs",
            value=log_content,
            height=550,
            disabled=True,
            label_visibility="collapsed"
        )
        
    with right_col:
        st.markdown('<div style="font-weight: 700; font-size: 1.2rem; color: #f8fafc; margin-bottom: 1rem; display: flex; align-items:center; gap: 8px;">🎯 Live Position Radar (Canlı Radar)</div>', unsafe_allow_html=True)
        
        # Display Live Market Data
        btc_data = market_data.get("BTC-USDT-SWAP")
        if btc_data:
            btc_last = btc_data.get("last", 0.0)
            btc_ask = btc_data.get("ask", 0.0)
            btc_bid = btc_data.get("bid", 0.0)
            btc_vol = btc_data.get("vol24h", 0.0)
            ob_imb = btc_data.get("ob_imbalance", 0.0)
            ob_color = '#10b981' if ob_imb > 0 else '#ef4444'
            
            st.markdown(clean_html(f"""
            <div class="position-card" style="border-color: #f59e0b; padding: 1rem; margin-bottom: 1.5rem; background: rgba(245, 158, 11, 0.05);">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span style="font-size: 1.8rem;">₿</span>
                        <div>
                            <div style="font-size: 1.1rem; font-weight: 800; color: #f8fafc;">BTC-USDT-SWAP <span style="font-size:0.75rem; color:#94a3b8; font-weight:500;">(Canlı Piyasa)</span></div>
                            <div style="font-size: 0.85rem; color: #f59e0b; font-weight: 700;">Hacim: {btc_vol:,.0f} USDT</div>
                        </div>
                    </div>
                    <div style="text-align: right;">
                        <div style="font-size: 1.6rem; font-weight: 800; color: #f8fafc;">${btc_last:,.2f}</div>
                        <div style="font-size: 0.8rem; color: #94a3b8;">
                            OB Dengesizlik: <span style="color:{ob_color}; font-weight:700;">{ob_imb * 100:+.1f}%</span> | Alış: ${btc_bid:,.2f} | Satış: ${btc_ask:,.2f}
                        </div>
                    </div>
                </div>
            </div>
            """), unsafe_allow_html=True)

        if not trackers:
            st.markdown(clean_html("""
            <div style="text-align: center; padding: 4rem; background: rgba(30, 41, 59, 0.2); border-radius: 12px; border: 1px dashed #334155; margin-bottom: 2rem;">
                <h3 style="color: #94a3b8; margin-bottom: 5px;">📡 Radar Taraması... Aktif pozisyon bulunamadı.</h3>
                <p style="color: #64748b; font-size: 0.9rem;">Skynet'in pozisyon açması veya manuel giriş yapılması bekleniyor.</p>
            </div>
            """), unsafe_allow_html=True)
        else:
            for inst_id, t_info in trackers.items():
                side = t_info.get("side", "long").upper()
                side_class = "side-long" if side == "LONG" else "side-short"
                
                state_val = t_info.get("state", "INIT")
                state_class = f"state-{state_val.lower()}"
                
                size = t_info.get("size", 0.0)
                entry_px = t_info.get("entry_price", 0.0)
                current_px = t_info.get("current_price", 0.0)
                
                trailing_stop = t_info.get("trailing_stop", 0.0)
                dist_to_stop = 0.0
                if trailing_stop and trailing_stop > 0 and current_px > 0:
                    dist_to_stop = abs(current_px - trailing_stop) / current_px * 100
                    
                lever = t_info.get("lever", 1.0)
                pnl_pct = 0.0
                if entry_px > 0:
                    if side == "LONG":
                        pnl_pct = ((current_px - entry_px) / entry_px) * 100.0
                    else:
                        pnl_pct = ((entry_px - current_px) / entry_px) * 100.0
                        
                est_roe = pnl_pct * lever
                pnl_color = "#10b981" if pnl_pct >= 0 else "#ef4444"
                
                atr_pct = (t_info.get("atr", 0.0) / current_px * 100) if current_px > 0 else 0.0
                vol_ratio = t_info.get("volume_ratio", 0.0)
                ob_imb = t_info.get("ob_imbalance", 0.0)
                
                tp1 = t_info.get("tp1_target", 0.0)
                tp2 = t_info.get("tp2_target", 0.0)
                target_tp_ratio = t_info.get("target_tp_ratio", 0.0)
                esik1_fraction = t_info.get("esik1_fraction", 0.50)
                tp1_pct = target_tp_ratio * esik1_fraction
                tp2_pct = target_tp_ratio
                
                tp1_style = "target-achieved" if state_val != "INIT" else "target-highlight"
                tp2_style = "target-achieved" if state_val == "TRAILING" else "target-highlight"
                
                sl_ts_html = ""
                if state_val == "INIT":
                    sl_ts_html = f'''
                        <div class="target-highlight" style="border-left: 3px solid #64748b; background: rgba(100, 116, 139, 0.05);">
                            <div style="font-size:0.65rem; color:#64748b; text-transform:uppercase;">🛑 SL (Zarar Kes)</div>
                            <div style="font-size:0.95rem; font-weight:700; color:#64748b;">Henüz Aktif Değil (Eşik 1 Bekleniyor)</div>
                        </div>
                    '''
                elif state_val == "RISK_ZERO":
                    be_px = t_info.get("breakeven_px", entry_px)
                    sl_ts_html = f'''
                        <div class="target-highlight" style="border-left: 3px solid #3b82f6; background: rgba(59, 130, 246, 0.05);">
                            <div style="font-size:0.65rem; color:#3b82f6; text-transform:uppercase;">🛡️ SL (Başa Baş Koruması)</div>
                            <div style="font-size:0.95rem; font-weight:700; color:#3b82f6;">Stop Seviyesi: ${be_px:.6f}</div>
                        </div>
                    '''
                elif state_val == "TRAILING":
                    ts_px = t_info.get("trailing_stop", 0.0)
                    ob_mult = t_info.get("ob_multiplier", 1.0)
                    sl_ts_html = f'''
                        <div class="target-highlight" style="border-left: 3px solid #8b5cf6; background: rgba(139, 92, 246, 0.05);">
                            <div style="font-size:0.65rem; color:#8b5cf6; text-transform:uppercase;">🏄 TAKİPÇİ STOP (TS)</div>
                            <div style="font-size:0.95rem; font-weight:800; color:#8b5cf6;">
                                Stop Seviyesi: ${ts_px:.6f} <span style="font-size:0.8rem; opacity:0.8;">(Mesafe: {ob_mult}x ATR)</span>
                            </div>
                        </div>
                    '''
                
                st.markdown(clean_html(f"""
                <div class="position-card">
                    <div class="card-header">
                        <div>
                            <span class="card-symbol">{inst_id}</span>
                            <span class="side-badge {side_class}" style="margin-left:8px;">{side}</span>
                        </div>
                        <span class="state-badge {state_class}">{state_val}</span>
                    </div>
                    
                    <div class="card-grid-3">
                        <div class="metric-box">
                            <div class="metric-label">Giriş Fiyatı</div>
                            <div class="metric-value">${entry_px:.6f}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Mevcut Fiyat</div>
                            <div class="metric-value" style="color: {pnl_color};">${current_px:.6f}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Spot Değişim</div>
                            <div class="metric-value" style="color: {pnl_color};">{pnl_pct:+.2f}%</div>
                        </div>
                    </div>
                    
                    <div class="card-grid-3">
                        <div class="metric-box">
                            <div class="metric-label">Canlı ATR</div>
                            <div class="metric-value" style="color: #38bdf8;">{atr_pct:.4f}%</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">10sn RVOL Oranı</div>
                            <div class="metric-value">RVOL: {vol_ratio:.2f}x</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">OB Dengesizlik</div>
                            <div class="metric-value" style="color: {'#10b981' if ob_imb > 0 else '#ef4444'};">OB: {ob_imb * 100:+.1f}%</div>
                        </div>
                    </div>

                    <!-- Target Triggers -->
                    <div style="margin-top: 1rem; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 0.75rem;">
                        <div class="{tp1_style}">
                            <div style="font-size:0.65rem; color:#94a3b8; text-transform:uppercase;">🟠 Eşik 1 (%{int(esik1_fraction * 100)}) &nbsp;|&nbsp; Hedef TP: %{target_tp_ratio:.2f}</div>
                            <div style="font-size:0.95rem; font-weight:700; color:{'#10b981' if state_val != 'INIT' else '#f59e0b'};">
                                Hedef: ${tp1:.6f} <span style="font-size:0.8rem; opacity:0.8;">(±%{tp1_pct:.2f})</span>
                            </div>
                        </div>
                        
                        <div class="{tp2_style}">
                            <div style="font-size:0.65rem; color:#94a3b8; text-transform:uppercase;">🟢 Eşik 2: Tam Hedef (%100)</div>
                            <div style="font-size:0.95rem; font-weight:700; color:{'#10b981' if state_val == 'TRAILING' else '#3b82f6'};">
                                Hedef: ${tp2:.6f} <span style="font-size:0.8rem; opacity:0.8;">(±%{tp2_pct:.2f})</span>
                            </div>
                        </div>
                        
                        {sl_ts_html}
                    </div>
                </div>
                """), unsafe_allow_html=True)
        
    # Small footnote
    st.markdown(clean_html(f"""
    <div style="text-align: center; color: #475569; font-size: 0.75rem; margin-top: 1rem; margin-bottom: 2rem;">
        Dashboard Yenilendi: {datetime.now().strftime("%H:%M:%S.%f")[:-3]} | Son Veri Güncelleme: {last_update}
    </div>
    """), unsafe_allow_html=True)


@fragment_decorator(run_every=10.0)
def render_historical_view():
    # Load current state
    state = load_aegis_state("aegis_state.json")
    if state is None:
        state = st.session_state.last_state
        
    col_lbl, col_btn = st.columns([8, 1.2])
    with col_lbl:
        st.markdown('<div style="font-weight: 700; font-size: 1.25rem; color: #f8fafc; margin-top: 1rem; margin-bottom: 0.5rem;">📊 Sistem Geçmişi & Performans Kayıtları</div>', unsafe_allow_html=True)
    with col_btn:
        st.button("🔄 Verileri Yenile", key="refresh_historical_btn", help="Analitikleri ve İşlem Kayıtlarını anında yeniler.")

    with st.expander("📊 Aegis Performans Analitiği", expanded=True):
        ledger_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aegis_trade_ledger.csv")
        if os.path.exists(ledger_path):
            try:
                df = pd.read_csv(ledger_path)
                if df.empty or "session_id" not in df.columns or "realized_pnl" not in df.columns:
                    st.info("Henüz tamamlanmış işlem kaydı bulunmuyor.")
                else:
                    # Group by session_id and sum realized_pnl
                    session_pnl = df.groupby("session_id")["realized_pnl"].sum().reset_index()
                    
                    total_sessions = len(session_pnl)
                    winning_sessions = len(session_pnl[session_pnl["realized_pnl"] > 0])
                    win_rate = (winning_sessions / total_sessions * 100) if total_sessions > 0 else 0.0
                    total_net_pnl = session_pnl["realized_pnl"].sum()
                    
                    session_pnl["cumulative_pnl"] = session_pnl["realized_pnl"].cumsum()
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        st.metric("Toplam Kazanma Oranı (Win Rate)", f"%{win_rate:.1f}")
                    with col2:
                        pnl_color = "normal" if total_net_pnl >= 0 else "inverse"
                        st.metric("Toplam Gerçekleşen Net PnL", f"%{total_net_pnl:+.2f}", delta_color=pnl_color)
                        
                    st.line_chart(session_pnl["cumulative_pnl"], use_container_width=True)
            except Exception as e:
                st.error(f"Kayıtlar okunurken hata oluştu: {e}")
        else:
            st.info("Henüz tamamlanmış işlem kaydı bulunmuyor.")

    with st.expander("Sistem Altyapı Logları"):
        raw_logs = state.get("logs", [])
        log_text = ""
        for l in reversed(raw_logs):
            log_text += f"[{l.get('time', '')}] {l.get('level', '')} - {l.get('msg', '')}\n"
        
        st.text_area(
            "Raw Backend Logs", 
            value=log_text, 
            height=300, 
            disabled=True, 
            label_visibility="collapsed"
        )

    with st.expander("🗃️ İşlem Kayıtları (Trade Ledger)"):
        ledger_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aegis_trade_ledger.csv")
        if os.path.exists(ledger_path):
            try:
                df = pd.read_csv(ledger_path)
                st.dataframe(df, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Kayıtlar okunurken hata oluştu: {e}")
        else:
            st.info("Henüz kaydedilmiş bir işlem verisi (Ledger) bulunmuyor.")


def render_static_guide():
    with st.expander("🧠 Aegis Algoritma Kılavuzu & Sistem Mantığı"):
        st.markdown(clean_html("""
        <div style="background: rgba(15, 23, 42, 0.4); border: 1px solid rgba(255,255,255,0.05); border-radius: 12px; padding: 1.5rem; margin-top: 0.5rem; line-height: 1.6; color: #cbd5e1;">
            <h3 style="color: #38bdf8; margin-bottom: 1rem; margin-top: 0;">🛡️ Aegis Akıllı Pozisyon Yönetimi Nasıl Çalışır?</h3>
            <p>Aegis, Skynet veya sizin tarafınızdan açılan işlemleri devralan, <b>kayıp riskini minimize edip kâr potansiyelini maksimize etmeyi</b> hedefleyen mikro-saniyeli bir Yüksek Frekanslı (HFT) algoritmadır.</p>
            
            <hr style="border-color: rgba(255,255,255,0.05); margin: 1.5rem 0;">
            
            <h4 style="color: #f1f5f9;">⚙️ 3 Aşamalı Pozisyon Evresi (FSM)</h4>
            <ul style="list-style-type: none; padding-left: 0;">
                <li style="margin-bottom: 0.8rem;">
                    <span style="color: #f59e0b; font-weight: 700;">1. INIT (Bekleme Evresi):</span> Pozisyon açıldığında ilk hedef fiyata (Eşik 1) ulaşılana kadar piyasa dinamikleri izlenir. Herhangi bir kâr alma veya stop daraltma işlemi yapılmaz.
                </li>
                <li style="margin-bottom: 0.8rem;">
                    <span style="color: #3b82f6; font-weight: 700;">2. RISK_ZERO (Başa Baş Evresi):</span> Fiyat, <b>Eşik 1'e (Ana Hedefin %50'si)</b> ulaştığı anda tetiklenir. Algoritma anında Zarar Kes (Stop Loss) seviyesini <b>Giriş Fiyatına (BreakEven)</b> çeker. Artık bu işlemden zarar etme ihtimaliniz sıfırlanmıştır.
                </li>
                <li style="margin-bottom: 0.8rem;">
                    <span style="color: #8b5cf6; font-weight: 700;">3. TRAILING (Takip Evresi):</span> Fiyat <b>Eşik 2'ye (Ana Hedefin %100'ü)</b> ulaştığında tetiklenir. Aegis işlemi hemen kapatmak yerine, trendin devamından ekstra kâr elde etmek için <b>Dinamik Takipçi Stop (Trailing Stop)</b> mekanizmasını devreye sokar ve fiyatla beraber stop'u yukarı/aşağı sürer.
                </li>
            </ul>

            <hr style="border-color: rgba(255,255,255,0.05); margin: 1.5rem 0;">
            
            <h4 style="color: #f1f5f9;">🛡️ Aktif Koruma Kalkanları (Shields)</h4>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem;">
                <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2); padding: 1rem; border-radius: 8px;">
                    <strong style="color: #10b981;">1. Sıçrama Koruması (Stop-Jump Lock)</strong><br>
                    <p style="margin-top: 0.5rem; font-size: 0.9rem; margin-bottom: 0;">Takip eden stop seviyesi (TS), yalnızca kâr yönünde ilerleyebilen tek yönlü bir matematiksel kilide (Ratchet) sahiptir. Tahta baskısı aniden düşse bile, Aegis stop çizgisini asla geriye (zarar yönüne) çekmez; kazancı sıkıca kilitler.</p>
                </div>
                <div style="background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.2); padding: 1rem; border-radius: 8px;">
                    <strong style="color: #38bdf8;">2. Ağ & Veri Kaybı Kalkanı (REST Fallback)</strong><br>
                    <p style="margin-top: 0.5rem; font-size: 0.9rem; margin-bottom: 0;">Milisaniyelik WebSocket veri akışında bir kesinti veya kopma yaşanırsa algoritma kör olmaz. Arka planda her 10 saniyede bir borsaya doğrudan sorgu (REST API Sync) atılarak pozisyonların güvenliği yedekli olarak sağlanır.</p>
                </div>
            </div>
            
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem;">
                <div style="background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2); padding: 1rem; border-radius: 8px;">
                    <strong style="color: #f59e0b;">3. Dinamik Volatilite Kalkanı (ATR Gap)</strong><br>
                    <p style="margin-top: 0.5rem; font-size: 0.9rem; margin-bottom: 0;">Sabit yüzdeli stoplar, piyasanın doğal oynaklığında ("sahte iğneler") kolayca patlayabilir. Aegis, stop mesafesini canlı ATR (Volatilite) değerine göre ayarlayarak fiyata sağlıklı bir nefes alma alanı (Gap) bırakır.</p>
                </div>
                <div style="background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.2); padding: 1rem; border-radius: 8px;">
                    <strong style="color: #3b82f6;">4. Başa Baş Kalkanı (BreakEven Shield)</strong><br>
                    <p style="margin-top: 0.5rem; font-size: 0.9rem; margin-bottom: 0;">Risk 0 evresine geçildiğinde aktifleşen bu kalkan, işlemi en kötü ihtimalle sıfır kâr/zararla (Giriş Fiyatı) kapatmayı garanti eder. Kârın aniden eksiye dönmesine kesinlikle izin vermez.</p>
                </div>
            </div>

            <hr style="border-color: rgba(255,255,255,0.05); margin: 1.5rem 0;">

            <h4 style="color: #f1f5f9;">🧬 Canlı Parametreler & Terimler Sözlüğü</h4>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem;">
                <div style="background: rgba(0,0,0,0.2); padding: 1rem; border-radius: 8px;">
                    <strong style="color: #38bdf8;">🏄 Dinamik Takipçi Çarpanı</strong><br>
                    Takip eden stop mesafesi sabit değildir. Borsa tahtasındaki emirlere (Order Book) göre şekillenir.
                    <ul style="margin-top: 0.5rem; padding-left: 1.2rem; font-size: 0.9rem;">
                        <li><span style="color:#10b981;">Alıcı Duvarı Varsa (>%15):</span> Mesafe <b>1.5x ATR</b> genişletilir. Sahte düşüşlerde (iğnelerde) trendden kopmamak hedeflenir.</li>
                        <li><span style="color:#ef4444;">Satıcı Duvarı Varsa (< -%15):</span> Mesafe aniden <b>0.4x ATR'ye</b> daraltılır. Kâr hemen kilitlenir.</li>
                    </ul>
                </div>
                
                <div style="background: rgba(0,0,0,0.2); padding: 1rem; border-radius: 8px;">
                    <strong style="color: #38bdf8;">📊 Live ATR & RVOL</strong><br>
                    <p style="margin-top: 0.5rem; font-size: 0.9rem; margin-bottom: 0.5rem;"><b>ATR (Average True Range):</b> Varlığın son mumlardaki ortalama oynaklığını ölçer. Sistem, stop mesafelerini sadece fiyata değil, o anki oynaklık yüzdesine (ATR) göre ayarlar.</p>
                    <p style="margin-top: 0; font-size: 0.9rem;"><b>RVOL (Relative Volume):</b> Son 10 saniyedeki hacmin, geçmiş hacimlere oranla ne kadar anormal (hızlı) aktığını gösterir. Yüksek RVOL kırılımları temsil eder.</p>
                </div>
            </div>
            
            <div style="background: rgba(0,0,0,0.2); padding: 1rem; border-radius: 8px; margin-top: 1rem;">
                <strong style="color: #10b981;">⚖️ OB Imbalance (Tahta Dengesizliği)</strong><br>
                <p style="margin-top: 0.5rem; font-size: 0.9rem; margin-bottom: 0;">Borsadaki ilk 5 derinlik (Bid/Ask) kademesindeki alım-satım emirlerinin yoğunluk farkıdır. Pozitif (+) değerler tahtada güçlü bir alım baskısı olduğunu, negatif (-) değerler ise baskın bir satış duvarı olduğunu gösterir. Aegis kararlarını milisaniyeler içinde bu baskıya bakarak şekillendirir.</p>
            </div>
        </div>
        """), unsafe_allow_html=True)


render_live_view(is_muted)
render_historical_view()
render_static_guide()
