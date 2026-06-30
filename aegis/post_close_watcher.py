# ==================================================
# POST-CLOSE WATCHER — Aegis Kapanış Sonrası İzleme
# ==================================================
# Pozisyon kapandıktan sonra ilgili coini 10-20 saniye canlı izler.
# İki senaryoda n8n'e webhook gönderir (SSF kararı n8n'de verilir,
# burada sadece "bu coine hemen tekrar bak" sinyali gönderilir):
#
#   SENARYO A — ERKEN TP (aynı yönde devam):
#     Pozisyon kârla kapandı ve kapanış sonrası fiyat AYNI yönde güçlü devam ediyor
#     → suggested_side = orijinal yön
#
#   SENARYO B — YANLIŞ YÖN (tersine dönüş):
#     Pozisyon zararla kapandı ve kapanış sonrası fiyat TERS yönde güçlü hareket ediyor
#     → suggested_side = ters yön

import asyncio
import time
import logging
import aiohttp

logger = logging.getLogger("Aegis.PostCloseWatcher")

# ============================================================
# AYARLAR
# ============================================================
WATCH_DURATION_SEC      = 15      # İzleme süresi (saniye)
WATCH_POLL_INTERVAL_SEC = 1.0     # Fiyat kontrol sıklığı

# Senaryo A — Erken TP (devam) eşiği
CONTINUATION_THRESHOLD_PCT    = 0.15  # Kapanış sonrası en az %0.15 aynı yönde devam etmeli
CONTINUATION_MAX_PULLBACK_PCT = 0.05  # İzleme süresince ters yönde max %0.05 pullback izni

# Senaryo B — Ters Yön eşiği
REVERSAL_THRESHOLD_PCT = 0.20  # Kapanış sonrası en az %0.20 ters yönde hareket etmeli

# Aynı coin için tekrar tekrar webhook atmayı önle (kısa süreli kilit)
_last_webhook_fire = {}  # {inst_id: timestamp}
WEBHOOK_COOLDOWN_SEC = 60  # Aynı coin için min 60 sn arayla webhook at


