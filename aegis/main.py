import asyncio
import json
import os
import time
import hmac
import hashlib
import base64
import logging
from datetime import datetime
import aiohttp
import websockets

import config
from tracker import PositionTracker

# Setup Logging
logger = logging.getLogger("Aegis")
logger.setLevel(logging.INFO)

# Custom Rolling Log Handler for UI Console
class RollingLogHandler(logging.Handler):
    def __init__(self, max_entries=50):
        super().__init__()
        self.max_entries = max_entries
        self.logs = []

    def emit(self, record):
        try:
            log_entry = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "msg": record.getMessage()
            }
            self.logs.append(log_entry)
            if len(self.logs) > self.max_entries:
                self.logs.pop(0)
        except Exception:
            self.handleError(record)

log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

rolling_handler = RollingLogHandler(max_entries=60)
logger.addHandler(rolling_handler)


class OKXExchange:
    def __init__(self):
        self.api_key = config.OKX_API_KEY
        self.secret_key = config.OKX_SECRET_KEY
        self.passphrase = config.OKX_PASSPHRASE
        self.is_simulated = config.OKX_IS_SIMULATED
        self.base_url = config.OKX_REST_URL
        
        # Order fill event tracking
        self.order_events = {}  # clOrdId -> asyncio.Event
        self.order_fills = {}   # clOrdId -> accFillSz
        self.instrument_info_cache = {}  # instId -> dict
        
        # State change callback hook
        self.on_state_change_cb = None

    def register_order_event(self, cl_ord_id: str, event: asyncio.Event):
        self.order_events[cl_ord_id] = event
        self.order_fills[cl_ord_id] = 0.0

    def unregister_order_event(self, cl_ord_id: str):
        self.order_events.pop(cl_ord_id, None)

    def notify_order_fill(self, cl_ord_id: str, filled_sz: float):
        self.order_fills[cl_ord_id] = filled_sz
        if cl_ord_id in self.order_events:
            logger.info(f"Setting order fill event for clOrdId: {cl_ord_id}, size: {filled_sz}")
            self.order_events[cl_ord_id].set()

    def get_cached_filled_sz(self, cl_ord_id: str) -> float:
        return self.order_fills.get(cl_ord_id, 0.0)

    def notify_state_change(self):
        if self.on_state_change_cb:
            self.on_state_change_cb()

    def _get_timestamp_iso(self) -> str:
        """Returns ISO 8601 UTC timestamp format required by OKX REST API."""
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _generate_signature(self, timestamp: str, method: str, request_path: str, body_str: str = "") -> str:
        """Generates HMAC-SHA256 signature for private endpoints."""
        pre_hash = timestamp + method + request_path + body_str
        mac = hmac.new(
            self.secret_key.encode("utf-8"),
            pre_hash.encode("utf-8"),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    async def request(self, method: str, path: str, params: dict = None, data: dict = None) -> dict:
        """Executes a signed or unsigned REST request to OKX API."""
        url = self.base_url + path
        timestamp = self._get_timestamp_iso()
        
        # Build request body string for signature
        body_str = ""
        if data:
            body_str = json.dumps(data)
            
        # Headers setup
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # Include simulated trading flag if set
        if self.is_simulated:
            headers["x-simulated-trading"] = "1"
            
        # If API keys are set, authenticate the request
        if self.api_key and self.secret_key:
            headers["OK-ACCESS-KEY"] = self.api_key
            headers["OK-ACCESS-PASSPHRASE"] = self.passphrase
            headers["OK-ACCESS-TIMESTAMP"] = timestamp
            headers["OK-ACCESS-SIGN"] = self._generate_signature(timestamp, method.upper(), path, body_str)
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, params=params, data=body_str if data else None, headers=headers) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error(f"HTTP Request {method} {path} failed with status {resp.status}: {text}")
                        return {"code": str(resp.status), "msg": text}
                    return await resp.json()
        except Exception as e:
            logger.error(f"HTTP Request Exception: {e}")
            return {"code": "-1", "msg": str(e)}

    def get_instrument_info(self, inst_id: str) -> dict:
        """Retrieves instrument detail configurations with caching."""
        if inst_id in self.instrument_info_cache:
            return self.instrument_info_cache[inst_id]
        
        # Fallback values if HTTP call fails
        fallback = {
            "instId": inst_id,
            "lotSz": "1" if "PEPE" in inst_id else "1" if "DOGE" in inst_id else "0.01",
            "tickSz": "0.00000001" if "PEPE" in inst_id else "0.0001" if "DOGE" in inst_id else "0.1"
        }
        return fallback

    async def fetch_instrument_info_rest(self, inst_id: str):
        """Fetches instrument specifications from public REST endpoint."""
        path = f"/api/v5/public/instruments?instType=SWAP&instId={inst_id}"
        res = await self.request("GET", path)
        if res and res.get("code") == "0" and len(res.get("data", [])) > 0:
            info = res["data"][0]
            self.instrument_info_cache[inst_id] = info
            logger.info(f"Loaded details for {inst_id}: lotSz={info.get('lotSz')}, tickSz={info.get('tickSz')}")
        else:
            logger.warning(f"Failed to fetch details for {inst_id} via REST. Using fallback.")

    async def get_candles(self, inst_id: str, bar: str = "1m", limit: int = 20) -> dict:
        """Fetches candlesticks for ATR calculation (Public REST endpoint)."""
        path = f"/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}"
        return await self.request("GET", path)

    async def place_order(self, inst_id: str, side: str, ord_type: str, sz: str, 
                          px: str = None, cl_ord_id: str = None, pos_side: str = None, mgn_mode: str = "isolated") -> dict:
        """Places a buy/sell limit or market order to OKX."""
        path = "/api/v5/trade/order"
        body = {
            "instId": inst_id,
            "tdMode": mgn_mode if mgn_mode in ("isolated", "cross") else "isolated",
            "side": side,
            "ordType": ord_type,
            "sz": sz,
            "reduceOnly": True
        }
        if px and ord_type == "limit":
            body["px"] = px
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id
        if pos_side:
            body["posSide"] = pos_side
            
        return await self.request("POST", path, data=body)

    async def cancel_order(self, inst_id: str, ord_id: str, cl_ord_id: str) -> dict:
        """Cancels an outstanding limit order."""
        path = "/api/v5/trade/cancel-order"
        body = {
            "instId": inst_id
        }
        if ord_id:
            body["ordId"] = ord_id
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id
            
        return await self.request("POST", path, data=body)

    async def place_algo_order(self, inst_id: str, side: str, ord_type: str, sz: str,
                               pos_side: str = None, mgn_mode: str = "isolated",
                               tp_trigger_px: str = None, tp_ord_px: str = "-1",
                               sl_trigger_px: str = None, sl_ord_px: str = "-1") -> dict:
        """Places a conditional take-profit/stop-loss algo order to OKX."""
        path = "/api/v5/trade/order-algo"
        body = {
            "instId": inst_id,
            "tdMode": mgn_mode if mgn_mode in ("isolated", "cross") else "isolated",
            "side": side,
            "ordType": ord_type,
            "sz": sz,
            "reduceOnly": True
        }
        if pos_side:
            body["posSide"] = pos_side
        if tp_trigger_px:
            body["tpTriggerPx"] = tp_trigger_px
            body["tpOrdPx"] = tp_ord_px
        if sl_trigger_px:
            body["slTriggerPx"] = sl_trigger_px
            body["slOrdPx"] = sl_ord_px
            
        return await self.request("POST", path, data=body)

    async def cancel_algo_order(self, inst_id: str, algo_id: str) -> dict:
        """Cancels a specific pending algo order."""
        path = "/api/v5/trade/cancel-algos"
        body = [{
            "algoId": algo_id,
            "instId": inst_id
        }]
        return await self.request("POST", path, data=body)


    async def cancel_algo_orders(self, inst_id: str) -> bool:
        """Fetches and cancels all pending algo orders (conditional, oco, trigger) for the instrument."""
        logger.info(f"[{inst_id}] Initiating cancel for all pending algo orders...")
        algo_types = ["conditional", "oco", "trigger"]
        all_algos = []
        for otype in algo_types:
            try:
                path = f"/api/v5/trade/orders-algo-pending?instType=SWAP&ordType={otype}&instId={inst_id}"
                res = await self.request("GET", path)
                if res and res.get("code") == "0":
                    all_algos.extend(res.get("data", []))
            except Exception as e:
                logger.error(f"[{inst_id}] Failed fetching pending algos ({otype}): {e}")

        if not all_algos:
            logger.info(f"[{inst_id}] No pending algo orders found to cancel.")
            return True

        # Cancel list construction
        cancel_payload = []
        for algo in all_algos:
            algo_id = algo.get("algoId")
            if algo_id:
                cancel_payload.append({
                    "algoId": algo_id,
                    "instId": inst_id
                })

        success = True
        # Batch cancel in chunks of 10
        for i in range(0, len(cancel_payload), 10):
            chunk = cancel_payload[i:i+10]
            try:
                path = "/api/v5/trade/cancel-algos"
                res = await self.request("POST", path, data=chunk)
                if res and res.get("code") == "0":
                    logger.info(f"[{inst_id}] Successfully cancelled {len(chunk)} algo orders.")
                else:
                    err_msg = res.get("msg", "Unknown error") if res else "No response"
                    logger.error(f"[{inst_id}] Failed cancelling algo orders chunk: {err_msg}")
                    success = False
            except Exception as e:
                logger.error(f"[{inst_id}] Exception during algo cancellation chunk: {e}")
                success = False

        return success

    async def get_order(self, inst_id: str, ord_id: str) -> dict:
        """Queries order status details."""
        path = f"/api/v5/trade/order?instId={inst_id}&ordId={ord_id}"
        return await self.request("GET", path)

    async def get_positions(self) -> dict:
        """Queries all active open positions."""
        path = "/api/v5/account/positions?instType=SWAP"
        return await self.request("GET", path)


