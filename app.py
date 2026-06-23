"""
Streamlit web interface for the SQL schema explorer.

Lets the user view all tables, views, stored procedures, and functions
from the connected database, select which ones to include (checkboxes,
grouped by type with select-all), then generate an architecture .md
document covering only the selected objects.

Run with: streamlit run app.py
"""

import asyncio
import json
import os

import streamlit as st
from dotenv import load_dotenv, dotenv_values, set_key
from copilot import CopilotClient
from copilot.session import PermissionHandler

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import sys

# Project and config paths used for loading and saving connection settings.
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_PROJECT_DIR, ".env")
if not os.path.exists(_ENV_PATH):
    open(_ENV_PATH, "a", encoding="utf-8").close()
load_dotenv(dotenv_path=_ENV_PATH)

COPILOT_MODEL = os.environ.get("COPILOT_MODEL", "gpt-5-mini")

print("=== Copilot SDK config check ===")
print(f"Model: {COPILOT_MODEL!r}")
print("=================================")

import sys

# Compute the venv's python.exe relative to this script's location, so this
# works on any machine without hardcoding a specific drive/folder path.
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_VENV_PYTHON = os.path.join(_PROJECT_DIR, ".venv", "Scripts", "python.exe")

if os.path.exists(_VENV_PYTHON):
    SERVER_PYTHON = _VENV_PYTHON
else:
    # Fall back to whatever Python is currently running this Streamlit app
    # (e.g. if there's no .venv folder, or it's named differently here).
    SERVER_PYTHON = sys.executable

SERVER_SCRIPT = os.path.join(_PROJECT_DIR, "server.py")

print("=== Subprocess launch config ===")
print(f"Project dir: {_PROJECT_DIR}")
print(f"Server python: {SERVER_PYTHON} (exists: {os.path.exists(SERVER_PYTHON)})")
print(f"Server script: {SERVER_SCRIPT} (exists: {os.path.exists(SERVER_SCRIPT)})")
print("=================================")

SYSTEM_PROMPT = """You are a database architecture documentation generator.

You have tools to explore a SQL Server database: list its tables, views,
stored procedures, and functions, and inspect their structure/definitions.

The user has already chosen exactly which objects they want documented.
Only document the objects explicitly listed below -- do not call list_*
tools to discover additional objects, and do not include anything that
was not listed.

Produce a clear architecture document in Markdown with this structure:

# Database Architecture Documentation

## Overview
A short paragraph describing the apparent purpose of the selected objects.

## Tables
For each selected table: name, columns (with types), and a one-line
plain-English description of what it likely stores.

## Views
For each selected view: name and a one-line plain-English description.

## Stored Procedures
For each selected stored procedure: name, parameters, and a plain-English
summary of what it does (based on reading its actual T-SQL body).

## Functions
Same as stored procedures, if any were selected.

Call the detail tools (get_table_columns, get_object_definition) for every
object listed below before writing the document. Only output the final
Markdown document as your last message -- no commentary before or after.
"""


# ---------- Async helpers (Streamlit is sync, MCP/OpenAI calls are async) ----------

async def fetch_schema():
    """Connect to the MCP server once and pull all object lists."""
    server_params = StdioServerParameters(command=SERVER_PYTHON, args=[SERVER_SCRIPT], env=get_mcp_env())
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(tool_name):
                result = await session.call_tool(tool_name, {})
                if result.content:
                    return [item.text for item in result.content if hasattr(item, "text")]
                return []

            tables = await call("list_tables")
            views = await call("list_views")
            sps = await call("list_stored_procedures")
            functions = await call("list_functions")

            return {
                "tables": tables,
                "views": views,
                "stored_procedures": sps,
                "functions": functions,
            }


async def generate_doc(selected_objects: dict):
    """
    Use the Copilot SDK to generate the doc. The SDK connects to our
    MCP server directly (as a local/stdio server) and handles tool
    discovery + the call loop internally -- we just send one prompt.
    """
    selected_summary = json.dumps(selected_objects, indent=2)
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Document exactly these selected objects:\n{selected_summary}"
    )

    async with CopilotClient() as client:
        session = await client.create_session(
            on_permission_request=PermissionHandler.approve_all,
            model=COPILOT_MODEL,
            mcp_servers={
                "sql-schema-explorer": {
                    "type": "local",
                    "command": SERVER_PYTHON,
                    "args": [SERVER_SCRIPT],
                    "env": get_mcp_env(),
                    "tools": ["*"],
                },
            },
        )
        async with session:
            response = await session.send_and_wait(prompt)
            return response.data.content if response and response.data else "(no content returned)"


def run_async(coro):
    """Helper to run an async coroutine from inside sync Streamlit code."""
    return asyncio.run(coro)


