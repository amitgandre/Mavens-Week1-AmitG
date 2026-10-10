"""Configure Gmail locally without putting credentials in chat or shell history."""
import argparse
import getpass
import crm
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import certifi
import tempfile


def main():
    parser = argparse.ArgumentParser(description="Configure Gmail SMTP privately.")
    parser.add_argument("--port", type=int, choices=[587, 465], default=587,
                        help="587 uses STARTTLS; 465 uses TLS from connection start.")
    args = parser.parse_args()
    address = input("Gmail sender address: ").strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
        raise SystemExit("Enter a valid Gmail or Google Workspace address.")
    password = "".join(getpass.getpass("Google App Password (hidden): ").split())
    if len(password) != 16:
        raise SystemExit("Google App Passwords must contain 16 characters after removing spaces. Generate a new App Password; do not use your normal Google password. Settings were not saved.")
    stage = "connection"
    print(f"Checking Gmail on port {args.port} (no email will be sent)...")
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        connection = smtplib.SMTP_SSL("smtp.gmail.com", args.port, context=context, timeout=20) if args.port == 465 else smtplib.SMTP("smtp.gmail.com", args.port, timeout=20)
        with connection as smtp:
            if args.port == 587:
                stage = "TLS certificate verification"
                smtp.starttls(context=context)
            stage = "authentication"
            smtp.login(address, password)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        raise SystemExit(f"Gmail {stage} failed: {crm.smtp_error_message(exc)} Settings were not saved.") from None
    path = Path(__file__).resolve().parent / ".env.smtp"
    settings = {
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": str(args.port),
        "SMTP_USER": address,
        "SMTP_PASSWORD": password,
        "SMTP_FROM": address,
        "CRM_URL": os.environ.get("CRM_URL", "http://localhost:8501"),
    }
    content = "# Private Gmail settings. Do not commit or share this file.\n"
    content += "\n".join(f"{key}={json.dumps(value)}" for key, value in settings.items()) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".smtp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print("Gmail authentication succeeded. Private settings saved to .env.smtp.")
    print("Refresh the Company Administrator page, then click Check email connection.")


if __name__ == "__main__":
    main()
