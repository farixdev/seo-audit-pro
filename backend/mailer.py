# -*- coding: utf-8 -*-
"""
SMTP delivery and the outbound email queue.

The SMTP password is stored encrypted, decrypted only in this module at send
time, and never returned by any API or written to logs.
"""

import logging
import os
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from backend import cache, config, db, events
from backend.security import decrypt_secret
from backend.util import iso_ago, now_iso

log = logging.getLogger("seo_audit_pro.mailer")

_WAKE = threading.Event()
_STARTED = False
_START_LOCK = threading.Lock()


class MailError(Exception):
    pass


def smtp_config():
    row = db.query_one("SELECT * FROM smtp_settings WHERE id = 'default'")
    if not row:
        return None
    password = decrypt_secret(row["password_enc"])
    if password is None:
        raise MailError("Stored SMTP credentials cannot be decrypted. A manager must re-enter the SMTP password.")
    row["password"] = password
    return row


def _is_gmail(cfg):
    return (cfg.get("host") or "").lower().strip() in ("smtp.gmail.com", "smtp.googlemail.com")


def _connect(cfg, timeout=20):
    host = (cfg.get("host") or "").strip()
    ctx = ssl.create_default_context()
    if host.lower() in ("localhost", "127.0.0.1", "::1"):
        # The mail server on this same machine (cPanel): its certificate names the
        # server, not "localhost", and the connection never leaves the machine.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    cfg = dict(cfg, host=host)
    if _is_gmail(cfg):  # Google shows app passwords in groups of four ("abcd efgh ijkl mnop")
        cfg["password"] = (cfg.get("password") or "").replace(" ", "")
    if cfg["encryption"] == "ssl":
        server = smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), timeout=timeout, context=ctx)
    else:
        server = smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=timeout)
        server.ehlo()
        if cfg["encryption"] == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
    if cfg.get("username"):
        server.login(cfg["username"], cfg["password"])
    return server


def _friendly(exc, cfg=None):
    cfg = cfg or {}
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        if _is_gmail(cfg):
            return ("Gmail rejected the sign-in. Use the full Gmail address as the username and a 16-letter "
                    "App Password (Google Account > Security > 2-Step Verification > App passwords), not the "
                    "normal Gmail password.")
        return "The SMTP server rejected the username or password."
    if isinstance(exc, ssl.SSLCertVerificationError):
        return ("The mail server's certificate does not match this host name. Use the server name your host "
                "shows for email (cPanel > Email Accounts > Connect Devices, for example server123.yourhost.com).")
    if isinstance(exc, ssl.SSLError):
        return "The port and encryption do not match: use port 465 with SSL, or port 587 with STARTTLS."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return ("The mail server refused the From address. It must be the mailbox you signed in with "
                "(or an address it may send as).")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The recipient address was rejected by the mail server."
    if isinstance(exc, (smtplib.SMTPConnectError, ConnectionError, OSError)):
        return "Could not connect to the SMTP server. Check host, port and encryption."
    if isinstance(exc, smtplib.SMTPException):
        return f"The mail server returned an error: {str(exc)[:160]}"
    return "Email delivery failed."


def test_connection(cfg):
    try:
        server = _connect(cfg, timeout=12)
        server.quit()
        return True, "Connected and authenticated successfully."
    except Exception as e:  # noqa: BLE001
        return False, _friendly(e, cfg)


def _build(cfg, to_email, subject, body_text, attachments=(), reply_to=None):
    msg = EmailMessage()
    msg["From"] = formataddr((cfg["from_name"], cfg["from_email"]))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)  # without it many mail servers score the email as spam
    msg["Message-ID"] = make_msgid(domain=cfg["from_email"].split("@")[-1])
    if reply_to and reply_to.lower() != (cfg["from_email"] or "").lower():
        msg["Reply-To"] = reply_to  # replies go to the person who sent it from the app
    msg.set_content(body_text)
    for name, mime, path in attachments:
        maintype, subtype = mime.split("/", 1)
        with open(path, "rb") as fh:
            msg.add_attachment(fh.read(), maintype=maintype, subtype=subtype, filename=name)
    return msg


