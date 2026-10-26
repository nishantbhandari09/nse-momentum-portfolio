import unittest

import tradingview_charts


class TradingViewIndexSymbolTests(unittest.TestCase):
    def test_nifty_core_index_symbols_use_valid_tradingview_aliases(self):
        self.assertEqual(tradingview_charts.tv_symbol("^NSEI"), "NSE:NIFTY")
        self.assertEqual(tradingview_charts.tv_symbol("^CRSLDX"), "NSE:CNX500")
        self.assertEqual(tradingview_charts.tv_symbol("^CNX200"), "NSE:CNX200")
        self.assertEqual(tradingview_charts.tv_symbol("^NSEBANK"), "NSE:BANKNIFTY")

    def test_additional_index_choices_resolve_to_tradingview_symbols(self):
        self.assertEqual(
            tradingview_charts.tv_symbol("TV:NIFTYNEXT50"),
            "NSE:NIFTYJR",
        )
        self.assertEqual(
            tradingview_charts.tv_symbol("TV:NIFTYMIDCAP100"),
            "NSE:CNXMIDCAP",
        )
        self.assertEqual(
            tradingview_charts.tv_symbol("TV:INDIAVIX"),
            "NSE:INDIAVIX",
        )

    def test_embedded_chart_uses_corrected_index_symbol(self):
        html = tradingview_charts.advanced_chart_html("^CRSLDX", theme="dark")
        self.assertIn('"symbol": "NSE:CNX500"', html)
        self.assertIn('"theme": "dark"', html)

    def test_additional_index_choices_are_marked_for_tradingview_only(self):
        self.assertIn("TV:NIFTY100", tradingview_charts.TRADINGVIEW_ONLY_INDEXES)
        self.assertIn("TV:NIFTYHEALTHCARE", tradingview_charts.TRADINGVIEW_ONLY_INDEXES)


if __name__ == "__main__":
    unittest.main()
