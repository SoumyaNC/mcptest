"""
Minimal MCP server for SQL Server schema exploration.

Exposes read-only tools to list tables, views, stored procedures,
functions, and fetch their definitions/structure.

Run with: py server.py   (but normally an MCP client launches this for you)
"""

import os
import pyodbc
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load .env from the same directory as this script, regardless of
# what directory the process was launched from.
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=_ENV_PATH)

# ---- Connection settings (hardcoded server/db for this test; we'll make
# this dynamic later when the tool needs to support "any" database).
# Credentials come from .env file, not hardcoded in source. ----
SERVER = r"localhost\SQLEXPRESS"
DATABASE = "test"
SQL_USER = os.environ.get("MCPTEST_SQL_USER", "mcptest_user")
SQL_PASSWORD = os.environ.get("MCPTEST_SQL_PASSWORD")

if not SQL_PASSWORD:
    raise RuntimeError(
        f"MCPTEST_SQL_PASSWORD not found. Looked for .env at: {_ENV_PATH}. "
        f"Make sure that file exists and contains MCPTEST_SQL_PASSWORD=..."
    )

CONN_STR = (
    f"DRIVER={{ODBC Driver 18 for SQL Server}};"
    f"SERVER={SERVER};"
    f"DATABASE={DATABASE};"
    f"UID={SQL_USER};"
    f"PWD={SQL_PASSWORD};"
    f"TrustServerCertificate=yes;"
)


def get_connection():
    return pyodbc.connect(CONN_STR)


mcp = FastMCP("sql-schema-explorer")


@mcp.tool()
def list_tables() -> list[str]:
    """List all user tables in the connected database."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TABLE_SCHEMA, TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """)
        return [f"{row.TABLE_SCHEMA}.{row.TABLE_NAME}" for row in cursor.fetchall()]


@mcp.tool()
def list_views() -> list[str]:
    """List all views in the connected database."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TABLE_SCHEMA, TABLE_NAME
            FROM INFORMATION_SCHEMA.VIEWS
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """)
        return [f"{row.TABLE_SCHEMA}.{row.TABLE_NAME}" for row in cursor.fetchall()]


@mcp.tool()
def list_stored_procedures() -> list[str]:
    """List all stored procedures in the connected database."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ROUTINE_SCHEMA, ROUTINE_NAME
            FROM INFORMATION_SCHEMA.ROUTINES
            WHERE ROUTINE_TYPE = 'PROCEDURE'
            ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
        """)
        return [f"{row.ROUTINE_SCHEMA}.{row.ROUTINE_NAME}" for row in cursor.fetchall()]


@mcp.tool()
def list_functions() -> list[str]:
    """List all functions in the connected database."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ROUTINE_SCHEMA, ROUTINE_NAME
            FROM INFORMATION_SCHEMA.ROUTINES
            WHERE ROUTINE_TYPE = 'FUNCTION'
            ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
        """)
        return [f"{row.ROUTINE_SCHEMA}.{row.ROUTINE_NAME}" for row in cursor.fetchall()]


@mcp.tool()
def get_table_columns(table_name: str) -> list[dict]:
    """
    Get column details for a given table.

    Args:
        table_name: name of the table (schema optional, e.g. 'Users' or 'dbo.Users')
    """
    name_only = table_name.split(".")[-1]
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
        """, name_only)
        return [
            {
                "column": row.COLUMN_NAME,
                "type": row.DATA_TYPE,
                "nullable": row.IS_NULLABLE,
                "max_length": row.CHARACTER_MAXIMUM_LENGTH,
            }
            for row in cursor.fetchall()
        ]


@mcp.tool()
def get_object_definition(object_name: str) -> str:
    """
    Get the full T-SQL definition of a stored procedure, view, or function.

    Args:
        object_name: name of the SP/view/function (schema optional)
    """
    name_only = object_name.split(".")[-1]
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT OBJECT_DEFINITION(OBJECT_ID(?)) AS definition", name_only)
        row = cursor.fetchone()
        if row and row.definition:
            return row.definition
        return f"No definition found for object: {object_name}"


if __name__ == "__main__":
    mcp.run()