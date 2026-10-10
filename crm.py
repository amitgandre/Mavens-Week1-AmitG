"""File-backed accounts and workbooks. All writes are locked and atomic."""
from __future__ import annotations
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import string
import smtplib
import ssl
import certifi
import time
from email.message import EmailMessage
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from urllib.parse import urlencode

import pandas as pd
from filelock import FileLock
from dotenv import dotenv_values

SMTP_CONFIG = Path(__file__).resolve().parent / ".env.smtp"
SESSION_IDLE_SECONDS = 15 * 60
SMTP_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "CRM_URL"]

DATA = Path(os.environ.get("CRM_DATA_DIR", "data"))
ROLES = ["Salesperson", "Company Administrator", "Executive Dashboard"]
STAGES = ["Open", "Prospecting", "Qualified", "Proposal", "Negotiation", "Closed", "Closed Won", "Closed Lost"]
CLOSED_STAGES = ["Closed", "Closed Won", "Closed Lost"]
PIPELINE_TEMPLATE = Path(__file__).resolve().parent / "Pipeline.xlsx"
PIPELINE_COLUMNS = ["Opportunity ID", "Client Name", "Opportunity Name", "Opportunity Value", "Creation Date", "Expected Closure Date", "Win Probability", "Open or Closed", "TCV", "ACV", "Contract Term", "Margin", "Industry Sector", "Salesperson Username"]
PIPELINE_STATUSES = ["Open", "Closed", "Closed Won", "Closed Lost"]
COLUMNS = ["Opportunity", "Customer", "Amount", "Stage", "Probability", "Close Date", "Last Contact", "Notes"]


def init():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)


def atomic_write(path, content):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(content)
    tmp.chmod(0o600)
    tmp.replace(path)


def read_accounts(include_deleted=False):
    init()
    path = DATA / "users.json"
    users = json.loads(path.read_text()) if path.exists() else {}
    return users if include_deleted else {key: user for key, user in users.items() if not user.get("deleted", False)}


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def create_session(username):
    user = read_accounts().get(username)
    if not user or not user["active"]:
        raise ValueError("Account is not active.")
    token = secrets.token_urlsafe(32)
    with FileLock(str(DATA / "sessions.lock")):
        path = DATA / "sessions.json"
        sessions = json.loads(path.read_text()) if path.exists() else {}
        sessions = {key: value for key, value in sessions.items() if value["expires"] > time.time()}
        sessions[hashlib.sha256(token.encode()).hexdigest()] = dict(
            username=username, role=user["role"], password_hash=user["password_hash"], last_activity=time.time(), expires=time.time() + 43200)
        atomic_write(path, json.dumps(sessions).encode())
    return token


def resume_session(token, *, touch=False, activity_at=None):
    if not token or len(token) > 128:
        return None
    init()
    with FileLock(str(DATA / "sessions.lock")):
        path = DATA / "sessions.json"
        sessions = json.loads(path.read_text()) if path.exists() else {}
        session = sessions.get(hashlib.sha256(token.encode()).hexdigest())
        now = time.time()
        if not session or session["expires"] <= now:
            return None
        last_activity = session.get("last_activity", session["expires"] - 43200)
        if now - last_activity >= SESSION_IDLE_SECONDS:
            return None
        user = read_accounts().get(session["username"])
        if not user or not user["active"] or user["role"] != session["role"] or user["password_hash"] != session["password_hash"]:
            return None
        if touch:
            activity = min(now, activity_at) if isinstance(activity_at, (int, float)) else now
            session["last_activity"] = last_activity = max(last_activity, activity)
            atomic_write(path, json.dumps(sessions).encode())
        return dict(user, username=session["username"], session_last_activity=last_activity)


def revoke_session(token):
    init()
    with FileLock(str(DATA / "sessions.lock")):
        path = DATA / "sessions.json"
        sessions = json.loads(path.read_text()) if path.exists() else {}
        sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        atomic_write(path, json.dumps(sessions).encode())


