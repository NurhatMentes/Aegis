import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock
from tracker import PositionTracker

class MockExchange:
    def __init__(self):
        self.placed_orders = []
        self.cancelled_orders = []
        self.order_events = {}
        self.order_fills = {}
        self.state_changed = False

    def get_instrument_info(self, inst_id):
        return {"instId": inst_id, "lotSz": "1", "tickSz": "0.01"}

    def register_order_event(self, cl_ord_id, event):
        self.order_events[cl_ord_id] = event
        self.order_fills[cl_ord_id] = 0.0

    def unregister_order_event(self, cl_ord_id):
        self.order_events.pop(cl_ord_id, None)

    def get_cached_filled_sz(self, cl_ord_id):
        return self.order_fills.get(cl_ord_id, 0.0)

    def notify_state_change(self):
        self.state_changed = True

    async def place_order(self, inst_id, side, ord_type, sz, px=None, cl_ord_id=None, pos_side=None, mgn_mode=None):
        self.placed_orders.append({
            "inst_id": inst_id, "side": side, "ord_type": ord_type, 
            "sz": sz, "px": px, "cl_ord_id": cl_ord_id, "pos_side": pos_side, "mgn_mode": mgn_mode
        })
        # Simulate successful API response
        return {"code": "0", "msg": "Success", "data": [{"ordId": "mock_ord_123"}]}

    async def cancel_order(self, inst_id, ord_id, cl_ord_id):
        self.cancelled_orders.append({"inst_id": inst_id, "ord_id": ord_id, "cl_ord_id": cl_ord_id})
        return {"code": "0", "msg": "Cancelled"}

    async def cancel_algo_orders(self, inst_id):
        self.cancelled_orders.append({"inst_id": inst_id, "ord_type": "algo"})
        return True

    async def place_algo_order(self, inst_id, side, ord_type, sz, pos_side=None, mgn_mode=None, tp_trigger_px=None, tp_ord_px=None, sl_trigger_px=None, sl_ord_px=None, callback_ratio=None, callback_spread=None, active_px=None):
        self.placed_orders.append({
            "inst_id": inst_id, "side": side, "ord_type": ord_type, "sz": sz,
            "pos_side": pos_side, "mgn_mode": mgn_mode, "tp_trigger_px": tp_trigger_px, "sl_trigger_px": sl_trigger_px,
            "callback_ratio": callback_ratio, "callback_spread": callback_spread, "active_px": active_px
        })
        return {"code": "0", "msg": "Success", "data": [{"algoId": "mock_algo_sl_123"}]}

    async def cancel_algo_order(self, inst_id, algo_id):
        self.cancelled_orders.append({"inst_id": inst_id, "algo_id": algo_id})
        return {"code": "0", "msg": "Cancelled"}


    async def get_order(self, inst_id, ord_id):
        # Return fully filled status
        return {"code": "0", "msg": "Success", "data": [{"ordId": ord_id, "state": "filled", "accFillSz": "3"}]}


