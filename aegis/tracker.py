import asyncio
import uuid
import logging
from config import get_coin_profile

logger = logging.getLogger("Aegis.Tracker")

class PositionTracker:
    def __init__(self, inst_id: str, side: str, size: float, entry_price: float, 
                 target_tp_ratio: float, atr: float, ct_val: float, 
                 exchange_interface, mgn_mode: str = "isolated", pos_side: str = None, action_log_cb=None, trade_ledger_cb=None, lever: float = 1.0, esik1_fraction: float = 0.50):
        """
        Object-Oriented PositionTracker operating as an isolated state machine per unique instrument ID.
        
        :param inst_id: Instrument ID (e.g. "PEPE-USDT-SWAP")
        :param side: Position side ("long" or "short")
        :param size: Position size in contracts (float)
        :param entry_price: Average entry price of the position
        :param target_tp_ratio: TP target percentage or fraction (e.g., 0.35 for 0.35%)
        :param atr: Average True Range (calculated from past 14 1m candles)
        :param ct_val: Contract face value (e.g. 0.01 for BTC, 10000 for PEPE)
        :param exchange_interface: OKXExchange client interface
        :param mgn_mode: Margin mode ("isolated" or "cross")
        :param pos_side: OKX position side ("long", "short", or "net")
        """
        self.inst_id = inst_id
        self.side = side.lower()  # "long" or "short"
        self.size = float(size)
        self.entry_price = float(entry_price)
        self.target_tp_ratio = float(target_tp_ratio)
        self.atr = float(atr)
        self.ct_val = float(ct_val)
        self.exchange = exchange_interface
        self.mgn_mode = mgn_mode
        self.pos_side = pos_side if pos_side else ("long" if self.side == "long" else "short")
        self.action_log_cb = action_log_cb
        self.trade_ledger_cb = trade_ledger_cb
        self.lever = float(lever)
        self.session_id = uuid.uuid4().hex[:8]
        self.esik1_fraction = esik1_fraction
        
        # Load profile
        self.profile = get_coin_profile(inst_id)
        
        # State machine initialization
        self.state = "INIT"  # "INIT", "RISK_ZERO", "TRAILING", "CLOSED"
        self.is_locked = False  # Idempotence/lock flag
        self.algo_sl_id = None
        self.last_placed_sl_px = None
        
        # Trailing variables
        self.highest_price = 0.0
        self.lowest_price = 0.0
        self.trailing_stop = 0.0
        
        # Real-time metrics
        self.current_price = 0.0
        self.volume_ratio = 0.0
        self.ob_imbalance = 0.0
        self.ob_multiplier = 1.0
        
        # Convert target_tp_ratio to decimal fraction.
        # If target_tp_ratio > 0.05, we treat it as a percentage (e.g. 0.35 means 0.0035 fraction)
        if self.target_tp_ratio > 0.05:
            self.target_tp_fraction = self.target_tp_ratio / 100.0
        else:
            self.target_tp_fraction = self.target_tp_ratio
            
        # Calculate Eşik targets
        if self.side == "long":
            self.tp1_target = self.entry_price * (1.0 + self.target_tp_fraction * self.esik1_fraction)
            self.tp2_target = self.entry_price * (1.0 + self.target_tp_fraction)
            self.breakeven_px = self.entry_price * 1.001
        else:
            self.tp1_target = self.entry_price * (1.0 - self.target_tp_fraction * self.esik1_fraction)
            self.tp2_target = self.entry_price * (1.0 - self.target_tp_fraction)
            self.breakeven_px = self.entry_price * 0.999
            
        if self.action_log_cb:
            e1_pct = self.target_tp_ratio * self.esik1_fraction
            e2_pct = self.target_tp_ratio
            symbol = self.inst_id.replace("-SWAP", "")
            esik1_percent_str = f"{int(self.esik1_fraction * 100)}"
            self.action_log_cb(f"🛰️ [{symbol}] Yeni pozisyon yakalandı! | Yön: {self.side.upper()} | Kaldıraç: {self.lever}x | Skynet Hedefi: %{self.target_tp_ratio:.2f} | Eşik 1 (%{esik1_percent_str}): ${self.tp1_target:.6f} | Eşik 2 (%100): ${self.tp2_target:.6f}")
            
        logger.info(f"Initialized PositionTracker for {self.inst_id} ({self.side.upper()}): "
                    f"Size={self.size}, Entry={self.entry_price}, TP1_Target={self.tp1_target:.6f}, "
                    f"TP2_Target={self.tp2_target:.6f}, Initial ATR={self.atr:.6f}")

    def to_dict(self) -> dict:
        """Serializes current tracker state for UI reporting."""
        return {
            "inst_id": self.inst_id,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "target_tp_ratio": self.target_tp_ratio,
            "target_tp_fraction": self.target_tp_fraction,
            "state": self.state,
            "is_locked": self.is_locked,
            "current_price": self.current_price,
            "atr": self.atr,
            "volume_ratio": self.volume_ratio,
            "ob_imbalance": self.ob_imbalance,
            "ob_multiplier": self.ob_multiplier,
            "tp1_target": self.tp1_target,
            "tp2_target": self.tp2_target,
            "esik1_fraction": getattr(self, "esik1_fraction", 0.50),
            "breakeven_px": self.breakeven_px,
            "trailing_stop": self.trailing_stop if self.state == "TRAILING" else None,
            "highest_price": self.highest_price if self.side == "long" and self.state == "TRAILING" else None,
            "lowest_price": self.lowest_price if self.side == "short" and self.state == "TRAILING" else None,
            "trailing_gap_atr": self.profile.get("trailing_gap_atr", 1.0),
            "mgn_mode": self.mgn_mode,
            "pos_side": self.pos_side,
            "lever": self.lever
        }

    def update_targets(self, new_tp_ratio: float, new_esik1_fraction: float = 0.50):
        """Dynamically updates TP targets if Skynet alters the target_tp_ratio or esik1_fraction on the fly."""
        if abs(self.target_tp_ratio - new_tp_ratio) > 0.0001 or abs(getattr(self, "esik1_fraction", 0.50) - new_esik1_fraction) > 0.0001:
            self.target_tp_ratio = float(new_tp_ratio)
            self.esik1_fraction = float(new_esik1_fraction)
            if self.target_tp_ratio > 0.05:
                self.target_tp_fraction = self.target_tp_ratio / 100.0
            else:
                self.target_tp_fraction = self.target_tp_ratio
                
            if self.side == "long":
                self.tp1_target = self.entry_price * (1.0 + self.target_tp_fraction * self.esik1_fraction)
                self.tp2_target = self.entry_price * (1.0 + self.target_tp_fraction)
            else:
                self.tp1_target = self.entry_price * (1.0 - self.target_tp_fraction * self.esik1_fraction)
                self.tp2_target = self.entry_price * (1.0 - self.target_tp_fraction)
                
            logger.info(f"[{self.inst_id}] Targets dynamically updated! New TP: {self.target_tp_ratio}, Eşik1: {self.esik1_fraction}, TP1: {self.tp1_target:.6f}, TP2: {self.tp2_target:.6f}")


    def _log_trade(self, action_event: str, exit_price: float, note: str):
        if not self.trade_ledger_cb:
            return
        
        # Calculate Spot Move %
        if self.side == "long":
            spot_move_pct = ((exit_price - self.entry_price) / self.entry_price) * 100.0
        else:
            spot_move_pct = ((self.entry_price - exit_price) / self.entry_price) * 100.0
            
        realized_pnl = spot_move_pct * self.lever
        
        self.trade_ledger_cb(
            session_id=self.session_id,
            symbol=self.inst_id,
            side=self.side.upper(),
            leverage=self.lever,
            action_event=action_event,
            price=exit_price,
            spot_move_pct=spot_move_pct,
            realized_pnl=realized_pnl,
            note=note
        )

    def update_tick(self, current_price: float, volume_ratio: float, ob_imbalance: float):
        """
        Updates the tracker with public real-time market data.
        Evaluates state transition triggers. Called on every market tick.
        """
        if self.state == "CLOSED" or self.is_locked:
            return

        self.current_price = float(current_price)
        self.volume_ratio = float(volume_ratio)
        self.ob_imbalance = float(ob_imbalance)

        # State Machine Logic
        if self.state == "INIT":
            # Check EŞİK 1 (Kısmi Kar Al)
            is_trigger = False
            if self.side == "long" and self.current_price >= self.tp1_target:
                is_trigger = True
            elif self.side == "short" and self.current_price <= self.tp1_target:
                is_trigger = True

            if is_trigger:
                self.is_locked = True
                logger.info(f"[{self.inst_id}] EŞİK 1 (TP1) Triggered at price {self.current_price:.6f}")
                if self.action_log_cb:
                    symbol = self.inst_id.replace("-SWAP", "")
                    self.action_log_cb(f"🎯 [{symbol}] Eşik 1 (%{int(self.esik1_fraction * 100)} Hedef) yakalandı! [{self.side.upper()} - {self.lever}x] Pozisyonun %30'u için LİMİT kapatma emri fırlatıldı.")
                
                # Cancel original TP/SL orders on the exchange
                asyncio.create_task(self.exchange.cancel_algo_orders(self.inst_id))
                
                # Calculate exit price with a 0.5 * ATR buffer
                if self.side == "long":
                    exit_price = self.current_price - (0.5 * self.atr)
                else:
                    exit_price = self.current_price + (0.5 * self.atr)
                
                # Spawn asynchronous exit task
                asyncio.create_task(self.execute_smart_exit(size_pct=0.30, price=exit_price, label="TP1"))

        elif self.state == "RISK_ZERO":
            # BreakEven Stop Loss check
            is_stopped_out = False
            if self.side == "long" and self.current_price <= self.breakeven_px:
                is_stopped_out = True
            elif self.side == "short" and self.current_price >= self.breakeven_px:
                is_stopped_out = True
                
            if is_stopped_out:
                self.is_locked = True
                logger.info(f"[{self.inst_id}] BreakEven Stop-Loss Triggered at {self.current_price:.6f}")
                if self.action_log_cb:
                    symbol = self.inst_id.replace("-SWAP", "")
                    pnl_pct = ((self.current_price - self.entry_price) / self.entry_price * self.lever * 100) if self.side == "long" else ((self.entry_price - self.current_price) / self.entry_price * self.lever * 100)
                    self.action_log_cb(f"⚠️ [{symbol}] Başa Baş Koruma Kalkanı Tetiklendi! Fiyat girişe sarktığı için kalan %70 emniyetli çıkışla (${self.current_price:.6f}) kapatıldı. Borsada Gerçekleşen PnL: {pnl_pct:+.2f}% | Sermaye başarıyla korundu.")
                # Cancel the exchange stop loss before exit
                if getattr(self, "algo_sl_id", None):
                    asyncio.create_task(self.exchange.cancel_algo_order(self.inst_id, self.algo_sl_id))
                    self.algo_sl_id = None
                asyncio.create_task(self.execute_smart_exit(size_pct=1.0, price=self.current_price, label="BE_EXIT"))
                return

            # Check EŞİK 2 (Ana Hedef / Sömürme)
            is_trigger = False
            if self.side == "long" and self.current_price >= self.tp2_target:
                is_trigger = True
            elif self.side == "short" and self.current_price <= self.tp2_target:
                is_trigger = True

            if is_trigger:
                self.is_locked = True
                logger.info(f"[{self.inst_id}] EŞİK 2 (TP2) Triggered at price {self.current_price:.6f}")
                
                # Check momentum and orderbook wall support
                # LONG: volume_ratio > 2.0 and ob_imbalance > 0.15 (bullish momentum)
                # SHORT: volume_ratio > 2.0 and ob_imbalance < -0.15 (bearish momentum)
                is_momentum_strong = False
                if self.side == "long" and self.volume_ratio > 2.0 and self.ob_imbalance > 0.15:
                    is_momentum_strong = True
                elif self.side == "short" and self.volume_ratio > 2.0 and self.ob_imbalance < -0.15:
                    is_momentum_strong = True

                if is_momentum_strong:
                    # Transition to TRAILING mode
                    self.state = "TRAILING"
                    if self.side == "long":
                        self.highest_price = self.current_price
                        self.trailing_stop = self.highest_price - (self.profile["trailing_gap_atr"] * self.atr)
                    else:
                        self.lowest_price = self.current_price
                        self.trailing_stop = self.lowest_price + (self.profile["trailing_gap_atr"] * self.atr)
                    
                    self.is_locked = False
                    logger.info(f"[{self.inst_id}] Strong momentum detected (VolRatio={self.volume_ratio:.2f}, OBImb={self.ob_imbalance:.2f}). "
                                f"Transitioned to TRAILING. Initial Trailing Stop={self.trailing_stop:.6f}")
                    if self.action_log_cb:
                        symbol = self.inst_id.replace("-SWAP", "")
                        self.action_log_cb(f"📈 [{symbol}] Eşik 2 (Tam Hedef) yakalandı! [{self.side.upper()} - {self.lever}x] Hacim güçlü ({self.volume_ratio:.2f}x). Orijinal TP iptal edildi, Takipçi Stop ${self.trailing_stop:.6f} seviyesinden aktif edildi.")
                    
                    # Cancel original TP/SL orders on the exchange
                    asyncio.create_task(self.exchange.cancel_algo_orders(self.inst_id))
                    # Reset local algo sl ID
                    self.algo_sl_id = None
                    # Place initial Trailing Stop on the exchange
                    asyncio.create_task(self.set_exchange_stop_loss(self.trailing_stop))
                else:
                    # No momentum: Execute 100% exit immediately
                    logger.info(f"[{self.inst_id}] Normal momentum (VolRatio={self.volume_ratio:.2f}, OBImb={self.ob_imbalance:.2f}). "
                                f"Triggering immediate 100% exit (TP2_EXIT)")
                    if self.action_log_cb:
                        symbol = self.inst_id.replace("-SWAP", "")
                        self.action_log_cb(f"⚡ [{symbol}] Eşik 2 ${self.current_price:.6f} seviyesinde tetiklendi. Normal momentum, anında %100 kapatma emri gönderildi.")
                    asyncio.create_task(self.exchange.cancel_algo_orders(self.inst_id))
                    # Reset local algo sl ID
                    self.algo_sl_id = None
                    asyncio.create_task(self.execute_smart_exit(size_pct=1.0, price=self.current_price, label="TP2_EXIT"))

        elif self.state == "TRAILING":
            # Trailing Cycle logic
            if self.side == "long":
                if self.current_price > self.highest_price:
                    self.highest_price = self.current_price
                    self.trailing_stop = self.highest_price - (self.profile["trailing_gap_atr"] * self.atr)
                    logger.debug(f"[{self.inst_id}] New High: {self.highest_price:.6f}, Trailing Stop moved to {self.trailing_stop:.6f}")
                
                # Check if we should update exchange stop loss
                should_update = False
                if not getattr(self, "last_placed_sl_px", None):
                    should_update = True
                else:
                    if self.trailing_stop > self.last_placed_sl_px + (0.1 * self.atr):
                        should_update = True
                        
                if should_update:
                    asyncio.create_task(self.set_exchange_stop_loss(self.trailing_stop))

                if self.current_price <= self.trailing_stop:
                    self.is_locked = True
                    logger.info(f"[{self.inst_id}] Trailing stop breached at {self.current_price:.6f} <= {self.trailing_stop:.6f}. Triggering trailing exit.")
                    if self.action_log_cb:
                        symbol = self.inst_id.replace("-SWAP", "")
                        self.action_log_cb(f"⚡ ÇIKIŞ TETİKLENDİ: [{symbol}] Takipçi Stop ${self.current_price:.6f} seviyesinde tetiklendi.")
                    # Cancel the exchange stop loss before exit
                    if getattr(self, "algo_sl_id", None):
                        asyncio.create_task(self.exchange.cancel_algo_order(self.inst_id, self.algo_sl_id))
                        self.algo_sl_id = None
                    asyncio.create_task(self.execute_smart_exit(size_pct=1.0, price=self.current_price, label="TRAILING_EXIT"))

            elif self.side == "short":
                if self.current_price < self.lowest_price:
                    self.lowest_price = self.current_price
                    self.trailing_stop = self.lowest_price + (self.profile["trailing_gap_atr"] * self.atr)
                    logger.debug(f"[{self.inst_id}] New Low: {self.lowest_price:.6f}, Trailing Stop moved to {self.trailing_stop:.6f}")

                # Check if we should update exchange stop loss
                should_update = False
                if not getattr(self, "last_placed_sl_px", None):
                    should_update = True
                else:
                    if self.trailing_stop < self.last_placed_sl_px - (0.1 * self.atr):
                        should_update = True
                        
                if should_update:
                    asyncio.create_task(self.set_exchange_stop_loss(self.trailing_stop))

                if self.current_price >= self.trailing_stop:
                    self.is_locked = True
                    logger.info(f"[{self.inst_id}] Trailing stop breached at {self.current_price:.6f} >= {self.trailing_stop:.6f}. Triggering trailing exit.")
                    if self.action_log_cb:
                        symbol = self.inst_id.replace("-SWAP", "")
                        self.action_log_cb(f"⚡ ÇIKIŞ TETİKLENDİ: [{symbol}] Takipçi Stop ${self.current_price:.6f} seviyesinde tetiklendi.")
                    # Cancel the exchange stop loss before exit
                    if getattr(self, "algo_sl_id", None):
                        asyncio.create_task(self.exchange.cancel_algo_order(self.inst_id, self.algo_sl_id))
                        self.algo_sl_id = None
                    asyncio.create_task(self.execute_smart_exit(size_pct=1.0, price=self.current_price, label="TRAILING_EXIT"))

    async def execute_smart_exit(self, size_pct: float, price: float, label: str):
        """
        Asynchronously handles smart execution with limit order, tolerance buffer, 
        and a 300ms timeout that cancels the limit order and falls back to a market order.
        """
        logger.info(f"[{self.inst_id}] Initiating Smart Exit ({label}) for {size_pct*100}% of position")
        
        try:
            # 1. Round/Format order size
            # Order size is size * size_pct. Let's get instrument info from exchange
            inst_info = self.exchange.get_instrument_info(self.inst_id)
            lot_sz = float(inst_info.get("lotSz", "1"))
            tick_sz = float(inst_info.get("tickSz", "0.01"))
            
            raw_sz = self.size * size_pct
            # Round sz to lotSz steps
            sz_units = round(raw_sz / lot_sz)
            sz = sz_units * lot_sz
            
            # Ensure sz is at least lot_sz if we have a position
            if sz < lot_sz and self.size > 0:
                sz = lot_sz
            # Ensure sz does not exceed the tracker's current size
            if sz > self.size:
                sz = round(self.size / lot_sz) * lot_sz
                if sz > self.size:
                    sz = int(self.size / lot_sz) * lot_sz
            
            if sz <= 0:
                logger.warning(f"[{self.inst_id}] Calculated close size {sz} is 0. Aborting.")
                self.is_locked = False
                return

            # Determine close side (opposite of position side)
            close_side = "sell" if self.side == "long" else "buy"
            
            # 2. Apply limit tolerance buffer
            # Close LONG (sell): Place order slightly below price. limit_price = price * (1 - limit_tolerance)
            # Close SHORT (buy): Place order slightly above price. limit_price = price * (1 + limit_tolerance)
            tolerance = self.profile["limit_tolerance"]
            if close_side == "sell":
                limit_px = price * (1.0 - tolerance)
            else:
                limit_px = price * (1.0 + tolerance)
                
            # Round price to tickSz steps
            limit_px = round(limit_px / tick_sz) * tick_sz

            # 3. Generate strict alphanumeric clientOrderId (no underscores, length < 32)
            # Prefix formats: AegisTP1, AegisTP2, AegisTRL
            prefix_map = {
                "TP1": "AegisTP1",
                "TP2_EXIT": "AegisTP2",
                "TRAILING_EXIT": "AegisTRL"
            }
            prefix = prefix_map.get(label, "AegisEXT")
            unique_suffix = uuid.uuid4().hex[:16]  # 16 alphanumeric characters
            cl_ord_id = f"{prefix}{unique_suffix}"[:30]  # Safe margin under 32 chars
            
            # Format numbers as strings for API
            sz_str = f"{sz:.8f}".rstrip("0").rstrip(".")
            px_str = f"{limit_px:.8f}".rstrip("0").rstrip(".")

            logger.info(f"[{self.inst_id}] Placing Limit Order {cl_ord_id} to {close_side.upper()} "
                        f"Size={sz_str}, Price={px_str} (Tolerance buffer applied)")
            
            # Register an event to listen for fills of this clOrdId
            fill_event = asyncio.Event()
            self.exchange.register_order_event(cl_ord_id, fill_event)
            
            # Submit Limit Order
            order_res = await self.exchange.place_order(
                inst_id=self.inst_id,
                side=close_side,
                ord_type="limit",
                sz=sz_str,
                px=px_str,
                cl_ord_id=cl_ord_id,
                pos_side=self.pos_side,
                mgn_mode=self.mgn_mode
            )
            
            if not order_res or order_res.get("code") != "0":
                err_msg = order_res.get("msg", "Unknown error") if order_res else "No response"
                logger.error(f"[{self.inst_id}] Limit order placement failed: {err_msg}")
                self.is_locked = False
                self.exchange.unregister_order_event(cl_ord_id)
                return

            ord_id = order_res["data"][0]["ordId"]
            logger.info(f"[{self.inst_id}] Limit Order placed successfully. OKX OrdId={ord_id}. Starting 300ms timeout check...")
            
            # 4. Asynchronous 300ms Timeout check
            is_filled = False
            try:
                # Wait for the WebSocket fill event, timeout in 300ms (0.3 seconds)
                await asyncio.wait_for(fill_event.wait(), timeout=0.3)
                is_filled = True
                logger.info(f"[{self.inst_id}] Limit order {cl_ord_id} fully filled within 300ms!")
            except asyncio.TimeoutError:
                if self.action_log_cb:
                    symbol = self.inst_id.replace("-SWAP", "")
                    self.action_log_cb(f"⏳ [{symbol}] 300ms Limit zaman aşımı! Kayma koruması aktif: Kalan kısım MARKET emriyle süpürüldü.")
                logger.warning(f"[{self.inst_id}] Limit order {cl_ord_id} not fully filled in 300ms. Cancelling and falling back to market order...")
                
            # Clean up the fill event listener
            self.exchange.unregister_order_event(cl_ord_id)
            
            actual_filled_sz = 0.0
            
            if not is_filled:
                # Cancel the outstanding limit order
                cancel_res = await self.exchange.cancel_order(self.inst_id, ord_id=ord_id, cl_ord_id=cl_ord_id)
                logger.info(f"[{self.inst_id}] Cancellation request sent for order {ord_id}. Response: {cancel_res}")
                
                # Check how much was filled before cancellation
                # We fetch order status from the exchange
                ord_status = await self.exchange.get_order(self.inst_id, ord_id=ord_id)
                if ord_status and ord_status.get("code") == "0":
                    order_data = ord_status["data"][0]
                    actual_filled_sz = float(order_data.get("accFillSz", "0"))
                    state = order_data.get("state")
                    logger.info(f"[{self.inst_id}] Post-cancel order state: {state}, accFillSz: {actual_filled_sz}")
                else:
                    # Fallback to local cached filled quantity if GET failed
                    actual_filled_sz = self.exchange.get_cached_filled_sz(cl_ord_id)
                    logger.warning(f"[{self.inst_id}] Failed to fetch order status. Using cached filled size: {actual_filled_sz}")
                
                remaining_sz = sz - actual_filled_sz
                # Round remaining size
                remaining_sz = round(remaining_sz / lot_sz) * lot_sz
                
                if remaining_sz > 0:
                    market_cl_ord_id = f"AegisMKT{uuid.uuid4().hex[:16]}"[:30]
                    remaining_sz_str = f"{remaining_sz:.8f}".rstrip("0").rstrip(".")
                    logger.info(f"[{self.inst_id}] Placing Market Order {market_cl_ord_id} for remaining size {remaining_sz_str}")
                    
                    market_res = await self.exchange.place_order(
                        inst_id=self.inst_id,
                        side=close_side,
                        ord_type="market",
                        sz=remaining_sz_str,
                        cl_ord_id=market_cl_ord_id,
                        pos_side=self.pos_side,
                        mgn_mode=self.mgn_mode
                    )
                    if market_res and market_res.get("code") == "0":
                        logger.info(f"[{self.inst_id}] Market Order {market_cl_ord_id} placed successfully.")
                        actual_filled_sz += remaining_sz
                    else:
                        market_err = market_res.get("msg", "Unknown error") if market_res else "No response"
                        logger.critical(f"[{self.inst_id}] Market order fallback failed: {market_err}")
                else:
                    logger.info(f"[{self.inst_id}] Limit order was fully filled just before cancellation completed.")
            else:
                actual_filled_sz = sz

            # 5. Transition FSM state based on fill
            # Subtract filled quantity from size
            self.size = max(0.0, self.size - actual_filled_sz)
            
            # Round remaining size to instrument lot size precision to prevent floating point residue
            lot_sz_str = inst_info.get("lotSz", "1")
            if "." in lot_sz_str:
                prec = len(lot_sz_str.split(".")[1].rstrip("0"))
                self.size = round(self.size, prec)
            else:
                self.size = round(self.size, 0)
                
            logger.info(f"[{self.inst_id}] Smart Exit execution complete. Remaining position size: {self.size}")
            
            # Transition state
            if label == "TP1":
                if self.size <= 0.0001:  # Close to zero
                    self.state = "CLOSED"
                else:
                    self.state = "RISK_ZERO"
                    if self.action_log_cb:
                        symbol = self.inst_id.replace("-SWAP", "")
                        self.action_log_cb(f"🛡️ [{symbol}] Kısmi satış onaylandı. [{self.side.upper()} - {self.lever}x] Stop-Loss noktası komisyonlar dahil BAŞA BAŞ seviyesine kilitlendi. Durum: RISK_ZERO (Risk Sıfır!).")
                        
                    self._log_trade(action_event="TP1_PARTIAL_EXIT", exit_price=price, note="30% Kısmi Kâr Alma")
                    # Place BreakEven stop loss order on the exchange
                    asyncio.create_task(self.set_exchange_stop_loss(self.breakeven_px))

            else:  # TP2_EXIT, TRAILING_EXIT, or BE_EXIT
                self.state = "CLOSED"
                if self.action_log_cb:
                    symbol = self.inst_id.replace("-SWAP", "")
                    pnl_pct = ((self.current_price - self.entry_price) / self.entry_price * self.lever * 100) if self.side == "long" else ((self.entry_price - self.current_price) / self.entry_price * self.lever * 100)
                    self.action_log_cb(f"🔴 [{symbol}] Pozisyon tamamen kapatıldı. Ses kesildi. | Yön: {self.side.upper()} | Kaldıraç: {self.lever}x | Borsada Gerçekleşen Toplam PnL: {pnl_pct:+.2f}%")
                    
                self._log_trade(action_event=label, exit_price=self.current_price, note="Pozisyon Tamamen Kapatıldı")
                
            logger.info(f"[{self.inst_id}] State transitioned to {self.state}")
            
        except Exception as e:
            logger.exception(f"[{self.inst_id}] Exception occurred during execute_smart_exit: {e}")
        finally:
            self.is_locked = False
            # Force trigger state write to JSON
            self.exchange.notify_state_change()

    async def set_exchange_stop_loss(self, trigger_px: float):
        """Places or updates a stop-loss algo order on the exchange for the remaining size."""
        try:
            # 1. Cancel existing stop loss if any
            if getattr(self, "algo_sl_id", None):
                logger.info(f"[{self.inst_id}] Cancelling existing exchange stop loss order {self.algo_sl_id}...")
                await self.exchange.cancel_algo_order(self.inst_id, self.algo_sl_id)
                self.algo_sl_id = None

            # Get instrument info for precision
            inst_info = self.exchange.get_instrument_info(self.inst_id)
            tick_sz = float(inst_info.get("tickSz", "0.01"))
            
            # Format trigger price
            rounded_trigger = round(trigger_px / tick_sz) * tick_sz
            px_str = f"{rounded_trigger:.8f}".rstrip("0").rstrip(".")
            sz_str = f"{self.size:.8f}".rstrip("0").rstrip(".")
            
            close_side = "sell" if self.side == "long" else "buy"
            
            logger.info(f"[{self.inst_id}] Placing new exchange Stop-Loss at {px_str} for size {sz_str}...")
            res = await self.exchange.place_algo_order(
                inst_id=self.inst_id,
                side=close_side,
                ord_type="conditional",
                sz=sz_str,
                pos_side=self.pos_side,
                mgn_mode=self.mgn_mode,
                sl_trigger_px=px_str,
                sl_ord_px="-1"
            )
            
            if res and res.get("code") == "0" and "data" in res:
                self.algo_sl_id = res["data"][0]["algoId"]
                self.last_placed_sl_px = trigger_px
                logger.info(f"[{self.inst_id}] Exchange Stop-Loss placed successfully. AlgoId: {self.algo_sl_id}, Price: {self.last_placed_sl_px}")
            else:
                err_msg = res.get("msg", "Unknown error") if res else "No response"
                logger.error(f"[{self.inst_id}] Failed to place exchange Stop-Loss: {err_msg}")
        except Exception as e:
            logger.exception(f"[{self.inst_id}] Exception in set_exchange_stop_loss: {e}")

