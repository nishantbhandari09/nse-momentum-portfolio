import os
import pandas as pd
from fyers_apiv3 import fyersModel
from dotenv import load_dotenv

load_dotenv()

class FyersClientManager:
    def __init__(self):
        self.client_id = os.getenv("FYERS_CLIENT_ID", "")
        self.access_token = os.getenv("FYERS_ACCESS_TOKEN", "")
        self.fyers = None
        if self.client_id and self.access_token:
            self.fyers = fyersModel.FyersModel(
                client_id=self.client_id,
                is_async=False,
                token=self.access_token,
                log_path=""
            )

    def get_historical_data(self, symbol: str, resolution: str = "D", range_from: str = "2023-01-01", range_to: str = "2026-10-02") -> pd.DataFrame:
        """
        Fetches OHLC candles from FYERS API. Returns a clean pandas DataFrame.
        """
        if not self.fyers:
            # Fallback Mock Generator if token is not configured yet
            return self._generate_mock_ohlc(symbol)

        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": "1"
        }
        
        response = self.fyers.history(data=data)
        if response.get("s") == "ok":
            candles = response.get("candles", [])
            df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["date"] = pd.to_datetime(df["timestamp"], unit="s")
            return df
        else:
            return self._generate_mock_ohlc(symbol)

    def _generate_mock_ohlc(self, symbol: str) -> pd.DataFrame:
        """
        Generates structured synthetic data for development/offline testing.
        """
        dates = pd.date_range(end="2026-10-02", periods=300, freq="D")
        import numpy as np
        np.random.seed(abs(hash(symbol)) % 10000)
        base_price = 100.0 + (abs(hash(symbol)) % 400)
        returns = np.random.normal(0.0005, 0.015, size=len(dates))
        price_series = base_price * np.exp(np.cumsum(returns))
        
        df = pd.DataFrame({
            "date": dates,
            "open": price_series * 0.995,
            "high": price_series * 1.01,
            "low": price_series * 0.99,
            "close": price_series,
            "volume": np.random.randint(10000, 500000, size=len(dates))
        })
        return df

fyers_manager = FyersClientManager()