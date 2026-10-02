from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Iterable

from fyers_data import get_app_id


class FyersLiveDataManager:
    """Persistent FYERS v3 market-data WebSocket for a Streamlit process/session.

    The manager keeps the latest SymbolUpdate payload for every subscribed symbol.
    It never places orders.
    """

    def __init__(self, access_token: str, app_id: str | None = None):
        self.access_token = str(access_token)
        self.app_id = app_id or get_app_id()
        if not self.app_id or not self.access_token:
            raise ValueError("FYERS app ID/access token is required for live data.")

        self._lock = threading.RLock()
        self._socket: Any | None = None
        self._thread: threading.Thread | None = None
        self._symbols: tuple[str, ...] = ()
        self._ticks: dict[str, dict[str, Any]] = {}
        self._connected = False
        self._connecting = False
        self._last_error = ""
        self._last_message_at: datetime | None = None
        self._connected_at: datetime | None = None
        self._tick_count = 0

    @staticmethod
    def _normalise_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(str(s).strip() for s in symbols if str(s).strip()))

    def _on_message(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        symbol = message.get("symbol") or message.get("n")
        if not symbol:
            return

        now = datetime.now()
        with self._lock:
            self._ticks[str(symbol)] = dict(message)
            self._last_message_at = now
            self._tick_count += 1

    def _on_error(self, message: Any) -> None:
        with self._lock:
            self._last_error = str(message)

    def _on_close(self, message: Any) -> None:
        with self._lock:
            self._connected = False
            if message:
                self._last_error = f"WebSocket closed: {message}"

    def _on_connect(self) -> None:
        with self._lock:
            self._connected = True
            self._connecting = False
            self._connected_at = datetime.now()
            self._last_error = ""
            symbols = list(self._symbols)
            socket = self._socket

        if socket is not None and symbols:
            try:
                socket.subscribe(symbols=symbols, data_type="SymbolUpdate")
            except Exception as exc:
                with self._lock:
                    self._last_error = f"Subscription failed: {exc}"

    def _run(self) -> None:
        try:
            from fyers_apiv3.FyersWebsocket import data_ws

            socket = data_ws.FyersDataSocket(
                access_token=f"{self.app_id}:{self.access_token}",
                log_path="",
                litemode=False,
                write_to_file=False,
                reconnect=True,
                on_connect=self._on_connect,
                on_close=self._on_close,
                on_error=self._on_error,
                on_message=self._on_message,
            )
            with self._lock:
                self._socket = socket

            # The official FYERS v3 client keeps the data socket alive after connect.
            # keep_running() is also used by FYERS' background examples.
            socket.connect()
            try:
                socket.keep_running()
            except Exception as exc:
                with self._lock:
                    self._last_error = f"WebSocket loop stopped: {exc}"
        except Exception as exc:
            with self._lock:
                self._connected = False
                self._connecting = False
                self._last_error = str(exc)

    def start(self, symbols: Iterable[str]) -> None:
        normalised = self._normalise_symbols(symbols)
        if not normalised:
            return

        with self._lock:
            old_symbols = set(self._symbols)
            new_symbols = set(normalised)
            self._symbols = normalised
            socket = self._socket
            connected = self._connected
            alive = bool(self._thread and self._thread.is_alive())

            if alive:
                if connected and socket is not None:
                    remove = sorted(old_symbols - new_symbols)
                    add = sorted(new_symbols - old_symbols)
                    try:
                        if remove:
                            socket.unsubscribe(symbols=remove, data_type="SymbolUpdate")
                        if add:
                            socket.subscribe(symbols=add, data_type="SymbolUpdate")
                    except Exception as exc:
                        self._last_error = f"Subscription update failed: {exc}"
                return

            self._connecting = True
            self._thread = threading.Thread(
                target=self._run,
                name="fyers-market-data",
                daemon=True,
            )
            self._thread.start()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {symbol: dict(payload) for symbol, payload in self._ticks.items()}

    def get(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._ticks.get(symbol)
            return dict(value) if value else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
            return {
                "connected": self._connected,
                "connecting": self._connecting,
                "thread_alive": thread_alive,
                "subscribed": len(self._symbols),
                "ticks_received": self._tick_count,
                "symbols_with_data": len(self._ticks),
                "last_message_at": self._last_message_at,
                "connected_at": self._connected_at,
                "last_error": self._last_error,
            }

    def stop(self) -> None:
        with self._lock:
            socket = self._socket
            self._connected = False
            self._connecting = False
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
