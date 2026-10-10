# Intelligent CRM

A Python / uv / Streamlit application for Salespeople, Company Administrators and Executives. Each account has one assigned role; the login role must match it. Multiple accounts per role are supported.

## Run locally

```sh
uv sync
uv run python manage.py create-admin
uv run streamlit run app.py
```

Open http://localhost:8501 and sign in using your administrator credentials and the Company Administrator role. The bootstrap command prompts for your name, username, email and password; there are no default credentials. Add salesperson and executive accounts in Account Administration after configuring SMTP.

## Email configuration

### Gmail setup

Enable Google 2-Step Verification and create an [App Password](https://myaccount.google.com/apppasswords) named Intelligent CRM. App Passwords may be unavailable for some organization accounts; see [Google's account requirements](https://support.google.com/mail/answer/185833).

Run the local setup command and enter your sender address and App Password when prompted (the password is hidden):

```sh
uv run python configure_smtp.py
```

The command checks STARTTLS authentication against `smtp.gmail.com:587` without sending an email, then writes `.env.smtp` with owner-only permissions. This file is excluded from Git. It contains the SMTP secret required for sending email, separate from the CRM users' password hashes. The sender and SMTP username are the same Gmail address. The command checks that the App Password has 16 characters, identifies the failing connection stage, and saves nothing if verification fails. An alternative is `uv run python configure_smtp.py --port 465`, which uses TLS from connection start. To check both ports without credentials or sending email, run `uv run python smtp_network_check.py`.

The app automatically reads `.env.smtp` on each interaction. Save changes and refresh the page; local file settings take precedence over environment variables. You can start the app normally or explicitly load the file:

```sh
uv run --env-file .env.smtp streamlit run app.py
```

The administrator account creation button becomes available once the settings are loaded. Do not use your normal Google account password. Gmail's server settings are documented [here](https://support.google.com/mail/answer/7104828).

### Other SMTP providers

Set these environment variables in the terminal running Streamlit. Keep credentials outside source control. You can use a private `.env` file and run `uv run --env-file .env streamlit run app.py`.

```dotenv
SMTP_HOST=smtp.your-provider.example
SMTP_PORT=587
SMTP_USER=your-smtp-user
SMTP_PASSWORD=your-smtp-password
SMTP_FROM=crm@your-company.example
CRM_URL=http://localhost:8501
```

SMTP uses STARTTLS with certificate verification against the certifi trust bundle and authenticated delivery. Use **Check email connection** on Company Administration or `uv run python check_smtp.py` to test authentication without sending email. Error messages identify authentication, certificate, recipient, sender and connection failures without displaying credentials. Gmail App Password spaces are removed automatically. Administrators choose and confirm a temporary password (at least 12 characters) during account creation. Account creation sends the username and chosen temporary password in separate emails. SMTP acceptance is recorded; it does not guarantee inbox delivery. If sending fails after account creation, the account remains created, and the administrator can choose a new password and resend credentials under Change a user's password. Passwords are never written to a local mail outbox. All new accounts must change the temporary password on first sign-in.

## Administrator account management

**Change user email address** updates the registered address for any active-list account, including your own administrator account. Future username reminders and password emails use the new address; saving an address does not automatically send an email or change the password. **Change user role** assigns another account to a new persona; existing sessions become invalid on their next interaction if the role changes. Passwords remain the same. **Delete account** requires confirmation, removes the account from the visible user list and prevents sign-in. The account is archived in users.json with deleted=true and active=false. Its pipeline workbook is moved intact to `data/archive/`, excluded from executive opportunity totals, and its username stays reserved to prevent another user inheriting its data. Administrators cannot delete themselves or change their own role. **Archived pipelines** on the Company Administrator page lists deleted users’ saved pipelines, displays their opportunity tables with formatting, and downloads the original archived Excel file. Previously deleted accounts with retained workbooks are archived when this section loads. Accounts without saved workbooks have no archived file. Archived workbooks are historical records and cannot be edited.

## Administrator password management

Each account row has **Reset password & email** and **Resend username** buttons. Reset generates a random 12-character temporary password using letters and numbers, stores only its hash and emails just that password to the registered address. Resend username sends just the username and leaves the password unchanged. SMTP authentication is checked before resetting; if message submission fails afterward, retry the reset button. For your own administrator account, use the sidebar password-change form.

In Account Administration, open **Change a user's password**, select an account, enter and confirm a new temporary password, then click **Set user password**. Sending two credential emails is optional here, so administrators can reset an account even if SMTP is unavailable. The password is saved as a salted hash, previous sessions become invalid on their next interaction, and login throttling is cleared for that account. The user must choose their own password on their next sign-in. If email delivery fails, the changed password remains in effect; choose another password and resend to retry. For your own password, use **Change password** in the sidebar.

## Salesperson workflow

1. Download **Pipeline.xlsx template** from Upload an Excel workbook. This is the project's original `Pipeline.xlsx`, with its `Sheet1` worksheet and these exact headers:

   `Opportunity ID`, `Client Name`, `Opportunity Name`, `Opportunity Value`, `Creation Date`, `Expected Closure Date`, `Win Probability`, `Open or Closed`, `TCV`, `ACV`, `Contract Term`, `Margin`, `Industry Sector`, `Salesperson Username`.

2. Add opportunities to Sheet1. Opportunity ID, Client Name, Opportunity Name, Opportunity Value, Expected Closure Date, Win Probability and Open or Closed are required. IDs must be unique within the workbook. Creation Date, TCV, ACV, Contract Term, Margin and Industry Sector are optional.
3. Use a probability from 0 to 100 (percentage points). Status supports Open, Closed, Closed Won and Closed Lost, case-insensitively. Won/lost statuses normalize probability to 100/0. Closed without an explicit outcome is excluded from open pipeline and win rates.
4. Leave Salesperson Username blank to fill it from your signed-in account, or supply that account's username. Rows assigned to another salesperson are rejected.
5. Upload the `.xlsx` file and confirm replacement of your saved opportunity list. Use **Add new row** to insert a draft opportunity with a generated ID and your username. Fill its client and opportunity names and review its values and dates. To remove entire rows, check the **Select** box beside each row (or click **Select all rows**) and click **Delete selected rows**. **Clear selection** unchecks all rows. Selection checkboxes are only UI controls and are never saved to Excel. These actions retain your current cell edits but do not change the saved workbook until you click **Save changes to Excel**. Download the saved workbook afterward. **Reload saved workbook** discards unsaved changes.

All 14 columns are retained when saved, including TCV, ACV, Contract Term and Margin. Opportunity Value, TCV and ACV display as dollar amounts; Win Probability and Margin display as percentages; dates display as YYYY-MM-DD. The saved Excel workbook uses corresponding cell formats while retaining numeric values (50 means 50%). The saved sheet is named Sheet1. Amounts must be nonnegative; numeric optional fields may be blank. Use one currency throughout the company. Contract Term is stored as text without assuming a unit. Margin uses percentage points (25 means 25%) for the distribution chart. Blank margins are excluded from that chart; negative margins have their own band.

Additional sheets, workbook formatting and formulas are not preserved. The original file on your computer cannot be edited by the browser; the application updates its project-side workbook and offers an updated download. Import replaces existing opportunities rather than merging them. The template contains only headers and no sample opportunities. The salesperson table always displays the 14 Pipeline.xlsx columns in template order. Older Opportunities workbooks are mapped to that layout, with generated opportunity IDs and open stages mapped to Open. On the first save in the new layout, the original workbook is retained in a `.legacy-backup.xlsx` file to preserve original notes, contact dates and detailed stages. New accounts begin with the Pipeline.xlsx schema.

## Editing opportunity tables

Click the linked name in the **Client Name** column of any opportunity table to search Google for company background, employee count and annual revenue. Links open Google search results; the app does not automatically infer or store financial figures. Verify the matching company, source and reporting year before using a result. Client names remain editable in the grid: replace the cell value with the new plain company name and save. Research URLs are only display values; Excel stores the original company names.

Every opportunity table shows each Pipeline column once, without the chart aliases such as Amount, Customer or Probability. Weighted Amount appears once as a computed, read-only value where relevant. Dollar, percentage and date formats are consistent across views. Financial metric cards and chart axes/hover values use $ signs, comma separators and two decimal places. Pie shares and win rates display percent signs, and counts are formatted as integers.

You can edit the intelligence, overdue, follow-up and executive All opportunities tables and click their **Save ... to Excel** button. Salespeople edit their own opportunities; executives can update opportunities across salespeople. Saves route each changed row to its original salesperson workbook and preserve all rows omitted by filtering or the top-ten limit. Salesperson Username stays fixed. IDs may be edited but must remain unique within the source workbook. Use the main salesperson table for adding and deleting rows.

Each view retains a snapshot for conflict detection. If a source workbook changes, refresh that table before saving. Charts and computed weighted amounts refresh after a successful save. The consolidated Excel download contains only the template columns. Unsaved changes in the main table still require their own Save button.

## Visualizations and intelligence

Won revenue sums opportunity values where Win Probability is 100% and Expected Closure Date is on or before today in America/Chicago, and at least one of TCV or ACV is nonzero (blanks count as zero), regardless of Open or Closed status. It respects the current dashboard filters.

Salesperson: stage pipeline, a pie chart of opportunity value by confidence (below 50%, 50–75% inclusive, and above 75%), a bar chart counting open opportunities by margin band, a pie chart of open opportunity value by industry sector, overdue deals, stale contact alerts, and the highest weighted-value opportunities.

Executive: all salesperson workbooks consolidated, stage pipeline, pipeline by salesperson, top-customer concentration, and salesperson win rates based only on closed deals. An industry-sector pie chart shows the share of open opportunity value, grouping blank sectors as Unspecified. Filters and the All opportunities table support deeper review. Both dashboards use saved data. Pipeline.xlsx has no Last Contact column, so stale-contact alerts only apply to legacy opportunities that contain that field. Weighted pipeline values multiply open opportunity amount by probability and are estimates, not guarantees. Intelligence is deterministic and rule-based; no external AI service or API key is required.

## Storage and access

`data/users.json` stores names, usernames, email, role and salted scrypt password hashes. Hashing allows verification without storing decryptable passwords. `data/opportunities_<username>.xlsx` holds each salesperson's opportunities. `data/attempts.json` persists login throttling (five failed attempts per username in 15 minutes). Files are excluded from Git, created with restrictive permissions and written atomically under file locks. Concurrent workbook saves detect stale versions and require a reload.

Salespeople can access their own workbook only; executives can view and edit consolidated opportunities; administrators manage accounts. Account deactivation and password resets invalidate existing sessions on their next interaction. You may override the storage directory with `CRM_DATA_DIR`. Back up this directory, restricting access to whoever runs the application.

This first version serves one company from a single application instance with persistent local storage. Use HTTPS and a secured host when serving it beyond localhost. It does not yet include multi-company isolation, MFA, password recovery links, audit history, quotas/targets, or an external database. Separate password emails are implemented as requested; future onboarding can use expiring invitation links instead.

## Verification

```sh
uv run pytest
```

Tests cover role matching, password hashing and throttling, workbook persistence and isolation, concurrent save protection, spreadsheet text safety, separate email messages, password changes, and Streamlit rendering for all roles.

Implementation references: [Streamlit editable dataframes](https://docs.streamlit.io/develop/concepts/design/dataframes), [Streamlit uploads](https://docs.streamlit.io/develop/api-reference/widgets/st.file_uploader), [uv projects](https://docs.astral.sh/uv/guides/projects/).

Dollar amounts in dashboards and tables use millions ($M). When editing, enter 1.5 for $1,500,000. Excel imports, saved workbooks and downloads retain dollar values.

Browser refresh restores sign-in using an opaque cookie and a server-side session lasting up to 12 hours, with automatic logout after 15 minutes of inactivity. Browser activity keeps the session active; refreshing does not reset the idle deadline. Sign out revokes the session. Password and role changes, deactivation, and deletion invalidate existing sessions.