def loading_banner(placeholder, text):
    """Render a visible animated loading banner into a placeholder slot."""
    placeholder.markdown(
        f'<div class="ssms-loading-banner"><div class="ssms-spinner"></div>{text}</div>',
        unsafe_allow_html=True,
    )


def is_connection_ready():
    info = st.session_state.connection_info
    return bool(info.get("server") and info.get("database") and info.get("user") and info.get("password"))


def get_mcp_env():
    env = os.environ.copy()
    info = st.session_state.connection_info
    env.update({
        "MCPTEST_SQL_SERVER": info["server"],
        "MCPTEST_SQL_DATABASE": info["database"],
        "MCPTEST_SQL_USER": info["user"],
        "MCPTEST_SQL_PASSWORD": info["password"],
    })
    return env


def save_connection_to_env(connection_info: dict):
    """Persist the current connection values to the .env file and process env."""
    set_key(_ENV_PATH, "MCPTEST_SQL_SERVER", connection_info["server"])
    set_key(_ENV_PATH, "MCPTEST_SQL_DATABASE", connection_info["database"])
    set_key(_ENV_PATH, "MCPTEST_SQL_USER", connection_info["user"])
    set_key(_ENV_PATH, "MCPTEST_SQL_PASSWORD", connection_info["password"])
    # Keep the running process env in sync too.
    os.environ["MCPTEST_SQL_SERVER"] = connection_info["server"]
    os.environ["MCPTEST_SQL_DATABASE"] = connection_info["database"]
    os.environ["MCPTEST_SQL_USER"] = connection_info["user"]
    os.environ["MCPTEST_SQL_PASSWORD"] = connection_info["password"]


def load_connection_from_env():
    """Load connection settings from .env or current environment."""
    values = dotenv_values(_ENV_PATH)
    return {
        "server": values.get("MCPTEST_SQL_SERVER") or os.environ.get("MCPTEST_SQL_SERVER", ""),
        "database": values.get("MCPTEST_SQL_DATABASE") or os.environ.get("MCPTEST_SQL_DATABASE", ""),
        "user": values.get("MCPTEST_SQL_USER") or os.environ.get("MCPTEST_SQL_USER", ""),
        "password": values.get("MCPTEST_SQL_PASSWORD") or os.environ.get("MCPTEST_SQL_PASSWORD", ""),
    }


def initialize_connection_form():
    if "conn_server" not in st.session_state:
        if st.session_state.connection_confirmed:
            st.session_state.conn_server = st.session_state.connection_info.get("server", "")
            st.session_state.conn_database = st.session_state.connection_info.get("database", "")
            st.session_state.conn_user = st.session_state.connection_info.get("user", "")
            st.session_state.conn_password = st.session_state.connection_info.get("password", "")
        else:
            st.session_state.conn_server = ""
            st.session_state.conn_database = ""
            st.session_state.conn_user = ""
            st.session_state.conn_password = ""


def reset_connection_form():
    for field in ["conn_server", "conn_database", "conn_user", "conn_password"]:
        if field in st.session_state:
            del st.session_state[field]


def show_connection_dialog():
    container = st.container()
    with container:
        st.markdown("### Connect to SQL Server")
        st.markdown("Fill in the SQL Server connection details below to continue.")

        initialize_connection_form()

        with st.form("connection_form"):
            st.text_input("Server", key="conn_server")
            st.text_input("Database", key="conn_database")
            st.text_input("Username", key="conn_user")
            st.text_input("Password", type="password", key="conn_password")
            save = st.form_submit_button("Save Connection")

        if save:
            new_info = {
                "server": st.session_state.get("conn_server", ""),
                "database": st.session_state.get("conn_database", ""),
                "user": st.session_state.get("conn_user", ""),
                "password": st.session_state.get("conn_password", ""),
            }
            st.session_state.connection_info = new_info
            save_connection_to_env(new_info)
            st.session_state.connection_confirmed = True
            st.session_state.editing_connection = False
            reset_connection_form()


# ---------------------------- Streamlit UI ----------------------------

import streamlit.components.v1 as components
from streamlit_tree_select import tree_select

st.set_page_config(page_title="DB Architecture Doc Generator", layout="centered")

if "connection_info" not in st.session_state:
    st.session_state.connection_info = {
        "server": os.environ.get("MCPTEST_SQL_SERVER", r"localhost\SQLEXPRESS"),
        "database": os.environ.get("MCPTEST_SQL_DATABASE", "test"),
        "user": os.environ.get("MCPTEST_SQL_USER", "mcptest_user"),
        "password": os.environ.get("MCPTEST_SQL_PASSWORD", ""),
    }
if "editing_connection" not in st.session_state:
    st.session_state.editing_connection = False
