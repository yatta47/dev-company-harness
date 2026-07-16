"""PreToolUse guard: stop writes that would fake a gate.

Runs on Write/Edit in Claude Code and, via its Claude-hook compatibility layer,
in Cursor CLI too (`PreToolUse` -> `preToolUse`; both confirmed firing).

Three things are protected, in descending order of how badly they break the
harness if an agent can touch them:

  1. `config/workspace-registry.*` — this file DEFINES the gates. An agent that
     can edit it can set `test = "true"` and pass everything forever. Nothing
     else here matters if this is writable.
  2. `verify.json` — the harness's own record of what the repo's commands
     actually did. It is evidence, not an artifact. An agent that can write it
     can report its own grade.
  3. `state.json` — the state machine's memory. Transitions go through the CLI
     so that they are validated and logged.

Then, per-stage artifacts: you may only write the thing the current stage is
for. Editing `plan.md` while in `reviewing` means the process went sideways —
return to `planning` and say why, on the record.

**Known limitation, stated plainly:** this sees Write/Edit, not Bash. An agent
with shell access can `echo ... > verify.json` and this will not stop it. The
Bash matcher below catches the naive shapes of that, but it is a speed bump,
not a wall — `python3 -c`, base64, a heredoc in a subshell all walk past it.
Treat tool restrictions as reducing accidents, not as containing an adversary.
The load-bearing guarantee of this harness is elsewhere: the harness has no
push path at all, so nothing here can reach the outside world regardless.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[1])).resolve()

# Which stage each artifact belongs to.
ARTIFACT_STATES = {
    "dod.json": {"dod"},
    "facts.json": {"researching"},
    "findings.md": {"researching"},
    "plan.md": {"planning"},
    "review.json": {"reviewing"},
    "review.md": {"reviewing"},
    "commit.md": {"packaging"},
}

# Never writable by an agent, in any stage, for any reason.
HARNESS_ONLY = {
    "state.json": (
        "state.json は状態機械の記憶です。直接編集せず "
        "`python3 -m harness.cli advance/return/abandon` を使ってください"
        "（検証とログを通すため）。"
    ),
    "verify.json": (
        "verify.json はハーネスが生成するゲートの証拠であり、成果物ではありません。"
        "`python3 -m harness.cli verify` が対象repoのコマンドを実際に走らせて書きます。"
        "エージェントが書けたら、それは自己採点です。読むのは構いません。"
    ),
}

REGISTRY_DENY = (
    "config/workspace-registry.toml はゲートの定義そのものです。"
    "作業者がこれを編集できると verify コマンドを骨抜きにして全ゲートを"
    "通せてしまうため、変更は人間が行います。"
)


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def _check_path(file_path: str) -> str | None:
    """Return a deny reason, or None to allow."""
    path = Path(file_path)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()

    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return None  # outside the harness (e.g. the target repo) — not ours to police

    parts = rel.parts

    # The registry defines the gates; protect it before anything else.
    if len(parts) >= 2 and parts[0] == "config" and parts[-1].startswith("workspace-registry"):
        if not parts[-1].endswith(".example.toml"):
            return REGISTRY_DENY

    if len(parts) < 3 or parts[0] != "tasks":
        return None

    slug, filename = parts[1], parts[-1]

    if filename in HARNESS_ONLY:
        return HARNESS_ONLY[filename]

    allowed_states = ARTIFACT_STATES.get(filename)
    if not allowed_states:
        return None

    state_path = ROOT / "tasks" / slug / "state.json"
    if not state_path.exists():
        return f"タスク {slug} の state.json がありません。Harness CLI でタスクを作成してください。"

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))["state"]
    except (OSError, json.JSONDecodeError, KeyError):
        return f"タスク {slug} の state.json を読み取れません。"

    if state not in allowed_states:
        return (
            f"{filename} は状態 {state} では編集できません。"
            f"許可状態: {', '.join(sorted(allowed_states))}。"
            f"必要なら `python3 -m harness.cli return --to <state> --reason ...` で"
            f"正しい工程へ差し戻してください（理由が記録されます）。"
        )
    return None


# Naive shell redirection into a protected file: `> verify.json`, `>> state.json`,
# `tee verify.json`. A speed bump for the obvious accident, not a containment
# boundary — see the module docstring.
_SHELL_WRITE = re.compile(
    r"(?:>>?|\btee\b(?:\s+-\w+)*)\s+[\"']?([^\s\"'|;&]+)",
)
_PROTECTED_NAMES = set(HARNESS_ONLY) | {"workspace-registry.toml"}


def _check_bash(command: str) -> str | None:
    for m in _SHELL_WRITE.finditer(command):
        target = m.group(1)
        if Path(target).name in _PROTECTED_NAMES:
            reason = HARNESS_ONLY.get(Path(target).name, REGISTRY_DENY)
            return f"シェル経由の書き込みも同じ理由で拒否します。{reason}"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail open: a broken hook must not block legitimate work

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}

    if tool == "Bash":
        reason = _check_bash(tool_input.get("command") or "")
    else:
        file_path = tool_input.get("file_path")
        reason = _check_path(file_path) if file_path else None

    if reason:
        deny(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
