"""Verify private SMTP settings without sending email or printing credentials."""
import crm

try:
    crm.check_smtp()
except ValueError as exc:
    raise SystemExit(str(exc)) from None
print("SMTP authentication succeeded. No email was sent.")
