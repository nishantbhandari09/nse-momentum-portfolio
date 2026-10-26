from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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

    def test_release_loader_downloads_requested_year_and_maps_old_symbols(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            symbols_path = root / "symbols.parquet"
            year_path = root / "prices_adj_2020.parquet"
            pd.DataFrame({
                "symbol": ["RELIANCE", "OLDRELIANCE", "TCS"],
                "sid": ["RELIANCE", "RELIANCE", "TCS"],
            }).to_parquet(symbols_path, index=False)
            pd.DataFrame({
                "date": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-01", "2020-01-02"]),
                "sid": ["RELIANCE", "RELIANCE", "TCS", "TCS"],
                "adj_close": [100.0, 102.0, 200.0, 202.0],
            }).to_parquet(year_path, index=False)

            manifest = {
                "symbols_asset": "symbols.parquet",
                "symbols_sha256": "symbols-hash",
                "years": {
                    "2020": {
                        "asset": "prices_adj_2020.parquet",
                        "sha256": "prices-hash",
                    }
                },
            }

            def fake_asset(filename, expected_sha256=None):
                return symbols_path if filename == "symbols.parquet" else year_path

            with patch.object(nse_archive_data, "get_manifest", return_value=manifest):
                with patch.object(nse_archive_data, "ensure_asset", side_effect=fake_asset):
                    prices = nse_archive_data.load_close_prices(
                        ["RELIANCE.NS", "OLDRELIANCE.NS", "TCS.NS", "^NSEI"],
                        "2020-01-01",
                        "2020-01-02",
                    )

            self.assertAlmostEqual(prices.loc[pd.Timestamp("2020-01-01"), "RELIANCE.NS"], 100.0)
            self.assertAlmostEqual(prices.loc[pd.Timestamp("2020-01-02"), "OLDRELIANCE.NS"], 102.0)
            self.assertAlmostEqual(prices.loc[pd.Timestamp("2020-01-01"), "TCS.NS"], 200.0)
            self.assertNotIn("^NSEI", prices.columns)



    def test_list_all_symbols_returns_one_latest_ticker_per_security_id(self):
        with TemporaryDirectory() as temp:
            symbols_path = Path(temp) / "symbols.parquet"
            pd.DataFrame({
                "symbol": ["OLDCO", "NEWCO", "RELIANCE"],
                "sid": ["ISIN1", "ISIN1", "ISIN2"],
                "first_date": ["2010-01-01", "2020-01-01", "2005-01-01"],
                "last_date": ["2019-12-31", "2025-01-01", "2025-01-01"],
            }).to_parquet(symbols_path, index=False)
            manifest = {
                "symbols_asset": "symbols.parquet",
                "symbols_sha256": "symbols-hash",
                "years": {},
            }
            with patch.object(nse_archive_data, "get_manifest", return_value=manifest):
                with patch.object(nse_archive_data, "ensure_asset", return_value=symbols_path):
                    tickers = nse_archive_data.list_all_symbols()
            self.assertEqual(tickers, ["NEWCO.NS", "RELIANCE.NS"])
            self.assertEqual(len(tickers), len(set(tickers)))

    def test_load_ohlcv_reads_adjusted_bars_and_volume_from_archive(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            symbols_path = root / "symbols.parquet"
            year_path = root / "prices_adj_2024.parquet"
            pd.DataFrame({
                "symbol": ["RELIANCE"],
                "sid": ["RELIANCE"],
            }).to_parquet(symbols_path, index=False)
            pd.DataFrame({
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "sid": ["RELIANCE", "RELIANCE"],
                "adj_open": [100.0, 103.0],
                "adj_high": [110.0, 112.0],
                "adj_low": [98.0, 102.0],
                "adj_close": [105.0, 108.0],
                "volume": [1000.0, 1500.0],
                "turnover": [105000.0, 162000.0],
                "trades": [250.0, 300.0],
            }).to_parquet(year_path, index=False)
            manifest = {
                "symbols_asset": "symbols.parquet",
                "symbols_sha256": "symbols-hash",
                "years": {
                    "2024": {
                        "asset": "prices_adj_2024.parquet",
                        "sha256": "year-hash",
                    }
                },
            }

            def fake_asset(filename, expected_sha256=None):
                return symbols_path if filename == "symbols.parquet" else year_path

            with patch.object(nse_archive_data, "get_manifest", return_value=manifest):
                with patch.object(nse_archive_data, "ensure_asset", side_effect=fake_asset):
                    frame = nse_archive_data.load_ohlcv(
                        "RELIANCE.NS", "2024-01-01", "2024-01-31"
                    )
            self.assertEqual(len(frame), 2)
            self.assertEqual(
                frame.columns.tolist(),
                ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume", "Traded Value", "Trades"],
            )
            self.assertEqual(frame.iloc[0]["Close"], 105.0)
            self.assertEqual(frame.iloc[1]["Volume"], 1500.0)
            self.assertEqual(frame.iloc[0]["Symbol"], "RELIANCE")


    def test_prefetch_years_downloads_only_years_in_requested_range(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            assets = {}
            years = {}
            for year, size in ((2021, 10), (2022, 20), (2023, 30), (2024, 40)):
                path = root / f"prices_adj_{year}.parquet"
                path.write_bytes(b"x" * size)
                assets[path.name] = path
                years[str(year)] = {
                    "asset": path.name,
                    "sha256": f"hash-{year}",
                    "rows": year,
                }
            manifest = {
                "years": years,
                "total_symbols": 1234,
                "first_date": "1994-11-03",
                "last_date": "2026-10-23",
                "generated_at_utc": "2026-10-23T00:00:00Z",
            }

            def fake_asset(filename, expected_sha256=None):
                return assets[filename]

            with patch.object(nse_archive_data, "get_manifest", return_value=manifest):
                with patch.object(nse_archive_data, "ensure_asset", side_effect=fake_asset):
                    result = nse_archive_data.prefetch_years("2021-10-01", "2023-03-01")
            self.assertEqual(result["years"], [2021, 2022, 2023])
            self.assertEqual(result["years_cached"], 3)
            self.assertEqual(result["bytes"], 60)
            self.assertEqual(result["symbols"], 1234)

if __name__ == "__main__":
    unittest.main()
