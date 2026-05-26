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

    async def place_algo_order(self, inst_id, side, ord_type, sz, pos_side=None, mgn_mode=None, tp_trigger_px=None, tp_ord_px=None, sl_trigger_px=None, sl_ord_px=None):
        self.placed_orders.append({
            "inst_id": inst_id, "side": side, "ord_type": ord_type, "sz": sz,
            "pos_side": pos_side, "mgn_mode": mgn_mode, "tp_trigger_px": tp_trigger_px, "sl_trigger_px": sl_trigger_px
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
        # TP2 target = 60000 * (1 + 0.004) = 60240
        self.assertAlmostEqual(tracker.tp2_target, 60240.0)

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

        # 4. Update price below TP2
        tracker.update_tick(60200.0, volume_ratio=1.0, ob_imbalance=0.0)
        self.assertEqual(tracker.state, "RISK_ZERO")
        self.assertFalse(tracker.is_locked)

        # 5. Update price above TP2 with normal momentum -> Should trigger immediate 100% exit
        tracker.execute_smart_exit = AsyncMock()
        tracker.update_tick(60250.0, volume_ratio=1.0, ob_imbalance=0.0)
        
        self.assertTrue(tracker.is_locked)
        tracker.execute_smart_exit.assert_called_once()
        args, kwargs = tracker.execute_smart_exit.call_args
        self.assertEqual(kwargs.get("size_pct"), 1.0)
        self.assertEqual(kwargs.get("label"), "TP2_EXIT")

        # 6. Reset lock, try with strong momentum -> Should transition to TRAILING state
        tracker.is_locked = False
        tracker.state = "RISK_ZERO"
        tracker.execute_smart_exit = AsyncMock()
        
        # Bullish momentum: vol_ratio > 2.0 (e.g. 2.5), ob_imbalance > 0.15 (e.g. 0.30)
        tracker.update_tick(60260.0, volume_ratio=2.5, ob_imbalance=0.30)
        
        # Should transition to TRAILING and NOT lock
        self.assertEqual(tracker.state, "TRAILING")
        self.assertFalse(tracker.is_locked)
        tracker.execute_smart_exit.assert_not_called()
        self.assertEqual(tracker.highest_price, 60260.0)
        # Trailing stop: 60260 - (1.5 * 100) = 60110
        self.assertEqual(tracker.trailing_stop, 60110.0)

        # 7. Price goes higher -> Trailing stop moves up
        tracker.update_tick(60400.0, volume_ratio=1.0, ob_imbalance=0.0)
        self.assertEqual(tracker.highest_price, 60400.0)
        # Trailing stop: 60400 - (1.0 * 100) = 60300
        self.assertEqual(tracker.trailing_stop, 60300.0)
        self.assertEqual(tracker.state, "TRAILING")

        # 8. Price hits trailing stop -> Triggers Trailing Exit
        tracker.update_tick(60240.0, volume_ratio=1.0, ob_imbalance=0.0)
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
        # Check that we placed both Stop-Loss and Take-Profit on exchange
        self.assertIsNotNone(tracker.algo_sl_id)
        self.assertIsNotNone(tracker.algo_tp_id)
        self.assertEqual(tracker.last_placed_sl_px, tracker.breakeven_px)
        self.assertEqual(tracker.last_placed_tp_px, tracker.tp2_target)
        
        # Verify MockExchange has the placed orders
        placed_algo = [o for o in exchange.placed_orders if o.get("ord_type") == "conditional"]
        self.assertEqual(len(placed_algo), 2)
        sl_order = [o for o in placed_algo if o.get("sl_trigger_px") is not None][0]
        tp_order = [o for o in placed_algo if o.get("tp_trigger_px") is not None][0]
        self.assertAlmostEqual(float(sl_order["sl_trigger_px"]), tracker.breakeven_px)
        self.assertAlmostEqual(float(tp_order["tp_trigger_px"]), tracker.tp2_target)
        
        # 2. Update price to TP2 target with strong momentum to transition to TRAILING
        # Reset MockExchange collections
        exchange.placed_orders = []
        exchange.cancelled_orders = []
        
        # Bullish momentum: vol_ratio > 2.0 (e.g. 2.5), ob_imbalance > 0.15 (e.g. 0.30)
        # With ob_imbalance > 0.15, ob_multiplier is 1.5.
        tracker.update_tick(60260.0, volume_ratio=2.5, ob_imbalance=0.30)
        await asyncio.sleep(0.01)
        
        self.assertEqual(tracker.state, "TRAILING")
        # Check that previous exchange SL and TP were cancelled
        cancelled_sl = [c for c in exchange.cancelled_orders if c.get("inst_id") == "BTC-USDT-SWAP"]
        self.assertEqual(len(cancelled_sl), 2)
        
        # Check that new trailing stop was placed
        # Initial trailing stop: 60260 - (1.5 * 100) = 60110
        self.assertEqual(tracker.trailing_stop, 60110.0)
        self.assertEqual(tracker.last_placed_sl_px, tracker.trailing_stop)
        placed_algo = [o for o in exchange.placed_orders if o.get("ord_type") == "conditional"]
        self.assertEqual(len(placed_algo), 1)
        self.assertAlmostEqual(float(placed_algo[0]["sl_trigger_px"]), tracker.trailing_stop)
        
        # 3. Price goes higher -> trailing stop moves up -> exchange stop loss is updated!
        # Initial trailing stop: 60110
        # Price goes to 60400, ob_imbalance is 0.0 (neutral), so multiplier becomes 1.0.
        # Calculated candidate_stop = 60400 - (1.0 * 100) = 60300.
        # Since 60300 > 60110, it moves up to 60300.
        # Difference = 60300 - 60110 = 190. Since 190 >= 0.1 * 100 (10), it updates!
        exchange.placed_orders = []
        exchange.cancelled_orders = []
        
        tracker.update_tick(60400.0, volume_ratio=1.0, ob_imbalance=0.0)
        await asyncio.sleep(0.01)
        self.assertEqual(tracker.trailing_stop, 60300.0)
        
        # Verify old SL cancelled and new placed
        cancelled_sl = [c for c in exchange.cancelled_orders if c.get("algo_id") == "mock_algo_sl_123"]
        self.assertEqual(len(cancelled_sl), 1)
        placed_algo = [o for o in exchange.placed_orders if o.get("ord_type") == "conditional"]
        self.assertEqual(len(placed_algo), 1)
        self.assertAlmostEqual(float(placed_algo[0]["sl_trigger_px"]), 60300.0)




if __name__ == "__main__":
    unittest.main()