class AegisOrchestrator:
    def __init__(self):
        self.exchange = OKXExchange()
        self.exchange.on_state_change_cb = self.serialize_state
        
        self.active_trackers = {}  # instId -> PositionTracker
        self.subscribed_instruments = set()  # set of instIds currently subscribed to on public WS
        
        # WebSocket latency and states
        self.pub_ws_connected = False
        self.priv_ws_connected = False
        self.last_ping_latency_ms = 0.0
        self.last_sync_time = 0.0
        
        # Telemetry targets override buffer (e.g. target_tp_ratio)
        self.telemetry_targets = {}
        
        # Queue for public subscriptions
        self.ws_sub_queue = asyncio.Queue()
        self.public_websocket = None
        self.private_websocket = None
        self.action_logs = []
        self.market_data = {}

        # Load persisted trackers from state file if available
        self.load_persisted_state()
        
    def add_action_log(self, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.action_logs.append(f"[KAYIT] [{ts}] {msg}")
        if len(self.action_logs) > 100:
            self.action_logs.pop(0)
        self.serialize_state()

    def log_trade_event(self, session_id: str, symbol: str, side: str, leverage: float, action_event: str, price: float, spot_move_pct: float, realized_pnl: float, note: str):
        """Appends a trade event to the persistent CSV ledger in a thread-safe manner."""
        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aegis_trade_ledger.csv")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Clean inputs
        note = note.replace(",", ";")
        
        row = f"{ts},{session_id},{symbol},{side},{leverage},{action_event},{price:.6f},{spot_move_pct:.4f},{realized_pnl:.4f},{note}\n"
        
        retries = 3
        for i in range(retries):
            try:
                # Add header if it doesn't exist
                if not os.path.exists(csv_path):
                    with open(csv_path, "w", encoding="utf-8") as f:
                        f.write("timestamp,session_id,symbol,side,leverage,action_event,price,spot_move_pct,realized_pnl,note\n")
                        
                with open(csv_path, "a", encoding="utf-8") as f:
                    f.write(row)
                break
            except PermissionError:
                if i < retries - 1:
                    time.sleep(0.05)
                else:
                    logger.error(f"Failed to write to trade ledger CSV after {retries} retries.")

    def load_persisted_state(self):
        """Loads state configuration from JSON to restore state machine on restart."""
        path = config.STATE_FILE_PATH
        if not os.path.exists(path):
            return
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            trackers_data = data.get("trackers", {})
            for inst_id, t_dict in trackers_data.items():
                # We skip trackers already loaded or closed
                if t_dict.get("state") == "CLOSED":
                    continue
                
                # Restore tracker target overrides
                self.telemetry_targets[inst_id] = t_dict.get("target_tp_ratio", 0.35)
                logger.info(f"Loaded override target for {inst_id} from saved state: {self.telemetry_targets[inst_id]}")
        except Exception as e:
            logger.error(f"Error loading persisted state file: {e}")

    def serialize_state(self):
        """Saves current engine state, latency, active trackers, and rolling logs atomically."""
        path = config.STATE_FILE_PATH
        state_data = {
            "system": {
                "ws_connected": self.pub_ws_connected and self.priv_ws_connected,
                "pub_ws_connected": self.pub_ws_connected,
                "priv_ws_connected": self.priv_ws_connected,
                "last_ping_latency_ms": round(self.last_ping_latency_ms, 2),
                "last_update_ts": datetime.now().isoformat(),
                "active_trackers_count": len(self.active_trackers)
            },
            "trackers": {inst_id: tracker.to_dict() for inst_id, tracker in self.active_trackers.items()},
            "market_data": self.market_data,
            "logs": rolling_handler.logs,
            "action_logs": self.action_logs
        }
        
        try:
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2, ensure_ascii=False)
            
            # Simple retry for file locking conflict (WinError 5)
            retries = 3
            for i in range(retries):
                try:
                    os.replace(tmp_path, path)  # Atomic rename
                    break
                except PermissionError:
                    if i < retries - 1:
                        time.sleep(0.05)
                    else:
                        logger.error(f"Failed to replace state JSON file after {retries} retries.")
        except Exception as e:
            logger.error(f"Failed to write state JSON file: {e}")

    def get_target_tp_ratio(self, inst_id: str) -> float:
        """Retrieves targeted TP ratio from telemetry file, override buffer, or defaults."""
        # 1. Check local telemetry file if it exists
        telemetry_path = "aegis_telemetry.json"
        if os.path.exists(telemetry_path):
            try:
                with open(telemetry_path, "r", encoding="utf-8") as f:
                    tel_data = json.load(f)
                    if inst_id in tel_data:
                        val = float(tel_data[inst_id])
                        self.telemetry_targets[inst_id] = val
                        logger.info(f"Intercepted target ratio for {inst_id} via telemetry file: {val}")
            except Exception as e:
                logger.error(f"Failed reading telemetry file: {e}")
                
        # 2. Check local memory overrides
        if inst_id in self.telemetry_targets:
            return self.telemetry_targets[inst_id]
            
        # 3. Dynamic Fallbacks based on coin
        # Standard: 0.35%, Volatile Meme: 0.28%, High Speed: 0.45%
        if "PEPE" in inst_id or "SHIB" in inst_id or "DOGE" in inst_id:
            return 0.28  # 0.28%
        elif "BTC" in inst_id:
            return 0.40  # 0.40%
        return 0.35  # 0.35%

    async def calculate_initial_atr(self, inst_id: str) -> float:
        """Fetches past 1m candles and calculates initial ATR."""
        try:
            res = await self.exchange.get_candles(inst_id, bar="1m", limit=25)
            if not res or res.get("code") != "0" or "data" not in res:
                logger.error(f"[{inst_id}] REST candle fetch failed. Using fallback ATR.")
                return 0.001
                
            candles = res["data"]
            # Filter completed candles (confirm == "1")
            completed = [c for c in candles if c[8] == "1"]
            # Sort chronologically
            completed.sort(key=lambda x: int(x[0]))
            
            if len(completed) < 2:
                logger.warning(f"[{inst_id}] Not enough candles for ATR. Fallback.")
                return 0.001
                
            # Calculate True Ranges
            trs = []
            for i in range(len(completed)):
                h = float(completed[i][2])
                l = float(completed[i][3])
                if i == 0:
                    tr = h - l
                else:
                    prev_c = float(completed[i-1][4])
                    tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                trs.append(tr)
                
            # Wilder's Smoothing
            if len(trs) >= 14:
                atr = sum(trs[:14]) / 14.0
                for tr in trs[14:]:
                    atr = (atr * 13.0 + tr) / 14.0
            else:
                atr = sum(trs) / len(trs)
                
            logger.info(f"[{inst_id}] Computed Initial ATR: {atr:.8f} using {len(completed)} completed candles")
            return atr
        except Exception as e:
            logger.error(f"[{inst_id}] ATR computation error: {e}")
            return 0.001

    async def add_tracker(self, inst_id: str, side: str, size: float, entry_price: float, mgn_mode: str, pos_side: str, lever: float = 1.0):
        """Instantiates a PositionTracker object, loads specifications, and starts tracking."""
        if inst_id in self.active_trackers:
            return

        # Fetch contract details via REST (rounds order quantity)
        await self.exchange.fetch_instrument_info_rest(inst_id)
        inst_info = self.exchange.get_instrument_info(inst_id)
        ct_val = float(inst_info.get("ctVal", "1"))
        
        # Calculate ATR and retrieve TP targets
        atr = await self.calculate_initial_atr(inst_id)
        target_tp = self.get_target_tp_ratio(inst_id)
        
        # Load dynamic settings
        esik1_fraction = 0.50
        settings_path = "aegis/aegis_settings.json"
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r") as f:
                    stg = json.load(f)
                    esik1_fraction = stg.get("esik1_ratio_pct", 50.0) / 100.0
            except: pass
        
        tracker = PositionTracker(
            inst_id=inst_id,
            side=side,
            size=size,
            entry_price=entry_price,
            target_tp_ratio=target_tp,
            atr=atr,
            ct_val=ct_val,
            exchange_interface=self.exchange,
            mgn_mode=mgn_mode,
            pos_side=pos_side,
            action_log_cb=self.add_action_log,
            trade_ledger_cb=self.log_trade_event,
            lever=lever,
            esik1_fraction=esik1_fraction
        )
        
        self.active_trackers[inst_id] = tracker
        
        # Request dynamic public WS subscription for the new instrument
        await self.ws_sub_queue.put(("subscribe", inst_id))
        self.add_action_log(f"🟢 POZİSYON DEVRALINDI: {inst_id} | Büyüklük: {size} | Giriş: ${entry_price:.6f}")
        logger.info(f"Successfully added tracking engine for {inst_id}")

    async def remove_tracker(self, inst_id: str, reason: str = ""):
        """Removes tracker and cleans up WS subscriptions."""
        if inst_id not in self.active_trackers:
            return
            
        tracker = self.active_trackers.pop(inst_id)
        
        # Cancel any active stop loss order we placed on the exchange for this tracker
        if getattr(tracker, "algo_sl_id", None):
            logger.info(f"[{inst_id}] Cleaning up active exchange stop-loss order {tracker.algo_sl_id}...")
            asyncio.create_task(self.exchange.cancel_algo_order(inst_id, tracker.algo_sl_id))
            tracker.algo_sl_id = None

        
        # If tracker is not CLOSED (closed externally or offline), log to CSV ledger
        if tracker.state != "CLOSED":
            tracker.state = "CLOSED"
            exit_price = tracker.current_price if tracker.current_price > 0 else tracker.entry_price
            reason_tr = reason
            if "No active position found via REST" in reason:
                reason_tr = "REST senkronizasyonu sırasında aktif pozisyon bulunamadı"
            elif "WebSocket position size hit 0" in reason:
                reason_tr = "WebSocket pozisyon büyüklüğü 0'a düştü"
            tracker._log_trade(action_event="EXTERNAL_CLOSE", exit_price=exit_price, note=f"Dışarıdan kapatıldı: {reason_tr}")
            
        tracker.state = "CLOSED"
        
        # Request dynamic unsubscribe
        await self.ws_sub_queue.put(("unsubscribe", inst_id))
        
        # We assume if PnL info is needed, tracker has current_price and entry_price
        pnl_str = ""
        if tracker.entry_price > 0:
            if tracker.side == "long":
                pnl_pct = ((tracker.current_price - tracker.entry_price) / tracker.entry_price) * 100.0
            else:
                pnl_pct = ((tracker.entry_price - tracker.current_price) / tracker.entry_price) * 100.0
            pnl_sign = "+" if pnl_pct >= 0 else ""
            pnl_str = f" | PnL: {pnl_sign}{pnl_pct:.2f}%"
            
        self.add_action_log(f"🔴 POZİSYON KAPATILDI: {inst_id}{pnl_str}")
        logger.info(f"[{inst_id}] Tracking engine closed & removed. Reason: {reason}")

    async def rest_sync_loop(self):
        """REST Synchronization loop executing every 10s to sync states with account truth."""
        while True:
            try:
                logger.info("Executing REST Sync Loop checking account positions")
                res = await self.exchange.get_positions()
                
                if res and res.get("code") == "0" and "data" in res:
                    active_positions = res["data"]
                    logger.info(f"REST Sync: Fetched {len(active_positions)} active position records from OKX.")
                    
                    # Fetch algo orders to sync TP from exchange
                    algo_orders = []
                    if self.active_trackers:
                        try:
                            res_algo = await self.exchange.request("GET", "/api/v5/trade/orders-algo-pending?instType=SWAP&ordType=conditional")
                            if res_algo and res_algo.get("code") == "0":
                                algo_orders.extend(res_algo.get("data", []))
                            res_oco = await self.exchange.request("GET", "/api/v5/trade/orders-algo-pending?instType=SWAP&ordType=oco")
                            if res_oco and res_oco.get("code") == "0":
                                algo_orders.extend(res_oco.get("data", []))
                        except Exception as e:
                            logger.error(f"Failed to fetch algo orders: {e}")
                        
                    exchange_tp_px_map = {}
                    for algo in algo_orders:
                        inst = algo.get("instId")
                        tp_px = algo.get("tpTriggerPx", "")
                        if not tp_px:
                            # Sometimes TP is just the triggerPx if it's a normal take-profit order
                            if algo.get("stopType") == "take_profit":
                                tp_px = algo.get("triggerPx", "")
                                
                        if tp_px and inst not in exchange_tp_px_map:
                            exchange_tp_px_map[inst] = float(tp_px)
                    
                    # Read dynamic settings for esik1_fraction
                    esik1_fraction = 0.50
                    settings_path = "aegis/aegis_settings.json"
                    if os.path.exists(settings_path):
                        try:
                            with open(settings_path, "r") as f:
                                stg = json.load(f)
                                esik1_fraction = stg.get("esik1_ratio_pct", 50.0) / 100.0
                        except: pass

                    # Update dynamic targets for existing active trackers
                    for inst_id, tracker in self.active_trackers.items():
                        new_tp = self.get_target_tp_ratio(inst_id)
                        
                        # Override with exchange TP if available
                        if inst_id in exchange_tp_px_map and tracker.entry_price > 0:
                            tp_price = exchange_tp_px_map[inst_id]
                            new_tp = abs(tp_price - tracker.entry_price) / tracker.entry_price * 100.0
                            
                        tracker.update_targets(new_tp, esik1_fraction)
                    
                    found_insts = set()
                    for pos_data in active_positions:
                        inst_id = pos_data["instId"]
                        size = float(pos_data.get("pos", "0"))
                        avg_px = float(pos_data.get("avgPx", "0"))
                        pos_side = pos_data.get("posSide", "long")
                        mgn_mode = pos_data.get("mgnMode", "isolated")
                        lever = float(pos_data.get("lever", "1"))
                        
                        # Determine position side (LONG/SHORT)
                        # Net position mode uses sign, long/short mode uses posSide
                        if pos_side == "net":
                            side = "long" if size > 0 else "short"
                            size = abs(size)
                        else:
                            side = "long" if pos_side == "long" else "short"
                            
                        if size > 0.0:
                            found_insts.add(inst_id)
                            if inst_id in self.active_trackers:
                                tracker = self.active_trackers[inst_id]
                                if abs(tracker.size - size) > 0.0001:
                                    logger.info(f"[{inst_id}] Syncing size modification via REST: {tracker.size} -> {size}")
                                    tracker.size = size
                            else:
                                logger.info(f"[{inst_id}] Intercepted untracked active position of size {size} via REST Sync.")
                                await self.add_tracker(inst_id, side, size, avg_px, mgn_mode, pos_side, lever)
                                
                    # Detect positions that were closed offline
                    for inst_id in list(self.active_trackers.keys()):
                        if inst_id not in found_insts:
                            logger.info(f"[{inst_id}] Position no longer active in REST response. Cleaning tracker.")
                            await self.remove_tracker(inst_id, reason="No active position found via REST")
                else:
                    err_msg = res.get("msg", "Unknown API error") if res else "No response"
                    logger.error(f"REST Sync Loop: Failed fetching account positions: {err_msg}")
                    
                self.last_sync_time = time.time()
                self.serialize_state()
                
            except Exception as e:
                logger.error(f"Exception in REST Sync loop: {e}")
                
            await asyncio.sleep(10)

    async def ws_dynamic_subscription_handler(self):
        """Asynchronously listens to ws_sub_queue to dynamically update public WebSocket subscriptions."""
        while True:
            try:
                op, inst_id = await self.ws_sub_queue.get()
                if not self.public_websocket:
                    self.ws_sub_queue.task_done()
                    continue
                    
                # Sub or Unsub message construction
                # OKX public channels needed: tickers, books5, candle1m, trades
                channels = ["tickers", "books5", "candle1m", "trades"]
                args = [{"channel": ch, "instId": inst_id} for ch in channels]
                
                sub_msg = {
                    "op": op,
                    "args": args
                }
                
                logger.info(f"Dynamic subscription update: Sending op={op.upper()} for {inst_id}")
                await self.public_websocket.send(json.dumps(sub_msg))
                
                if op == "subscribe":
                    self.subscribed_instruments.add(inst_id)
                else:
                    self.subscribed_instruments.discard(inst_id)
                    
                self.ws_sub_queue.task_done()
                
            except Exception as e:
                logger.error(f"WS Dynamic Subscription Handler Error: {e}")
                await asyncio.sleep(1)

    async def run_public_ws(self):
        """Manages persistent public market data stream connection with heartbeat."""
        backoff = 1
        while True:
            try:
                url = config.OKX_WS_PUBLIC
                logger.info(f"Connecting to OKX Public WebSocket: {url}...")
                self.pub_ws_connected = False
                
                async with websockets.connect(url, ping_interval=None) as ws:
                    self.public_websocket = ws
                    self.pub_ws_connected = True
                    logger.info("OKX Public WebSocket connection established.")
                    
                    # Always subscribe to BTC-USDT-SWAP
                    btc_sub = {"op": "subscribe", "args": [
                        {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
                        {"channel": "books5", "instId": "BTC-USDT-SWAP"}
                    ]}
                    await ws.send(json.dumps(btc_sub))
                    
                    # Re-subscribe to all active trackers
                    if self.active_trackers:
                        args = []
                        for inst_id in self.active_trackers.keys():
                            for ch in ["tickers", "books5", "candle1m", "trades"]:
                                args.append({"channel": ch, "instId": inst_id})
                                
                        if args:
                            sub_msg = {"op": "subscribe", "args": args}
                            await ws.send(json.dumps(sub_msg))
                            self.subscribed_instruments.update(self.active_trackers.keys())
                            logger.info(f"Re-subscribed public market data for active trackers: {list(self.active_trackers.keys())}")
                            
                    backoff = 1  # Reset backoff
                    
                    # Keep track of heartbeats
                    heartbeat_task = asyncio.create_task(self._ws_heartbeat_loop(ws, "PUBLIC"))
                    
                    # Main read loop
                    async for raw_msg in ws:
                        # Message is a string
                        if raw_msg == "pong":
                            continue
                            
                        data = json.loads(raw_msg)
                        if "arg" in data and "data" in data:
                            channel = data["arg"]["channel"]
                            inst_id = data["arg"]["instId"]
                            payload = data["data"]
                            
                            # Route messages to appropriate tracker
                            if channel == "tickers":
                                last_px = float(payload[0].get("last", "0"))
                                if inst_id not in self.market_data: self.market_data[inst_id] = {}
                                self.market_data[inst_id].update({
                                    "last": last_px,
                                    "ask": float(payload[0].get("askPx", "0")),
                                    "bid": float(payload[0].get("bidPx", "0")),
                                    "vol24h": float(payload[0].get("volCcy24h", "0")),
                                })
                                if inst_id in self.active_trackers:
                                    tracker = self.active_trackers[inst_id]
                                    tracker.update_tick(current_price=last_px, 
                                                        volume_ratio=tracker.volume_ratio, 
                                                        ob_imbalance=tracker.ob_imbalance)
                                self.serialize_state()
                                
                            elif channel == "books5":
                                bids = payload[0].get("bids", [])
                                asks = payload[0].get("asks", [])
                                bid_vol = sum(float(b[1]) for b in bids)
                                ask_vol = sum(float(a[1]) for a in asks)
                                total_vol = bid_vol + ask_vol
                                ob_imb = (bid_vol - ask_vol) / total_vol if total_vol > 0.0 else 0.0
                                
                                if inst_id not in self.market_data: self.market_data[inst_id] = {}
                                self.market_data[inst_id]["ob_imbalance"] = ob_imb
                                
                                if inst_id in self.active_trackers:
                                    self.active_trackers[inst_id].ob_imbalance = ob_imb
                                self.serialize_state()
                                
                            if inst_id in self.active_trackers:
                                tracker = self.active_trackers[inst_id]
                                if channel == "candle1m":
                                    candle = payload[0]
                                    confirm = candle[8]
                                    if confirm == "1":
                                        h = float(candle[2])
                                        l = float(candle[3])
                                        c = float(candle[4])
                                        # Use last ticker px as prev close or close itself if unavailable
                                        prev_c = tracker.current_price if tracker.current_price > 0 else c
                                        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                                        tracker.atr = (tracker.atr * 13.0 + tr) / 14.0
                                        logger.info(f"[{inst_id}] Live candle closed. Updated ATR: {tracker.atr:.8f}")
                                        
                                elif channel == "trades":
                                    # We maintain 10s volume for the tracker
                                    if not hasattr(tracker, "_trades_buffer"):
                                        tracker._trades_buffer = []  # List of tuples (ts, value)
                                        
                                    now = time.time()
                                    # Convert trade sizes to USDT and store
                                    for t in payload:
                                        sz = float(t["sz"])
                                        px = float(t["px"])
                                        val = sz * tracker.ct_val * px
                                        tracker._trades_buffer.append((now, val))
                                        
                                    # Clean old trades
                                    tracker._trades_buffer = [tb for tb in tracker._trades_buffer if now - tb[0] <= 10.0]
                                    
                                    # Calculate volume ratio
                                    last_10s_vol = sum(tb[1] for tb in tracker._trades_buffer)
                                    avg_1m_vol = tracker.profile["avg_volume"]
                                    if avg_1m_vol > 0:
                                        # Volume ratio is 10s volume scaled to 1m vs average 1m volume
                                        tracker.volume_ratio = (last_10s_vol * 6) / avg_1m_vol
                                    else:
                                        tracker.volume_ratio = 0.0
                                        
                    # If we exit the loop cleanly, cancel the heartbeat
                    heartbeat_task.cancel()
                    
            except Exception as e:
                logger.error(f"Public WebSocket Error: {e}")
                
            self.pub_ws_connected = False
            self.serialize_state()
            logger.warning(f"Public WebSocket disconnected. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(30, backoff * 2)

    async def run_private_ws(self):
        """Manages persistent private account trade channel connections with signed login."""
        backoff = 1
        while True:
            try:
                url = config.OKX_WS_PRIVATE
                logger.info(f"Connecting to OKX Private WebSocket: {url}...")
                self.priv_ws_connected = False
                
                async with websockets.connect(url, ping_interval=None) as ws:
                    self.private_websocket = ws
                    logger.info("OKX Private connection established. Authenticating...")
                    
                    # 1. Login Authentication payload
                    timestamp = str(int(time.time()))
                    sign_str = timestamp + "GET" + "/users/self/verify"
                    sign = base64.b64encode(
                        hmac.new(
                            self.exchange.secret_key.encode("utf-8"),
                            sign_str.encode("utf-8"),
                            hashlib.sha256
                        ).digest()
                    ).decode("utf-8")
                    
                    login_msg = {
                        "op": "login",
                        "args": [
                            {
                                "apiKey": self.exchange.api_key,
                                "passphrase": self.exchange.passphrase,
                                "timestamp": timestamp,
                                "sign": sign
                            }
                        ]
                    }
                    
                    await ws.send(json.dumps(login_msg))
                    
                    # Wait for login confirmation
                    resp = await ws.recv()
                    login_res = json.loads(resp)
                    if login_res.get("event") == "login" and login_res.get("code") == "0":
                        logger.info("OKX Private WebSocket successfully logged in.")
                        self.priv_ws_connected = True
                    else:
                        logger.error(f"OKX Private WebSocket authentication failed: {login_res}")
                        await ws.close()
                        await asyncio.sleep(5)
                        continue
                        
                    # 2. Subscribe to Private Channels (positions, orders, account)
                    sub_msg = {
                        "op": "subscribe",
                        "args": [
                            {"channel": "positions", "instType": "SWAP"},
                            {"channel": "orders", "instType": "SWAP"},
                            {"channel": "account"}
                        ]
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info("Subscribed to positions, orders, and account channels.")
                    
                    backoff = 1  # Reset backoff
                    
                    # Keep track of heartbeats
                    heartbeat_task = asyncio.create_task(self._ws_heartbeat_loop(ws, "PRIVATE"))
                    
                    # Main read loop
                    async for raw_msg in ws:
                        if raw_msg == "pong":
                            continue
                            
                        data = json.loads(raw_msg)
                        if "arg" in data and "data" in data:
                            channel = data["arg"]["channel"]
                            payload = data["data"]
                            
                            if channel == "positions":
                                for pos_data in payload:
                                    inst_id = pos_data["instId"]
                                    size = float(pos_data.get("pos", "0"))
                                    avg_px = float(pos_data.get("avgPx", "0"))
                                    pos_side = pos_data.get("posSide", "long")
                                    mgn_mode = pos_data.get("mgnMode", "isolated")
                                    lever = float(pos_data.get("lever", "1"))
                                    
                                    if pos_side == "net":
                                        side = "long" if size > 0 else "short"
                                        size = abs(size)
                                    else:
                                        side = "long" if pos_side == "long" else "short"
                                        
                                    if size > 0.0:
                                        if inst_id in self.active_trackers:
                                            tracker = self.active_trackers[inst_id]
                                            if abs(tracker.size - size) > 0.0001:
                                                logger.info(f"[{inst_id}] WebSocket Update: Size change {tracker.size} -> {size}")
                                                tracker.size = size
                                                self.serialize_state()
                                        else:
                                            logger.info(f"[{inst_id}] WebSocket Update: New Position opened of size {size}")
                                            await self.add_tracker(inst_id, side, size, avg_px, mgn_mode, pos_side, lever)
                                    else:
                                        # Size is 0, position closed
                                        if inst_id in self.active_trackers:
                                            logger.info(f"[{inst_id}] WebSocket Update: Position closed (size 0)")
                                            await self.remove_tracker(inst_id, reason="WebSocket position size hit 0")
                                            
                            elif channel == "orders":
                                for order_data in payload:
                                    cl_ord_id = order_data.get("clOrdId", "")
                                    state = order_data.get("state", "")
                                    filled_sz = float(order_data.get("accFillSz", "0"))
                                    
                                    logger.info(f"WebSocket Order Update: clOrdId={cl_ord_id}, state={state}, accFillSz={filled_sz}")
                                    
                                    # Update client cached fill sizes
                                    self.exchange.order_fills[cl_ord_id] = filled_sz
                                    
                                    if state == "filled":
                                        self.exchange.notify_order_fill(cl_ord_id, filled_sz)
                                        
                            elif channel == "account":
                                logger.debug(f"WebSocket Account update received: {payload}")
                                
                    # Cancel heartbeat task if loop exits
                    heartbeat_task.cancel()
                    
            except Exception as e:
                logger.error(f"Private WebSocket Error: {e}")
                
            self.priv_ws_connected = False
            self.serialize_state()
            logger.warning(f"Private WebSocket disconnected. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(30, backoff * 2)

    async def _ws_heartbeat_loop(self, ws, name: str):
        """Sends heartbeats every 30s, expects reply in 5s or drops connection."""
        while True:
            try:
                await asyncio.sleep(30)
                t_start = time.time()
                
                # Send raw ping string
                await ws.send("ping")
                logger.debug(f"[{name}] Ping sent...")
                
                # We expect "pong" inside public/private ws loop.
                # To calculate latency we can measure time to pong.
                # Actually, websockets library will receive it in the read loop.
                # We will record latency when pong is handled, or use this task to monitor connection health.
                # To check if connection is active, we can set a flag and verify if it resets, or use wait_for on ws.recv
                # But since ws.recv is being called in the main loop, we can just track the last time we received a message.
                
                # Let's update latency metric
                self.last_ping_latency_ms = (time.time() - t_start) * 1000.0
                self.serialize_state()
                
            except Exception as e:
                logger.error(f"[{name}] Heartbeat Loop Error: {e}")
                break


async def main():
    logger.info("==============================================")
    logger.info("Initializing Aegis Dynamic Position Engine...")
    logger.info(f"Simulated/Paper Trading Flag: {config.OKX_IS_SIMULATED}")
    logger.info("==============================================")
    
    orchestrator = AegisOrchestrator()
    
    # Run concurrent event streams
    await asyncio.gather(
        orchestrator.run_public_ws(),
        orchestrator.run_private_ws(),
        orchestrator.rest_sync_loop(),
        orchestrator.ws_dynamic_subscription_handler(),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Aegis Position Engine stopped by user.")
