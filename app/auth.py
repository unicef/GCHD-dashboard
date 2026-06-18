import os, secrets, smtplib, sqlite3, time
from email.mime.text import MIMEText

ALLOWED_DOMAIN  = "unicef.org"
OTP_EXPIRY_SECS = 600   # 10 minutes
SESSION_HOURS   = 8

_DB_PATH = os.path.join(os.path.dirname(__file__), "otp_store.db")


def _get_db():
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS otp_pending (
            email      TEXT PRIMARY KEY,
            code       TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def _smtp_creds():
    base = os.path.dirname(__file__)
    user = open(os.path.join(base, "credentials", "smtp_user.txt")).read().strip()
    pwd  = open(os.path.join(base, "credentials", "smtp_pass.txt")).read().strip()
    return user, pwd


def request_otp(email):
    email = email.strip().lower()
    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        return False, "Only @unicef.org addresses are allowed."
    code       = str(secrets.randbelow(900000) + 100000)
    expires_at = time.time() + OTP_EXPIRY_SECS
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO otp_pending (email, code, expires_at) VALUES (?, ?, ?)",
            (email, code, expires_at),
        )
    try:
        _send_email(email, code)
        print(f"[OTP] Sent to {email}: {code}", flush=True)
    except Exception as e:
        print(f"[OTP] Email failed for {email}: {e} — code: {code}", flush=True)
        return False, f"Failed to send email: {e}"
    return True, None


def verify_otp(email, code):
    email = email.strip().lower()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT code, expires_at FROM otp_pending WHERE email = ?", (email,)
        ).fetchone()
        if not row:
            return False
        stored_code, expires_at = row
        conn.execute("DELETE FROM otp_pending WHERE email = ?", (email,))
    if time.time() > expires_at:
        return False
    return code.strip() == stored_code


def _send_email(to, code):
    user, pwd = _smtp_creds()
    msg = MIMEText(
        f"Your UNICEF Hazard Database verification code is:\n\n"
        f"  {code}\n\n"
        f"This code expires in 10 minutes. Do not share it."
    )
    msg["Subject"] = "UNICEF Hazard Database — Verification Code"
    msg["From"]    = f"UNICEF Hazard DB <{user}>"
    msg["To"]      = to
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
