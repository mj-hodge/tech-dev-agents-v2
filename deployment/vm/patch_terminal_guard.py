"""Monkey-patch Hermes terminal_tool to enforce command whitelist.

Run at gateway startup to wrap the terminal tool with the guard.
Deployed to /opt/agent/patch_terminal_guard.py.

Usage in cloud-init or deploy script:
    python3 /opt/agent/patch_terminal_guard.py
"""

import importlib
import json
import sys
import os

GUARD_PATH = "/opt/agent/terminal_guard.py"
TERMINAL_TOOL_PATH = "/opt/hermes-agent/tools/terminal_tool.py"
PATCH_MARKER = "# === TERMINAL GUARD PATCHED ==="


def patch():
    """Inject guard check at the top of terminal_tool function."""

    with open(TERMINAL_TOOL_PATH, "r") as f:
        source = f.read()

    # Remove existing patch blocks (marker-paired), not "first marker to last marker".
    # Removing first→last can delete unrelated code when duplicate markers exist.
    if PATCH_MARKER in source:
        lines = source.split("\n")
        cleaned: list[str] = []
        in_patch_block = False
        marker_count = 0

        for line in lines:
            if PATCH_MARKER in line:
                marker_count += 1
                in_patch_block = not in_patch_block
                continue
            if not in_patch_block:
                cleaned.append(line)

        if in_patch_block:
            print(
                "[patch_terminal_guard] ERROR: Unmatched patch marker pair in terminal_tool.py; "
                "refusing to patch to avoid truncating file."
            )
            sys.exit(1)

        if marker_count:
            source = "\n".join(cleaned)
            print("[patch_terminal_guard] Removed old patch block(s), re-applying fresh.")

    # Find the terminal_tool function body — inject guard after docstring
    # Look for the line: global _active_environments, _last_activity
    target = "    global _active_environments, _last_activity"
    if target not in source:
        print(f"[patch_terminal_guard] ERROR: Could not find target line in {TERMINAL_TOOL_PATH}")
        sys.exit(1)

    guard_code = f'''
    {PATCH_MARKER}
    # Terminal guard — restrict commands to whitelist (self-contained imports)
    # FAIL-OPEN on transient errors (import failure, disk I/O) to prevent deny-all lockouts.
    # The guard file itself is the security boundary; if it can't load, log loudly and allow.
    try:
        import sys as _gs
        import json as _gj
        if "/opt/agent" not in _gs.path:
            _gs.path.insert(0, "/opt/agent")
        from terminal_guard import check_command as _guard_check
        _guard_ok, _guard_reason = _guard_check(command)
        if not _guard_ok:
            import logging as _gl
            _gl.getLogger("terminal_guard").warning("DENIED: %s", command[:200])
            return _gj.dumps({{"output": _guard_reason, "exit_code": 1, "error": True}})
    except Exception as _guard_exc:
        # FAIL-OPEN: log the error loudly but allow the command through.
        # A broken guard should not brick the entire agent.
        import logging as _gl
        _gl.getLogger("terminal_guard").error("Guard FAIL-OPEN (command allowed): %s", _guard_exc)
    {PATCH_MARKER}
'''

    patched = source.replace(target, guard_code + "\n" + target)

    # Make sure json and sys imports exist at top of file
    if "import json" not in source:
        patched = "import json\n" + patched
    if "import sys" not in source:
        patched = "import sys\n" + patched

    with open(TERMINAL_TOOL_PATH, "w") as f:
        f.write(patched)

    print(f"[patch_terminal_guard] Patched {TERMINAL_TOOL_PATH} successfully.")


if __name__ == "__main__":
    patch()
