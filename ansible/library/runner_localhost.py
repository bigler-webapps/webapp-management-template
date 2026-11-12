#!/usr/bin/python
"""Ensure localhost resolves through both runner loopback addresses."""

from __future__ import annotations

import ipaddress
import contextlib
import os
import tempfile
from pathlib import Path


def _record(line: str):
    content = line.rstrip("\r\n")
    body, marker, comment = content.partition("#")
    fields = body.split()
    if not fields:
        return None, [], body, marker, comment
    try:
        address = ipaddress.ip_address(fields[0])
    except ValueError:
        return None, fields, body, marker, comment
    return address, fields[1:], body, marker, comment


def _ensure_mapping(lines: list[str], address_text: str, hostname: str, newline: str) -> list[str]:
    target = ipaddress.ip_address(address_text)
    for index, line in enumerate(lines):
        address, aliases, body, marker, comment = _record(line)
        if address != target:
            continue
        if hostname.casefold() in {alias.casefold() for alias in aliases}:
            return lines

        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        trimmed_body = body.rstrip()
        spacing = body[len(trimmed_body):]
        updated = f"{trimmed_body} {hostname}{spacing}"
        if marker:
            updated += marker + comment
        lines[index] = updated + ending
        return lines

    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline
    lines.append(f"{address_text} {hostname}{newline}")
    return lines


def reconcile_hosts(content: str) -> str:
    """Return content with localhost on 127.0.0.1 and ::1, preserving records."""
    lines = content.splitlines(keepends=True)
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    lines = _ensure_mapping(lines, "127.0.0.1", "localhost", newline)
    lines = _ensure_mapping(lines, "::1", "localhost", newline)
    return "".join(lines)


def main():
    from ansible.module_utils.basic import AnsibleModule

    module = AnsibleModule(
        argument_spec={"path": {"type": "path", "default": "/etc/hosts"}},
        supports_check_mode=True,
    )
    path = Path(module.params["path"])
    try:
        original = path.read_bytes().decode("utf-8")
        updated = reconcile_hosts(original)
        changed = updated != original
        if changed and not module.check_mode:
            fd, temporary = tempfile.mkstemp(prefix=".runner-localhost-", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(updated.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                module.atomic_move(temporary, str(path), unsafe_writes=False)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temporary)
    except (OSError, UnicodeError) as exc:
        module.fail_json(msg=f"Could not reconcile {path}: {exc}")
    module.exit_json(changed=changed)


if __name__ == "__main__":
    main()