def send_now(to_email, subject, body_text, attachments=(), reply_to=None):
    cfg = smtp_config()
    if not cfg:
        raise MailError("SMTP is not configured.")
    try:
        server = _connect(cfg)
        try:
            server.send_message(_build(cfg, to_email, subject, body_text, attachments, reply_to))
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass
    except MailError:
        raise
    except Exception as e:  # noqa: BLE001
        raise MailError(_friendly(e, cfg)) from e


def send_password_reset(user, code):
    try:
        send_now(user["email"], "Your SEO Audit Pro password reset code",
                 f"Hello {user['name']},\n\nUse this code to reset your SEO Audit Pro password:\n\n    {code}\n\n"
                 "The code expires in 30 minutes. If you did not request a reset, you can ignore this email; "
                 "your password has not been changed.\n")
    except MailError as e:
        log.warning("Password reset email not sent for user %s: %s", user["id"], e)


# ---------------------------------------------------------------------------
# Outbound queue
# ---------------------------------------------------------------------------
def _attachments_for(email_id):
    rows = db.query("""SELECT ea.report_id, ea.upload_id, up.stored_name, up.original_name, up.mime
                       FROM email_attachments ea
                       LEFT JOIN uploads up ON up.id = ea.upload_id
                       WHERE ea.email_id = %s""", (email_id,))
    from backend import reports, uploads

    out = []
    for r in rows:
        if r["report_id"]:
            # Rendered on first use (PDF) or rebuilt after a restart wiped the disk.
            rep = reports.ensure_file(db.query_one("SELECT * FROM reports WHERE id = %s", (r["report_id"],)))
            mime = "application/pdf" if rep["format"] == "pdf" else \
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            label = "SEO-Audit-Report" if rep["kind"] == "client" else "SEO-Technical-Audit"
            out.append((f"{label}.{rep['format']}", mime, reports.report_path(rep)))
        else:
            path = uploads.upload_path({"id": r["upload_id"], "stored_name": r["stored_name"]})
            out.append((r["original_name"], r["mime"], path))
    return out


def _process_one():
    sent_today = db.scalar("SELECT COUNT(*) FROM emails WHERE status = 'sent' AND sent_at > %s", (iso_ago(86400),)) or 0
    if sent_today >= config.DAILY_EMAIL_LIMIT:
        return "limited"
    row = db.query_one("SELECT * FROM emails WHERE status = 'queued' ORDER BY queued_at LIMIT 1")
    if not row:
        return "idle"
    with db.transaction() as tx:
        tx.execute("UPDATE emails SET status = 'sending', attempts = attempts + 1, updated_at = %s "
                   "WHERE id = %s AND status = 'queued'", (now_iso(), row["id"]))
        if tx.rowcount != 1:
            return "raced"
    sender = db.query_one("SELECT * FROM users WHERE id = %s", (row["sender_id"],))
    try:
        send_now(row["to_email"], row["subject"], row["body"], _attachments_for(row["id"]),
                 reply_to=(sender or {}).get("email"))
    except Exception as e:  # noqa: BLE001
        reason = str(e) if isinstance(e, MailError) else getattr(e, "message", None) or "Email delivery failed."
        db.execute("UPDATE emails SET status = 'failed', error = %s, updated_at = %s WHERE id = %s",
                   (reason, now_iso(), row["id"]))
        events.audit(sender, "EMAIL_FAILED", "email", row["id"], {"lead_id": row["lead_id"], "reason": reason})
        events.notify([row["sender_id"]], "email_failed", "Email failed",
                      f"Delivery to {row['to_email']} failed: {reason}", "email", row["id"])
        return "failed"
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("UPDATE emails SET status = 'sent', error = NULL, sent_at = %s, updated_at = %s WHERE id = %s",
                   (now, now, row["id"]))
        tx.execute("UPDATE leads SET last_contact_at = %s, updated_at = %s, version = version + 1, "
                   "status = CASE WHEN status IN ('new', 'assigned', 'audited') THEN 'contacted' ELSE status END "
                   "WHERE id = %s", (now, now, row["lead_id"]))
        events.audit(sender, "EMAIL_SENT", "email", row["id"], {"lead_id": row["lead_id"], "to": row["to_email"],
                                                                 "batch_id": row["batch_id"]}, tx=tx)
    cache.bump()
    events.notify([row["sender_id"]], "email_sent", "Email sent", f"Your email to {row['to_email']} was delivered to the mail server.",
                  "email", row["id"])
    return "sent"


