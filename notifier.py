"""
notifier.py — Polymarket Weather Bot 2026
Email-сповіщення через Gmail SMTP (безкоштовно).
Telegram повністю видалено.
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import os

import config

logger = logging.getLogger(__name__)


def _send_email(subject: str, body: str) -> bool:
    if not config.EMAIL_ENABLED:
        return False
    sender = config.EMAIL_SENDER
    recipient = config.EMAIL_RECIPIENT
    app_password = os.getenv("EMAIL_APP_PASSWORD", "")
    if not sender or not recipient or not app_password:
        logger.debug("Email: не налаштовано")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Polymarket Bot <{sender}>"
        msg["To"] = recipient
        text_part = MIMEText(body, "plain", "utf-8")
        html_body = body.replace("\n", "<br>")
        html_body = f"""<div style="font-family:Arial,sans-serif;max-width:600px;
padding:20px;border:1px solid #ddd;border-radius:8px;">
<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:8px;">
🌤 Polymarket Weather Bot</h3>
<div style="font-size:14px;line-height:1.6;color:#333;">{html_body}</div>
<div style="margin-top:20px;font-size:11px;color:#999;border-top:1px solid #eee;padding-top:10px;">
{datetime.now().strftime('%d.%m.%Y %H:%M:%S')} UTC</div></div>"""
        html_part = MIMEText(html_body, "html", "utf-8")
        msg.attach(text_part)
        msg.attach(html_part)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(sender, app_password)
            server.sendmail(sender, recipient, msg.as_string())
        logger.debug(f"Email відправлено: {subject}")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("Email: помилка автентифікації! Перевір App Password у .env")
        return False
    except Exception as e:
        logger.debug(f"Email error: {e}")
        return False


def notify_trade_open(direction, question, size_usd, price, edge, dry_run=True):
    mode = "DRY-RUN (симуляція)" if dry_run else "РЕАЛЬНА УГОДА"
    _send_email(
        f"[PolyBot] Нова угода: {direction} | ${size_usd:.2f}",
        f"Режим: {mode}\n\nДія: {direction}\nРинок: {question}\n"
        f"Розмір: ${size_usd:.2f}\nЦіна: {price:.4f}\nEdge: {edge:.1%}\n"
    )


def notify_trade_close(direction, question, pnl_usd, pnl_pct, reason):
    emoji = "Прибуток" if pnl_usd >= 0 else "Збиток"
    _send_email(
        f"[PolyBot] Закрито: {emoji} ${pnl_usd:+.2f}",
        f"Ринок: {question}\nPnL: ${pnl_usd:+.2f} ({pnl_pct:+.1%})\nПричина: {reason}\n"
    )


def notify_whale_alert(whale_summary):
    _send_email("[PolyBot] Whale Alert!", f"Велика угода:\n\n{whale_summary}")


def notify_error(error_msg):
    _send_email("[PolyBot] Помилка бота", f"Помилка:\n\n{error_msg[:1000]}")


def notify_startup(dry_run, capital):
    mode = "DRY-RUN" if dry_run else "РЕАЛЬНА ТОРГІВЛЯ"
    _send_email(
        "[PolyBot] Бот запущено",
        f"Режим: {mode}\nКапітал: ${capital:.2f}\nEdge YES: {getattr(config, 'MIN_EDGE_YES', 0.20):.0%}\nEdge NO: {getattr(config, 'MIN_EDGE_NO', 0.20):.0%}\n"
    )


def notify_daily_summary(capital, total_pnl, win_rate, total_trades):
    emoji = "ЗРОСТАННЯ" if total_pnl >= 0 else "ПАДІННЯ"
    _send_email(
        f"[PolyBot] Щоденний звіт | {emoji} ${total_pnl:+.2f}",
        f"Капітал: ${capital:.2f}\nPnL: ${total_pnl:+.2f}\n"
        f"Win rate: {win_rate:.1%}\nУгод: {total_trades}\n"
    )