def verify_password(password, stored):
    _, salt, digest = stored.split("$")
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return hmac.compare_digest(actual.hex(), digest)


def create_account(name, username, email, role, password, temporary=True):
    init()
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
        raise ValueError("Username must be 3–64 letters, numbers, dots, underscores or hyphens.")
    if not name.strip() or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Provide a name and a valid email address.")
    if role not in ROLES or len(password) < 12:
        raise ValueError("Choose a supported role and a password of at least 12 characters.")
    with FileLock(str(DATA / "users.lock")):
        users = read_accounts(include_deleted=True)
        if username in users:
            raise ValueError("That username already exists.")
        users[username] = dict(name=name.strip(), email=email.strip(), role=role,
                               password_hash=password_hash(password), temporary=temporary,
                               active=True, email_status="Not sent")
        atomic_write(DATA / "users.json", json.dumps(users, indent=2).encode())
    return username


def update_account(username, **changes):
    with FileLock(str(DATA / "users.lock")):
        users = read_accounts(include_deleted=True)
        users[username].update(changes)
        atomic_write(DATA / "users.json", json.dumps(users, indent=2).encode())


def change_user_email(actor_username, username, email):
    email = email.strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Enter a valid email address.")
    with FileLock(str(DATA / "users.lock")):
        users = read_accounts(include_deleted=True)
        actor = users.get(actor_username)
        if not actor or not actor["active"] or actor.get("deleted") or actor["role"] != "Company Administrator":
            raise ValueError("Only an active company administrator can change user email addresses.")
        target = users.get(username)
        if not target or target.get("deleted"):
            raise ValueError("That account does not exist.")
        target.update(email=email, email_status="Email address updated; credentials not resent")
        atomic_write(DATA / "users.json", json.dumps(users, indent=2).encode())


def manage_account(actor_username, username, *, role=None, delete=False):
    """Administrators can change other users' roles or archive their accounts."""
    with FileLock(str(DATA / "users.lock")):
        users = read_accounts(include_deleted=True)
        actor = users.get(actor_username)
        if not actor or not actor["active"] or actor.get("deleted") or actor["role"] != "Company Administrator":
            raise ValueError("Only an active company administrator can manage accounts.")
        if username == actor_username:
            raise ValueError("You cannot delete your own account or change your own role.")
        target = users.get(username)
        if not target or target.get("deleted"):
            raise ValueError("That account does not exist.")
        if delete:
            source = workbook_path(username)
            destination = archive_path(username)
            with FileLock(str(source) + ".lock"):
                moved = False
                if source.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if destination.exists():
                        raise ValueError("An archived pipeline already exists for this account.")
                    source.replace(destination)
                    moved = True
                target.update(active=False, deleted=True, deleted_at=datetime.now(timezone.utc).isoformat())
                if moved:
                    target["pipeline_archived_at"] = target["deleted_at"]
                try:
                    atomic_write(DATA / "users.json", json.dumps(users, indent=2).encode())
                except Exception:
                    if moved:
                        destination.replace(source)
                    raise
            return
        else:
            if role not in ROLES:
                raise ValueError("Choose a supported role.")
            target["role"] = role
        atomic_write(DATA / "users.json", json.dumps(users, indent=2).encode())


def require_administrator(username):
    user = read_accounts().get(username)
    if not user or not user["active"] or user["role"] != "Company Administrator":
        raise ValueError("Only an active company administrator can access archived pipelines.")


def archive_path(username):
    return DATA / "archive" / workbook_path(username).name


def archived_pipelines(actor_username):
    """Archive previously deleted users' retained pipelines as well."""
    require_administrator(actor_username)
    records = []
    with FileLock(str(DATA / "users.lock")):
        users = read_accounts(include_deleted=True)
        changed = False
        for username, user in users.items():
            if not user.get("deleted"):
                continue
            source, destination = workbook_path(username), archive_path(username)
            with FileLock(str(source) + ".lock"):
                if source.exists() and not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    source.replace(destination)
                    user["pipeline_archived_at"] = datetime.now(timezone.utc).isoformat()
                    changed = True
                if destination.exists():
                    records.append({"Username": username, "Name": user["name"],
                        "Archived at": user.get("pipeline_archived_at", "Unknown"), "File": destination.name})
        if changed:
            atomic_write(DATA / "users.json", json.dumps(users, indent=2).encode())
    return records