def maintenance():
    """Prune short-lived security records."""
    try:
        db.execute("DELETE FROM idempotency_keys WHERE created_at < %s", (iso_ago(7 * 86400),))
        db.execute("DELETE FROM login_attempts WHERE created_at < %s", (iso_ago(30 * 86400),))
        db.execute("DELETE FROM password_resets WHERE expires_at < %s", (iso_ago(7 * 86400),))
        db.execute("DELETE FROM sessions WHERE expires_at < %s OR (revoked_at IS NOT NULL AND revoked_at < %s)",
                   (iso_ago(86400), iso_ago(30 * 86400)))
        from backend import lead_import
        lead_import.cleanup_pending()
    except Exception as e:  # noqa: BLE001
        log.warning("Maintenance failed: %s", e)


def _worker():
    last_maintenance = 0.0
    while True:
        if time.monotonic() - last_maintenance > 6 * 3600:
            maintenance()
            last_maintenance = time.monotonic()
        try:
            outcome = _process_one()
        except Exception as e:  # noqa: BLE001
            log.error("Email queue error: %s", e)
            outcome = "error"
        if outcome in ("sent", "failed"):
            time.sleep(config.EMAIL_SEND_INTERVAL_SECONDS)
        elif outcome == "raced":
            continue
        else:
            # Idle: sleep until an email is queued (wake()), so an idle server leaves the
            # database alone and Neon can suspend it. The daily limit is checked again later.
            _WAKE.wait({"limited": 900, "error": 60}.get(outcome, 6 * 3600))
            _WAKE.clear()


def recover_interrupted():
    """An email left in 'sending' may or may not have been delivered before a
    crash. Mark it failed rather than risk sending it twice."""
    db.execute("UPDATE emails SET status = 'failed', error = %s, updated_at = %s WHERE status = 'sending'",
               ("Delivery was interrupted by a server restart. Check before resending.", now_iso()))


def start_worker():
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True
    recover_interrupted()
    threading.Thread(target=_worker, name="email-queue", daemon=True).start()


def wake():
    if config.SHARED_HOSTING:  # no queue thread here: send now in a short-lived thread (the cron job is the backup)
        threading.Thread(target=_drain_once, name="email-send", daemon=True).start()
        return
    _WAKE.set()


_DRAIN_LOCK = threading.Lock()


def _drain_once():
    if _DRAIN_LOCK.acquire(blocking=False):  # one sender per process; the database stops two processes sending one email
        try:
            drain(110)
        finally:
            _DRAIN_LOCK.release()


def release_stuck(minutes=10):
    """An email left in 'sending' (the process was stopped while sending) may
    or may not have gone out: mark it failed rather than risk sending it twice."""
    db.execute("UPDATE emails SET status = 'failed', error = %s, updated_at = %s WHERE status = 'sending' AND updated_at < %s",
               ("Delivery was interrupted. Check the recipient's inbox before resending.", now_iso(), iso_ago(minutes * 60)))


def drain(max_seconds=50):
    """Send queued emails until none are left or ``max_seconds`` pass (the cron
    job runs this every minute on shared hosting). Returns how many were sent."""
    deadline = time.monotonic() + max_seconds
    sent = 0
    while time.monotonic() < deadline:
        try:
            outcome = _process_one()
        except Exception as e:  # noqa: BLE001
            log.error("Email queue error: %s", e)
            return sent
        if outcome == "sent":
            sent += 1
        if outcome in ("sent", "failed"):
            if time.monotonic() + config.EMAIL_SEND_INTERVAL_SECONDS >= deadline:
                break
            time.sleep(config.EMAIL_SEND_INTERVAL_SECONDS)
        elif outcome != "raced":
            break  # idle or daily limit reached
    return sent
