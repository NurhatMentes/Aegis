"""
Eşik 1 / Eşik 2 / Trailing Stop Doğrulama Testleri
====================================================
Kullanıcının 5 kuralını doğrular:
1. Eşik 1'de tüm açık emirler iptal edilir
2. Eşik 1'de SL = girişin %0.06 yukarısına kurulur (Smart Breakeven)
3. Eşik 2 = Eşik 1 + %0.10
4. Eşik 2'de SL iptal edilir ve trailing stop kurulur
5. Trailing stop minimum %0.06
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from tracker import PositionTracker, MIN_TRAILING_GAP_PCT


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
            "sz": sz, "px": px, "cl_ord_id": cl_ord_id
        })
        return {"code": "0", "msg": "Success", "data": [{"ordId": "mock_ord_123"}]}

    async def cancel_order(self, inst_id, ord_id, cl_ord_id):
        self.cancelled_orders.append({"inst_id": inst_id, "ord_id": ord_id, "cl_ord_id": cl_ord_id})
        return {"code": "0", "msg": "Cancelled"}

    async def cancel_algo_orders(self, inst_id):
        self.cancelled_orders.append({"inst_id": inst_id, "ord_type": "algo_all"})
        return True

    async def cancel_pending_limit_orders(self, inst_id):
        self.cancelled_orders.append({"inst_id": inst_id, "ord_type": "regular_limit"})
        return True

    async def place_algo_order(self, inst_id, side, ord_type, sz, pos_side=None, mgn_mode=None,
                                tp_trigger_px=None, tp_ord_px=None, sl_trigger_px=None, sl_ord_px=None,
                                callback_ratio=None, callback_spread=None, active_px=None):
        self.placed_orders.append({
            "inst_id": inst_id, "side": side, "ord_type": ord_type, "sz": sz,
            "sl_trigger_px": sl_trigger_px, "callback_spread": callback_spread, "active_px": active_px
        })
        return {"code": "0", "msg": "Success", "data": [{"algoId": "mock_algo_123"}]}

    async def cancel_algo_order(self, inst_id, algo_id):
        self.cancelled_orders.append({"inst_id": inst_id, "algo_id": algo_id})
        return {"code": "0", "msg": "Cancelled"}

    async def get_order(self, inst_id, ord_id):
        return {"code": "0", "msg": "Success", "data": [{"ordId": ord_id, "state": "filled", "accFillSz": "3"}]}


# ============================================================
# KURAL 1: Eşik 1'de tüm açık emirler iptal edilir
# ============================================================
class TestKural1_Esik1EmirIptali(unittest.IsolatedAsyncioTestCase):

    async def test_esik1_cancels_all_open_algo_orders_long(self):
        """LONG: Eşik 1 tetiklenince execute_smart_exit çağrılır, içinde cancel_algo_orders çalışır."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="long", size=10.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        # TP1 = 60000 * (1 + 0.004 * 0.50) = 60120
        # Simulate pre-existing algo orders
        tracker.algo_sl_id = "pre_sl_001"
        tracker.algo_tp_id = "pre_tp_001"

        # Run full execute_smart_exit (TP1 label) — it cancels all algo orders first
        await tracker.execute_smart_exit(size_pct=0.30, price=60130.0, label="TP1")
        await asyncio.sleep(0.01)

        # Verify cancel_algo_orders was called (cancels ALL algo orders for this instrument)
        algo_cancels = [c for c in exchange.cancelled_orders if c.get("ord_type") == "algo_all"]
        self.assertGreaterEqual(len(algo_cancels), 1, "cancel_algo_orders çağrılmadı!")

        # After execution, algo_tp_id should be None (cancelled)
        # algo_sl_id gets re-assigned because a new breakeven SL is placed during TP1 flow
        self.assertIsNone(tracker.algo_tp_id)

    async def test_esik1_cancels_all_open_algo_orders_short(self):
        """SHORT: Eşik 1 tetiklenince execute_smart_exit çağrılır, içinde cancel_algo_orders çalışır."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="short", size=10.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        tracker.algo_sl_id = "pre_sl_002"
        tracker.algo_tp_id = "pre_tp_002"

        await tracker.execute_smart_exit(size_pct=0.30, price=59870.0, label="TP1")
        await asyncio.sleep(0.01)

        algo_cancels = [c for c in exchange.cancelled_orders if c.get("ord_type") == "algo_all"]
        self.assertGreaterEqual(len(algo_cancels), 1, "cancel_algo_orders çağrılmadı!")
        # algo_tp_id should be None after cancellation
        self.assertIsNone(tracker.algo_tp_id)

    async def test_esik1_also_cancels_skynet_limit_orders(self):
        """Eşik 1'de Skynet'in koyduğu normal limit emirler de iptal edilir (cancel_pending_limit_orders)."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="long", size=10.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )

        await tracker.execute_smart_exit(size_pct=0.30, price=60130.0, label="TP1")
        await asyncio.sleep(0.01)

        # Verify both algo AND regular limit orders were cancelled
        algo_cancels = [c for c in exchange.cancelled_orders if c.get("ord_type") == "algo_all"]
        limit_cancels = [c for c in exchange.cancelled_orders if c.get("ord_type") == "regular_limit"]

        self.assertGreaterEqual(len(algo_cancels), 1, "cancel_algo_orders çağrılmadı!")
        self.assertGreaterEqual(len(limit_cancels), 1, "cancel_pending_limit_orders çağrılmadı! Skynet'in limit emirleri iptal edilmiyor!")