class PostCloseWatcher:
    """
    Tek bir kapanan pozisyon için 15 saniyelik izleme penceresi.
    AegisOrchestrator.market_data'dan canlı fiyat okur.
    WS subscription remove_tracker'da hemen kapatılmaz,
    watcher bitince kapatılır.
    """

    def __init__(self, orchestrator, inst_id: str, original_side: str,
                 entry_price: float, exit_price: float, exit_reason: str,
                 realized_pnl_pct: float):
        self.orch = orchestrator
        self.inst_id = inst_id
        self.original_side = original_side.lower()  # "long" / "short"
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.exit_reason = exit_reason  # "TRAILING_EXIT", "BE_EXIT", "EXTERNAL_CLOSE", vb.
        self.realized_pnl_pct = realized_pnl_pct  # kapanıştaki gerçekleşen PnL %

        self.was_profitable = realized_pnl_pct > 0

    async def run(self):
        """İzleme döngüsünü başlat, sonunda karar ver ve gerekirse webhook at."""
        start_price = self.exit_price
        start_time = time.time()

        max_favorable_pct = 0.0   # orijinal yönde en fazla ne kadar devam etti
        max_adverse_pct   = 0.0   # orijinal yöne ters en fazla ne kadar gitti
        last_price = start_price

        logger.info(f"[{self.inst_id}] Post-close watcher başladı. "
                    f"Yön={self.original_side} | Kapanış={start_price} | "
                    f"Sebep={self.exit_reason} | PnL%={self.realized_pnl_pct:.2f}")

        try:
            ticks = 0
            while time.time() - start_time < WATCH_DURATION_SEC:
                await asyncio.sleep(WATCH_POLL_INTERVAL_SEC)
                ticks += 1

                md = self.orch.market_data.get(self.inst_id, {})
                price = md.get("last", 0.0)
                if price <= 0:
                    continue
                last_price = price

                # Fiyatın orijinal pozisyon yönüne göre hareketi
                if self.original_side == "long":
                    move_pct = ((price - start_price) / start_price) * 100.0
                else:
                    move_pct = ((start_price - price) / start_price) * 100.0

                if move_pct > 0:
                    max_favorable_pct = max(max_favorable_pct, move_pct)
                else:
                    max_adverse_pct = max(max_adverse_pct, abs(move_pct))

            # ------------------------------------------------------
            # KARAR MANTIĞI
            # ------------------------------------------------------
            decision = None
            suggested_side = None
            reason_text = ""

            # SENARYO A — Erken TP, aynı yönde devam
            if self.was_profitable and self.exit_reason in ("TRAILING_EXIT", "BE_EXIT"):
                if (max_favorable_pct >= CONTINUATION_THRESHOLD_PCT
                        and max_adverse_pct <= CONTINUATION_MAX_PULLBACK_PCT):
                    decision = "CONTINUATION"
                    suggested_side = self.original_side
                    reason_text = (f"Erken TP: kapanış sonrası {max_favorable_pct:.2f}% "
                                   f"aynı yönde devam etti, pullback sadece {max_adverse_pct:.2f}%.")

            # SENARYO B — Yanlış yön, tersine dönüş
            if decision is None and not self.was_profitable:
                if self.original_side == "long":
                    adverse_move_pct = ((start_price - last_price) / start_price) * 100.0
                else:
                    adverse_move_pct = ((last_price - start_price) / start_price) * 100.0

                if adverse_move_pct >= REVERSAL_THRESHOLD_PCT:
                    decision = "REVERSAL"
                    suggested_side = "short" if self.original_side == "long" else "long"
                    reason_text = (f"Ters yön: kapanış sonrası fiyat {adverse_move_pct:.2f}% "
                                   f"orijinal pozisyonun aleyhine devam etti — "
                                   f"{suggested_side.upper()} fırsatı olabilir.")

            # ------------------------------------------------------
            # SONUÇ
            # ------------------------------------------------------
            if decision:
                logger.info(f"[{self.inst_id}] Post-close kararı: {decision} → "
                            f"suggested_side={suggested_side} | {reason_text}")
                await self._fire_webhook(decision, suggested_side, reason_text,
                                         max_favorable_pct, max_adverse_pct, ticks)
                if self.orch.action_log_cb:
                    symbol = self.inst_id.replace("-SWAP", "")
                    if decision == "CONTINUATION":
                        self.orch.add_action_log(
                            f"🔁 [{symbol}] Kapanış sonrası fiyat aynı yönde devam ediyor! "
                            f"+{max_favorable_pct:.2f}% | n8n'e sinyal gönderildi.")
                    else:
                        self.orch.add_action_log(
                            f"↩️ [{symbol}] Kapanış sonrası fiyat ters yöne döndü! "
                            f"{suggested_side.upper()} fırsatı | n8n'e sinyal gönderildi.")
            else:
                logger.info(f"[{self.inst_id}] Post-close: koşullar sağlanmadı, "
                            f"izleme sessizce bitti. (favorable={max_favorable_pct:.2f}%, "
                            f"adverse={max_adverse_pct:.2f}%)")

        except Exception as e:
            logger.exception(f"[{self.inst_id}] Post-close watcher hatası: {e}")
        finally:
            # İzleme bitti — WS aboneliğini kapat (eğer yeni tracker açılmadıysa)
            if self.inst_id not in self.orch.active_trackers:
                await self.orch.ws_sub_queue.put(("unsubscribe", self.inst_id))
                logger.info(f"[{self.inst_id}] Post-close watcher bitti, WS unsubscribe gönderildi.")
            else:
                logger.info(f"[{self.inst_id}] Post-close watcher bitti, yeni tracker açık — unsubscribe atlandı.")

    async def _fire_webhook(self, decision, suggested_side, reason_text,
                            max_favorable_pct, max_adverse_pct, ticks):
        """n8n'e POST at — SSF zincirini tetikle, karar n8n'de verilir."""
        import config as cfg
        webhook_url = getattr(cfg, "N8N_REENTRY_WEBHOOK_URL", "")
        if not webhook_url:
            logger.warning(f"[{self.inst_id}] N8N_REENTRY_WEBHOOK_URL tanımlı değil, webhook atlanıyor.")
            return

        # Cooldown kontrolü — aynı coin için spam önle
        now = time.time()
        last_fire = _last_webhook_fire.get(self.inst_id, 0)
        if now - last_fire < WEBHOOK_COOLDOWN_SEC:
            logger.info(f"[{self.inst_id}] Webhook cooldown aktif "
                        f"({now - last_fire:.0f}s < {WEBHOOK_COOLDOWN_SEC}s), atlanıyor.")
            return
        _last_webhook_fire[self.inst_id] = now

        payload = {
            "instId": self.inst_id,
            "trigger_source": "aegis_post_close",
            "decision": decision,                     # "CONTINUATION" | "REVERSAL"
            "suggested_side": suggested_side,          # "long" | "short"
            "original_side": self.original_side,
            "exit_reason": self.exit_reason,
            "realized_pnl_pct": round(self.realized_pnl_pct, 3),
            "max_favorable_pct": round(max_favorable_pct, 3),
            "max_adverse_pct": round(max_adverse_pct, 3),
            "watch_duration_sec": WATCH_DURATION_SEC,
            "ticks_observed": ticks,
            "reason": reason_text,
            "timestamp": time.time(),
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        logger.info(f"[{self.inst_id}] n8n webhook başarıyla gönderildi. Karar: {decision}")
                    else:
                        body = await resp.text()
                        logger.warning(f"[{self.inst_id}] n8n webhook hata: HTTP {resp.status} — {body[:200]}")
        except asyncio.TimeoutError:
            logger.error(f"[{self.inst_id}] n8n webhook zaman aşımı (10s)")
        except Exception as e:
            logger.error(f"[{self.inst_id}] n8n webhook gönderim hatası: {e}")