if "connection_confirmed" not in st.session_state:
    st.session_state.connection_confirmed = False

if not st.session_state.connection_confirmed:
    st.session_state.editing_connection = True
    show_connection_dialog()
    st.stop()

# ---- SSMS-style theming (light/classic: white background, blue accents,
# grid-like panels, Segoe UI font to match Windows/SSMS look) ----
SSMS_CSS = """
<style>
    /* Overall page background -- soft neutral grey behind the centered card */
    .stApp {
        background-color: #eef1f5;
        font-family: "Segoe UI", "Segoe UI Web", Tahoma, Arial, sans-serif;
        color: #1e1e1e;
    }

    /* Constrain and center the main content, like a floating card */
    .block-container {
        max-width: 880px;
        margin: 0 auto;
        padding-top: 32px;
        padding-bottom: 60px;
    }

    /* The whole app wrapped in a white card with shadow */
    .ssms-card-wrapper {
        background-color: #ffffff;
        border: 1px solid #d6d6d6;
        border-radius: 6px;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
        padding: 28px 32px 8px 32px;
    }

    /* Header bar styled like SSMS title/menu bar */
    .ssms-header {
        background-color: #007ACC;
        color: white;
        padding: 16px 22px;
        border-radius: 4px;
        margin-bottom: 22px;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .ssms-header h1 {
        color: white;
        font-size: 19px;
        font-weight: 600;
        margin: 0;
    }

    /* Panel/card look for sections, like SSMS grid panels */
    .ssms-panel {
        border: 1px solid #d6d6d6;
        background-color: #f7f9fb;
        border-radius: 4px;
        padding: 18px 20px;
        margin-bottom: 18px;
    }
    .ssms-panel-title {
        font-size: 13px;
        font-weight: 600;
        color: #003c6c;
        text-transform: uppercase;
        letter-spacing: 0.4px;
        border-bottom: 1px solid #d6d6d6;
        padding-bottom: 8px;
        margin-bottom: 14px;
    }

    /* Buttons: SSMS blue accent */
    .stButton > button {
        background-color: #0078D4;
        color: white;
        border: 1px solid #0067b8;
        border-radius: 3px;
        padding: 7px 20px;
        font-size: 13px;
        font-weight: 500;
        width: 100%;
    }
    .stButton > button:hover {
        background-color: #006cbe;
        border-color: #005a9e;
        color: white;
    }
    .stButton > button:disabled {
        background-color: #cccccc;
        border-color: #bbbbbb;
        color: #777777;
    }

    /* Download button: secondary grey-blue style */
    .stDownloadButton > button {
        background-color: #e8edf2;
        color: #003c6c;
        border: 1px solid #b9c7d4;
        border-radius: 3px;
        font-weight: 500;
        width: 100%;
    }

    /* Status badge row */
    .ssms-statusbar {
        background-color: #107C10;
        color: white;
        font-size: 12px;
        padding: 5px 14px;
        border-radius: 3px;
        margin-top: 8px;
        display: inline-block;
    }

    /* Loading banner -- animated, clearly visible progress indicator */
    .ssms-loading-banner {
        background-color: #fff4ce;
        border: 1px solid #ffd335;
        color: #5c4400;
        font-size: 13px;
        font-weight: 500;
        padding: 12px 16px;
        border-radius: 4px;
        margin: 10px 0;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .ssms-spinner {
        width: 16px;
        height: 16px;
        border: 2.5px solid #ffd335;
        border-top: 2.5px solid #5c4400;
        border-radius: 50%;
        animation: ssms-spin 0.8s linear infinite;
        flex-shrink: 0;
    }
    @keyframes ssms-spin {
        to { transform: rotate(360deg); }
    }

    /* Markdown output area styled like a results grid */
    .ssms-results {
        border: 1px solid #d6d6d6;
        background-color: #ffffff;
        padding: 20px 24px;
        border-radius: 4px;
    }

    /* Section step labels */
    .ssms-step-label {
        color: #003c6c;
        font-weight: 600;
        font-size: 15px;
        margin-bottom: 6px;
    }
</style>
"""
st.markdown(SSMS_CSS, unsafe_allow_html=True)
st.markdown('<div class="ssms-card-wrapper">', unsafe_allow_html=True)

st.markdown(
    """
    <div class="ssms-header">
        <h1>&#128190; Database Architecture Documentation Generator</h1>
    </div>
    """,
    unsafe_allow_html=True,
)

if "schema" not in st.session_state:
    st.session_state.schema = None
if "generated_doc" not in st.session_state:
    st.session_state.generated_doc = None