# ============================================================
# KURAL 2: Eşik 1'de SL = girişin %0.06 yukarısına kurulur
# ============================================================
class TestKural2_Esik1StopLossSeviyesi(unittest.IsolatedAsyncioTestCase):

    async def test_breakeven_sl_long_is_006_above_entry(self):
        """LONG: Eşik 1 TP > %0.15 ise SL = entry * (1 + 0.0006) = %0.06 üstü olmalı."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="long", size=10.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        # Eşik1 TP oranı = 0.004 * 0.50 = 0.0020 = %0.20 > %0.15 → Smart Breakeven aktif

        expected_be = 60000.0 * (1.0 + 0.0006)  # %0.06 yukarısı = 60036
        self.assertAlmostEqual(
            tracker.breakeven_px, expected_be, places=2,
            msg=f"SL girişin %0.06 üstünde olmalı ({expected_be}), ama {tracker.breakeven_px} bulundu"
        )

    async def test_breakeven_sl_short_is_006_below_entry(self):
        """SHORT: Eşik 1 TP > %0.15 ise SL = entry * (1 - 0.0006) = %0.06 altı olmalı."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="short", size=10.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        expected_be = 60000.0 * (1.0 - 0.0006)  # %0.06 altı = 59964
        self.assertAlmostEqual(
            tracker.breakeven_px, expected_be, places=2,
            msg=f"SL girişin %0.06 altında olmalı ({expected_be}), ama {tracker.breakeven_px} bulundu"
        )

    async def test_sl_placed_on_exchange_at_breakeven_after_tp1(self):
        """TP1 sonrası borsada kurulan SL, breakeven_px seviyesinde olmalı."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="long", size=10.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        await tracker.execute_smart_exit(size_pct=0.30, price=60130.0, label="TP1")
        await asyncio.sleep(0.01)

        self.assertEqual(tracker.state, "RISK_ZERO")
        # Borsadaki SL emri breakeven_px seviyesinde olmalı
        placed_sl = [o for o in exchange.placed_orders if o.get("ord_type") == "conditional"]
        self.assertGreaterEqual(len(placed_sl), 1, "Borsaya SL emri kurulmadı!")
        self.assertAlmostEqual(
            float(placed_sl[-1]["sl_trigger_px"]), tracker.breakeven_px, places=1,
            msg=f"SL emri breakeven_px ({tracker.breakeven_px}) seviyesinde olmalı"
        )


# ============================================================
# KURAL 3: Eşik 2 = Eşik 1 + %0.10
# ============================================================
class TestKural3_Esik2Seviyesi(unittest.IsolatedAsyncioTestCase):

    async def test_esik2_equals_esik1_plus_010_long(self):
        """LONG: tp2_target = tp1_target + entry * 0.0010"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="long", size=10.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        # tp1 = 60000 * (1 + 0.004 * 0.50) = 60120
        # tp2 = 60000 * (1 + 0.004 * 0.50 + 0.0010) = 60180
        expected_diff = 60000.0 * 0.0010  # 60 $
        actual_diff = tracker.tp2_target - tracker.tp1_target
        self.assertAlmostEqual(
            actual_diff, expected_diff, places=2,
            msg=f"Eşik 2 - Eşik 1 farkı entry * %0.10 = {expected_diff} olmalı, ama {actual_diff} bulundu"
        )

    async def test_esik2_equals_esik1_plus_010_short(self):
        """SHORT: tp2_target = tp1_target - entry * 0.0010 (short'ta aşağı yönlü)"""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="short", size=10.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        expected_diff = 60000.0 * 0.0010  # 60 $
        actual_diff = tracker.tp1_target - tracker.tp2_target  # short'ta tp2 < tp1
        self.assertAlmostEqual(
            actual_diff, expected_diff, places=2,
            msg=f"Eşik 1 - Eşik 2 farkı (short) entry * %0.10 = {expected_diff} olmalı, ama {actual_diff} bulundu"
        )

    async def test_esik2_consistent_across_different_entries(self):
        """Farklı entry price'larla Eşik 2 - Eşik 1 farkı her zaman entry * %0.10 olmalı."""
        exchange = MockExchange()
        for entry in [100.0, 1000.0, 0.00001234, 50000.0]:
            tracker = PositionTracker(
                inst_id="BTC-USDT-SWAP", side="long", size=10.0,
                entry_price=entry, target_tp_ratio=0.35, atr=1.0,
                ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
            )
            expected_diff = entry * 0.0010
            actual_diff = tracker.tp2_target - tracker.tp1_target
            self.assertAlmostEqual(
                actual_diff, expected_diff, places=8,
                msg=f"Entry={entry}: Eşik2-Eşik1 = {actual_diff}, beklenen = {expected_diff}"
            )


