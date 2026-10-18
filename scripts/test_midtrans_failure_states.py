"""Unit test pemetaan status deny/failure KURO WEAR.

Menjalankan objek pesanan/pembayaran tiruan di memori. Tidak memanggil
Midtrans, tidak membaca/mengubah database SQLite, dan tidak membuat pesanan.

Jalankan dari root proyek:
    python -m unittest scripts.test_midtrans_failure_states -v
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.blueprints.cart import routes as cart_routes


class FakeDatabaseSession:
    """Pengganti sesi SQLAlchemy: tidak pernah menyentuh database asli."""

    def __init__(self):
        self.commit_calls = 0

    def commit(self):
        self.commit_calls += 1

    def __getattr__(self, name):
        raise AssertionError(f"Akses database asli tidak diizinkan: {name}")


class MidtransFailureStateTests(unittest.TestCase):
    def setUp(self):
        self.fake_session = FakeDatabaseSession()
        session_patch = patch.object(cart_routes.db, "session", self.fake_session)
        stock_patch = patch.object(
            cart_routes,
            "synchronize_paid_stock",
            side_effect=AssertionError("Stok tidak boleh dikurangi pada deny/failure"),
        )
        session_patch.start()
        self.addCleanup(session_patch.stop)
        self.stock_mock = stock_patch.start()
        self.addCleanup(stock_patch.stop)

    @staticmethod
    def fake_order_and_payment(initial_status="pending"):
        order = SimpleNamespace(
            order_number="KW-UNIT-TEST-NOT-SAVED",
            total=999000,
            payment_status=initial_status,
            stock_deducted_at=None,
            paid_at=None,
            fulfillment_status="unfulfilled",
        )
        payment = SimpleNamespace(
            midtrans_transaction_id=None,
            transaction_status="pending",
            payment_type=None,
            fraud_status=None,
            bank=None,
            verified_at=None,
            settled_at=None,
        )
        return order, payment

    @staticmethod
    def fake_midtrans_result(status):
        return {
            "order_id": "KW-UNIT-TEST-NOT-SAVED",
            "transaction_id": "UNIT-TRANSACTION-ID",
            "transaction_status": status,
            "status_code": "202" if status == "deny" else "500",
            "gross_amount": "999000.00",
            "payment_type": "bank_transfer",
        }

    def test_status_deny_dipetakan_ke_failed(self):
        self.assertEqual(
            cart_routes.payment_state(self.fake_midtrans_result("deny")),
            "failed",
        )

    def test_status_failure_dipetakan_ke_failed(self):
        self.assertEqual(
            cart_routes.payment_state(self.fake_midtrans_result("failure")),
            "failed",
        )

    def test_pending_ke_deny_tidak_memotong_stok(self):
        order, payment = self.fake_order_and_payment()
        result = cart_routes.apply_verified_payment_status(
            order, payment, self.fake_midtrans_result("deny")
        )
        self.assertEqual(result, "failed")
        self.assertEqual(order.payment_status, "failed")
        self.assertEqual(payment.transaction_status, "deny")
        self.assertIsNone(order.stock_deducted_at)
        self.assertEqual(order.fulfillment_status, "unfulfilled")
        self.assertEqual(self.fake_session.commit_calls, 1)
        self.stock_mock.assert_not_called()

    def test_pending_ke_failure_tidak_memotong_stok(self):
        order, payment = self.fake_order_and_payment()
        result = cart_routes.apply_verified_payment_status(
            order, payment, self.fake_midtrans_result("failure")
        )
        self.assertEqual(result, "failed")
        self.assertEqual(order.payment_status, "failed")
        self.assertEqual(payment.transaction_status, "failure")
        self.assertIsNone(order.stock_deducted_at)
        self.assertEqual(order.fulfillment_status, "unfulfilled")
        self.assertEqual(self.fake_session.commit_calls, 1)
        self.stock_mock.assert_not_called()

    def test_pesanan_lunas_tidak_diturunkan_ke_failed(self):
        for status in ("deny", "failure"):
            with self.subTest(status=status):
                order, payment = self.fake_order_and_payment("paid")
                order.stock_deducted_at = datetime.now(timezone.utc)
                payment.transaction_status = "settlement"
                before = self.fake_session.commit_calls
                result = cart_routes.apply_verified_payment_status(
                    order, payment, self.fake_midtrans_result(status)
                )
                self.assertEqual(result, "paid")
                self.assertEqual(order.payment_status, "paid")
                self.assertEqual(payment.transaction_status, "settlement")
                self.assertEqual(self.fake_session.commit_calls, before)
                self.stock_mock.assert_not_called()

    def test_pengulangan_failure_tidak_memotong_stok(self):
        order, payment = self.fake_order_and_payment()
        result = self.fake_midtrans_result("failure")
        self.assertEqual(cart_routes.apply_verified_payment_status(order, payment, result), "failed")
        self.assertEqual(cart_routes.apply_verified_payment_status(order, payment, result), "failed")
        self.assertIsNone(order.stock_deducted_at)
        self.assertEqual(self.fake_session.commit_calls, 2)
        self.stock_mock.assert_not_called()

    def test_nomor_pesanan_tidak_cocok_ditolak(self):
        order, payment = self.fake_order_and_payment()
        result = self.fake_midtrans_result("deny")
        result["order_id"] = "WRONG-ORDER"
        with self.assertRaises(ValueError):
            cart_routes.apply_verified_payment_status(order, payment, result)
        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(self.fake_session.commit_calls, 0)

    def test_nominal_tidak_cocok_ditolak(self):
        order, payment = self.fake_order_and_payment()
        result = self.fake_midtrans_result("failure")
        result["gross_amount"] = "1.00"
        with self.assertRaises(ValueError):
            cart_routes.apply_verified_payment_status(order, payment, result)
        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(self.fake_session.commit_calls, 0)

    def test_id_transaksi_tidak_cocok_ditolak(self):
        order, payment = self.fake_order_and_payment()
        payment.midtrans_transaction_id = "OTHER-TRANSACTION-ID"
        with self.assertRaises(ValueError):
            cart_routes.apply_verified_payment_status(
                order, payment, self.fake_midtrans_result("deny")
            )
        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(self.fake_session.commit_calls, 0)

    def test_status_tidak_dikenal_ditolak(self):
        with self.assertRaises(ValueError):
            cart_routes.payment_state(self.fake_midtrans_result("unknown_status"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
