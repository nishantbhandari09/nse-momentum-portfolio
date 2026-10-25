"""Tests for per-strategy EOD email report helpers."""
import unittest
from datetime import date, datetime

from eod_reporting import (
    build_strategy_update_report,
    is_valid_email_address,
    rebalance_notification_cycle,
)


class StrategyEmailTests(unittest.TestCase):
    def test_email_recipient_validation(self):
        self.assertTrue(is_valid_email_address("trader@example.com"))
        self.assertFalse(is_valid_email_address(""))
        self.assertFalse(is_valid_email_address("not-an-email"))
        self.assertFalse(is_valid_email_address("Trader <trader@example.com>"))

    def test_monthly_rebalance_cycle_waits_for_configured_day(self):
        self.assertIsNone(
            rebalance_notification_cycle("Monthly", 15, datetime(2026, 10, 14, 17, 0))
        )
        self.assertEqual(
            rebalance_notification_cycle("Monthly", 15, datetime(2026, 10, 15, 17, 0)),
            "M-2026-10",
        )

    def test_weekly_and_quarterly_rebalance_cycle(self):
        self.assertEqual(
            rebalance_notification_cycle("Weekly", 1, date(2026, 10, 27)),
            "W-2026-44",
        )
        self.assertIsNone(
            rebalance_notification_cycle("Quarterly", 15, date(2026, 10, 10))
        )
        self.assertEqual(
            rebalance_notification_cycle("Quarterly", 15, date(2026, 10, 15)),
            "Q-2026-4",
        )

    def test_report_contains_entry_exit_and_portfolio_details(self):
        body = build_strategy_update_report(
            strategy_name="Momentum",
            strategy_config={"status": "Active", "groups": ["Nifty 500"], "rebalance_freq": "Monthly", "rebalance_day": 5},
            event_type="SCAN",
            target_symbols=["NEW.NS", "HOLD.NS"],
            current_positions=[
                {"Symbol": "HOLD.NS", "Buy Qty": 2, "Buy Price": 100.0, "Entry Date": "2026-01-01"},
                {"Symbol": "EXIT.NS", "Buy Qty": 1, "Buy Price": 80.0, "Entry Date": "2026-01-01"},
            ],
            before_positions=[
                {"Symbol": "HOLD.NS", "Buy Qty": 2, "Buy Price": 100.0},
                {"Symbol": "EXIT.NS", "Buy Qty": 1, "Buy Price": 80.0},
            ],
            price_by_symbol={"NEW.NS": 150.0, "HOLD.NS": 110.0, "EXIT.NS": 75.0},
            signal_date="2026-10-23",
        )
        self.assertIn("ENTRY TARGETS (1)", body)
        self.assertIn("NEW: ₹150.00", body)
        self.assertIn("EXIT CANDIDATES / EXECUTED EXITS (1)", body)
        self.assertIn("Priced holdings unrealized P&L", body)
        self.assertIn("Signal / price session: 2026-10-23", body)


if __name__ == "__main__":
    unittest.main()