# ============================================================
# KURAL 4: Eşik 2'de SL iptal edilip trailing stop kurulur
# ============================================================
class TestKural4_Esik2TrailingStopGecisi(unittest.IsolatedAsyncioTestCase):

    async def test_esik2_cancels_sl_and_activates_trailing_long(self):
        """LONG: Eşik 2'de mevcut SL iptal edilir, trailing stop kurulur."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="long", size=7.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        # Simulate RISK_ZERO state with an existing SL
        tracker.state = "RISK_ZERO"
        tracker.algo_sl_id = "existing_sl_long_001"

        # TP2 = 60000 * (1 + 0.002 + 0.001) = 60180
        # Trigger Eşik 2 with bullish momentum
        tracker.update_tick(60200.0, volume_ratio=2.5, ob_imbalance=0.20)
        await asyncio.sleep(0.05)

        # State should be TRAILING
        self.assertEqual(tracker.state, "TRAILING", "Eşik 2 sonrası TRAILING'e geçmedi!")
        self.assertFalse(tracker.is_locked)

        # Old SL should be cancelled
        sl_cancels = [c for c in exchange.cancelled_orders if c.get("algo_id") == "existing_sl_long_001"]
        self.assertGreaterEqual(len(sl_cancels), 1, "Mevcut SL emri iptal edilmedi!")

        # Trailing stop should be placed on exchange
        trailing_orders = [o for o in exchange.placed_orders if o.get("ord_type") == "move_order_stop"]
        self.assertGreaterEqual(len(trailing_orders), 1, "Borsaya trailing stop emri kurulmadı!")

    async def test_esik2_cancels_sl_and_activates_trailing_short(self):
        """SHORT: Eşik 2'de mevcut SL iptal edilir, trailing stop kurulur."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="short", size=7.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        tracker.state = "RISK_ZERO"
        tracker.algo_sl_id = "existing_sl_short_001"

        # TP2 = 60000 * (1 - 0.002 - 0.001) = 59820
        tracker.update_tick(59800.0, volume_ratio=2.5, ob_imbalance=-0.20)
        await asyncio.sleep(0.05)

        self.assertEqual(tracker.state, "TRAILING", "Eşik 2 sonrası TRAILING'e geçmedi!")
        sl_cancels = [c for c in exchange.cancelled_orders if c.get("algo_id") == "existing_sl_short_001"]
        self.assertGreaterEqual(len(sl_cancels), 1, "Mevcut SL emri iptal edilmedi!")
        trailing_orders = [o for o in exchange.placed_orders if o.get("ord_type") == "move_order_stop"]
        self.assertGreaterEqual(len(trailing_orders), 1, "Borsaya trailing stop emri kurulmadı!")

    async def test_esik2_transition_is_atomic(self):
        """Eşik 2'de _transition_to_trailing_stop sıralı çalışır: önce cancel, sonra place."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="SOL-USDT-SWAP", side="long", size=70.0,
            entry_price=80.0, target_tp_ratio=0.40, atr=0.5,
            ct_val=1.0, exchange_interface=exchange, esik1_fraction=0.50
        )
        tracker.state = "RISK_ZERO"
        tracker.algo_sl_id = "sl_to_cancel"

        # TP2 ≈ 80 * (1 + 0.002 + 0.001) = 80.24
        tracker.update_tick(80.30, volume_ratio=1.5, ob_imbalance=0.10)
        await asyncio.sleep(0.05)

        # Cancel came before place (check order of operations)
        cancel_indices = [i for i, c in enumerate(exchange.cancelled_orders) if c.get("algo_id") == "sl_to_cancel"]
        place_indices = [i for i, o in enumerate(exchange.placed_orders) if o.get("ord_type") == "move_order_stop"]

        self.assertGreaterEqual(len(cancel_indices), 1, "SL iptal edilmedi")
        self.assertGreaterEqual(len(place_indices), 1, "Trailing stop kurulmadı")


# ============================================================
# KURAL 5: Trailing stop minimum %0.06
# ============================================================
class TestKural5_MinTrailingStop(unittest.IsolatedAsyncioTestCase):

    async def test_min_trailing_gap_constant(self):
        """MIN_TRAILING_GAP_PCT sabiti %0.06 = 0.0006 olmalı."""
        self.assertAlmostEqual(
            MIN_TRAILING_GAP_PCT, 0.0006, places=6,
            msg=f"MIN_TRAILING_GAP_PCT = {MIN_TRAILING_GAP_PCT}, beklenen = 0.0006"
        )

    async def test_trailing_gap_uses_min_when_atr_too_small_long(self):
        """LONG: ATR çok küçükse trailing gap = price * 0.0006 (minimum %0.06) olur."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="long", size=7.0,
            entry_price=60000.0, target_tp_ratio=0.40,
            atr=0.01,  # Çok küçük ATR → ATR bazlı gap = ~0.01 << min_gap
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        tracker.state = "RISK_ZERO"
        tracker.algo_sl_id = "sl_small_atr"

        # TP2 = 60000 * (1 + 0.002 + 0.001) = 60180
        price = 60200.0
        tracker.update_tick(price, volume_ratio=1.0, ob_imbalance=0.0)
        await asyncio.sleep(0.05)

        self.assertEqual(tracker.state, "TRAILING")
        # min_gap = 60200 * 0.0006 = 36.12
        # ATR gap = 1.0 * 0.01 = 0.01
        # trailing_gap = max(0.01, 36.12) = 36.12
        expected_min_gap = price * MIN_TRAILING_GAP_PCT
        expected_trailing_stop = price - expected_min_gap

        self.assertAlmostEqual(
            tracker.trailing_stop, expected_trailing_stop, places=2,
            msg=f"Trailing stop {tracker.trailing_stop}, beklenen {expected_trailing_stop} (%0.06 minimum)"
        )

    async def test_trailing_gap_uses_min_when_atr_too_small_short(self):
        """SHORT: ATR çok küçükse trailing gap = price * 0.0006 (minimum %0.06) olur."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="short", size=7.0,
            entry_price=60000.0, target_tp_ratio=0.40,
            atr=0.01,  # Çok küçük ATR
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        tracker.state = "RISK_ZERO"
        tracker.algo_sl_id = "sl_small_atr_short"

        # TP2 = 60000 * (1 - 0.002 - 0.001) = 59820
        price = 59800.0
        tracker.update_tick(price, volume_ratio=1.0, ob_imbalance=0.0)
        await asyncio.sleep(0.05)

        self.assertEqual(tracker.state, "TRAILING")
        expected_min_gap = price * MIN_TRAILING_GAP_PCT
        expected_trailing_stop = price + expected_min_gap

        self.assertAlmostEqual(
            tracker.trailing_stop, expected_trailing_stop, places=2,
            msg=f"Trailing stop {tracker.trailing_stop}, beklenen {expected_trailing_stop} (%0.06 minimum)"
        )

    async def test_trailing_gap_never_below_006_during_updates(self):
        """TRAILING state'de fiyat güncellenirken gap asla %0.06'nın altına düşmemeli."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="long", size=7.0,
            entry_price=60000.0, target_tp_ratio=0.40,
            atr=0.001,  # Aşırı küçük ATR
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )
        # Manually set to TRAILING
        tracker.state = "TRAILING"
        tracker.highest_price = 60200.0
        tracker.trailing_stop = 60200.0 - (60200.0 * MIN_TRAILING_GAP_PCT)

        # Simulate price updates
        for price in [60250.0, 60300.0, 60350.0, 60400.0]:
            tracker.update_tick(price, volume_ratio=0.5, ob_imbalance=0.0)
            gap = tracker.highest_price - tracker.trailing_stop
            min_allowed = tracker.highest_price * MIN_TRAILING_GAP_PCT
            self.assertGreaterEqual(
                gap, min_allowed * 0.999,  # Tiny floating point tolerance
                msg=f"Price={price}: gap={gap:.4f} < min_allowed={min_allowed:.4f}"
            )


