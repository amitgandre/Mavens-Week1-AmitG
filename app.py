from datetime import date, datetime
from zoneinfo import ZoneInfo
from uuid import uuid4
import hashlib
import importlib
import textwrap
from html import escape
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components
import crm

# Streamlit can rerun this file while retaining an older imported CRM module.
if not hasattr(crm, "linked_client_table") or not hasattr(crm, "SESSION_IDLE_SECONDS"):
    crm = importlib.reload(crm)

st.set_page_config(page_title="Intelligent CRM", page_icon="◈", layout="wide")
session_cookie = components.declare_component("crm_session_cookie", path=str(Path(__file__).resolve().parent / "components" / "session_cookie"))
styles = (Path(__file__).resolve().parent / "assets" / "styles.css").read_text()
st.markdown(f'<style>{styles}</style><footer class="crm-footer">Designed by Amit G</footer>', unsafe_allow_html=True)
px.defaults.color_discrete_sequence = ["#087F8C", "#274C77", "#3B82B8", "#D49A3A", "#6B8798", "#69B6AD", "#9BAFC3"]



MONEY_COLUMNS = ["Opportunity Value", "TCV", "ACV", "Weighted Amount"]


def display_table(data):
    result = crm.linked_client_table(data)
    for column in MONEY_COLUMNS:
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce") / 1_000_000
    return result


def restore_table(edited, original):
    result = crm.restore_client_names(edited, original)
    for column in MONEY_COLUMNS:
        if column in result:
            displayed = pd.to_numeric(result[column], errors="coerce")
            source = pd.to_numeric(original[column], errors="coerce")
            # Preserve untouched values exactly, avoiding rounding on repeated saves.
            result[column] = source.where(displayed.eq(source / 1_000_000), displayed * 1_000_000)
    return result


def login():
    st.markdown('<div class="crm-brand"><span class="crm-brand-mark">◈</span>Intelligent CRM</div>', unsafe_allow_html=True)
    overview, center = st.columns([1.25, 1], gap="large")
    with overview:
        st.markdown("""<section class="crm-hero">
        <div class="crm-eyebrow">Customer relationship management</div>
        <h1>Clarity for every<br>customer relationship.</h1>
        <p>Bring your opportunities, customer insights, and team performance into one focused workspace.</p>
        <span class="crm-chip">Sales workspace</span><span class="crm-chip">Executive insights</span>
        </section>
        <div class="crm-feature"><strong>Keep your pipeline moving</strong><p>Manage opportunities, update your Excel data, and identify the next step.</p></div>
        <div class="crm-feature"><strong>See the bigger picture</strong><p>Understand opportunity value, confidence, margins, and industry coverage.</p></div>""", unsafe_allow_html=True)
    with center:
        st.subheader("Welcome back")
        st.caption("Sign in to your business workspace.")
        if not crm.read_accounts():
            st.info("Create your first administrator from the project terminal: uv run python manage.py create-admin")
            st.stop()
        with st.form("login"):
            username = st.text_input("Username", placeholder="Enter your username")
            password = st.text_input("Password", type="password", placeholder="Enter your password")
            role = st.selectbox("Role", crm.ROLES)
            submit = st.form_submit_button("Sign in", use_container_width=True, type="primary")
        if submit:
            user, error = crm.authenticate(username, password, role)
            if error:
                st.error(error)
            else:
                st.session_state.user = user
                st.session_state.session_token = crm.create_session(user["username"])
                st.rerun()
    st.stop()


