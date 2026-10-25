"""Unit tests for portfolio trade-cost accounting."""
import unittest
import pandas as pd
from trading_costs import normalize_cost_config, rebalance_holdings


class TradingCostTests(unittest.TestCase):
    def test_buy_fees_reduce_cash_and_are_reported(self):
        holdings, cash, stats = rebalance_holdings(
            current_holdings={},
            target_holdings={"ABC.NS": 5},
            close_row=pd.Series({"ABC.NS": 100.0}),
            current_cash=1000.0,
            config={"buy_fee_bps": 10, "sell_fee_bps": 20, "slippage_bps": 0},
            allow_leverage=False,
        )
        self.assertEqual(holdings, {"ABC.NS": 5})
        self.assertAlmostEqual(stats["Buy Turnover"], 500.0)
        self.assertAlmostEqual(stats["Trading Costs"], 0.5)
        self.assertAlmostEqual(cash, 499.5)
        self.assertEqual(stats["Trade Count"], 1)

    def test_sale_cost_is_deducted(self):
        holdings, cash, stats = rebalance_holdings(
            current_holdings={"ABC.NS": 5},
            target_holdings={},
            close_row=pd.Series({"ABC.NS": 100.0}),
            current_cash=0.0,
            config={"buy_fee_bps": 10, "sell_fee_bps": 20, "slippage_bps": 0},
            allow_leverage=False,
        )
        self.assertEqual(holdings, {})
        self.assertAlmostEqual(stats["Sell Turnover"], 500.0)
        self.assertAlmostEqual(stats["Trading Costs"], 1.0)
        self.assertAlmostEqual(cash, 499.0)

    def test_unleveraged_buys_are_cash_limited(self):
        holdings, cash, stats = rebalance_holdings(
            current_holdings={},
            target_holdings={"ABC.NS": 10},
            close_row=pd.Series({"ABC.NS": 100.0}),
            current_cash=500.0,
            config={"buy_fee_bps": 100, "sell_fee_bps": 100, "slippage_bps": 0},
            allow_leverage=False,
        )
        self.assertEqual(holdings, {"ABC.NS": 4})
        self.assertGreaterEqual(cash, 0.0)
        self.assertAlmostEqual(stats["Trading Costs"], 4.0)

    def test_cost_rates_are_bounded_and_invalid_values_default(self):
        config = normalize_cost_config({"buy_fee_bps": -5, "sell_fee_bps": "invalid", "slippage_bps": 2000})
        self.assertEqual(config["buy_fee_bps"], 0.0)
        self.assertEqual(config["sell_fee_bps"], 25.0)
        self.assertEqual(config["slippage_bps"], 1000.0)


if __name__ == "__main__":
    unittest.main()