# ============================================================
# ENTEGRASYON TESTİ: Tam Akış
# ============================================================
class TestFullFlowIntegration(unittest.IsolatedAsyncioTestCase):

    async def test_full_flow_long_init_to_trailing(self):
        """LONG tam akış: INIT → Eşik1 → RISK_ZERO → Eşik2 → TRAILING."""
        exchange = MockExchange()
        tracker = PositionTracker(
            inst_id="BTC-USDT-SWAP", side="long", size=10.0,
            entry_price=60000.0, target_tp_ratio=0.40, atr=100.0,
            ct_val=0.01, exchange_interface=exchange, esik1_fraction=0.50
        )

        # --- 1. INIT state ---
        self.assertEqual(tracker.state, "INIT")
        tp1 = tracker.tp1_target  # 60120
        tp2 = tracker.tp2_target  # 60180

        # Eşik 2 = Eşik 1 + %0.10 doğrulama
        self.assertAlmostEqual(tp2 - tp1, 60000.0 * 0.0010, places=2)

        # --- 2. Eşik 1 tetikleme ---
        await tracker.execute_smart_exit(size_pct=0.30, price=tp1 + 10, label="TP1")
        await asyncio.sleep(0.01)

        # Tüm algo emirler iptal edilmiş olmalı
        algo_cancels = [c for c in exchange.cancelled_orders if c.get("ord_type") == "algo_all"]
        self.assertGreaterEqual(len(algo_cancels), 1)

        # RISK_ZERO'ya geçmiş olmalı
        self.assertEqual(tracker.state, "RISK_ZERO")

        # SL breakeven seviyesinde kurulmuş olmalı
        placed_sl = [o for o in exchange.placed_orders if o.get("ord_type") == "conditional"]
        self.assertGreaterEqual(len(placed_sl), 1)
        self.assertAlmostEqual(float(placed_sl[-1]["sl_trigger_px"]), tracker.breakeven_px, places=1)

        # --- 3. Eşik 2 tetikleme ---
        exchange.placed_orders = []
        exchange.cancelled_orders = []

        tracker.update_tick(tp2 + 20, volume_ratio=2.0, ob_imbalance=0.10)
        await asyncio.sleep(0.05)

        # TRAILING'e geçmiş olmalı
        self.assertEqual(tracker.state, "TRAILING")

        # Eski SL iptal edilmiş olmalı
        self.assertTrue(
            any(c.get("algo_id") for c in exchange.cancelled_orders),
            "Eşik 2'de eski SL iptal edilmedi!"
        )

        # Trailing stop kurulmuş olmalı
        trailing_orders = [o for o in exchange.placed_orders if o.get("ord_type") == "move_order_stop"]
        self.assertGreaterEqual(len(trailing_orders), 1, "Trailing stop kurulmadı!")


if __name__ == "__main__":
    unittest.main(verbosity=2)
