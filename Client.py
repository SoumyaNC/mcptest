"""
Minimal MCP client to test server.py.

Launches server.py as a subprocess, connects via MCP (stdio), and calls
each tool once so we can see real schema data come back.

Run with: py client.py
"""

import asyncio
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_VENV_PYTHON = os.path.join(_PROJECT_DIR, ".venv", "Scripts", "python.exe")
SERVER_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable
SERVER_SCRIPT = os.path.join(_PROJECT_DIR, "server.py")


async def print_result(label, session, tool_name, args=None):
    print(f"=== {label} ===")
    result = await session.call_tool(tool_name, args or {})
    if result.content:
        for item in result.content:
            print(item.text)
    else:
        print("(empty result)")
    print()


async def main():
    server_params = StdioServerParameters(
        command=SERVER_PYTHON,
        args=[SERVER_SCRIPT],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("=== Available tools ===")
            tools = await session.list_tools()
            for tool in tools.tools:
                print(f"- {tool.name}: {tool.description}")
            print()

            await print_result("list_tables", session, "list_tables")
            await print_result("list_views", session, "list_views")
            await print_result("list_stored_procedures", session, "list_stored_procedures")
            await print_result("list_functions", session, "list_functions")
            await print_result("get_table_columns('Users')", session, "get_table_columns", {"table_name": "Users"})
            await print_result("get_object_definition('GetUserOrders')", session, "get_object_definition", {"object_name": "GetUserOrders"})


if __name__ == "__main__":
    asyncio.run(main())