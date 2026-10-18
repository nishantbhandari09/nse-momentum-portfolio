from __future__ import annotations

import unittest

import pandas as pd

import nse_archive_data


class NSEArchivePanelTests(unittest.TestCase):
    def test_normalise_ticker_handles_yahoo_and_fyers_names(self):
        self.assertEqual(nse_archive_data._normalise_ticker("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(nse_archive_data._normalise_ticker("NSE:RELIANCE-EQ"), "RELIANCE")
        self.assertEqual(nse_archive_data._normalise_ticker(" infy.ns "), "INFY")

    def test_nse_history_fills_old_dates_and_keeps_fyers_values(self):
        dates_primary = pd.to_datetime(["2020-01-02", "2020-01-04"])
        primary = pd.DataFrame(
            {"RELIANCE.NS": [100.0, 104.0]},
            index=pd.DatetimeIndex(dates_primary, name="Date"),
        )
        dates_fallback = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"])
        fallback = pd.DataFrame(
            {"RELIANCE.NS": [49.0, 50.0, 51.0, 52.0]},
            index=pd.DatetimeIndex(dates_fallback, name="Date"),
        )

        merged = nse_archive_data.merge_price_panels(primary, fallback)

        self.assertAlmostEqual(merged.loc[pd.Timestamp("2020-01-01"), "RELIANCE.NS"], 98.0)
        self.assertAlmostEqual(merged.loc[pd.Timestamp("2020-01-02"), "RELIANCE.NS"], 100.0)
        self.assertAlmostEqual(merged.loc[pd.Timestamp("2020-01-03"), "RELIANCE.NS"], 102.0)
        self.assertAlmostEqual(merged.loc[pd.Timestamp("2020-01-04"), "RELIANCE.NS"], 104.0)

    def test_fallback_only_columns_are_preserved(self):
        primary = pd.DataFrame(
            {"RELIANCE.NS": [100.0]},
            index=pd.to_datetime(["2020-01-02"]),
        )
        fallback = pd.DataFrame(
            {"TCS.NS": [200.0]},
            index=pd.to_datetime(["2020-01-01"]),
        )
        merged = nse_archive_data.merge_price_panels(primary, fallback)
        self.assertAlmostEqual(merged.loc[pd.Timestamp("2020-01-01"), "TCS.NS"], 200.0)
        self.assertAlmostEqual(merged.loc[pd.Timestamp("2020-01-02"), "RELIANCE.NS"], 100.0)


if __name__ == "__main__":
    unittest.main()
