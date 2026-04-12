"""Email delivery via SMTP (works locally and in GitHub Actions).

Uses Gmail SMTP with an App Password — no OAuth required.
Generate one at: https://myaccount.google.com/apppasswords

Required env vars:
    GMAIL_USER        your Gmail address (e.g. you@gmail.com)
    GMAIL_APP_PASSWORD  the 16-char app password from Google
    RECIPIENT_EMAIL   where to send the newsletter
"""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from loguru import logger

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_newsletter(
    subject: str,
    html_content: str,
    md_content: str,
    recipient: str | None = None,
) -> bool:
    gmail_user = os.environ.get("GMAIL_USER")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = recipient or os.environ.get("RECIPIENT_EMAIL")

    if not gmail_user or not app_password:
        logger.warning("[Email] GMAIL_USER / GMAIL_APP_PASSWORD not set — skipping send")
        return False
    if not recipient:
        logger.warning("[Email] RECIPIENT_EMAIL not set — skipping send")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = recipient

    msg.attach(MIMEText(md_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, app_password)
            server.sendmail(gmail_user, recipient, msg.as_string())
        logger.info(f"[Email] Sent to {recipient}")
        return True
    except Exception as exc:
        logger.error(f"[Email] Send failed: {exc}")
        return False
