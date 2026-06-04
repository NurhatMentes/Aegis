import asyncio
import uuid
import logging
import time
from config import get_coin_profile, safe_float

logger = logging.getLogger("Aegis.Tracker")

# Varsayılan minimum trailing stop mesafesi: fiyatın %0.17'si
DEFAULT_MIN_TRAILING_GAP_PCT = 0.0017
# Varsayılan smart breakeven offset: %0.12 (giriş fiyatının altına/üstüne)
DEFAULT_SMART_BE_OFFSET_PCT = 0.0012

class PositionTracker:
    def __init__(self, inst_id: str, side: str, size: float, entry_price: float, 
                 target_tp_ratio: float, atr: float, ct_val: float, 
                 exchange_interface, mgn_mode: str = "isolated", pos_side: str = None, action_log_cb=None, trade_ledger_cb=None, lever: float = 1.0, esik1_fraction: float = 0.50,
                 min_trailing_gap_pct: float = None, smart_be_offset_pct: float = None):
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
        self.min_trailing_gap_pct = min_trailing_gap_pct if min_trailing_gap_pct is not None else DEFAULT_MIN_TRAILING_GAP_PCT
        self.smart_be_offset_pct = smart_be_offset_pct if smart_be_offset_pct is not None else DEFAULT_SMART_BE_OFFSET_PCT
        
        # Load profile
        self.profile = get_coin_profile(inst_id)
        
        # State machine initialization
        self.state = "INIT"  # "INIT", "RISK_ZERO", "TRAILING", "CLOSED"
        self.is_locked = False  # Idempotence/lock flag
        self.algo_sl_id = None
        self.last_placed_sl_px = None
        self.last_placed_spread = None
        self.last_ts_update_time = 0.0
        self.algo_tp_id = None
        self.last_placed_tp_px = None
        self.squeeze_defense_active = False
        
        # Trailing variables
        self.highest_price = 0.0
        self.lowest_price = 0.0
        self.trailing_stop = 0.0
        
        # Real-time metrics
        self.current_price = 0.0
        self.volume_ratio = 0.0
        self.ob_imbalance = 0.0
        self.ob_multiplier = 1.0
        
        # Convert target_tp_ratio to decimal fraction (always treat it as a percentage).
        self.target_tp_fraction = self.target_tp_ratio / 100.0
            
        # Eşik 2 = Eşik 1 fiyatı + sabit %0.10 (entry üzerinden)
        # Böylece Eşik 1 nerede belirlense Eşik 2 her zaman 0.10% üstünde olur.
        ESIK2_FIXED_INCREMENT = 0.0010  # %0.10 sabit artış

        # Calculate Eşik targets
        if self.side == "long":
            self.tp1_target = self.entry_price * (1.0 + self.target_tp_fraction * self.esik1_fraction)
            self.tp2_target = self.entry_price * (1.0 + self.target_tp_fraction * self.esik1_fraction + ESIK2_FIXED_INCREMENT)
        else:
            self.tp1_target = self.entry_price * (1.0 - self.target_tp_fraction * self.esik1_fraction)
            self.tp2_target = self.entry_price * (1.0 - self.target_tp_fraction * self.esik1_fraction - ESIK2_FIXED_INCREMENT)

        # Smart Breakeven: Eşik 1 TP oranı %0.15'ten yüksekse, SL'yi girişin altına/üstüne kur
        # smart_be_offset_pct kadar giriş fiyatının altına (long) veya üstüne (short) SL konur.
        # Bu, küçük bir zarar toleransı tanıyarak erken çıkışı önler.
        esik1_tp_pct = self.target_tp_fraction * self.esik1_fraction  # örn: 0.00175
        if esik1_tp_pct > 0.0015:  # > %0.15
            if self.side == "long":
                self.breakeven_px = self.entry_price * (1.0 - self.smart_be_offset_pct)
            else:
                self.breakeven_px = self.entry_price * (1.0 + self.smart_be_offset_pct)
        else:
            self.breakeven_px = self.entry_price

        if self.action_log_cb:
            e1_pct = self.target_tp_ratio * self.esik1_fraction
            e2_pct = self.target_tp_ratio * self.esik1_fraction + 0.10  # Eşik1 + 0.10% sabit
            symbol = self.inst_id.replace("-SWAP", "")
            esik1_percent_str = f"{int(self.esik1_fraction * 100)}"
            self.action_log_cb(f"🛰️ [{symbol}] Yeni pozisyon yakalandı! | Yön: {self.side.upper()} | Kaldıraç: {self.lever}x | Skynet Hedefi: %{self.target_tp_ratio:.2f} | Eşik 1 (%{esik1_percent_str}): ${self.tp1_target:.6f} | Eşik 2 (Eşik1+%0.10): ${self.tp2_target:.6f}")
            
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
            "min_trailing_gap_pct": getattr(self, "min_trailing_gap_pct", DEFAULT_MIN_TRAILING_GAP_PCT),
            "smart_be_offset_pct": getattr(self, "smart_be_offset_pct", DEFAULT_SMART_BE_OFFSET_PCT),
            "breakeven_px": self.breakeven_px,
            "trailing_stop": self.trailing_stop if self.state == "TRAILING" else None,
            "highest_price": self.highest_price if self.side == "long" and self.state == "TRAILING" else None,
            "lowest_price": self.lowest_price if self.side == "short" and self.state == "TRAILING" else None,
            "trailing_gap_atr": self.profile.get("trailing_gap_atr", 1.0),
            "mgn_mode": self.mgn_mode,
            "pos_side": self.pos_side,
            "lever": self.lever,
            "algo_sl_id": getattr(self, "algo_sl_id", None),
            "algo_tp_id": getattr(self, "algo_tp_id", None),
            "last_placed_sl_px": getattr(self, "last_placed_sl_px", None),
            "last_placed_tp_px": getattr(self, "last_placed_tp_px", None),
            "session_id": self.session_id
        }

    def update_targets(self, new_tp_ratio: float, new_esik1_fraction: float = 0.50, new_min_trailing_gap_pct: float = None, new_smart_be_offset_pct: float = None):
        """Dynamically updates TP targets if Skynet alters the target_tp_ratio or esik1_fraction on the fly."""
        # Eşik 1 geçildikten sonra (RISK_ZERO veya TRAILING) hedefleri değiştirmeyi reddet
        if self.state != "INIT":
            return
            
        if abs(self.target_tp_ratio - new_tp_ratio) > 0.0001 or abs(getattr(self, "esik1_fraction", 0.50) - new_esik1_fraction) > 0.0001:
            self.target_tp_ratio = float(new_tp_ratio)
            self.esik1_fraction = float(new_esik1_fraction)
            if new_min_trailing_gap_pct is not None:
                self.min_trailing_gap_pct = new_min_trailing_gap_pct
            if new_smart_be_offset_pct is not None:
                self.smart_be_offset_pct = new_smart_be_offset_pct
            self.target_tp_fraction = self.target_tp_ratio / 100.0
                
            ESIK2_FIXED_INCREMENT = 0.0010  # %0.10 sabit artış
            if self.side == "long":
                self.tp1_target = self.entry_price * (1.0 + self.target_tp_fraction * self.esik1_fraction)
                self.tp2_target = self.entry_price * (1.0 + self.target_tp_fraction * self.esik1_fraction + ESIK2_FIXED_INCREMENT)
            else:
                self.tp1_target = self.entry_price * (1.0 - self.target_tp_fraction * self.esik1_fraction)
                self.tp2_target = self.entry_price * (1.0 - self.target_tp_fraction * self.esik1_fraction - ESIK2_FIXED_INCREMENT)

            # Smart Breakeven güncelle
            esik1_tp_pct = self.target_tp_fraction * self.esik1_fraction
            if esik1_tp_pct > 0.0015:  # > %0.15
                if self.side == "long":
                    self.breakeven_px = self.entry_price * (1.0 - self.smart_be_offset_pct)
                else:
                    self.breakeven_px = self.entry_price * (1.0 + self.smart_be_offset_pct)
            else:
                self.breakeven_px = self.entry_price
                
            logger.info(f"[{self.inst_id}] Targets dynamically updated! New TP: {self.target_tp_ratio}, Eşik1: {self.esik1_fraction}, TP1: {self.tp1_target:.6f}, TP2 (Eşik1+%0.10): {self.tp2_target:.6f}, BE: {self.breakeven_px:.6f}")


    def _log_trade(self, action_event: str, exit_price: float, note: str):
        if not self.trade_ledger_cb:
            return
        
        # Calculate Spot Move %
        if self.side == "long":
            spot_move_pct = ((exit_price - self.entry_price) / self.entry_price) * 100.0
        else:
            spot_move_pct = ((self.entry_price - exit_price) / self.entry_price) * 100.0
            
        realized_pnl = spot_move_pct * self.lever
        
        smart_be_pct = (self.smart_be_offset_pct * 100.0) if self.smart_be_offset_pct is not None else 0.0
        min_trail_pct = (self.min_trailing_gap_pct * 100.0) if self.min_trailing_gap_pct is not None else 0.0
        
        self.trade_ledger_cb(
            session_id=self.session_id,
            symbol=self.inst_id,
            side=self.side.upper(),
            leverage=self.lever,
            action_event=action_event,
            price=exit_price,
            spot_move_pct=spot_move_pct,
            realized_pnl=realized_pnl,
            smart_be_offset_pct=smart_be_pct,
            min_trailing_gap_pct=min_trail_pct,
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
                
                # Calculate exit price with a 0.5 * ATR buffer
                # LONG close = SELL -> place slightly BELOW market to get filled as price rises
                # SHORT close = BUY  -> place slightly BELOW market so the tolerance buffer brings it near market
                exit_price = self.current_price - (0.5 * self.atr)
                
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
                
                # Seçenek 2: Eşik 2'ye ulaşıldığında KOŞULSUZ olarak TRAILING moduna geç.
                # Hacim ve emir defteri verisi (ob_imbalance) trailing aktifken stop mesafesini
                # daraltmak / genişletmek için kullanılır (calculate_ob_multiplier).
                # Balina duvarı tespit edilirse stop sıkışır, momentum güçlüyse stop genişler.
                
                # Trailing stop başlangıç noktası: mevcut fiyat
                # Gap hesabı: calculate_ob_multiplier() zaten ob_imbalance'a göre dinamik
                initial_mult = self.calculate_ob_multiplier()
                self.ob_multiplier = initial_mult
                
                self.state = "TRAILING"
                min_gap = self.current_price * self.min_trailing_gap_pct
                trailing_gap = max(initial_mult * self.atr, min_gap)
                if self.side == "long":
                    self.highest_price = self.current_price
                    self.trailing_stop = self.highest_price - trailing_gap
                else:
                    self.lowest_price = self.current_price
                    self.trailing_stop = self.lowest_price + trailing_gap
                
                # Lock'u serbest bırak: TRAILING state'de tick loop çalışmaya devam etmeli
                self.is_locked = False
                
                logger.info(
                    f"[{self.inst_id}] TRAILING başlatıldı (koşulsuz). "
                    f"VolRatio={self.volume_ratio:.2f}, OBImb={self.ob_imbalance:.2f}, "
                    f"Multiplier={initial_mult:.1f}x, InitialStop={self.trailing_stop:.6f}"
                )
                if self.action_log_cb:
                    symbol = self.inst_id.replace("-SWAP", "")
                    # Multiplier'a göre kullanıcıya bilgi ver
                    if initial_mult >= 1.5:
                        market_note = f"Alıcı baskısı güçlü (OB: {self.ob_imbalance:+.2f}) — Geniş takip ({initial_mult:.1f}x ATR)"
                    elif initial_mult <= 0.4:
                        market_note = f"🚨 Balina baskısı! (OB: {self.ob_imbalance:+.2f}) — Dar takip ({initial_mult:.1f}x ATR)"
                    else:
                        market_note = f"Nötr piyasa (OB: {self.ob_imbalance:+.2f}) — Standart takip ({initial_mult:.1f}x ATR)"
                    self.action_log_cb(
                        f"📈 [{symbol}] Eşik 2 (Tam Hedef) yakalandı! [{self.side.upper()} - {self.lever}x] "
                        f"Takipçi Stop ${self.trailing_stop:.6f} seviyesinden aktif edildi. {market_note}"
                    )
                
                # OCO emrini iptal edip takipçi stop'u atomik olarak kur.
                # _transition_to_trailing_stop: önce OCO cancel, sonra SL place (sıralı, race condition yok)
                asyncio.create_task(self._transition_to_trailing_stop(self.trailing_stop))

        elif self.state == "TRAILING":
            # Dynamic OB trailing stop logic
            mult = self.calculate_ob_multiplier()
            self.ob_multiplier = mult
            
            # Squeeze defense logging
            is_squeeze = (mult == 0.4)
            if is_squeeze and not getattr(self, "squeeze_defense_active", False):
                self.squeeze_defense_active = True
                logger.warning(f"[{self.inst_id}] CRITICAL BALENA DEFENSE ACTIVATED! OB Imbalance={self.ob_imbalance:.2f}. Collapsing trailing gap to 0.4x ATR.")
                if self.action_log_cb:
                    symbol = self.inst_id.replace("-SWAP", "")
                    self.action_log_cb(f"🚨 [{symbol}] Balina Baskısı Tespit Edildi! Squeeze riski nedeniyle Takipçi Stop 0.4x ATR seviyesine daraltıldı.")
            elif not is_squeeze:
                self.squeeze_defense_active = False

            if self.side == "long":
                if self.current_price > self.highest_price:
                    self.highest_price = self.current_price
                
                # Dynamic ATR gap with minimum trailing gap floor
                min_gap = self.current_price * self.min_trailing_gap_pct
                trailing_gap = max(self.ob_multiplier * self.atr, min_gap)
                candidate_stop = self.highest_price - trailing_gap
                
                # Jump Protection: Stop only moves tighter (higher)
                if self.trailing_stop == 0.0:
                    self.trailing_stop = candidate_stop
                else:
                    self.trailing_stop = max(self.trailing_stop, candidate_stop)
                    
                logger.debug(f"[{self.inst_id}] Long Trailing: High={self.highest_price:.6f}, Imb={self.ob_imbalance:.2f}, Mult={self.ob_multiplier}, Stop={self.trailing_stop:.6f}")
                
                # Check if we should update exchange trailing stop order
                target_spread = trailing_gap
                now = time.time()
                should_update = False
                if not getattr(self, "last_placed_spread", None):
                    should_update = True
                else:
                    is_tighter = target_spread < (self.last_placed_spread - 0.1 * self.atr)
                    if is_tighter:
                        is_squeeze = (self.ob_multiplier == 0.4)
                        time_passed = (now - getattr(self, "last_ts_update_time", 0.0)) >= 15.0
                        if is_squeeze or time_passed:
                            should_update = True
                        
                if should_update:
                    self.last_placed_spread = target_spread
                    asyncio.create_task(self.set_exchange_trailing_stop(callback_spread=target_spread))

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
                
                # Dynamic ATR gap with minimum trailing gap floor
                min_gap = self.current_price * self.min_trailing_gap_pct
                trailing_gap = max(self.ob_multiplier * self.atr, min_gap)
                candidate_stop = self.lowest_price + trailing_gap
                
                # Jump Protection: Stop only moves tighter (lower)
                if self.trailing_stop == 0.0:
                    self.trailing_stop = candidate_stop
                else:
                    self.trailing_stop = min(self.trailing_stop, candidate_stop)
                    
                logger.debug(f"[{self.inst_id}] Short Trailing: Low={self.lowest_price:.6f}, Imb={self.ob_imbalance:.2f}, Mult={self.ob_multiplier}, Stop={self.trailing_stop:.6f}")

                # Check if we should update exchange trailing stop order
                target_spread = trailing_gap
                now = time.time()
                should_update = False
                if not getattr(self, "last_placed_spread", None):
                    should_update = True
                else:
                    is_tighter = target_spread < (self.last_placed_spread - 0.1 * self.atr)
                    if is_tighter:
                        is_squeeze = (self.ob_multiplier == 0.4)
                        time_passed = (now - getattr(self, "last_ts_update_time", 0.0)) >= 15.0
                        if is_squeeze or time_passed:
                            should_update = True
                        
                if should_update:
                    self.last_placed_spread = target_spread
                    asyncio.create_task(self.set_exchange_trailing_stop(callback_spread=target_spread))

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
            # Cancel all outstanding exchange algo orders (SL/TP) first to free up margin
            logger.info(f"[{self.inst_id}] Cancelling pending exchange algo orders before smart exit...")
            await self.exchange.cancel_algo_orders(self.inst_id)
            self.algo_sl_id = None
            self.algo_tp_id = None
            # Also cancel any pending regular limit orders (e.g. Skynet's limit TP)
            await self.exchange.cancel_pending_limit_orders(self.inst_id)
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

            # --- GUARD: For TP1 partial exits, if rounded size == full position size,
            # the position is too small to split (e.g. 0.01 BTC where 30% = 0.003 < lot_sz).
            # In this case, skip the actual sell and just transition to RISK_ZERO + OCO order.
            if label == "TP1" and sz >= self.size:
                logger.warning(
                    f"[{self.inst_id}] TP1 partial size {sz} equals full position size {self.size} "
                    f"(position too small to split). Skipping partial sell — moving to RISK_ZERO directly."
                )
                self.state = "RISK_ZERO"
                self._log_trade(action_event="TP1_NO_SPLIT", exit_price=self.current_price,
                                note="Pozisyon bölünemeyecek kadar küçük — RISK_ZERO'ya geçildi")
                if self.action_log_cb:
                    symbol = self.inst_id.replace("-SWAP", "")
                    self.action_log_cb(
                        f"🛡️ [{symbol}] Pozisyon lot boyutu nedeniyle bölünemedi. "
                        f"Direkt RISK_ZERO moduna geçildi. Başa Baş SL kuruluyor, Eşik 2 takipçi stop için bekleniyor."
                    )
                # Sadece başa baş SL koy — Eşik 2 TP'si borsaya verilmez.
                # TP emri olsaydı fiyat Eşik 2'ye gelince borsa pozisyonu kapatırdı,
                # tracker TRAILING'e geçemezdi.
                asyncio.create_task(self.set_exchange_stop_loss(self.breakeven_px))
                logger.info(f"[{self.inst_id}] State transitioned to RISK_ZERO (no-split path)")
                return  # finally block releases lock

            # Determine close side (opposite of position side)
            close_side = "sell" if self.side == "long" else "buy"
            
            # 2. Apply limit tolerance buffer
            # LONG close (SELL):  place below market so we get filled as price rises to target.
            #   exit_price = current - 0.5*ATR  →  limit_px = exit_price * (1 - tol)  → well below market
            # SHORT close (BUY):  exit_price is already current - 0.5*ATR (below market).
            #   Tolerance brings it slightly ABOVE that floor, still comfortably below market.
            #   This keeps the limit price inside the allowed OKX range.
            tolerance = self.profile["limit_tolerance"]
            if close_side == "sell":
                limit_px = price * (1.0 - tolerance)
            else:
                # For BUY (close short): add tolerance to bring limit_px up toward market
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
            
            # --- CRITICAL: If limit fails, do NOT release lock. Fall straight to market order. ---
            limit_placement_failed = (not order_res or order_res.get("code") != "0")
            if limit_placement_failed:
                err_msg = order_res.get("msg", "Unknown error") if order_res else "No response"
                logger.error(f"[{self.inst_id}] Limit order placement failed: {err_msg}. Falling back to MARKET order immediately.")
                self.exchange.unregister_order_event(cl_ord_id)
                market_cl_ord_id = f"AegisMKT{uuid.uuid4().hex[:16]}"[:30]
                logger.info(f"[{self.inst_id}] Placing emergency Market Order {market_cl_ord_id} for size {sz_str}")
                market_res = await self.exchange.place_order(
                    inst_id=self.inst_id,
                    side=close_side,
                    ord_type="market",
                    sz=sz_str,
                    cl_ord_id=market_cl_ord_id,
                    pos_side=self.pos_side,
                    mgn_mode=self.mgn_mode
                )
                if market_res and market_res.get("code") == "0":
                    logger.info(f"[{self.inst_id}] Emergency Market Order {market_cl_ord_id} placed successfully.")
                    actual_filled_sz = sz
                else:
                    market_err = market_res.get("msg", "Unknown") if market_res else "No response"
                    logger.critical(f"[{self.inst_id}] Emergency market order also failed: {market_err}. Aborting exit.")
                    return  # keep is_locked=False via finally block, but state unchanged
                # Jump directly to FSM state transition
                self.size = max(0.0, self.size - actual_filled_sz)
                lot_sz_str_fb = inst_info.get("lotSz", "1")
                if "." in lot_sz_str_fb:
                    prec_fb = len(lot_sz_str_fb.split(".")[1].rstrip("0"))
                    self.size = round(self.size, prec_fb)
                else:
                    self.size = round(self.size, 0)
                logger.info(f"[{self.inst_id}] Emergency exit complete. Remaining size: {self.size}")
                if label == "TP1":
                    if self.size <= 0.0001:
                        self.state = "CLOSED"
                    else:
                        self.state = "RISK_ZERO"
                        self._log_trade(action_event="TP1_PARTIAL_EXIT", exit_price=self.current_price, note="30% Kısmi Kâr Alma (Market Emir)")
                        if self.action_log_cb:
                            symbol = self.inst_id.replace("-SWAP", "")
                            self.action_log_cb(f"🛡️ [{symbol}] Kısmi satış (market) onaylandı. Başa Baş SL kuruluyor, Eşik 2 izleniyor...")
                        # Sadece başa baş SL — borsaya TP emri verilmez, Eşik 2 tracker tarafından izlenir
                        asyncio.create_task(self.set_exchange_stop_loss(self.breakeven_px))
                else:
                    self.state = "CLOSED"
                    exit_note = {"TRAILING_EXIT": "Takipçi Stop Tetiklendi — Pozisyon Kapatıldı", "BE_EXIT": "Başa Baş Koruması Tetiklendi — Pozisyon Kapatıldı"}.get(label, "Pozisyon Tamamen Kapatıldı")
                    self._log_trade(action_event=label, exit_price=self.current_price, note=exit_note)
                logger.info(f"[{self.inst_id}] State transitioned to {self.state} (emergency market path)")
                return  # finally block will run and release lock

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
                    actual_filled_sz = safe_float(order_data.get("accFillSz", "0"))
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
                        be_offset_pct_str = f"{self.smart_be_offset_pct * 100:.2f}"
                        if self.side == "long":
                            be_label = f"girişin %{be_offset_pct_str} altı (${self.breakeven_px:.6f})" if self.breakeven_px != self.entry_price else f"BAŞA BAŞ (${self.breakeven_px:.6f})"
                        else:
                            be_label = f"girişin %{be_offset_pct_str} üstü (${self.breakeven_px:.6f})" if self.breakeven_px != self.entry_price else f"BAŞA BAŞ (${self.breakeven_px:.6f})"
                        self.action_log_cb(f"🛡️ [{symbol}] Kısmi satış onaylandı. [{self.side.upper()} - {self.lever}x] Stop-Loss noktası {be_label} seviyesine kilitlendi. Eşik 2 takipçi stop için bekleniyor. Durum: RISK_ZERO (Risk Sıfır!).")
                        
                    self._log_trade(action_event="TP1_PARTIAL_EXIT", exit_price=price, note="30% Kısmi Kâr Alma")
                    # Sadece başa baş SL — borsaya TP emri VERİLMEZ.
                    # Eşik 2 update_tick() tarafından izlenir; oraya gelince SL iptal + trailing stop kurulur.
                    # OCO/TP emri olsaydı borsa Eşik 2'de pozisyonu kapatır, tracker TRAILING'e geçemezdi.
                    asyncio.create_task(self.set_exchange_stop_loss(self.breakeven_px))

            else:  # TP2_EXIT, TRAILING_EXIT, or BE_EXIT
                self.state = "CLOSED"
                if self.action_log_cb:
                    symbol = self.inst_id.replace("-SWAP", "")
                    pnl_pct = ((self.current_price - self.entry_price) / self.entry_price * self.lever * 100) if self.side == "long" else ((self.entry_price - self.current_price) / self.entry_price * self.lever * 100)
                    self.action_log_cb(f"🔴 [{symbol}] Pozisyon tamamen kapatıldı. Ses kesildi. | Yön: {self.side.upper()} | Kaldıraç: {self.lever}x | Borsada Gerçekleşen Toplam PnL: {pnl_pct:+.2f}%")
                    
                exit_note = {"TRAILING_EXIT": "Takipçi Stop Tetiklendi — Pozisyon Kapatıldı", "BE_EXIT": "Başa Baş Koruması Tetiklendi — Pozisyon Kapatıldı"}.get(label, "Pozisyon Tamamen Kapatıldı")
                self._log_trade(action_event=label, exit_price=self.current_price, note=exit_note)
                
            logger.info(f"[{self.inst_id}] State transitioned to {self.state}")
            
        except Exception as e:
            logger.exception(f"[{self.inst_id}] Exception occurred during execute_smart_exit: {e}")
        finally:
            self.is_locked = False
            # Force trigger state write to JSON
            self.exchange.notify_state_change()

    async def set_exchange_stop_loss(self, trigger_px: float):
        """Places or updates a stop-loss algo order on the exchange for the remaining size."""
        if getattr(self, "_is_updating_sl", False):
            logger.debug(f"[{self.inst_id}] Already updating SL. Skipping concurrent set_exchange_stop_loss call.")
            return
            
        self._is_updating_sl = True
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
        finally:
            self._is_updating_sl = False

    async def set_exchange_trailing_stop(self, callback_spread: float, active_px: float = None):
        """Places or updates a native trailing stop (move_order_stop) on the exchange."""
        if getattr(self, "_is_updating_sl", False):
            logger.debug(f"[{self.inst_id}] Already updating SL/TS. Skipping concurrent set_exchange_trailing_stop call.")
            return
            
        self._is_updating_sl = True
        try:
            # 1. Cancel existing stop loss / trailing stop if any
            if getattr(self, "algo_sl_id", None):
                logger.info(f"[{self.inst_id}] Cancelling existing exchange stop order {self.algo_sl_id}...")
                await self.exchange.cancel_algo_order(self.inst_id, self.algo_sl_id)
                self.algo_sl_id = None

            # Get instrument info for precision
            inst_info = self.exchange.get_instrument_info(self.inst_id)
            tick_sz = float(inst_info.get("tickSz", "0.01"))
            
            # Format callback spread
            rounded_spread = max(tick_sz, round(callback_spread / tick_sz) * tick_sz)
            spread_str = f"{rounded_spread:.8f}".rstrip("0").rstrip(".")
            sz_str = f"{self.size:.8f}".rstrip("0").rstrip(".")
            
            active_px_str = None
            if active_px:
                rounded_active = round(active_px / tick_sz) * tick_sz
                active_px_str = f"{rounded_active:.8f}".rstrip("0").rstrip(".")
            
            close_side = "sell" if self.side == "long" else "buy"
            
            logger.info(f"[{self.inst_id}] Placing native Trailing Stop (move_order_stop) with callbackSpread={spread_str} (size {sz_str})...")
            res = await self.exchange.place_algo_order(
                inst_id=self.inst_id,
                side=close_side,
                ord_type="move_order_stop",
                sz=sz_str,
                pos_side=self.pos_side,
                mgn_mode=self.mgn_mode,
                callback_spread=spread_str,
                active_px=active_px_str
            )
            
            if res and res.get("code") == "0" and "data" in res:
                self.algo_sl_id = res["data"][0]["algoId"]
                self.last_placed_spread = callback_spread
                self.last_ts_update_time = time.time()
                logger.info(f"[{self.inst_id}] Exchange native Trailing Stop placed successfully. AlgoId: {self.algo_sl_id}, Spread: {spread_str}")
            else:
                err_msg = res.get("msg", "Unknown error") if res else "No response"
                logger.error(f"[{self.inst_id}] Failed to place exchange native Trailing Stop: {err_msg}")
        except Exception as e:
            logger.exception(f"[{self.inst_id}] Exception in set_exchange_trailing_stop: {e}")
        finally:
            self._is_updating_sl = False



    async def _transition_to_trailing_stop(self, trailing_px: float):
        """Atomically cancels the existing OCO/TP/SL order and places a new trailing stop.
        
        Used in the Eşik 2 momentum branch to avoid race conditions between
        cancel_exchange_take_profit and set_exchange_stop_loss running concurrently.
        """
        try:
            # Step 1: Cancel the existing OCO (or any TP/SL) atomically
            algo_id_to_cancel = getattr(self, "algo_tp_id", None) or getattr(self, "algo_sl_id", None)
            if algo_id_to_cancel:
                logger.info(f"[{self.inst_id}] [TRAILING TRANSITION] Cancelling existing OCO/algo order {algo_id_to_cancel}...")
                await self.exchange.cancel_algo_order(self.inst_id, algo_id_to_cancel)
                self.algo_tp_id = None
                self.algo_sl_id = None
                self.last_placed_tp_px = None
                self.last_placed_sl_px = None
                logger.info(f"[{self.inst_id}] [TRAILING TRANSITION] OCO/algo order cancelled successfully.")
            else:
                logger.info(f"[{self.inst_id}] [TRAILING TRANSITION] No existing OCO/algo order to cancel.")

            # Step 2: Place the new trailing stop (minimum trailing gap floor enforced)
            min_gap = self.current_price * self.min_trailing_gap_pct
            gap_distance = max(self.ob_multiplier * self.atr, min_gap)
            await self.set_exchange_trailing_stop(callback_spread=gap_distance, active_px=self.current_price)
            logger.info(f"[{self.inst_id}] [TRAILING TRANSITION] Native trailing stop placed with spread {gap_distance:.6f} at active_px {self.current_price:.6f}. Ready.")
        except Exception as e:
            logger.exception(f"[{self.inst_id}] Exception in _transition_to_trailing_stop: {e}")

    def calculate_ob_multiplier(self) -> float:
        """Calculates the dynamic ATR multiplier based on orderbook imbalance."""
        if self.side == "long":
            if self.ob_imbalance > 0.15:
                return 1.5
            elif self.ob_imbalance < -0.20:
                return 0.4
            else:
                return 1.0
        else:  # shorts
            if self.ob_imbalance < -0.15:
                return 1.5
            elif self.ob_imbalance > 0.20:
                return 0.4
            else:
                return 1.0