class TestPositionTracker(unittest.IsolatedAsyncioTestCase):
    async def test_long_fsm_execution(self):
        # Initialize mock exchange
        exchange = MockExchange()
        
        # Create a tracker for BTC-USDT-SWAP, LONG position, size 10 contracts
        # Entry price: 60000, target_tp_ratio: 0.40% (0.0040 fraction)
        # Profile: initial_tp_trigger_pct = 0.40 (TP1 is at 40% of target = 0.16% PnL)
        # ATR: 100
        # ct_val: 0.01
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="long",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.40,  # 0.40%
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="long",
            esik1_fraction=0.40
        )
        
        # Verify initial calculations
        self.assertEqual(tracker.state, "INIT")
        # TP1 target = 60000 * (1 + 0.004 * 0.40) = 60000 * 1.0016 = 60096
        self.assertAlmostEqual(tracker.tp1_target, 60096.0)
        # TP2 target = 60000 * (1 + 0.004 * 0.40 + 0.001) = 60000 * 1.0026 = 60156
        self.assertAlmostEqual(tracker.tp2_target, 60156.0)

        # 1. Update price below TP1 target
        tracker.update_tick(60050.0, volume_ratio=1.0, ob_imbalance=0.0)
        self.assertEqual(tracker.state, "INIT")
        self.assertFalse(tracker.is_locked)

        # 2. Update price above TP1 target -> Should trigger TP1 exit task
        # Mock the execute_smart_exit method to avoid sleeping in tests
        original_exit = tracker.execute_smart_exit
        tracker.execute_smart_exit = AsyncMock()
        
        tracker.update_tick(60100.0, volume_ratio=1.0, ob_imbalance=0.0)
        
        # Must lock and spawn task
        self.assertTrue(tracker.is_locked)
        tracker.execute_smart_exit.assert_called_once()
        # Price passed should be current_price - 0.5 * atr = 60100 - 50 = 60050
        args, kwargs = tracker.execute_smart_exit.call_args
        self.assertAlmostEqual(kwargs.get("price"), 60050.0)
        self.assertEqual(kwargs.get("size_pct"), 0.30)
        self.assertEqual(kwargs.get("label"), "TP1")

        # 3. Simulate completion of TP1 exit
        # Reset tracker lock and transition state manually (since we mocked execute_smart_exit)
        tracker.is_locked = False
        tracker.state = "RISK_ZERO"
        # Remaining size would be 10 - 3 = 7 contracts
        tracker.size = 7.0

        # 4. Update price below TP2 (TP2 is 60156)
        tracker.update_tick(60120.0, volume_ratio=1.0, ob_imbalance=0.0)
        self.assertEqual(tracker.state, "RISK_ZERO")
        self.assertFalse(tracker.is_locked)

        # 5. Update price above TP2 -> Should transition to TRAILING state immediately
        tracker.execute_smart_exit = AsyncMock()
        # Bullish momentum: vol_ratio > 2.0 (e.g. 2.5), ob_imbalance > 0.15 (e.g. 0.30)
        tracker.update_tick(60180.0, volume_ratio=2.5, ob_imbalance=0.30)
        
        # Should transition to TRAILING and NOT lock
        self.assertEqual(tracker.state, "TRAILING")
        self.assertFalse(tracker.is_locked)
        tracker.execute_smart_exit.assert_not_called()
        self.assertEqual(tracker.highest_price, 60180.0)
        # Trailing stop: 60180 - (1.5 * 100) = 60030
        self.assertEqual(tracker.trailing_stop, 60030.0)

        # 6. Price goes higher -> Trailing stop moves up
        tracker.update_tick(60400.0, volume_ratio=1.0, ob_imbalance=0.0)
        self.assertEqual(tracker.highest_price, 60400.0)
        # Trailing stop: 60400 - (1.0 * 100) = 60300
        self.assertEqual(tracker.trailing_stop, 60300.0)
        self.assertEqual(tracker.state, "TRAILING")

        # 7. Price hits trailing stop -> Triggers Trailing Exit (Trailing stop is at 60300.0)
        tracker.update_tick(60290.0, volume_ratio=1.0, ob_imbalance=0.0)
        self.assertTrue(tracker.is_locked)
        tracker.execute_smart_exit.assert_called_once()
        args, kwargs = tracker.execute_smart_exit.call_args
        self.assertEqual(kwargs.get("size_pct"), 1.0)
        self.assertEqual(kwargs.get("label"), "TRAILING_EXIT")

    async def test_exchange_stop_loss_placement(self):
        # Initialize mock exchange
        exchange = MockExchange()
        
        # Create a tracker for BTC-USDT-SWAP, LONG position, size 10 contracts
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="long",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.40,
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="long",
            esik1_fraction=0.40
        )
        
        # 1. Simulate the FSM transitioning to RISK_ZERO (e.g. by running execute_smart_exit with TP1)
        await tracker.execute_smart_exit(size_pct=0.30, price=60100.0, label="TP1")
        await asyncio.sleep(0.01)
        
        self.assertEqual(tracker.state, "RISK_ZERO")
        # Check that we placed a Stop-Loss on exchange (not OCO)
        self.assertIsNotNone(tracker.algo_sl_id)
        self.assertIsNone(tracker.algo_tp_id)
        self.assertEqual(tracker.last_placed_sl_px, tracker.breakeven_px)
        
        # Verify MockExchange has the placed order
        placed_algo = [o for o in exchange.placed_orders if o.get("ord_type") == "conditional"]
        self.assertEqual(len(placed_algo), 1)
        self.assertAlmostEqual(float(placed_algo[0]["sl_trigger_px"]), tracker.breakeven_px)
        
        # 2. Update price to TP2 target with strong momentum to transition to TRAILING
        # Reset MockExchange collections
        exchange.placed_orders = []
        exchange.cancelled_orders = []
        
        # Bullish momentum: vol_ratio > 2.0 (e.g. 2.5), ob_imbalance > 0.15 (e.g. 0.30)
        # With ob_imbalance > 0.15, ob_multiplier is 1.5.
        # TP2 target is 60156.0.
        tracker.update_tick(60180.0, volume_ratio=2.5, ob_imbalance=0.30)
        await asyncio.sleep(0.01)
        
        self.assertEqual(tracker.state, "TRAILING")
        # Check that previous exchange SL was cancelled
        cancelled_sl = [c for c in exchange.cancelled_orders if c.get("inst_id") == "BTC-USDT-SWAP"]
        self.assertEqual(len(cancelled_sl), 1)
        
        # Check that new trailing stop was placed
        # Initial trailing stop: 60180 - (1.5 * 100) = 60030
        self.assertEqual(tracker.trailing_stop, 60030.0)
        self.assertEqual(tracker.last_placed_spread, 150.0)
        placed_algo = [o for o in exchange.placed_orders if o.get("ord_type") == "move_order_stop"]
        self.assertEqual(len(placed_algo), 1)
        self.assertAlmostEqual(float(placed_algo[0]["callback_spread"]), 150.0)
        self.assertAlmostEqual(float(placed_algo[0]["active_px"]), 60180.0)
        
        # 3. Price goes higher -> trailing stop moves up -> exchange trailing stop is updated if spread changes!
        # Initial trailing stop: 60110 (spread 150)
        # Price goes to 60400, ob_imbalance is 0.0 (neutral), so multiplier becomes 1.0 (spread 100).
        # Target spread becomes 100. Since abs(150 - 100) = 50 > 0.1 * 100 (10), it updates!
        exchange.placed_orders = []
        exchange.cancelled_orders = []
        
        tracker.last_ts_update_time = 0.0
        tracker.update_tick(60400.0, volume_ratio=1.0, ob_imbalance=0.0)
        await asyncio.sleep(0.01)
        self.assertEqual(tracker.trailing_stop, 60300.0)
        self.assertEqual(tracker.last_placed_spread, 100.0)
        
        # Verify old SL/TS cancelled and new placed
        cancelled_sl = [c for c in exchange.cancelled_orders if c.get("algo_id") == "mock_algo_sl_123"]
        self.assertEqual(len(cancelled_sl), 1)
        placed_algo = [o for o in exchange.placed_orders if o.get("ord_type") == "move_order_stop"]
        self.assertEqual(len(placed_algo), 1)
        self.assertAlmostEqual(float(placed_algo[0]["callback_spread"]), 100.0)

    async def test_short_fsm_execution_and_trailing(self):
        # Initialize mock exchange
        exchange = MockExchange()
        
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="short",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.40,  # 0.40%
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="short",
            esik1_fraction=0.40
        )
        
        # Verify initial calculations
        self.assertEqual(tracker.state, "INIT")
        # TP1 target = 60000 * (1 - 0.004 * 0.40) = 60000 * 0.9984 = 59904
        self.assertAlmostEqual(tracker.tp1_target, 59904.0)
        # TP2 target = 60000 * (1 - 0.004 * 0.40 - 0.001) = 60000 * 0.9974 = 59844
        self.assertAlmostEqual(tracker.tp2_target, 59844.0)

        # 1. Update price above TP1 target
        tracker.update_tick(60050.0, volume_ratio=1.0, ob_imbalance=0.0)
        self.assertEqual(tracker.state, "INIT")
        self.assertFalse(tracker.is_locked)

        # 2. Update price below TP1 target -> Should trigger TP1 exit task
        tracker.execute_smart_exit = AsyncMock()
        tracker.update_tick(59900.0, volume_ratio=1.0, ob_imbalance=0.0)
        
        # Must lock and spawn task
        self.assertTrue(tracker.is_locked)
        tracker.execute_smart_exit.assert_called_once()
        args, kwargs = tracker.execute_smart_exit.call_args
        self.assertAlmostEqual(kwargs.get("price"), 59850.0)
        self.assertEqual(kwargs.get("size_pct"), 0.30)
        self.assertEqual(kwargs.get("label"), "TP1")

        # 3. Simulate completion of TP1 exit
        tracker.is_locked = False
        tracker.state = "RISK_ZERO"
        tracker.size = 7.0

        # 4. Update price above TP2 (TP2 is 59844)
        tracker.update_tick(59880.0, volume_ratio=1.0, ob_imbalance=0.0)
        self.assertEqual(tracker.state, "RISK_ZERO")
        self.assertFalse(tracker.is_locked)

        # 5. Update price below TP2 -> Should transition to TRAILING state immediately
        tracker.update_tick(59800.0, volume_ratio=2.5, ob_imbalance=-0.30)
        await asyncio.sleep(0.01)
        
        self.assertEqual(tracker.state, "TRAILING")
        self.assertFalse(tracker.is_locked)
        self.assertEqual(tracker.lowest_price, 59800.0)
        # Trailing stop: 59800 + (1.5 * 100) = 59950
        self.assertEqual(tracker.trailing_stop, 59950.0)
        self.assertEqual(tracker.last_placed_spread, 150.0)
        
        # Check that trailing stop was placed on mock exchange
        placed_algo = [o for o in exchange.placed_orders if o.get("ord_type") == "move_order_stop"]
        self.assertEqual(len(placed_algo), 1)
        self.assertAlmostEqual(float(placed_algo[0]["callback_spread"]), 150.0)

        # 6. Price goes lower -> Trailing stop moves down
        tracker.update_tick(59600.0, volume_ratio=1.0, ob_imbalance=0.0)
        self.assertEqual(tracker.lowest_price, 59600.0)
        self.assertEqual(tracker.trailing_stop, 59700.0)
        
        # Spread changed from 150 to 100.
        # Reset last_ts_update_time to bypass 15s cooldown
        tracker.last_ts_update_time = 0.0
        exchange.placed_orders = []
        exchange.cancelled_orders = []
        
        tracker.update_tick(59500.0, volume_ratio=1.0, ob_imbalance=0.0)
        await asyncio.sleep(0.01)
        self.assertEqual(tracker.lowest_price, 59500.0)
        self.assertEqual(tracker.trailing_stop, 59600.0)
        self.assertEqual(tracker.last_placed_spread, 100.0)
        
        placed_algo = [o for o in exchange.placed_orders if o.get("ord_type") == "move_order_stop"]
        self.assertEqual(len(placed_algo), 1)
        self.assertAlmostEqual(float(placed_algo[0]["callback_spread"]), 100.0)

    async def test_small_tp_ratio_conversion(self):
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="long",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.035,  # 0.035%
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="long",
            esik1_fraction=0.50
        )
        # 0.035% target_tp_ratio should be divided by 100 -> 0.00035 fraction
        self.assertAlmostEqual(tracker.target_tp_fraction, 0.00035)
        # tp1_target = 60000 * (1 + 0.00035 * 0.50) = 60010.5
        self.assertAlmostEqual(tracker.tp1_target, 60010.5)
        # tp2_target = 60000 * (1 + 0.00035 * 0.50 + 0.0010) = 60070.5
        self.assertAlmostEqual(tracker.tp2_target, 60070.5)
        
        # Test updating targets dynamically with small ratio
        tracker.update_targets(0.024, new_esik1_fraction=0.50)
        # 0.024% should be divided by 100 -> 0.00024 fraction
        self.assertAlmostEqual(tracker.target_tp_fraction, 0.00024)
        # tp1_target = 60000 * (1 + 0.00024 * 0.50) = 60007.2
        self.assertAlmostEqual(tracker.tp1_target, 60007.2)

    # ============ SMART BREAKEVEN TESTS ============

    async def test_smart_breakeven_long_above_015(self):
        """LONG: Eşik 1 TP > %0.15 → breakeven = entry + %0.09"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="long",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.40,  # 0.40%
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="long",
            esik1_fraction=0.50  # Eşik1 TP = 0.40 * 0.50 = 0.20% > 0.15%
        )
        # Eşik1 TP oranı = 0.004 * 0.50 = 0.0020 (= %0.20) > %0.15
        # breakeven = 60000 * (1 + 0.0009) = 60054
        expected_be = 60000.0 * (1.0 + 0.0009)
        self.assertAlmostEqual(tracker.breakeven_px, expected_be)
        self.assertGreater(tracker.breakeven_px, tracker.entry_price)

    async def test_smart_breakeven_long_below_015(self):
        """LONG: Eşik 1 TP ≤ %0.15 → breakeven = exact entry"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="long",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.20,  # 0.20%
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="long",
            esik1_fraction=0.50  # Eşik1 TP = 0.20 * 0.50 = 0.10% < 0.15%
        )
        # Eşik1 TP oranı = 0.002 * 0.50 = 0.0010 (= %0.10) ≤ %0.15
        # breakeven = exact entry
        self.assertAlmostEqual(tracker.breakeven_px, 60000.0)

    async def test_smart_breakeven_short_above_015(self):
        """SHORT: Eşik 1 TP > %0.15 → breakeven = entry - %0.09"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="short",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.40,  # 0.40%
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="short",
            esik1_fraction=0.50  # Eşik1 TP = 0.40 * 0.50 = 0.20% > 0.15%
        )
        # breakeven = 60000 * (1 - 0.0009) = 59946
        expected_be = 60000.0 * (1.0 - 0.0009)
        self.assertAlmostEqual(tracker.breakeven_px, expected_be)
        self.assertLess(tracker.breakeven_px, tracker.entry_price)

    async def test_smart_breakeven_short_below_015(self):
        """SHORT: Eşik 1 TP ≤ %0.15 → breakeven = exact entry"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="short",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.20,  # 0.20%
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="short",
            esik1_fraction=0.50  # Eşik1 TP = 0.20 * 0.50 = 0.10% ≤ 0.15%
        )
        self.assertAlmostEqual(tracker.breakeven_px, 60000.0)

    async def test_smart_breakeven_exact_boundary(self):
        """Eşik 1 TP = tam %0.15 → breakeven = exact entry (sınır dahil değil, > 0.15 olmalı)"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="long",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.30,  # 0.30%
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="long",
            esik1_fraction=0.50  # Eşik1 TP = 0.30 * 0.50 = 0.15% = sınır
        )
        # 0.003 * 0.50 = 0.0015 = %0.15 → NOT > 0.15, so exact entry
        self.assertAlmostEqual(tracker.breakeven_px, 60000.0)

    async def test_smart_breakeven_update_targets(self):
        """update_targets ile TP oranı değişince breakeven da güncellenir"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="long",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.20,  # 0.20% → Eşik1 = 0.10% < 0.15%
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="long",
            esik1_fraction=0.50
        )
        # Initially exact entry
        self.assertAlmostEqual(tracker.breakeven_px, 60000.0)
        
        # Update to higher ratio: 0.40% → Eşik1 = 0.20% > 0.15%
        tracker.update_targets(0.40, new_esik1_fraction=0.50)
        expected_be = 60000.0 * (1.0 + 0.0009)
        self.assertAlmostEqual(tracker.breakeven_px, expected_be)

    async def test_smart_breakeven_sl_placement_on_exchange(self):
        """TP1 sonrası borsaya kurulan SL, smart breakeven fiyatını kullanır"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="SOL-USDT-SWAP",
            side="long",
            size=100.0,
            entry_price=80.0,
            target_tp_ratio=0.40,  # 0.40% → Eşik1 = 0.20% > 0.15%
            atr=0.5,
            ct_val=1.0,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="long",
            esik1_fraction=0.50
        )
        
        expected_be = 80.0 * (1.0 + 0.0009)  # 80.072
        self.assertAlmostEqual(tracker.breakeven_px, expected_be)
        
        # Execute TP1
        await tracker.execute_smart_exit(size_pct=0.30, price=80.2, label="TP1")
        await asyncio.sleep(0.01)
        
        self.assertEqual(tracker.state, "RISK_ZERO")
        # SL on exchange should be at smart breakeven, NOT exact entry
        placed_sl = [o for o in exchange.placed_orders if o.get("ord_type") == "conditional"]
        self.assertEqual(len(placed_sl), 1)
        self.assertAlmostEqual(float(placed_sl[0]["sl_trigger_px"]), expected_be, places=1)

    # ============ TRAILING STOP FULL FLOW TEST ============

    async def test_trailing_stop_full_flow_long(self):
        """Eşik2 sonrası TRAILING → exchange'e move_order_stop yerleştirilir → güncellenir → tetiklenir"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="SOL-USDT-SWAP",
            side="long",
            size=70.0,
            entry_price=80.0,
            target_tp_ratio=0.40,
            atr=0.5,
            ct_val=1.0,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="long",
            esik1_fraction=0.50
        )
        
        # Simulate already at RISK_ZERO after TP1
        tracker.state = "RISK_ZERO"
        tracker.algo_sl_id = "existing_sl_123"
        
        # Trigger Eşik 2: TP2 ≈ 80 * (1 + 0.002 + 0.001) = 80.24
        # Price goes above TP2 with bullish momentum
        tracker.update_tick(80.30, volume_ratio=2.5, ob_imbalance=0.30)
        await asyncio.sleep(0.01)
        
        # Should be in TRAILING
        self.assertEqual(tracker.state, "TRAILING")
        self.assertFalse(tracker.is_locked)
        
        # Old SL should be cancelled
        cancelled = [c for c in exchange.cancelled_orders if c.get("algo_id") == "existing_sl_123"]
        self.assertEqual(len(cancelled), 1)
        
        # New native trailing stop should be placed
        placed_ts = [o for o in exchange.placed_orders if o.get("ord_type") == "move_order_stop"]
        self.assertEqual(len(placed_ts), 1)
        # OB imbalance > 0.15 → multiplier = 1.5 → spread = 1.5 * 0.5 = 0.75
        self.assertAlmostEqual(float(placed_ts[0]["callback_spread"]), 0.75, places=1)
        self.assertAlmostEqual(float(placed_ts[0]["active_px"]), 80.30, places=1)

    async def test_trailing_stop_full_flow_short(self):
        """SHORT: Eşik2 sonrası TRAILING → move_order_stop → güncellenir → tetiklenir"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="SOL-USDT-SWAP",
            side="short",
            size=70.0,
            entry_price=80.0,
            target_tp_ratio=0.40,
            atr=0.5,
            ct_val=1.0,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="short",
            esik1_fraction=0.50
        )
        
        # Simulate already at RISK_ZERO after TP1
        tracker.state = "RISK_ZERO"
        tracker.algo_sl_id = "existing_sl_456"
        
        # Trigger Eşik 2: TP2 ≈ 80 * (1 - 0.002 - 0.001) = 79.76
        # Price goes below TP2 with bearish momentum
        tracker.update_tick(79.70, volume_ratio=2.5, ob_imbalance=-0.30)
        await asyncio.sleep(0.01)
        
        # Should be in TRAILING
        self.assertEqual(tracker.state, "TRAILING")
        self.assertFalse(tracker.is_locked)
        
        # Old SL should be cancelled
        cancelled = [c for c in exchange.cancelled_orders if c.get("algo_id") == "existing_sl_456"]
        self.assertEqual(len(cancelled), 1)
        
        # New trailing stop placed
        placed_ts = [o for o in exchange.placed_orders if o.get("ord_type") == "move_order_stop"]
        self.assertEqual(len(placed_ts), 1)
        # OB imbalance < -0.15 → multiplier = 1.5 → spread = 1.5 * 0.5 = 0.75
        self.assertAlmostEqual(float(placed_ts[0]["callback_spread"]), 0.75, places=1)

    # ============ NO OCO/TP FUNCTIONS EXIST TEST ============

    async def test_no_oco_or_tp_functions(self):
        """set_exchange_oco_order ve set_exchange_take_profit artık mevcut değil"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP",
            side="long",
            size=10.0,
            entry_price=60000.0,
            target_tp_ratio=0.40,
            atr=100.0,
            ct_val=0.01,
            exchange_interface=exchange,
            mgn_mode="isolated",
            pos_side="long",
            esik1_fraction=0.50
        )
        self.assertFalse(hasattr(tracker, "set_exchange_oco_order"))
        self.assertFalse(hasattr(tracker, "set_exchange_take_profit"))
        self.assertFalse(hasattr(tracker, "cancel_exchange_take_profit"))


if __name__ == "__main__":
    unittest.main()
