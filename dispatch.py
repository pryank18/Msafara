"""
Real outbound dispatch via SMTP. Falls back to a no-op (just logs) if
credentials aren't configured, so the rest of the system still runs
without this being wired up yet.

Env vars needed:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_ADDRESS

For Gmail: use an App Password (not your regular password), host
smtp.gmail.com, port 587. Other providers: check their SMTP docs.

Subject line convention: every outbound message includes the request_id
in the subject (e.g. "[req-81018e90] Room Block Request — ..."). This is
how inbox_poll.py matches incoming replies back to the right request —
most email clients preserve the subject on reply, so this survives the
round trip without needing real threading/Message-ID tracking.
"""
import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM_ADDRESS = os.environ.get("SMTP_FROM_ADDRESS", SMTP_USER)

DISPATCH_CONFIGURED = all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD])

def subject_for(request_id: str, need_description: str) -> str:
    return f"[{request_id}] Request — {need_description}"

def send_email(request_id: str, to_address: str, subject: str, body: str) -> bool:
    """Returns True if actually sent, False if dispatch isn't configured
    OR if sending failed (caller falls back to displaying the draft for
    manual sending either way — a bad SMTP config should degrade the
    experience, not crash the request that triggered it)."""
    if not DISPATCH_CONFIGURED:
        print(f"[dispatch not configured — SMTP_HOST/SMTP_USER/SMTP_PASSWORD not set]")
        print(f"[would send to {to_address}: {subject}]")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM_ADDRESS
    msg["To"] = to_address
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        # Bad credentials, unreachable host, etc. Log and fall back to
        # manual-send behavior rather than letting this crash the caller
        # (node_send etc.) mid-graph-invocation, which would lose the
        # request before it's ever saved.
        print(f"[dispatch failed for {request_id}: {e}]")
        print(f"[would send to {to_address}: {subject}]")
        return False
