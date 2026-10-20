"""Tiny stdio MCP server used by the tests: two tools, no side effects (MCP Python SDK 2.x)."""
import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

TOOLS = [
    types.Tool(name="list_notes", description="List note ids.",
               input_schema={"type": "object", "properties": {}}),
    types.Tool(name="read_note", description="Read one note by id.",
               input_schema={"type": "object", "properties": {"id": {"type": "string"}},
                             "required": ["id"]}),
]


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(ctx, params) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text="[]")])


server = Server("fake-notes", version="0.0.1", instructions="Call list_notes before read_note.",
                on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
