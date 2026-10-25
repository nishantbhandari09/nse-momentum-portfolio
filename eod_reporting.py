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
