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
from dotenv import load_dotenv
from openai import AzureOpenAI

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

AZURE_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
AZURE_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
AZURE_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")

print("=== Azure OpenAI config check ===")
print(f"Endpoint set: {bool(AZURE_ENDPOINT)} -> {AZURE_ENDPOINT}")
print(f"API key set: {bool(AZURE_API_KEY)} (length: {len(AZURE_API_KEY)})")
print(f"Deployment: {AZURE_DEPLOYMENT!r}")
print(f"API version: {AZURE_API_VERSION!r}")
print("==================================")

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
    server_params = StdioServerParameters(command=SERVER_PYTHON, args=[SERVER_SCRIPT])
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
    """Run the agentic loop, but scoped only to the selected objects."""
    server_params = StdioServerParameters(command=SERVER_PYTHON, args=[SERVER_SCRIPT])

    client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_API_KEY,
        api_version=AZURE_API_VERSION,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools = await session.list_tools()
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.inputSchema or {"type": "object", "properties": {}},
                    },
                }
                for t in mcp_tools.tools
            ]

            selected_summary = json.dumps(selected_objects, indent=2)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Document exactly these selected objects:\n{selected_summary}",
                },
            ]

            max_turns = 25
            for _ in range(max_turns):
                response = client.chat.completions.create(
                    model=AZURE_DEPLOYMENT,
                    messages=messages,
                    tools=openai_tools,
                    tool_choice="auto",
                )
                choice = response.choices[0]
                messages.append(choice.message.model_dump(exclude_none=True))

                if choice.message.tool_calls:
                    for tool_call in choice.message.tool_calls:
                        tool_name = tool_call.function.name
                        try:
                            tool_args = json.loads(tool_call.function.arguments or "{}")
                        except json.JSONDecodeError:
                            tool_args = {}
                        result = await session.call_tool(tool_name, tool_args)
                        if result.content:
                            result_text = "\n".join(
                                item.text for item in result.content if hasattr(item, "text")
                            )
                        else:
                            result_text = "(empty result)"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_text,
                        })
                    continue
                else:
                    return choice.message.content or "(no content returned)"

            return "Reached max turns without a final answer."


def run_async(coro):
    """Helper to run an async coroutine from inside sync Streamlit code."""
    return asyncio.run(coro)


def loading_banner(placeholder, text):
    """Render a visible animated loading banner into a placeholder slot."""
    placeholder.markdown(
        f'<div class="ssms-loading-banner"><div class="ssms-spinner"></div>{text}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------- Streamlit UI ----------------------------

import streamlit.components.v1 as components
from streamlit_tree_select import tree_select

st.set_page_config(page_title="DB Architecture Doc Generator", layout="centered")

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

if st.button("Load Database Schema"):
    loading_slot = st.empty()
    loading_banner(loading_slot, "Connecting via MCP and fetching schema&hellip;")
    try:
        st.session_state.schema = run_async(fetch_schema())
        loading_slot.empty()
        st.markdown('<span class="ssms-statusbar">Connected &mdash; schema loaded successfully</span>', unsafe_allow_html=True)
    except Exception as e:
        loading_slot.empty()
        st.error(f"Failed to load schema: {e}")

st.markdown('</div>', unsafe_allow_html=True)

selected = {"tables": [], "views": [], "stored_procedures": [], "functions": []}

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