def read_archived_pipeline(actor_username, username):
    require_administrator(actor_username)
    user = read_accounts(include_deleted=True).get(username)
    path = archive_path(username)
    if not user or not user.get("deleted") or not path.exists():
        raise ValueError("No archived pipeline exists for that account.")
    content = path.read_bytes()
    df = pd.read_excel(io.BytesIO(content), dtype={"Opportunity ID": str})
    return pipeline_table(df, username), content


def authenticate(username, password, role):
    init()
    username = username.strip().lower()
    with FileLock(str(DATA / "auth.lock")):
        path = DATA / "attempts.json"
        attempts = json.loads(path.read_text()) if path.exists() else {}
        recent = [t for t in attempts.get(username, []) if time.time() - t < 900]
        if len(recent) >= 5:
            return None, "Too many attempts. Try again in 15 minutes."
        user = read_accounts().get(username)
        if user and user["active"] and user["role"] == role and verify_password(password, user["password_hash"]):
            attempts.pop(username, None)
            atomic_write(path, json.dumps(attempts).encode())
            return dict(user, username=username), None
        attempts[username] = recent + [time.time()]
        atomic_write(path, json.dumps(attempts).encode())
        return None, "Username, password or role is incorrect."


def change_password(username, old, new):
    if len(new) < 12 or new == old:
        raise ValueError("Use a new password with at least 12 characters.")
    with FileLock(str(DATA / "users.lock")):
        users = read_accounts(include_deleted=True)
        if not verify_password(old, users[username]["password_hash"]):
            raise ValueError("Current password is incorrect.")
        users[username].update(password_hash=password_hash(new), temporary=False)
        atomic_write(DATA / "users.json", json.dumps(users, indent=2).encode())


def set_user_password(admin_username, username, password):
    """An active administrator can assign a temporary password to another user."""
    if len(password) < 12:
        raise ValueError("Use a password with at least 12 characters.")
    if admin_username == username:
        raise ValueError("Use Change password in the sidebar for your own account.")
    with FileLock(str(DATA / "users.lock")):
        users = read_accounts(include_deleted=True)
        actor = users.get(admin_username)
        if not actor or not actor["active"] or actor["role"] != "Company Administrator":
            raise ValueError("Only an active company administrator can set user passwords.")
        if username not in users or users[username].get("deleted"):
            raise ValueError("That account does not exist.")
        users[username].update(password_hash=password_hash(password), temporary=True,
                               email_status="Password updated; credentials not sent")
        atomic_write(DATA / "users.json", json.dumps(users, indent=2).encode())
    with FileLock(str(DATA / "auth.lock")):
        path = DATA / "attempts.json"
        attempts = json.loads(path.read_text()) if path.exists() else {}
        attempts.pop(username, None)
        atomic_write(path, json.dumps(attempts).encode())


def smtp_settings():
    """Read private file on each interaction; local settings override environment."""
    settings = {key: os.environ.get(key, "") for key in SMTP_KEYS}
    if SMTP_CONFIG.exists():
        local = dotenv_values(SMTP_CONFIG, interpolate=False)
        settings.update({key: local[key] or "" for key in SMTP_KEYS if key in local})
    for key in SMTP_KEYS:
        if key != "SMTP_PASSWORD":
            settings[key] = settings[key].strip()
    if settings["SMTP_HOST"].lower() == "smtp.gmail.com":
        settings["SMTP_PASSWORD"] = "".join(settings["SMTP_PASSWORD"].split())
    return settings


