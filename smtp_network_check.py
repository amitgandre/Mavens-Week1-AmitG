"""Check Gmail's encrypted SMTP ports without reading credentials or signing in."""
import smtplib
import ssl
import certifi
import crm

context = ssl.create_default_context(cafile=certifi.where())
for port in [587, 465]:
    stage = "connect"
    try:
        connection = smtplib.SMTP_SSL("smtp.gmail.com", port, context=context, timeout=15) if port == 465 else smtplib.SMTP("smtp.gmail.com", port, timeout=15)
        with connection as smtp:
            if port == 587:
                stage = "STARTTLS"
                smtp.starttls(context=context)
            stage = "EHLO"
            smtp.ehlo()
            print(f"Port {port}: encrypted connection succeeded; authentication supported: {smtp.has_extn('auth')}.")
    except Exception as exc:
        print(f"Port {port}: {stage} failed. {crm.smtp_error_message(exc)}")