# ---- Step 1: Connect & Load ----
st.markdown('<div class="ssms-panel">', unsafe_allow_html=True)
st.markdown('<div class="ssms-panel-title">Step 1 &middot; Connect &amp; Load Schema</div>', unsafe_allow_html=True)

conn = st.session_state.connection_info
st.markdown(f"**Connection target:** `{conn['server']}` / `{conn['database']}` / `{conn['user']}`", unsafe_allow_html=True)
if st.button("Edit Connection"):
    st.session_state.editing_connection = True

if st.button("Test Connection"):
    if not is_connection_ready():
        st.error("Please fill in server, database, username, and password before testing connection.")
    else:
        loading_slot = st.empty()
        loading_banner(loading_slot, "Testing connection to SQL Server…")
        try:
            st.session_state.schema = run_async(fetch_schema())
            loading_slot.empty()
            st.markdown('<span class="ssms-statusbar">Connected — schema loaded successfully</span>', unsafe_allow_html=True)
        except Exception as e:
            loading_slot.empty()
            st.error(f"Failed to load schema: {e}")

st.markdown('</div>', unsafe_allow_html=True)

selected = {"tables": [], "views": [], "stored_procedures": [], "functions": []}

if st.session_state.editing_connection:
    show_connection_dialog()
    st.stop()

if st.session_state.schema:
    schema = st.session_state.schema

    # ---- Step 2: Object Explorer (tree view) ----
    st.markdown('<div class="ssms-panel">', unsafe_allow_html=True)
    st.markdown('<div class="ssms-panel-title">Step 2 &middot; Object Explorer &mdash; Select Objects to Document</div>', unsafe_allow_html=True)

    group_labels = {
        "tables": "Tables",
        "views": "Views",
        "stored_procedures": "Stored Procedures",
        "functions": "Functions",
    }
    group_icons = {
        "tables": "\U0001F4CB",       # clipboard
        "views": "\U0001F441",         # eye
        "stored_procedures": "\u2699", # gear
        "functions": "\U0001F9EE",     # abacus
    }

    tree_nodes = []
    for group_key, label in group_labels.items():
        items = schema.get(group_key, [])
        children = [{"label": item, "value": f"{group_key}::{item}"} for item in items]
        tree_nodes.append({
            "label": f"{group_icons.get(group_key, '')}  {label} ({len(items)})",
            "value": f"group::{group_key}",
            "children": children,
        })

    tree_result = tree_select(
        tree_nodes,
        check_model="all",
        only_leaf_checkboxes=False,
        expanded=[f"group::{k}" for k in group_labels.keys()],
        no_cascade=False,
    )

    checked_values = tree_result.get("checked", []) if tree_result else []
    for val in checked_values:
        if "::" in val:
            group_key, item_name = val.split("::", 1)
            if group_key in selected:
                selected[group_key].append(item_name)

    st.markdown('</div>', unsafe_allow_html=True)

    # ---- Step 3: Generate ----
    st.markdown('<div class="ssms-panel">', unsafe_allow_html=True)
    st.markdown('<div class="ssms-panel-title">Step 3 &middot; Generate Documentation</div>', unsafe_allow_html=True)

    total_selected = sum(len(v) for v in selected.values())
    st.markdown(f'<span class="ssms-statusbar">{total_selected} object(s) selected</span>', unsafe_allow_html=True)
    st.write("")

    if st.button("Generate Architecture Document", disabled=(total_selected == 0)):
        loading_slot = st.empty()
        loading_banner(loading_slot, "Calling Azure OpenAI and exploring selected objects&hellip; this may take up to a minute.")
        try:
            doc = run_async(generate_doc(selected))
            st.session_state.generated_doc = doc
            loading_slot.empty()
            st.markdown('<span class="ssms-statusbar">Document generated successfully</span>', unsafe_allow_html=True)
        except Exception as e:
            loading_slot.empty()
            import traceback
            error_details = traceback.format_exc()
            print("=== FULL ERROR TRACEBACK ===")
            print(error_details)
            print("=============================")
            st.error(f"Failed to generate document: {e}")
            st.code(error_details, language="text")

    st.markdown('</div>', unsafe_allow_html=True)

if st.session_state.generated_doc:
    st.markdown('<div class="ssms-panel">', unsafe_allow_html=True)
    st.markdown('<div class="ssms-panel-title">Step 4 &middot; Result</div>', unsafe_allow_html=True)
    st.markdown('<div class="ssms-results">', unsafe_allow_html=True)
    st.markdown(st.session_state.generated_doc)
    st.markdown('</div>', unsafe_allow_html=True)
    st.write("")
    st.download_button(
        "Download as .md",
        data=st.session_state.generated_doc,
        file_name="architecture_doc.md",
        mime="text/markdown",
    )
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)  # close ssms-card-wrapper