def smtp_missing():
    settings = smtp_settings()
    return [key for key in ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"] if not settings[key].strip()]


def smtp_ready():
    return not smtp_missing()


def smtp_error_message(exc):
    """Classify failures without exposing server text, recipient addresses or secrets."""
    code = getattr(exc, "smtp_code", None)
    suffix = f" (SMTP {code})" if isinstance(code, int) else ""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "SMTP authentication failed" + suffix + ". For Gmail, use a new 16-character Google App Password for SMTP_USER, not your normal Google password."
    if isinstance(exc, ssl.SSLError):
        return "The secure SMTP connection failed. Check the server certificate and your Python certificate installation."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server rejected the recipient address. Check the user's email address."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "The mail server rejected SMTP_FROM" + suffix + ". Use the authenticated Gmail address or a verified sending alias."
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "The SMTP server closed the connection. Check your Gmail App Password and account settings, then use Check email connection to retry."
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return "Could not connect to the SMTP server. Check the host, port, network and firewall."
    if isinstance(exc, smtplib.SMTPResponseException):
        return "The mail server rejected the email" + suffix + ". Check provider sending limits and account permissions."
    if isinstance(exc, smtplib.SMTPException):
        return "The SMTP connection failed. Check the host, port and STARTTLS support."
    if isinstance(exc, (ValueError, TypeError)):
        return "SMTP settings are invalid. Check the numeric SMTP_PORT and email addresses."
    return "Email delivery failed. Check the SMTP configuration and try again."


@contextmanager
def smtp_connection(settings):
    port = int(settings["SMTP_PORT"] or "587")
    context = ssl.create_default_context(cafile=certifi.where())
    connection = smtplib.SMTP_SSL(settings["SMTP_HOST"], port, context=context, timeout=20) if port == 465 else smtplib.SMTP(settings["SMTP_HOST"], port, timeout=20)
    with connection as smtp:
        if port != 465:
            smtp.starttls(context=context)
        smtp.login(settings["SMTP_USER"], settings["SMTP_PASSWORD"])
        yield smtp


def check_smtp():
    """Authenticate only; never send an email."""
    if not smtp_ready():
        raise ValueError("Missing SMTP settings: " + ", ".join(smtp_missing()))
    try:
        with smtp_connection(smtp_settings()):
            pass
    except Exception as exc:
        raise ValueError(smtp_error_message(exc)) from None


def email_credentials(username, password):
    user = read_accounts()[username]
    if not smtp_ready():
        raise ValueError("Configure SMTP before sending account emails.")
    settings = smtp_settings()
    link = settings["CRM_URL"] or "http://localhost:8501"
    messages = [
        ("Your Intelligent CRM username", f"Hello {user['name']},\n\nUsername: {username}\nRole: {user['role']}\nSign in: {link}\nYour temporary password will arrive in a separate email."),
        ("Your Intelligent CRM temporary password", f"Your temporary password: {password}\n\nPlease change this password at your first sign-in."),
    ]
    sent = 0
    try:
        with smtp_connection(settings) as smtp:
            for subject, body in messages:
                msg = EmailMessage()
                msg["From"], msg["To"], msg["Subject"] = settings["SMTP_FROM"], user["email"], subject
                msg.set_content(body)
                smtp.send_message(msg)
                sent += 1
        update_account(username, email_status="Both emails accepted by SMTP")
    except Exception as exc:
        reason = smtp_error_message(exc)
        update_account(username, email_status=f"Delivery failed after {sent}/2 emails. {reason}")
        raise ValueError(f"{reason} {sent}/2 credential emails were accepted. The account password is already saved; correct SMTP, then reset and resend credentials.") from None


def send_account_reminder(actor_username, username, reset_password=False):
    """Email only the requested credential to the target's registered address."""
    require_administrator(actor_username)
    user = read_accounts().get(username)
    if not user:
        raise ValueError("That account does not exist.")
    if reset_password and username == actor_username:
        raise ValueError("Use Change password in the sidebar for your own account.")
    if not smtp_ready():
        raise ValueError("Configure SMTP before sending account emails.")
    settings = smtp_settings()
    changed = False
    try:
        # Authenticate first so invalid SMTP credentials do not reset a user's password.
        with smtp_connection(settings) as smtp:
            message = EmailMessage()
            message["From"], message["To"] = settings["SMTP_FROM"], user["email"]
            if reset_password:
                password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
                set_user_password(actor_username, username, password)
                changed = True
                message["Subject"] = "Your Intelligent CRM temporary password"
                message.set_content(f"Hello {user['name']},\n\nYour temporary password: {password}\n\nChange this password at your next sign-in.\nSign in: {settings['CRM_URL'] or 'http://localhost:8501'}")
            else:
                message["Subject"] = "Your Intelligent CRM username"
                message.set_content(f"Hello {user['name']},\n\nUsername: {username}\nRole: {user['role']}\nSign in: {settings['CRM_URL'] or 'http://localhost:8501'}")
            smtp.send_message(message)
    except Exception as exc:
        reason = smtp_error_message(exc)
        if changed:
            update_account(username, email_status="Password reset email failed. " + reason)
            reason += " The password was changed; retry Reset password & email to generate and send a new one."
        raise ValueError(reason) from None
    update_account(username, email_status=("Password reset email" if reset_password else "Username reminder") + " accepted by SMTP")


def normalize(df):
    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]
    if "Opportunity ID" in df.columns or "Opportunity Value" in df.columns:
        return normalize_pipeline(df)
    return normalize_legacy(df)


