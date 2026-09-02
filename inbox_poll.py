"""
Polls an IMAP inbox for vendor replies and matches them back to open
requests by request_id in the subject line (see dispatch.py's
subject_for() — most email clients preserve subject text on reply, so
"[req-81018e90] Request — ..." becomes "Re: [req-81018e90] Request —
...", and we just regex out the id).

This is meant to be called periodically (same cadence as the scheduled
timeout check, or its own cron entry) — it does one pass over unread
mail and returns.

Env vars needed:
  IMAP_HOST, IMAP_USER, IMAP_PASSWORD

For Gmail: imap.gmail.com, same App Password as SMTP.
"""
import os
import re
import email
import imaplib
from email.header import decode_header

IMAP_HOST = os.environ.get("IMAP_HOST")
IMAP_USER = os.environ.get("IMAP_USER")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD")

POLL_CONFIGURED = all([IMAP_HOST, IMAP_USER, IMAP_PASSWORD])

REQUEST_ID_PATTERN = re.compile(r"\[(req-[a-f0-9]+)\]")

def _decode(value) -> str:
    parts = decode_header(value)
    return "".join(
        p.decode(enc or "utf-8") if isinstance(p, bytes) else p
        for p, enc in parts
    )

def extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="replace")
        return ""
    return msg.get_payload(decode=True).decode(errors="replace")

def fetch_unread_replies() -> list[dict]:
    """Returns a list of {request_id, from_address, subject, body} for
    every unread email whose subject contains a recognizable request_id.
    Marks matched messages as read so they aren't reprocessed next poll.
    Returns an empty list (with a logged reason) on any connection or
    auth failure instead of raising — a poll failure shouldn't 500 the
    endpoint that calls it, it should just mean "nothing new this time"."""
    if not POLL_CONFIGURED:
        print("[inbox poll not configured — IMAP_HOST/IMAP_USER/IMAP_PASSWORD not set]")
        return []
    results = []
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, timeout=15)
        conn.login(IMAP_USER, IMAP_PASSWORD)
        conn.select("INBOX")
        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            return results
        for num in data[0].split():
            status, msg_data = conn.fetch(num, "(RFC822)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode(msg.get("Subject", ""))
            match = REQUEST_ID_PATTERN.search(subject)
            if not match:
                continue  # not a reply to one of our requests, leave unread
            results.append({
                "request_id": match.group(1),
                "from_address": msg.get("From", ""),
                "subject": subject,
                "body": extract_body(msg).strip(),
            })
            conn.store(num, "+FLAGS", "\\Seen")
    except (imaplib.IMAP4.error, OSError) as e:
        print(f"[inbox poll failed: {e}]")
        return []
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass  # already disconnected or never fully connected — nothing to clean up
    return results
