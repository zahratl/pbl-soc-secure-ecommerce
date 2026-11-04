import smtplib
import ssl

from email.message import EmailMessage
from email.utils import formataddr
from flask import current_app


class EmailDeliveryError(Exception):
    """Kesalahan pengiriman email."""


def send_email(recipient, subject, text_body, html_body=None):
    """Mengirim email melalui SMTP dengan STARTTLS."""
    host = current_app.config.get('SMTP_HOST')
    port = current_app.config.get('SMTP_PORT', 587)
    username = current_app.config.get('SMTP_USERNAME')
    password = current_app.config.get('SMTP_APP_PASSWORD')
    sender = current_app.config.get('SMTP_SENDER') or username

    if not host or not username or not password or not sender:
        raise EmailDeliveryError('Konfigurasi Gmail SMTP belum lengkap.')

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = formataddr(('KURO WEAR', sender))
    message['To'] = recipient
    message.set_content(text_body)

    if html_body:
        message.add_alternative(html_body, subtype='html')

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(username, password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError('Gagal mengirim email melalui Gmail SMTP.') from exc

    return True