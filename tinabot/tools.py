"""Tool schemas and executors for OpenAI-compatible providers.

Provides tools (Bash, Read, Write, Glob, Grep, WebFetch) with:
- OpenAI function-calling JSON schemas
- Async executor functions
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx
from loguru import logger

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function calling format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: dict[str, dict] = {
    "Bash": {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": (
                "Execute a bash command. Use for git, npm, system commands, "
                "running tests, etc. Output is truncated at 30k characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 120, max 600).",
                    },
                },
                "required": ["command"],
            },
        },
    },
    "Read": {
        "type": "function",
        "function": {
            "name": "Read",
            "description": (
                "Read a file from disk. Returns content with line numbers. "
                "Supports offset/limit for large files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to read.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start from (1-based).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of lines to read.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    "Write": {
        "type": "function",
        "function": {
            "name": "Write",
            "description": (
                "Write content to a file, creating parent directories as needed. "
                "Overwrites the file if it exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    "Glob": {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": (
                "Find files matching a glob pattern. Returns up to 200 matching paths."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": 'Glob pattern, e.g. "**/*.py" or "src/**/*.ts".',
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (defaults to cwd).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    "Grep": {
        "type": "function",
        "function": {
            "name": "Grep",
            "description": (
                "Search file contents using ripgrep (or grep fallback). "
                "Returns matching file paths or content lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search in (defaults to cwd).",
                    },
                    "glob": {
                        "type": "string",
                        "description": 'Glob to filter files, e.g. "*.py".',
                    },
                    "include_content": {
                        "type": "boolean",
                        "description": "If true, show matching lines instead of just file paths.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    "WebFetch": {
        "type": "function",
        "function": {
            "name": "WebFetch",
            "description": (
                "Fetch content from a URL. Returns the text/HTML content, "
                "truncated at 50k characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch.",
                    },
                },
                "required": ["url"],
            },
        },
    },
}


def get_tool_schemas(allowed_tools: list[str]) -> list[dict]:
    """Return tool schemas filtered by the allowed tools list."""
    return [
        TOOL_SCHEMAS[name]
        for name in allowed_tools
        if name in TOOL_SCHEMAS
    ]


def get_codex_tool_schemas(allowed_tools: list[str]) -> list[dict]:
    """Return tool schemas in Responses API format (type=function at top level)."""
    result = []
    for name in allowed_tools:
        schema = TOOL_SCHEMAS.get(name)
        if not schema:
            continue
        func = schema["function"]
        result.append({
            "type": "function",
            "name": func["name"],
            "description": func["description"],
            "parameters": func["parameters"],
        })
    return result


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------

MAX_OUTPUT = 30_000  # chars


async def _exec_bash(args: dict, cwd: str) -> str:
    command = args.get("command", "")
    if not command:
        return "Error: no command provided."
    timeout = min(args.get("timeout", 120), 600)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + f"\n... (truncated at {MAX_OUTPUT} chars)"
        exit_info = f"\n[exit code: {proc.returncode}]" if proc.returncode != 0 else ""
        return output + exit_info
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return f"Command timed out after {timeout}s."
    except Exception as e:
        return f"Error executing command: {e}"


async def _exec_read(args: dict, cwd: str) -> str:
    file_path = args.get("file_path", "")
    if not file_path:
        return "Error: no file_path provided."

    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = Path(cwd) / p

    if not p.exists():
        return f"Error: file not found: {p}"
    if not p.is_file():
        return f"Error: not a file: {p}"

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Error reading file: {e}"

    lines = text.splitlines()
    offset = max(args.get("offset", 1), 1) - 1  # 1-based to 0-based
    limit = args.get("limit", len(lines))
    selected = lines[offset : offset + limit]

    # Format with line numbers
    numbered = []
    for i, line in enumerate(selected, start=offset + 1):
        # Truncate very long lines
        if len(line) > 2000:
            line = line[:2000] + "..."
        numbered.append(f"{i:>6}\t{line}")

    result = "\n".join(numbered)
    if len(result) > MAX_OUTPUT:
        result = result[:MAX_OUTPUT] + f"\n... (truncated at {MAX_OUTPUT} chars)"
    return result


async def _exec_write(args: dict, cwd: str) -> str:
    file_path = args.get("file_path", "")
    content = args.get("content", "")
    if not file_path:
        return "Error: no file_path provided."

    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = Path(cwd) / p

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {p}"
    except Exception as e:
        return f"Error writing file: {e}"


async def _exec_glob(args: dict, cwd: str) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: no pattern provided."

    search_dir = Path(args.get("path", cwd)).expanduser()
    if not search_dir.is_dir():
        return f"Error: directory not found: {search_dir}"

    try:
        matches = sorted(search_dir.glob(pattern))[:200]
        if not matches:
            return "No files matched."
        return "\n".join(str(m) for m in matches)
    except Exception as e:
        return f"Error: {e}"


async def _exec_grep(args: dict, cwd: str) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: no pattern provided."

    search_path = args.get("path", cwd)
    include_content = args.get("include_content", False)
    glob_filter = args.get("glob", "")

    # Try ripgrep first, fall back to grep
    rg = shutil.which("rg")
    if rg:
        cmd_parts = [rg, "--no-heading"]
        if not include_content:
            cmd_parts.append("--files-with-matches")
        else:
            cmd_parts.append("--line-number")
        if glob_filter:
            cmd_parts.extend(["--glob", glob_filter])
        cmd_parts.extend(["--", pattern, search_path])
    else:
        cmd_parts = ["grep", "-r"]
        if not include_content:
            cmd_parts.append("-l")
        else:
            cmd_parts.append("-n")
        if glob_filter:
            cmd_parts.extend(["--include", glob_filter])
        cmd_parts.extend(["--", pattern, search_path])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace")
        if not output.strip():
            return "No matches found."
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + f"\n... (truncated at {MAX_OUTPUT} chars)"
        return output
    except asyncio.TimeoutError:
        return "Grep timed out after 30s."
    except Exception as e:
        return f"Error: {e}"


async def _exec_webfetch(args: dict, cwd: str) -> str:
    url = args.get("url", "")
    if not url:
        return "Error: no url provided."

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
            if len(text) > 50_000:
                text = text[:50_000] + f"\n... (truncated at 50k chars)"
            return text
    except Exception as e:
        return f"Error fetching URL: {e}"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_EXECUTORS = {
    "Bash": _exec_bash,
    "Read": _exec_read,
    "Write": _exec_write,
    "Glob": _exec_glob,
    "Grep": _exec_grep,
    "WebFetch": _exec_webfetch,
}


async def execute_tool(name: str, arguments: dict, cwd: str) -> str:
    """Execute a tool by name and return the result string."""
    executor = _EXECUTORS.get(name)
    if not executor:
        return f"Error: unknown tool '{name}'."
    logger.debug(f"Tool {name}: {arguments}")
    return await executor(arguments, cwd)
