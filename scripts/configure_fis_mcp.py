#!/usr/bin/env python3
"""Declare the public, read-only FIS MCP server in open-webui.env."""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


ENV_KEY = "TOOL_SERVER_CONNECTIONS"
SERVER_ID = "fis"
SERVER_URL = "https://fis--gunn0511.shu-dcc.net/mcp"
REQUIRED_TOOLS = {
    "list_supported_entry_years",
    "get_graduation_requirements",
    "search_courses",
    "check_graduation",
    "plan_remaining_courses",
}

FIS_CONNECTION: dict[str, Any] = {
    "url": SERVER_URL,
    "path": "",
    "type": "mcp",
    "auth_type": "none",
    "headers": None,
    "key": None,
    "config": {
        "enable": True,
        "access_grants": [
            {
                "principal_type": "user",
                "principal_id": "*",
                "permission": "read",
            }
        ],
    },
    "info": {
        "id": SERVER_ID,
        "name": "FIS 履修・卒業判定",
        "description": "情報科学部の科目検索、履修計画、進級・卒業要件を匿名・読み取り専用で確認します。",
    },
}


def mcp_request(method: str, request_id: int) -> dict[str, Any]:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": (
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "dccai-fis-config", "version": "1.0.0"},
                }
                if method == "initialize"
                else {}
            ),
        }
    ).encode()
    request = urllib.request.Request(
        SERVER_URL,
        data=payload,
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": "DCC-AI-MCP-Configurator/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode()
    data_lines = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
    if not data_lines:
        raise RuntimeError("FIS MCP returned no JSON-RPC data")
    result = json.loads(data_lines[-1])
    if "error" in result:
        raise RuntimeError(f"FIS MCP error: {result['error']}")
    return result


def verify_server() -> list[str]:
    initialized = mcp_request("initialize", 1)
    server_name = initialized.get("result", {}).get("serverInfo", {}).get("name")
    if server_name != "fis-graduation-checker":
        raise RuntimeError(f"Unexpected FIS MCP server name: {server_name!r}")

    listed = mcp_request("tools/list", 2)
    tools = listed.get("result", {}).get("tools", [])
    names = sorted(tool.get("name") for tool in tools if tool.get("name"))
    missing = sorted(REQUIRED_TOOLS.difference(names))
    if missing:
        raise RuntimeError(f"FIS MCP is missing required tools: {', '.join(missing)}")
    return names


def is_fis_connection(connection: dict[str, Any]) -> bool:
    info = connection.get("info") or {}
    return connection.get("type") == "mcp" and (
        info.get("id") == SERVER_ID or connection.get("url") == SERVER_URL
    )


def load_env(path: Path) -> tuple[list[str], list[dict[str, Any]], int | None]:
    lines = path.read_text(encoding="utf-8").splitlines()
    matches = [index for index, line in enumerate(lines) if line.startswith(f"{ENV_KEY}=")]
    if len(matches) > 1:
        raise RuntimeError(f"{ENV_KEY} is declared more than once")
    if not matches:
        return lines, [], None

    index = matches[0]
    raw_value = lines[index].split("=", 1)[1].strip()
    connections = json.loads(raw_value)
    if not isinstance(connections, list):
        raise RuntimeError(f"{ENV_KEY} is not a JSON list")
    return lines, connections, index


def write_env(path: Path, lines: list[str], connections: list[dict[str, Any]], index: int | None) -> None:
    value = json.dumps(connections, ensure_ascii=False, separators=(",", ":"))
    declaration = f"{ENV_KEY}={value}"
    if index is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(["# Open WebUI MCP Streamable HTTP servers", declaration])
    else:
        lines[index] = declaration

    original_mode = stat.S_IMODE(path.stat().st_mode)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write("\n".join(lines) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, original_mode)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def configure(path: Path, remove: bool, check: bool) -> None:
    lines, connections, index = load_env(path)
    conflicting = [
        connection
        for connection in connections
        if connection.get("type") == "mcp"
        and (connection.get("info") or {}).get("id") == SERVER_ID
        and connection.get("url") != SERVER_URL
    ]
    if conflicting:
        raise RuntimeError(f"MCP server id '{SERVER_ID}' is already used by another URL")

    preserved = [connection for connection in connections if not is_fis_connection(connection)]
    if remove:
        updated = preserved
        expected = False
    else:
        tool_names = verify_server()
        updated = [*preserved, FIS_CONNECTION]
        expected = True

    if not check and updated != connections:
        write_env(path, lines, updated, index)

    _, stored, _ = load_env(path)
    present = any(is_fis_connection(connection) for connection in stored)
    if present != expected:
        action = "absent" if remove else "present"
        raise RuntimeError(f"FIS MCP must be {action} in {path}")

    if remove:
        print("FIS MCP is absent; all other tool server connections were preserved")
    else:
        print(f"FIS MCP is declared for all authenticated users ({len(tool_names)} tools)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    configure(args.env_file, args.remove, args.check)


if __name__ == "__main__":
    main()