def password_form(user, required=False):
    st.subheader("Change your password")
    if required:
        st.info("Set your own password before accessing your workspace.")
    with st.form("password"):
        old = st.text_input("Current password", type="password")
        new = st.text_input("New password (12+ characters)", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submit = st.form_submit_button("Update password")
    if submit:
        try:
            if new != confirm:
                raise ValueError("The new passwords do not match.")
            crm.change_password(user["username"], old, new)
            st.session_state.user.update(crm.read_accounts()[user["username"]])
            crm.revoke_session(st.session_state.get("session_token", ""))
            st.session_state.session_token = crm.create_session(user["username"])
            st.rerun()
        except ValueError as e:
            st.error(str(e))


def metrics(df):
    opened = df[~df.Stage.isin(crm.CLOSED_STAGES)]
    a, b, c, d = st.columns(4)
    a.metric("Open opportunities", f"{len(opened):,}")
    b.metric("Open pipeline", f"${opened.Amount.sum() / 1_000_000:,.2f}M")
    c.metric("Weighted pipeline", f"${(opened.Amount * opened.Probability / 100).sum() / 1_000_000:,.2f}M")
    today = pd.Timestamp(datetime.now(ZoneInfo("America/Chicago")).date())
    tcv = pd.to_numeric(df.get("TCV", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    acv = pd.to_numeric(df.get("ACV", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    won = df["Probability"].eq(100) & df["Close Date"].le(today) & (tcv.ne(0) | acv.ne(0))
    d.metric("Won revenue", f"${df.loc[won, 'Amount'].sum() / 1_000_000:,.2f}M")
    st.caption("Financial values are displayed in millions of dollars ($M). Table edits use millions: enter 1.5 for $1,500,000. Excel stores dollars. Use one currency across all salespeople. Weighted pipeline = amount × probability; this is an estimate. Won revenue includes 100% probability opportunities with closure dates on or before today (America/Chicago), and nonzero TCV or ACV.")


def opportunity_columns(data=None):
    columns = {
        "Opportunity ID": st.column_config.TextColumn(required=True),
        "Client Name": st.column_config.LinkColumn(required=True, display_text=r"#client=([\s\S]*)$", help="Click to research company background, employee count and revenue on Google. To rename, replace the cell value with the company name."),
        "Opportunity Name": st.column_config.TextColumn(required=True),
        **{column: st.column_config.NumberColumn(min_value=0, format="$%.2fM", help="Enter millions of dollars: 1.5 means $1,500,000.", step=0.001, width="medium",
            required=column == "Opportunity Value") for column in ["Opportunity Value", "TCV", "ACV"]},
        "Weighted Amount": st.column_config.NumberColumn(format="$%.2fM", disabled=True, width="medium"),
        "Win Probability": st.column_config.NumberColumn(min_value=0, max_value=100,
            required=True, format="%.2f%%", width="medium", help="Enter 50 for 50%.", step=0.01),
        "Margin": st.column_config.NumberColumn(format="%.2f%%", width="small", help="Enter 25 for 25%.", step=0.01),
        "Open or Closed": st.column_config.SelectboxColumn(options=crm.PIPELINE_STATUSES, required=True),
        "Creation Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Expected Closure Date": st.column_config.DateColumn(required=True, format="YYYY-MM-DD"),
        "Industry Sector": st.column_config.TextColumn(),
        "Salesperson Username": st.column_config.TextColumn(disabled=True),
        "Contract Term": st.column_config.TextColumn(),
    }
    for name, config in columns.items():
        config["alignment"] = "center"
        if data is not None and name in data:
            def display(value):
                if pd.isna(value):
                    return ""
                if name in ["Opportunity Value", "TCV", "ACV", "Weighted Amount"]:
                    return f"${value / 1_000_000:,.2f}M"
                if name in ["Win Probability", "Margin"]:
                    return f"{value:.2f}%"
                if name in ["Creation Date", "Expected Closure Date"]:
                    return pd.Timestamp(value).strftime("%Y-%m-%d")
                return str(value)
            longest = max([len(name)] + [len(display(value)) for value in data[name]])
            config["width"] = max(110, longest * 9 + 48)
    return columns


def invalidate_opportunity_views():
    for name in list(st.session_state):
        if name.startswith("snapshot_"):
            del st.session_state[name]
    st.session_state.table_revision = st.session_state.get("table_revision", 0) + 1


def editable_opportunities(df, key, label):
    revision = st.session_state.get("table_revision", 0)
    membership = pd.util.hash_pandas_object(df[["Salesperson Username", "__row"]], index=False).values.tobytes()
    scope = hashlib.sha256(membership).hexdigest()[:12]
    generation = f"{key}_{scope}_{revision}"
    snapshot_key = f"snapshot_{generation}"
    if snapshot_key not in st.session_state:
        st.session_state[snapshot_key] = df.copy()
    original = st.session_state[snapshot_key]
    st.caption("Dollar amounts are shown and edited in millions ($M); Excel retains values in dollars.")
    with st.form(f"form_{generation}"):
        edited_links = st.data_editor(display_table(crm.visible_opportunities(original)), hide_index=True, row_height=30,
            num_rows="fixed", use_container_width=True, column_config=opportunity_columns(crm.visible_opportunities(original)),
            key=f"million_table_{generation}")
        save = st.form_submit_button(f"Save {label} to Excel", type="primary")
    edited = restore_table(edited_links, crm.visible_opportunities(original))
    if save:
        try:
            count = crm.update_opportunity_rows(st.session_state.user["username"], original, edited)
            if count:
                if st.session_state.user["role"] == "Salesperson":
                    latest, version = crm.load_salesperson_table(st.session_state.user["username"])
                    if st.session_state.opportunity_draft.equals(st.session_state.sheet):
                        st.session_state.opportunity_draft = latest.copy()
                        st.session_state.version = version
                        st.session_state.editor_generation = st.session_state.get("editor_generation", 0) + 1
                    st.session_state.sheet = latest
                invalidate_opportunity_views()
                st.session_state.table_notice = f"Saved {count} updated opportunities to their Excel workbooks."
                st.rerun()
            else:
                st.info("No changes to save.")
        except ValueError as exc:
            st.error(str(exc))
    if st.button(f"Refresh {label}", key=f"refresh_{generation}"):
        invalidate_opportunity_views()
        st.rerun()


def compact_chart(figure):
    for axis in [figure.layout.xaxis, figure.layout.yaxis]:
        axis.automargin = True
    figure.update_layout(height=320, margin=dict(l=20, r=20, t=45, b=65),
        title_font_size=15, font_size=11,
        legend=dict(orientation="h", yanchor="top", y=-0.25, xanchor="center", x=0.5, font_size=10))
    for trace in figure.data:
        if "$" in (trace.hovertemplate or ""):
            field = "values" if trace.type == "pie" else ("x" if trace.orientation == "h" else "y")
            setattr(trace, field, [value / 1_000_000 for value in getattr(trace, field)])
            trace.hovertemplate = trace.hovertemplate.replace(":,.2f}", ":,.2f}M")
            if trace.type == "bar":
                axis = figure.layout.xaxis if trace.orientation == "h" else figure.layout.yaxis
                axis.tickformat = ",.2f"
                axis.ticksuffix = "M"
                axis.title.text = axis.title.text.replace("($)", "($M)")
        if trace.type == "pie":
            trace.text = ["<br>".join(textwrap.wrap(str(label), 22)) for label in trace.labels]
            trace.texttemplate = "%{text}<br>%{percent:.1%}"
            trace.textposition = "inside"
            trace.textfont = dict(size=11)
            figure.update_layout(showlegend=False)
        elif trace.type == "bar":
            labels = trace.y if trace.orientation == "h" else trace.x
            if labels is not None and all(isinstance(label, str) for label in labels):
                axis = figure.layout.yaxis if trace.orientation == "h" else figure.layout.xaxis
                axis.tickmode = "array"
                axis.tickvals = list(labels)
                axis.ticktext = ["<br>".join(textwrap.wrap(label, 22)) for label in labels]
    return figure


def charts(df, executive=False):
    if df.empty:
        st.info("Upload opportunities to see visualizations.")
        return
    filtered = df.copy()
    f1, f2 = st.columns(2)
    stages = f1.multiselect("Stages", crm.STAGES, default=crm.STAGES)
    if executive:
        owners = sorted(df.Salesperson.unique())
        selected = f2.multiselect("Salespeople", owners, default=owners)
        filtered = filtered[filtered.Salesperson.isin(selected)]
    filtered = filtered[filtered.Stage.isin(stages)]
    if filtered.empty:
        st.info("No opportunities match these filters.")
        return
    metrics(filtered)
    opened = filtered[~filtered.Stage.isin(crm.CLOSED_STAGES)].copy()
    opened["Weighted Amount"] = opened.Amount * opened.Probability / 100
    left, right = st.columns(2)
    stage_data = filtered.groupby("Stage", as_index=False).agg(Amount=("Amount", "sum"), Opportunities=("Opportunity", "count"))
    stage_chart = px.bar(stage_data, x="Stage", y="Amount", color="Stage", hover_data=["Opportunities"], category_orders={"Stage": crm.STAGES}, title="Pipeline value by stage")
    stage_chart.update_yaxes(tickprefix="$", tickformat=",.2f", title="Opportunity value ($)")
    stage_chart.update_traces(hovertemplate="%{x}<br>Opportunity value: $%{y:,.2f}<br>Opportunities: %{customdata[0]:,.0f}<extra></extra>")
    compact_chart(stage_chart).update_layout(showlegend=False)
    left.plotly_chart(stage_chart, use_container_width=True)
    if executive:
        totals = opened.groupby("Salesperson", as_index=False)[["Amount", "Weighted Amount"]].sum()
        salesperson_chart = px.bar(totals, y="Salesperson", x=["Amount", "Weighted Amount"], barmode="group", title="Pipeline by salesperson")
        salesperson_chart.update_xaxes(tickprefix="$", tickformat=",.2f", title="Pipeline value ($)")
        salesperson_chart.update_traces(hovertemplate="%{y}<br>%{fullData.name}: $%{x:,.2f}<extra></extra>")
        right.plotly_chart(compact_chart(salesperson_chart), use_container_width=True)
        bottom_left, bottom_right = st.columns(2)
        with bottom_right:
            sector_container, win_container = st.tabs(["Industry sector", "Win rate"])
        customer = opened.groupby("Customer", as_index=False).Amount.sum().nlargest(10, "Amount")
        customer_chart = px.bar(customer, x="Amount", y="Customer", title="Top 10 customers · open pipeline")
        customer_chart.update_xaxes(tickprefix="$", tickformat=",.2f", title="Opportunity value ($)")
        customer_chart.update_traces(hovertemplate="%{y}<br>Opportunity value: $%{x:,.2f}<extra></extra>")
        bottom_left.plotly_chart(compact_chart(customer_chart), use_container_width=True)
        closed = filtered[filtered.Stage.isin(["Closed Won", "Closed Lost"])]
        if not closed.empty:
            rates = closed.assign(Won=closed.Stage.eq("Closed Won")).groupby("Salesperson").agg(Wins=("Won", "sum"), Closed=("Won", "count")).reset_index()
            rates["Win rate (%)"] = 100 * rates.Wins / rates.Closed
            win_chart = px.bar(rates, x="Salesperson", y="Win rate (%)", hover_data=["Wins", "Closed"], range_y=[0, 100], title="Win rate · closed opportunities only")
            win_chart.update_yaxes(ticksuffix="%", tickformat=".1f")
            win_chart.update_traces(hovertemplate="%{x}<br>Win rate: %{y:.1f}%<br>Wins: %{customdata[0]:,.0f}<br>Closed: %{customdata[1]:,.0f}<extra></extra>")
            win_container.plotly_chart(compact_chart(win_chart), use_container_width=True)
        else:
            win_container.info("Close opportunities as Closed Won or Closed Lost to see win rates.")
    else:
        confidence_data = opened.assign(Confidence=pd.Categorical(
            opened["Probability"].map(lambda probability: "Below 50%" if probability < 50
                else "50–75%" if probability <= 75 else "Above 75%"),
            categories=["Below 50%", "50–75%", "Above 75%"], ordered=True,
        )).groupby("Confidence", observed=True, as_index=False).agg(
            Amount=("Amount", "sum"), Opportunities=("Opportunity", "count"))
        if confidence_data.Amount.sum() > 0:
            confidence_pie = px.pie(confidence_data, names="Confidence", values="Amount",
                hover_data=["Opportunities"], title="Opportunity value by confidence",
                color="Confidence", color_discrete_map={
                    "Below 50%": "#F59E0B", "50–75%": "#274C77", "Above 75%": "#10B981"})
            confidence_pie.update_traces(textinfo="label+percent", texttemplate="%{label}<br>%{percent:.1%}", sort=False,
                hovertemplate="%{label}<br>Opportunity value: $%{value:,.2f}<br>Share: %{percent:.1%}<br>Opportunities: %{customdata[0]:,.0f}<extra></extra>")
            right.plotly_chart(compact_chart(confidence_pie), use_container_width=True)
            right.caption("Slices show each confidence level’s share of total open opportunity value.")
        else:
            right.info("Add open opportunities with a positive value to see the confidence pie chart.")
        bottom_left, bottom_right = st.columns(2)
        margins = opened["Margin"].dropna() if "Margin" in opened else pd.Series(dtype=float)
        if not margins.empty:
            margin_bands = pd.cut(margins,
                bins=[float("-inf"), 0, 10, 20, 30, 40, 50, float("inf")], right=False,
                labels=["Below 0%", "0–<10%", "10–<20%", "20–<30%", "30–<40%", "40–<50%", "50% or higher"])
            margin_counts = margin_bands.value_counts(sort=False).rename_axis("Margin band").reset_index(name="Opportunities")
            margin_chart = px.bar(margin_counts, x="Margin band", y="Opportunities",
                text="Opportunities", title="Opportunity distribution by margin",
                color_discrete_sequence=["#274C77"])
            margin_chart.update_yaxes(dtick=1, tickformat=",.0f", title="Number of open opportunities")
            margin_chart.update_traces(texttemplate="%{y:,.0f}", hovertemplate="Margin: %{x}<br>Opportunities: %{y:,.0f}<extra></extra>")
            bottom_left.plotly_chart(compact_chart(margin_chart), use_container_width=True)
            bottom_left.caption(f"Margin uses percentage points (25 means 25%). {len(opened) - len(margins)} opportunities with no margin are excluded.")
        else:
            bottom_left.info("Enter Margin values in your opportunities to see the margin distribution chart (25 means 25%).")
    if not executive:
        sector_container = bottom_right
    if "Industry Sector" in opened and opened.Amount.sum() > 0:
        sectors = opened["Industry Sector"].fillna("").astype(str).str.strip().replace("", "Unspecified")
        sector_totals = opened.assign(Sector=sectors).groupby("Sector", as_index=False).agg(
            Amount=("Amount", "sum"), Opportunities=("Opportunity", "count"))
        sector_totals = sector_totals[sector_totals.Amount > 0]
        sector_pie = px.pie(sector_totals, names="Sector", values="Amount",
            custom_data=["Opportunities"], title="Opportunity amount by industry sector",
            color_discrete_sequence=px.colors.qualitative.Set2)
        sector_pie.update_traces(textinfo="label+percent", texttemplate="%{label}<br>%{percent:.1%}",
            hovertemplate="%{label}<br>Opportunity amount: $%{value:,.2f}<br>Share: %{percent:.1%}<br>Opportunities: %{customdata[0]:,.0f}<extra></extra>")
        sector_container.plotly_chart(compact_chart(sector_pie), use_container_width=True)
        sector_container.caption("Slices show each industry's share of open opportunity value. Blank industry sectors are grouped as Unspecified.")
    else:
        sector_container.info("Add open opportunities with an Industry Sector and a positive value to see the industry pie chart.")
    st.subheader("Opportunity intelligence")
    st.caption("Rule-based suggestions using saved opportunity dates, values and probabilities. Closed without an outcome is excluded from win rates.")
    today = pd.Timestamp(date.today())
    overdue = opened[opened["Close Date"] < today]
    stale = opened[opened["Last Contact"].isna() | (opened["Last Contact"] < today - pd.Timedelta(days=14))] if "Last Contact" in opened else opened.iloc[:0]
    if "Opportunity ID" in opened and "Last Contact" in opened:
        stale = stale[stale["Opportunity ID"].isna()]
    if not overdue.empty:
        st.warning(f"{len(overdue)} open opportunities have passed their close date. Update the forecast or confirm next steps.")
    if not stale.empty:
        st.warning(f"{len(stale)} open opportunities have no contact recorded in the last 14 days. Prioritize a follow-up.")
    if "Last Contact" not in opened:
        st.caption("Pipeline.xlsx has no Last Contact field, so follow-up alerts are unavailable for these rows.")
    if overdue.empty and stale.empty:
        st.success("No overdue close dates or recorded stale contacts in the selected pipeline.")
    editable_opportunities(opened.sort_values("Weighted Amount", ascending=False).head(10), "intelligence", "intelligence changes")
    if not overdue.empty:
        with st.expander("Review overdue opportunities"):
            editable_opportunities(overdue, "overdue", "overdue changes")
    if not stale.empty:
        with st.expander("Review follow-up opportunities"):
            editable_opportunities(stale, "followup", "follow-up changes")


def salesperson(user):
    st.subheader("Your opportunity workspace")
    username = user["username"]
    if "sheet" not in st.session_state:
        st.session_state.sheet, st.session_state.version = crm.load_salesperson_table(username)
    if list(st.session_state.sheet.columns) != crm.PIPELINE_COLUMNS:
        st.session_state.sheet = crm.pipeline_table(st.session_state.sheet, username)
        st.session_state.editor_generation = st.session_state.get("editor_generation", 0) + 1
    if "notice" in st.session_state:
        st.success(st.session_state.pop("notice"))
    with st.expander("Upload an Excel workbook", expanded=st.session_state.sheet.empty):
        st.caption("Use Sheet1 from Pipeline.xlsx. Import replaces your saved opportunity list. All template columns are retained; additional sheets and formatting are not imported.")
        st.download_button("Download Pipeline.xlsx template", crm.PIPELINE_TEMPLATE.read_bytes(), "Pipeline.xlsx")
        st.caption("Win Probability uses 0–100. Open or Closed accepts Open, Closed, Closed Won or Closed Lost. Leave Salesperson Username blank to use your account.")
        upload = st.file_uploader("Opportunity workbook (.xlsx)", type=["xlsx"])
        replace = st.checkbox("Replace my saved opportunities with this upload")
        if st.button("Import workbook", disabled=upload is None or not replace):
            try:
                incoming = crm.read_upload(upload.getvalue(), username)
                crm.save_workbook(username, incoming, st.session_state.version)
                st.session_state.sheet, st.session_state.version = crm.load_salesperson_table(username)
                st.session_state.opportunity_draft = st.session_state.sheet.copy()
                st.session_state.opportunity_row_checks = []
                st.session_state.editor_generation = st.session_state.get("editor_generation", 0) + 1
                invalidate_opportunity_views()
                st.session_state.notice = "Workbook imported and saved."
                st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")
    if "opportunity_draft" not in st.session_state:
        st.session_state.opportunity_draft = st.session_state.sheet.copy()
    if list(st.session_state.opportunity_draft.columns) != crm.PIPELINE_COLUMNS:
        st.session_state.opportunity_draft = st.session_state.opportunity_draft.reindex(columns=crm.PIPELINE_COLUMNS)
        st.session_state.editor_generation = st.session_state.get("editor_generation", 0) + 1
    edit_tab, visual_tab = st.tabs(["Opportunities", "Visualizations"])
    with edit_tab:
        st.caption("Dollar amounts are shown and edited in millions ($M): enter 1.5 for $1,500,000. Click a client name to research it on Google. Check the Select box beside each row to delete multiple rows, or use Select all rows. Row changes remain a draft until you click Save changes to Excel. Visualizations reflect saved data.")
        with st.form("opportunities"):
            draft = st.session_state.opportunity_draft
            with st.container(horizontal=True, gap="small"):
                add_row = st.form_submit_button("Add new row")
                select_all = st.form_submit_button("Select all rows", disabled=draft.empty)
                clear_selection = st.form_submit_button("Clear selection", disabled=draft.empty)
                delete_rows = st.form_submit_button("Delete selected rows", disabled=draft.empty)
            selectable = draft.copy()
            checks = st.session_state.get("opportunity_row_checks", [])
            selectable.insert(0, "Select", pd.Series(checks if len(checks) == len(draft) else [False] * len(draft), index=draft.index, dtype=bool))
            editor_result = st.data_editor(display_table(selectable), num_rows="fixed", hide_index=True, row_height=30, use_container_width=True, column_order=["Select"] + crm.PIPELINE_COLUMNS,
                key=f"million_pipeline_editor_{st.session_state.get('editor_generation', 0)}", column_config={
                    "Select": st.column_config.CheckboxColumn("Select", default=False, help="Check rows to delete them together.", alignment="center"),
                    **opportunity_columns(selectable),
                })
            save = st.form_submit_button("Save changes to Excel", type="primary")
        row_checks = editor_result["Select"].fillna(False).astype(bool).tolist()
        rows_to_delete = [index for index, checked in enumerate(row_checks) if checked]
        edited = restore_table(editor_result.drop(columns=["Select"]), draft)
        if select_all or clear_selection:
            st.session_state.opportunity_draft = edited.copy()
            st.session_state.opportunity_row_checks = [select_all] * len(edited)
            st.session_state.editor_generation = st.session_state.get("editor_generation", 0) + 1
            st.rerun()
        if add_row or delete_rows:
            updated = edited.copy()
            if add_row:
                row = {column: None for column in crm.PIPELINE_COLUMNS}
                row.update({"Opportunity ID": f"OPP-{uuid4().hex[:10].upper()}",
                    "Client Name": "", "Opportunity Name": "", "Opportunity Value": 0.0,
                    "Creation Date": pd.Timestamp(date.today()), "Expected Closure Date": pd.Timestamp(date.today()),
                    "Win Probability": 0.0, "Open or Closed": "Open", "Contract Term": "",
                    "Salesperson Username": username})
                updated = pd.concat([updated, pd.DataFrame([row])], ignore_index=True)
                row_checks.append(False)
            elif rows_to_delete:
                updated = updated.drop(index=updated.index[rows_to_delete]).reset_index(drop=True)
                row_checks = [False] * len(updated)
            else:
                st.warning("Select at least one row to delete.")
            if add_row or rows_to_delete:
                st.session_state.opportunity_draft = updated
                st.session_state.opportunity_row_checks = row_checks
                st.session_state.editor_generation = st.session_state.get("editor_generation", 0) + 1
                st.rerun()
        if save:
            st.session_state.opportunity_draft = edited.copy()
            st.session_state.opportunity_row_checks = row_checks
            try:
                crm.save_workbook(username, edited, st.session_state.version)
                st.session_state.sheet, st.session_state.version = crm.load_salesperson_table(username)
                st.session_state.opportunity_draft = st.session_state.sheet.copy()
                st.session_state.opportunity_row_checks = []
                st.session_state.editor_generation = st.session_state.get("editor_generation", 0) + 1
                invalidate_opportunity_views()
                st.session_state.notice = "Changes saved to your Excel workbook."
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        st.download_button("Download saved Excel", crm.excel_bytes(st.session_state.sheet), f"opportunities_{username}.xlsx")
        if st.button("Reload saved workbook (discard unsaved edits)"):
            st.session_state.sheet, st.session_state.version = crm.load_salesperson_table(username)
            st.session_state.opportunity_draft = st.session_state.sheet.copy()
            st.session_state.opportunity_row_checks = []
            st.session_state.editor_generation = st.session_state.get("editor_generation", 0) + 1
            invalidate_opportunity_views()
            st.rerun()
    with visual_tab:
        st.button("Refresh visualizations")
        saved, saved_version = crm.load_salesperson_table(username)
        chart_data = crm.analytics_data(saved)
        chart_data["__row"] = range(len(chart_data))
        chart_data["__version"] = saved_version
        charts(chart_data)


def admin():
    st.subheader("Account Administration")
    if "account_notice" in st.session_state:
        st.success(st.session_state.pop("account_notice"))
    configured = crm.smtp_ready()
    if not configured:
        st.info("Fill these settings in .env.smtp: " + ", ".join(crm.smtp_missing()) + ". Save the file and refresh this page.")
    if st.button("Check email connection", disabled=not configured):
        try:
            crm.check_smtp()
            st.success("SMTP authentication succeeded. No email was sent.")
        except ValueError as exc:
            st.error(str(exc))
    with st.form("new_account", clear_on_submit=True):
        a, b = st.columns(2)
        name, email = a.text_input("Full name"), b.text_input("Email")
        username, role = a.text_input("Username"), b.selectbox("Assigned role", crm.ROLES)
        password = a.text_input("Temporary password (12+ characters)", type="password")
        confirm = b.text_input("Confirm temporary password", type="password")
        submit = st.form_submit_button("Create account", disabled=not configured, type="primary")
    if submit:
        try:
            if password != confirm:
                raise ValueError("The passwords do not match.")
            key = crm.create_account(name, username, email, role, password)
            crm.email_credentials(key, password)
            st.success("Account created successfully.")
        except ValueError as e:
            st.error(str(e))
    users = crm.read_accounts()
    st.caption("Use the buttons beside each account to email its registered address. Password resets require a change at the next sign-in.")
    header = st.columns([1.5, 1.1, 1.8, 1.5, 2.8], gap="small")
    for column, label in zip(header, ["Name", "Username", "Email", "Role / access", "Actions"]):
        column.caption(label)
    for account_username, account_user in users.items():
        with st.container(border=True, key=f"account_row_{account_username}"):
            name_col, username_col, email_col, role_col, actions = st.columns([1.5, 1.1, 1.8, 1.5, 2.8], gap="small")
            name_col.write(account_user["name"])
            username_col.write(account_username)
            email_col.write(account_user["email"])
            role_col.write(account_user["role"])
            role_col.caption("Active" if account_user["active"] else "Inactive")
            with actions:
                with st.container(horizontal=True, gap="small"):
                    reset_email = st.button("Reset password & email", key=f"reset_email_{account_username}",
                        disabled=not configured or account_username == st.session_state.user["username"])
                    resend_username = st.button("Resend username", key=f"resend_username_{account_username}", disabled=not configured)
                st.caption(account_user["email_status"])
            if reset_email or resend_username:
                try:
                    crm.send_account_reminder(st.session_state.user["username"], account_username, reset_password=reset_email)
                    st.session_state.account_notice = ("Password reset and email accepted by the mail server." if reset_email
                        else "Username email accepted by the mail server.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    with st.expander("Change a user's password"):
        others = [key for key in users if key != st.session_state.user["username"]]
        st.caption("Choose a new temporary password. The user must change it at sign-in. Existing sessions will be signed out on their next interaction.")
        if others:
            with st.form("admin_password", clear_on_submit=True):
                selected = st.selectbox("User", others)
                new_password = st.text_input("New temporary password (12+ characters)", type="password")
                confirm_password = st.text_input("Confirm new temporary password", type="password")
                send_email = st.checkbox("Email updated credentials in two separate emails", value=configured, disabled=not configured)
                reset = st.form_submit_button("Set user password", type="primary")
            if reset:
                try:
                    if new_password != confirm_password:
                        raise ValueError("The passwords do not match.")
                    crm.set_user_password(st.session_state.user["username"], selected, new_password)
                    st.success("Password updated. The user must change it at their next sign-in.")
                    if send_email:
                        crm.email_credentials(selected, new_password)
                        st.success("Both credential emails were accepted by the mail server.")
                except ValueError as e:
                    st.error(str(e))
        else:
            st.info("Create another account to manage its password. Use Change password in the sidebar for your own account.")
    with st.expander("Change user email address"):
        target = st.selectbox("Account for email change", list(users))
        with st.form("change_user_email"):
            new_email = st.text_input("New email address", value=users[target]["email"], key=f"email_for_{target}")
            st.caption("Future username and password emails will go to this registered address.")
            update_email = st.form_submit_button("Update email address", type="primary")
        if update_email:
            try:
                crm.change_user_email(st.session_state.user["username"], target, new_email)
                st.session_state.account_notice = "Registered email address updated."
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    manageable = [key for key in users if key != st.session_state.user["username"]]
    with st.expander("Change user role"):
        if manageable:
            target = st.selectbox("Account for role change", manageable)
            new_role = st.selectbox("New role", crm.ROLES, index=crm.ROLES.index(users[target]["role"]), key=f"role_for_{target}")
            st.caption("The user must sign in with their new role. Their password remains the same.")
            if st.button("Change role", type="primary"):
                try:
                    crm.manage_account(st.session_state.user["username"], target, role=new_role)
                    st.session_state.account_notice = "User role updated."
                    invalidate_opportunity_views()
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        else:
            st.info("Create another account to change its role.")
    with st.expander("Delete account"):
        if manageable:
            target = st.selectbox("Account to delete", manageable)
            st.caption("Removes the account from active users and blocks sign-in. Their pipeline moves to Archived pipelines, where administrators can view and download it. The username cannot be reused.")
            confirmed = st.checkbox(f"Confirm deletion of {target}", key=f"delete_confirm_{target}")
            if st.button("Delete account", disabled=not confirmed, type="primary"):
                try:
                    crm.manage_account(st.session_state.user["username"], target, delete=True)
                    st.session_state.account_notice = "Account deleted."
                    invalidate_opportunity_views()
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        else:
            st.info("No other accounts to delete.")
    with st.expander("Activate or deactivate account"):
        others = [k for k in users if k != st.session_state.user["username"]]
        if others:
            selected = st.selectbox("Account to update", others)
            if st.button("Toggle account access"):
                crm.update_account(selected, active=not users[selected]["active"])
                st.rerun()

    st.subheader("Archived pipelines")
    try:
        archives = crm.archived_pipelines(st.session_state.user["username"])
        if archives:
            st.dataframe(archives, hide_index=True, row_height=30, use_container_width=True,
                column_config={name: st.column_config.Column(alignment="center") for name in archives[0]})
            owner = st.selectbox("Archived salesperson", [record["Username"] for record in archives],
                format_func=lambda key: next(f"{record['Name']} ({key})" for record in archives if record["Username"] == key))
            archived_table, content = crm.read_archived_pipeline(st.session_state.user["username"], owner)
            st.caption("Archived pipelines are retained as historical records.")
            st.dataframe(display_table(archived_table), hide_index=True, row_height=30, use_container_width=True, column_config=opportunity_columns(archived_table))
            st.download_button("Download archived pipeline", content,
                file_name=f"archived_pipeline_{owner}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.info("No archived pipelines yet. Deleting an account with a saved pipeline moves it here.")
    except ValueError as exc:
        st.error(str(exc))


# Upgrade already signed-in connections to the persistent idle-limited session.
if "user" in st.session_state and not st.session_state.get("session_token"):
    existing = st.session_state.user
    account = crm.read_accounts().get(existing["username"])
    if account and account["active"] and account["role"] == existing["role"] and account["password_hash"] == existing["password_hash"]:
        st.session_state.session_token = crm.create_session(existing["username"])
if "user" not in st.session_state:
    token = st.context.cookies.get("crm_session", "")
    restored = crm.resume_session(token)
    if restored:
        st.session_state.user = restored
        st.session_state.session_token = token
event = st.session_state.get("browser_session")
token = st.session_state.get("session_token", "")
fresh_event = isinstance(event, dict) and event.get("token") == token and event.get("id") != st.session_state.get("last_session_event")
if fresh_event:
    st.session_state.last_session_event = event["id"]
    if event.get("kind") == "expired":
        crm.revoke_session(token)
session = crm.resume_session(token, touch=fresh_event and event.get("kind") == "activity",
    activity_at=event.get("activity_at") if fresh_event else None) if token else None
if token and not session:
    crm.revoke_session(token)
    st.session_state.clear()
    st.session_state.session_notice = "You were signed out because your session expired. Please sign in again."
session_cookie(token=st.session_state.get("session_token", ""),
    last_activity=session["session_last_activity"] if session else 0,
    idle_seconds=crm.SESSION_IDLE_SECONDS, key="browser_session")
if "user" not in st.session_state:
    if "session_notice" in st.session_state:
        st.info(st.session_state.pop("session_notice"))
    login()
user = st.session_state.user
current = crm.read_accounts().get(user["username"])
if not current or not current["active"] or current["role"] != user["role"] or current["password_hash"] != user["password_hash"]:
    crm.revoke_session(st.session_state.get("session_token", ""))
    st.session_state.clear()
    st.rerun()
user.update(current)
with st.sidebar:
    st.markdown('<div class="crm-sidebar-brand"><span>◈</span> Intelligent CRM</div><div class="crm-sidebar-tagline">Business workspace</div>', unsafe_allow_html=True)
    st.write(user["name"])
    st.caption(user["role"])
    st.divider()
    st.markdown(f"📊 **{user['role']}**")
    settings = st.checkbox("Change password")
    if st.button("Sign out", use_container_width=True):
        crm.revoke_session(st.session_state.get("session_token", ""))
        st.session_state.clear()
        st.rerun()
descriptions = {
    "Salesperson": "Manage relationships. Move opportunities forward.",
    "Executive Dashboard": "A clear view of your company's opportunity performance.",
    "Company Administrator": "Manage your people, permissions, and account access.",
}
st.markdown(f'<section class="crm-page-banner"><div class="crm-eyebrow">Intelligent CRM · Business workspace</div><h1>{escape(user["role"])}</h1><p>{descriptions[user["role"]]}</p></section>', unsafe_allow_html=True)
if "table_notice" in st.session_state:
    st.success(st.session_state.pop("table_notice"))
if user["temporary"] or settings:
    password_form(user, required=user["temporary"])
    st.stop()
if user["role"] == "Salesperson":
    salesperson(user)
elif user["role"] == "Executive Dashboard":
    st.caption("A consolidated view of saved opportunities from every salesperson.")
    st.button("Refresh company dashboard")
    df = crm.all_opportunities()
    charts(df, executive=True)
    with st.expander("All opportunities"):
        editable_opportunities(df, "all", "all opportunity changes")
        st.download_button("Download consolidated Excel", crm.excel_bytes(crm.visible_opportunities(df)), "company_opportunities.xlsx")
else:
    admin()
