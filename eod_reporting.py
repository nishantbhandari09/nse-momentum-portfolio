"""Delivery helpers for user-generated end-of-day reports.

The app generates signals from the chosen strategy and locally stored market
data. This module only delivers that report; it does not place trades.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Mapping

import requests


def send_telegram_alert(bot_token: str, chat_id: str, message: str) -> tuple[bool, str]:
    """Send plain-text report text to Telegram, splitting at safe message lengths."""
    token = str(bot_token or "").strip()
    chat = str(chat_id or "").strip()
    if not token or not chat:
        return False, "Telegram is not configured (missing bot token or chat ID)."
    chunks = []
    remaining = str(message or "")
    while remaining:
        if len(remaining) <= 3800:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, 3800)
        if split_at < 1000:
            split_at = 3800
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    if not chunks:
        chunks = ["End-of-day trading report is empty."]
    try:
        for chunk in chunks:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": chunk, "disable_web_page_preview": True},
                timeout=20,
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("ok", False):
                return False, "Telegram rejected the message. Check the bot token and chat ID."
        return True, f"Telegram report sent ({len(chunks)} message(s))."
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status:
            return False, f"Telegram delivery failed (HTTP {status})."
        return False, f"Telegram delivery failed: {type(exc).__name__}."


def send_email_alert(
    subject: str,
    body: str,
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    sender: str,
    recipient: str,
    use_tls: bool = True,
) -> tuple[bool, str]:
    """Send an email report through a configured SMTP server."""
    required = [smtp_host, sender, recipient]
    if not all(str(value or "").strip() for value in required):
        return False, "Email is not configured (missing SMTP host, sender, or recipient)."
    try:
        port = int(smtp_port or 587)
        message = EmailMessage()
        message["Subject"] = str(subject or "NSE Momentum Portfolio — EOD Report")
        message["From"] = str(sender).strip()
        message["To"] = str(recipient).strip()
        message.set_content(str(body or "End-of-day report is empty."))
        if port == 465:
            with smtplib.SMTP_SSL(
                str(smtp_host).strip(), port, timeout=25,
                context=ssl.create_default_context()
            ) as server:
                if username:
                    server.login(str(username), str(password or ""))
                server.send_message(message)
        else:
            with smtplib.SMTP(str(smtp_host).strip(), port, timeout=25) as server:
                server.ehlo()
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if username:
                    server.login(str(username), str(password or ""))
                server.send_message(message)
        return True, "Email report sent."
    except Exception as exc:
        return False, f"Email delivery failed: {type(exc).__name__}."



def is_valid_email_address(value: str) -> bool:
    """Basic syntax check for an email recipient (not an inbox verification)."""
    import re
    from email.utils import parseaddr

    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 254:
        return False
    parsed_name, parsed_address = parseaddr(candidate)
    if parsed_name or parsed_address != candidate:
        return False
    return bool(re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", candidate))


def rebalance_notification_cycle(rebalance_freq: str, rebalance_day: int, now=None) -> str | None:
    """Return a cadence key when a configured rebalance-email cycle is due.

    Monthly: after configured day of month.
    Weekly: once per ISO week, after Monday.
    Quarterly: after configured day in the first month of the quarter.
    The caller records a successfully sent cycle to avoid duplicate messages.
    """
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    if now is None:
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
    elif isinstance(now, date) and not isinstance(now, datetime):
        now = datetime.combine(now, datetime.min.time())
    today = now.date() if isinstance(now, datetime) else now
    day = max(1, min(28, int(rebalance_day or 1)))
    frequency = str(rebalance_freq or "Monthly").strip().lower()

    if frequency == "weekly":
        iso_year, iso_week, _ = today.isocalendar()
        return f"W-{iso_year:04d}-{iso_week:02d}"
    if frequency == "quarterly":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        target = date(today.year, quarter_start_month, day)
        if today < target:
            return None
        quarter = ((today.month - 1) // 3) + 1
        return f"Q-{today.year:04d}-{quarter}"
    target = date(today.year, today.month, day)
    if today < target:
        return None
    return f"M-{today.year:04d}-{today.month:02d}"


def build_strategy_update_report(
    strategy_name: str,
    strategy_config: Mapping[str, Any],
    event_type: str,
    target_symbols=None,
    current_positions=None,
    before_positions=None,
    price_by_symbol=None,
    entered_symbols=None,
    exited_symbols=None,
    retained_symbols=None,
    signal_date=None,
    note: str | None = None,
) -> str:
    """Build a readable plain-text email with entry/exit and portfolio details."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    target_symbols = list(dict.fromkeys(str(s).strip() for s in (target_symbols or []) if str(s).strip()))
    current_positions = list(current_positions or [])
    before_positions = list(before_positions if before_positions is not None else current_positions)
    price_by_symbol = dict(price_by_symbol or {})

    def _symbol(position):
        return str(position.get("Symbol", "")).strip() if isinstance(position, Mapping) else ""

    def _price(symbol, fallback=None):
        raw = price_by_symbol.get(symbol, fallback)
        try:
            number = float(raw)
            return number if number > 0 else None
        except (TypeError, ValueError):
            return None

    old_by_symbol = {_symbol(pos): pos for pos in before_positions if _symbol(pos)}
    current_by_symbol = {_symbol(pos): pos for pos in current_positions if _symbol(pos)}
    old_symbols = set(old_by_symbol)
    target_set = set(target_symbols)
    entered = list(entered_symbols) if entered_symbols is not None else sorted(target_set - old_symbols)
    exited = list(exited_symbols) if exited_symbols is not None else sorted(old_symbols - target_set)
    retained = list(retained_symbols) if retained_symbols is not None else sorted(old_symbols & target_set)

    tz_now = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M IST")
    lines = [
        "NSE MOMENTUM PORTFOLIO — STRATEGY UPDATE",
        f"Event: {str(event_type or 'Strategy update').replace('_', ' ').upper()}",
        f"Generated: {tz_now}",
        f"Strategy: {strategy_name}",
        f"Strategy status: {strategy_config.get('status', 'Active')}",
        f"Universe: {', '.join(strategy_config.get('groups', ['Nifty 500']))}",
        f"Rebalance cadence: {strategy_config.get('rebalance_freq', 'Monthly')} (configured day {strategy_config.get('rebalance_day', 1)})",
        f"Signal / price session: {signal_date if signal_date is not None else 'latest available stored session'}",
        "",
        f"ENTRY TARGETS ({len(entered)})",
    ]
    if entered:
        for symbol in entered:
            pos = old_by_symbol.get(symbol, {})
            px = _price(symbol, pos.get("Buy Price") if isinstance(pos, Mapping) else None)
            px_text = f"₹{px:,.2f}" if px is not None else "price unavailable"
            lines.append(f"- {symbol.replace('.NS', '')}: {px_text}; eligible entry target, not an order")
    else:
        lines.append("- None")

    lines.extend(["", f"EXIT CANDIDATES / EXECUTED EXITS ({len(exited)})"])
    if exited:
        for symbol in exited:
            pos = old_by_symbol.get(symbol, {})
            qty = pos.get("Buy Qty", "unknown") if isinstance(pos, Mapping) else "unknown"
            px = _price(symbol, pos.get("Buy Price") if isinstance(pos, Mapping) else None)
            px_text = f"₹{px:,.2f}" if px is not None else "price unavailable"
            lines.append(f"- {symbol.replace('.NS', '')}: quantity {qty}; indicative/last price {px_text}")
    else:
        lines.append("- None")

    lines.extend(["", f"RETAINED TARGETS ({len(retained)})"])
    if retained:
        lines.extend(f"- {symbol.replace('.NS', '')}" for symbol in retained)
    else:
        lines.append("- None")

    total_market_value = 0.0
    total_cost_basis = 0.0
    total_pnl = 0.0
    priced_positions = 0
    lines.extend(["", f"CURRENT PORTFOLIO ({len(current_by_symbol)} position(s))"])
    if current_by_symbol:
        lines.append("Symbol | Qty | Buy price | Latest price | Market value | Unrealized P&L")
        for symbol, pos in sorted(current_by_symbol.items()):
            try:
                qty = int(pos.get("Buy Qty", 0))
                buy_price = float(pos.get("Buy Price", 0))
            except (TypeError, ValueError):
                qty, buy_price = 0, 0.0
            current_price = _price(symbol, buy_price)
            current_value = qty * current_price if current_price is not None else 0.0
            cost_basis = qty * buy_price
            pnl = current_value - cost_basis if current_price is not None else None
            if current_price is not None:
                priced_positions += 1
                total_market_value += current_value
                total_cost_basis += cost_basis
                total_pnl += float(pnl or 0.0)
            lines.append(
                f"{symbol.replace('.NS', '')} | {qty} | ₹{buy_price:,.2f} | "
                f"{'₹{:,.2f}'.format(current_price) if current_price is not None else 'n/a'} | "
                f"{'₹{:,.2f}'.format(current_value) if current_price is not None else 'n/a'} | "
                f"{'₹{:,.2f}'.format(pnl) if pnl is not None else 'n/a'}"
            )
        if priced_positions:
            lines.extend([
                f"Priced holdings market value: ₹{total_market_value:,.2f}",
                f"Priced holdings cost basis: ₹{total_cost_basis:,.2f}",
                f"Priced holdings unrealized P&L: ₹{total_pnl:,.2f}",
                f"Holdings with current price available: {priced_positions}/{len(current_by_symbol)}",
            ])
    else:
        lines.append("- No holdings recorded in the strategy.")

    lines.extend(["", f"TARGET PORTFOLIO ({len(target_symbols)})"])
    if target_symbols:
        for rank, symbol in enumerate(target_symbols, start=1):
            px = _price(symbol)
            px_text = f"₹{px:,.2f}" if px is not None else "price unavailable"
            status = "RETAINED" if symbol in old_symbols else "ENTRY TARGET"
            lines.append(f"{rank}. {symbol.replace('.NS', '')} | {px_text} | {status}")
    else:
        lines.append("- No stock passed the configured filters.")

    if note:
        lines.extend(["", "NOTE", str(note)])
    lines.extend([
        "",
        "Important: an entry target or exit candidate is a rules-based signal, not proof of execution. "
        "A scan email does not place orders. Prices may be delayed or reflect the latest saved close.",
    ])
    return "\n".join(lines)