def normalize_legacy(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if df.columns.duplicated().any():
        raise ValueError("Column names must be unique.")
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError("Missing columns: " + ", ".join(sorted(missing)) + ". Download the template.")
    if len(df) > 10000:
        raise ValueError("Each workbook supports up to 10,000 opportunities.")
    df = df[COLUMNS].copy()
    for c in ["Opportunity", "Customer", "Stage", "Notes"]:
        df[c] = df[c].fillna("").astype(str).str.strip()
    if df[["Opportunity", "Customer"]].eq("").any().any():
        raise ValueError("Every opportunity needs an opportunity name and customer.")
    if not df["Stage"].isin(STAGES).all():
        raise ValueError("Stages must match the template's supported stages.")
    for c in ["Amount", "Probability"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        if df[c].isna().any() or not df[c].map(lambda x: float('-inf') < x < float('inf')).all():
            raise ValueError(f"{c} must contain finite numbers.")
    if (df.Amount < 0).any() or not df.Probability.between(0, 100).all():
        raise ValueError("Amounts must be nonnegative and probabilities between 0 and 100.")
    for c in ["Close Date", "Last Contact"]:
        original = df[c]
        parsed = pd.to_datetime(original, errors="coerce")
        nonblank = original.notna() & original.astype(str).str.strip().ne("")
        if (nonblank & parsed.isna()).any() or (c == "Close Date" and parsed.isna().any()):
            raise ValueError(f"{c} must contain valid dates" + (" (required)." if c == "Close Date" else "."))
        df[c] = parsed.dt.normalize()
    df.loc[df.Stage == "Closed Won", "Probability"] = 100
    df.loc[df.Stage == "Closed Lost", "Probability"] = 0
    return df


def normalize_pipeline(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if df.columns.duplicated().any():
        raise ValueError("Column names must be unique.")
    if "Industry Sector" not in df:
        df["Industry Sector"] = ""
    missing = set(PIPELINE_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError("Missing Pipeline.xlsx columns: " + ", ".join(sorted(missing)))
    if len(df) > 10000:
        raise ValueError("Each workbook supports up to 10,000 opportunities.")
    df = df[PIPELINE_COLUMNS].copy()
    for col in ["Opportunity ID", "Client Name", "Opportunity Name", "Open or Closed", "Contract Term", "Industry Sector", "Salesperson Username"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    if df[["Opportunity ID", "Client Name", "Opportunity Name"]].eq("").any().any():
        raise ValueError("Every row needs Opportunity ID, Client Name and Opportunity Name.")
    if df["Opportunity ID"].duplicated().any():
        raise ValueError("Opportunity IDs must be unique within your workbook.")
    statuses = {value.lower(): value for value in PIPELINE_STATUSES}
    df["Open or Closed"] = df["Open or Closed"].str.lower().map(statuses)
    if df["Open or Closed"].isna().any():
        raise ValueError("Open or Closed must be Open, Closed, Closed Won or Closed Lost.")
    for col in ["Opportunity Value", "Win Probability", "TCV", "ACV", "Margin"]:
        original = df[col]
        provided = original.notna() & original.astype(str).str.strip().ne("")
        values = pd.to_numeric(original, errors="coerce")
        if (provided & (values.isna() | ~values.map(lambda x: float('-inf') < x < float('inf')))).any():
            raise ValueError(f"{col} must contain finite numbers.")
        if col in ["Opportunity Value", "Win Probability"] and values.isna().any():
            raise ValueError(f"{col} is required.")
        if col in ["Opportunity Value", "TCV", "ACV"] and (values < 0).any():
            raise ValueError(f"{col} must be nonnegative.")
        if col == "Win Probability" and not values.between(0, 100).all():
            raise ValueError("Win Probability must be between 0 and 100 (percentage points).")
        df[col] = values
    for col in ["Creation Date", "Expected Closure Date"]:
        original = df[col]
        provided = original.notna() & original.astype(str).str.strip().ne("")
        parsed = pd.to_datetime(original, errors="coerce")
        if (provided & parsed.isna()).any() or (col == "Expected Closure Date" and parsed.isna().any()):
            raise ValueError(f"{col} must contain valid dates" + (" (required)." if col == "Expected Closure Date" else "."))
        df[col] = parsed.dt.normalize()
    if (df["Creation Date"].notna() & (df["Creation Date"] > df["Expected Closure Date"])).any():
        raise ValueError("Expected Closure Date cannot precede Creation Date.")
    df["Salesperson Username"] = df["Salesperson Username"].str.lower()
    df.loc[df["Open or Closed"] == "Closed Won", "Win Probability"] = 100
    df.loc[df["Open or Closed"] == "Closed Lost", "Win Probability"] = 0
    return df


def read_upload(content, username):
    workbook = pd.ExcelFile(io.BytesIO(content))
    for sheet in ["Sheet1", "Opportunities"]:
        if sheet in workbook.sheet_names:
            df = pd.read_excel(workbook, sheet_name=sheet, dtype={"Opportunity ID": str})
            return owned_opportunities(df.dropna(how="all"), username)
    raise ValueError("Upload a workbook with the Pipeline.xlsx Sheet1 sheet or the legacy Opportunities sheet.")


def owned_opportunities(df, username):
    clean = normalize(df)
    if "Salesperson Username" in clean:
        assigned = clean["Salesperson Username"]
        if (assigned.ne("") & assigned.ne(username)).any():
            raise ValueError("Salesperson Username must be blank or match your signed-in username.")
        clean["Salesperson Username"] = username
    return clean


def pipeline_table(df, username):
    """Present every salesperson workbook using the template's exact layout."""
    if "Opportunity Value" in df:
        return owned_opportunities(df, username)[PIPELINE_COLUMNS]
    legacy = normalize_legacy(df)
    result = pd.DataFrame(index=legacy.index, columns=PIPELINE_COLUMNS)
    result["Opportunity ID"] = [f"OPP-{index + 1:05d}" for index in range(len(legacy))]
    for source, target in {"Customer": "Client Name", "Opportunity": "Opportunity Name", "Amount": "Opportunity Value", "Close Date": "Expected Closure Date", "Probability": "Win Probability"}.items():
        result[target] = legacy[source]
    result["Open or Closed"] = legacy.Stage.where(legacy.Stage.isin(CLOSED_STAGES), "Open")
    result["Salesperson Username"] = username
    return normalize_pipeline(result)


def load_salesperson_table(username):
    df, version = load_workbook(username)
    return pipeline_table(df, username), version


def analytics_data(df):
    """Keep pipeline fields while providing common columns for the charts."""
    if "Opportunity Value" not in df:
        return df.copy()
    result = df.copy()
    for source, target in {"Client Name": "Customer", "Opportunity Name": "Opportunity", "Opportunity Value": "Amount", "Expected Closure Date": "Close Date", "Win Probability": "Probability", "Open or Closed": "Stage"}.items():
        result[target] = result[source]
    return result


def excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sheet = "Sheet1" if "Opportunity Value" in df.columns else "Opportunities"
        df.to_excel(writer, index=False, sheet_name=sheet)
        # Uploaded text must remain text, including values beginning with '='.
        for row in writer.book[sheet].iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                column = df.columns[cell.column - 1]
                if column in ["Opportunity Value", "TCV", "ACV", "Amount", "Weighted Amount"]:
                    cell.number_format = '$#,##0.00'
                elif column in ["Win Probability", "Margin", "Probability"]:
                    # Stored values are percentage points: 25 must display as 25%.
                    cell.number_format = '0.00"%"'
                elif column in ["Creation Date", "Expected Closure Date", "Close Date", "Last Contact"]:
                    cell.number_format = "yyyy-mm-dd"
    return output.getvalue()


def workbook_path(username):
    # Path derives only from validated account identifiers.
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
        raise ValueError("Invalid account identifier.")
    return DATA / f"opportunities_{username}.xlsx"


def load_workbook(username):
    path = workbook_path(username)
    if not path.exists():
        return normalize(pd.DataFrame(columns=PIPELINE_COLUMNS)), ""
    content = path.read_bytes()
    return owned_opportunities(pd.read_excel(io.BytesIO(content), dtype={"Opportunity ID": str}), username), hashlib.sha256(content).hexdigest()


def save_workbook(username, df, expected_version):
    init()
    path = workbook_path(username)
    clean = owned_opportunities(df, username)
    with FileLock(str(path) + ".lock"):
        if read_accounts(include_deleted=True).get(username, {}).get("deleted"):
            raise ValueError("This account was deleted. Its pipeline cannot be updated.")
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
        if current != expected_version:
            raise ValueError("This workbook changed in another session. Reload before saving.")
        if path.exists() and "Opportunity Value" in clean:
            previous = pd.read_excel(path, nrows=0)
            if "Opportunity Value" not in previous.columns:
                # Retain original notes, contact dates and detailed stages before conversion.
                backup = path.with_name(path.stem + ".legacy-backup.xlsx")
                if not backup.exists():
                    atomic_write(backup, path.read_bytes())
        atomic_write(path, excel_bytes(clean))


def all_opportunities():
    frames = []
    for username, user in read_accounts().items():
        if user["role"] == "Salesperson":
            source, version = load_workbook(username)
            df = analytics_data(pipeline_table(source, username))
            # Keep source row positions and versions private for safe subset edits.
            df["__row"] = range(len(df))
            df["__version"] = version
            df["Salesperson"] = user["name"] + " (" + username + ")"
            frames.append(df)
    if frames:
        return pd.concat(frames, ignore_index=True)
    empty = analytics_data(normalize_pipeline(pd.DataFrame(columns=PIPELINE_COLUMNS)))
    empty["Salesperson"] = pd.Series(dtype=str)
    empty["__row"] = pd.Series(dtype=int)
    empty["__version"] = pd.Series(dtype=str)
    return empty


def client_research_url(name):
    if pd.isna(name) or not str(name).strip():
        return ""
    name = str(name).strip()
    query = urlencode({"q": f'"{name}" company background employee count annual revenue'})
    # The fragment supplies the visible link label; only the encoded query reaches Google.
    return f"https://www.google.com/search?{query}#client={name}"


def linked_client_table(data):
    result = data.copy()
    if "Client Name" in result:
        result["Client Name"] = result["Client Name"].map(client_research_url)
    return result


def restore_client_names(edited, original):
    result = edited.copy()
    lookup = {client_research_url(name): name for name in original["Client Name"]}
    # Newly entered plain company names stay editable and become links after saving.
    result["Client Name"] = result["Client Name"].map(lambda value: lookup.get(value, value))
    return result


def visible_opportunities(df):
    """Use only template fields and the distinct computed weighted value."""
    columns = [column for column in PIPELINE_COLUMNS if column in df]
    if "Weighted Amount" in df:
        columns.append("Weighted Amount")
    return df[columns].copy().reset_index(drop=True)


def update_opportunity_rows(actor_username, original, edited):
    """Update changed rows in their source workbooks, never replacing filtered-out rows."""
    users = read_accounts()
    actor = users.get(actor_username)
    if not actor or not actor["active"]:
        raise ValueError("Your account is not active.")
    if len(original) != len(edited):
        raise ValueError("Use the main Opportunities table to add or delete rows.")
    before = original[PIPELINE_COLUMNS].reset_index(drop=True)
    after = edited[PIPELINE_COLUMNS].reset_index(drop=True)
    if not before["Salesperson Username"].equals(after["Salesperson Username"]):
        raise ValueError("An opportunity cannot be reassigned through this table.")
    same = (before.eq(after) | (before.isna() & after.isna())).all(axis=1)
    changes = original.reset_index(drop=True).loc[~same]
    if changes.empty:
        return 0
    owners = sorted(changes["Salesperson Username"].unique())
    for owner in owners:
        if actor["role"] == "Salesperson" and owner != actor_username:
            raise ValueError("You can only edit your own opportunities.")
        if actor["role"] not in ["Salesperson", "Executive Dashboard", "Company Administrator"]:
            raise ValueError("Your role cannot edit opportunities.")
        if owner not in users or users[owner]["role"] != "Salesperson":
            raise ValueError("The source salesperson account no longer exists.")
    pending = []
    with ExitStack() as locks:
        for owner in owners:
            locks.enter_context(FileLock(str(workbook_path(owner)) + ".lock"))
        for owner in owners:
            source, current = load_salesperson_table(owner)
            owner_changes = changes[changes["Salesperson Username"] == owner]
            if not owner_changes["__version"].eq(current).all():
                raise ValueError("A source workbook changed in another session. Refresh this table before saving.")
            for index, row in owner_changes.iterrows():
                position = int(row["__row"])
                if position < 0 or position >= len(source):
                    raise ValueError("The source row has changed. Refresh this table before saving.")
                source.loc[source.index[position], PIPELINE_COLUMNS] = after.loc[index, PIPELINE_COLUMNS]
            # Validate every affected workbook before writing any of them.
            clean = owned_opportunities(source, owner)
            pending.append((owner, excel_bytes(clean)))
        for owner, content in pending:
            path = workbook_path(owner)
            if "Opportunity Value" not in pd.read_excel(path, nrows=0).columns:
                backup = path.with_name(path.stem + ".legacy-backup.xlsx")
                if not backup.exists():
                    atomic_write(backup, path.read_bytes())
            atomic_write(path, content)
    return len(changes)


def sample_data():
    today = pd.Timestamp.today().normalize()
    return normalize(pd.DataFrame([
        ["Cloud migration", "Acme", 85000, "Proposal", 60, today + pd.Timedelta(days=30), today - pd.Timedelta(days=4), "Confirm procurement timeline"],
        ["Annual renewal", "Northstar", 42000, "Negotiation", 80, today + pd.Timedelta(days=15), today - pd.Timedelta(days=20), "Schedule follow-up"],
        ["Analytics rollout", "Vertex", 120000, "Qualified", 35, today + pd.Timedelta(days=60), today - pd.Timedelta(days=8), "Identify decision maker"],
        ["Support package", "Acme", 18000, "Closed Won", 100, today - pd.Timedelta(days=10), today - pd.Timedelta(days=10), "Signed"],
    ], columns=COLUMNS))
