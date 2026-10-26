import unittest
from datetime import date
import pandas as pd

import fyers_chart_data


class FakeFyersClient:
    def __init__(self, rows_by_range=None):
        self.calls = []
        self.rows_by_range = rows_by_range or {}

    def history(self, data):
        self.calls.append(dict(data))
        return self.rows_by_range.get(
            (data["range_from"], data["range_to"]),
            {"s": "ok", "candles": []},
        )


class FyersNativeIndexChartTests(unittest.TestCase):
    def test_maps_core_nifty_indices_to_fyers_symbols(self):
        self.assertEqual(fyers_chart_data.INDEX_FYERS_SYMBOLS["^NSEI"], "NSE:NIFTY50-INDEX")
        self.assertEqual(fyers_chart_data.INDEX_FYERS_SYMBOLS["^CRSLDX"], "NSE:NIFTY500-INDEX")
        self.assertEqual(fyers_chart_data.INDEX_FYERS_SYMBOLS["^CNX200"], "NSE:NIFTY200-INDEX")

    def test_fetch_chunks_history_into_at_most_366_calendar_days(self):
        client = FakeFyersClient()
        result = fyers_chart_data.fetch_index_history(
            client, "^CRSLDX", date(2024, 1, 1), date(2025, 1, 1)
        )
        self.assertTrue(result.empty)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["symbol"], "NSE:NIFTY500-INDEX")
        self.assertEqual(client.calls[0]["resolution"], "D")
        for call in client.calls:
            start = date.fromisoformat(call["range_from"])
            end = date.fromisoformat(call["range_to"])
            self.assertLessEqual((end - start).days + 1, 366)

    def test_normalizes_valid_candles_and_discards_invalid_ohlc(self):
        ts = int(pd.Timestamp("2024-01-02", tz="Asia/Kolkata").timestamp())
        ts_bad = int(pd.Timestamp("2024-01-03", tz="Asia/Kolkata").timestamp())
        frame = fyers_chart_data.normalize_history_response({
            "s": "ok",
            "candles": [
                [ts, 100, 110, 95, 105, 12345],
                [ts_bad, 100, 90, 95, 105, 12345],
            ],
        })
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["Close"], 105)
        self.assertEqual(frame.columns.tolist(), fyers_chart_data.OHLCV_COLUMNS)

    def test_reports_fyers_api_errors_clearly(self):
        with self.assertRaisesRegex(RuntimeError, "FYERS history request failed"):
            fyers_chart_data.normalize_history_response({
                "s": "error", "code": -1, "message": "invalid symbol"
            })

    def test_rejects_invalid_date_ranges_and_unknown_indices(self):
        client = FakeFyersClient()
        with self.assertRaises(ValueError):
            fyers_chart_data.fetch_index_history(
                client, "^CRSLDX", date(2025, 1, 1), date(2024, 1, 1)
            )
        with self.assertRaises(ValueError):
            fyers_chart_data.fetch_index_history(
                client, "RELIANCE.NS", date(2024, 1, 1), date(2024, 2, 1)
            )


if __name__ == "__main__":
    unittest.main()
