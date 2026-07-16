"""The workspace registry: the harness's map of real repositories.

Two things live here, and they are the reason this harness is not just the
writing harness with the nouns changed.

**Gates are exit codes.** The writing harness asked "is the draft >= 500
characters, are the [TODO] markers gone" — questions that mean nothing about
code. Here, whether `implementing` is done is decided by the target repo's own
lint/build/test, and the harness runs them itself. It does not ask the agent
how it went. An agent that reports its own grade is not being graded.

**Irreversibility is detected, not declared.** If verify output contains a
marker like `forces replacement`, the change destroys something. The harness
notices that from the output text, forces the repo's advisors to be consulted,
and takes proxy approval off the table. The agent is not consulted about
whether this applies to it.

Parsed with `tomllib` (stdlib, Python 3.11+): no third-party dependency, so the
harness installs on a machine where you may not control `pip`.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "workspace-registry.toml"
EXAMPLE_PATH = ROOT / "config" / "workspace-registry.example.toml"

# A verify command is someone's test suite; it can legitimately take minutes.
# But it must not hang the harness forever, so every run is bounded. Override
# per invocation if a repo genuinely needs longer.
DEFAULT_TIMEOUT_S = 900

# Output kept per check. Enough to see a failure and to scan for markers,
# bounded so a chatty test suite cannot bloat the task's artifacts.
MAX_CAPTURE_CHARS = 20_000


class RegistryError(RuntimeError):
    pass


@dataclass
class Repo:
    name: str
    path: Path
    role: str
    purpose: str = ""
    write: str = "read-only"
    remote: str = "none"
    branch_convention: str = ""
    entrypoints: list[str] = field(default_factory=list)
    verify: dict[str, str] = field(default_factory=dict)
    verify_ok_exit_codes: dict[str, list[int]] = field(default_factory=dict)
    advisors: list[str] = field(default_factory=list)
    irreversible_markers: list[str] = field(default_factory=list)

    @property
    def writable(self) -> bool:
        """Whether an agent may modify this repo at all. Note that even a
        writable repo is `agent-commit-only`: nothing in this harness pushes."""
        return self.write == "agent-commit-only"

    def exists(self) -> bool:
        return self.path.is_dir()


@dataclass
class VerifyResult:
    check: str
    command: str
    exit_code: int
    passed: bool
    duration_ms: int
    output: str
    irreversible_hits: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "irreversible_hits": self.irreversible_hits,
            "output_tail": self.output[-2000:] if self.output else "",
            **({"error": self.error} if self.error else {}),
        }


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


def load_registry(path: Path | None = None) -> dict[str, Any]:
    path = path or REGISTRY_PATH
    if not path.exists():
        raise RegistryError(
            f"No workspace registry at {path.name}. "
            f"Copy the example first:\n"
            f"  cp config/{EXAMPLE_PATH.name} config/{REGISTRY_PATH.name}"
        )
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise RegistryError(f"Invalid TOML in {path.name}: {exc}") from exc


def list_repos(path: Path | None = None) -> list[Repo]:
    data = load_registry(path)
    defaults = data.get("defaults", {})
    repos: list[Repo] = []
    for raw in data.get("repos", []):
        if not raw.get("name"):
            raise RegistryError("every [[repos]] entry needs a name")
        if not raw.get("path"):
            raise RegistryError(f'repo {raw["name"]}: path is required')
        repos.append(Repo(
            name=raw["name"],
            path=_expand(raw["path"]),
            role=raw.get("role", "reference"),
            purpose=raw.get("purpose", ""),
            # Deny by default: a repo is read-only unless it opts in. A typo in
            # `write` should fail closed, never open.
            write=raw.get("write", defaults.get("write", "read-only")),
            remote=raw.get("remote", "none"),
            branch_convention=raw.get("branch_convention", ""),
            entrypoints=raw.get("entrypoints", []),
            verify=raw.get("verify", {}),
            verify_ok_exit_codes=raw.get("verify_ok_exit_codes", {}),
            advisors=raw.get("advisors", defaults.get("advisors", [])),
            irreversible_markers=raw.get(
                "irreversible_markers", defaults.get("irreversible_markers", [])
            ),
        ))
    return repos


def get_repo(name: str, path: Path | None = None) -> Repo:
    """Resolve a repo by NAME. A DoD never names a path: paths differ between
    your machine and your work machine, the name does not."""
    repos = list_repos(path)
    for repo in repos:
        if repo.name == name:
            return repo
    known = ", ".join(r.name for r in repos) or "(registry is empty)"
    raise RegistryError(f"Unknown repo: {name!r}. Registered: {known}")


def scan_irreversible(text: str, markers: list[str]) -> list[str]:
    """Markers are matched case-insensitively: `terraform plan` says
    "must be destroyed", a migration tool may shout "DROP TABLE"."""
    low = text.lower()
    return [m for m in markers if m.lower() in low]


def run_check(
    repo: Repo,
    check: str,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> VerifyResult:
    """Run one verify check in `repo` and judge it.

    The command comes from the registry — the user's own file — and is run
    through a shell on purpose: real verify commands are shell (`a && b`,
    pipes, env prefixes). This is not an injection surface in the usual sense
    (you are executing your own config), but it *is* why the registry belongs
    outside version control and under your control alone.
    """
    command = repo.verify.get(check)
    if not command:
        raise RegistryError(f"repo {repo.name}: no verify.{check} defined")
    if not repo.exists():
        return VerifyResult(
            check=check, command=command, exit_code=-1, passed=False,
            duration_ms=0, output="",
            error=f"repo path does not exist: {repo.path}",
        )

    ok_codes = repo.verify_ok_exit_codes.get(check, [0])
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=repo.path,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        output = ((proc.stdout or "") + (proc.stderr or ""))[:MAX_CAPTURE_CHARS]
        exit_code = proc.returncode
        error = None
    except subprocess.TimeoutExpired:
        # A timeout is a failure, never a pass. A hung test suite must not be
        # able to wave a change through by simply never answering.
        return VerifyResult(
            check=check, command=command, exit_code=-1, passed=False,
            duration_ms=int((time.monotonic() - started) * 1000), output="",
            error=f"timed out after {timeout_s}s",
        )
    except OSError as exc:
        return VerifyResult(
            check=check, command=command, exit_code=-1, passed=False,
            duration_ms=int((time.monotonic() - started) * 1000), output="",
            error=f"could not run: {exc}",
        )

    return VerifyResult(
        check=check,
        command=command,
        exit_code=exit_code,
        passed=exit_code in ok_codes,
        duration_ms=int((time.monotonic() - started) * 1000),
        output=output,
        # Markers are scanned regardless of pass/fail: a `terraform plan` that
        # exits 2 (changes present) is a *pass* and is exactly where "forces
        # replacement" shows up. Only scanning failures would miss every real
        # case.
        irreversible_hits=scan_irreversible(output, repo.irreversible_markers),
        error=error,
    )


def run_verify(
    repo: Repo,
    checks: list[str] | None = None,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> list[VerifyResult]:
    """Run several checks. Every requested check runs even if an earlier one
    failed: you want the whole picture in one pass, not a bisect."""
    names = checks if checks is not None else list(repo.verify.keys())
    return [run_check(repo, c, timeout_s=timeout_s) for c in names if c in repo.verify]


def summarize(results: list[VerifyResult]) -> dict[str, Any]:
    hits: list[str] = []
    for r in results:
        for h in r.irreversible_hits:
            if h not in hits:
                hits.append(h)
    return {
        "passed": all(r.passed for r in results) if results else False,
        "checks": [r.as_dict() for r in results],
        "irreversible": bool(hits),
        "irreversible_hits": hits,
        # Consequence, not advice. When the harness sees a destructive change,
        # the secretary loses the right to approve it — this flag is what the
        # gate reads, and no confidence score overrides it.
        "requires_human": bool(hits),
    }


def git_state(repo: Repo) -> dict[str, Any]:
    """What `approved` actually asserts: the work is committed locally, and it
    has NOT been pushed. The harness has no push path; this is how it proves it
    did not grow one."""
    def git(*args: str) -> tuple[int, str]:
        try:
            p = subprocess.run(
                ["git", "-C", str(repo.path), *args],
                capture_output=True, text=True, timeout=30,
            )
            # rstrip("\n"), NOT strip(): `status --porcelain` encodes state in
            # two leading columns (" M path", "?? path"), so stripping leading
            # whitespace silently shifts every path by one character. No other
            # command used here emits leading whitespace, so this is safe for
            # all of them.
            return p.returncode, (p.stdout or "").rstrip("\n")
        except (OSError, subprocess.TimeoutExpired):
            return -1, ""

    rc, _ = git("rev-parse", "--git-dir")
    if rc != 0:
        return {"is_git_repo": False}

    _, dirty = git("status", "--porcelain")
    _, branch = git("branch", "--show-current")
    _, head = git("rev-parse", "--short", "HEAD")

    # Unpushed = commits on this branch with no upstream, or ahead of it.
    rc_up, upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc_up != 0:
        unpushed, has_upstream = None, False
    else:
        has_upstream = True
        _, count = git("rev-list", "--count", f"{upstream}..HEAD")
        unpushed = int(count) if count.isdigit() else None

    return {
        "is_git_repo": True,
        "branch": branch,
        "head": head,
        "clean": dirty == "",
        "dirty_files": [l[3:] for l in dirty.splitlines()] if dirty else [],
        "has_upstream": has_upstream,
        "unpushed_commits": unpushed,
    }
