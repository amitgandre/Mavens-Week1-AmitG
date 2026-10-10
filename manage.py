"""Bootstrap the first administrator without default credentials."""
import argparse
import getpass
import crm

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["create-admin"])
args = parser.parse_args()
if any(u["role"] == "Company Administrator" for u in crm.read_accounts().values()):
    parser.error("An administrator already exists. Use Account Administration to add another.")
name = input("Full name: ")
username = input("Username: ")
email = input("Email: ")
password = getpass.getpass("Password (12+ characters): ")
if password != getpass.getpass("Confirm password: "):
    raise SystemExit("Passwords do not match.")
try:
    crm.create_account(name, username, email, "Company Administrator", password, temporary=False)
except ValueError as exc:
    raise SystemExit(str(exc))
print("Administrator created. Run: uv run streamlit run app.py")
