from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .core import (
    ROOT,
    HarnessError,
    abandon,
    aggregate_metrics,
    create_task,
    get_current,
    list_tasks,
    load_state,
    load_workflow,
    record_decision,
    record_event,
    resolve_task,
    return_to,
    run_verify_and_record,
    set_current,
    transition,
    validate_task,
)


def _profile_path() -> Path:
    """The secretary's profile lives OUTSIDE the repo on purpose: .gitignore is
    a request, but a file that is not in the tree cannot be committed at all."""
    override = os.environ.get("DEV_HARNESS_PROFILE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "dev-harness" / "profile.md"


def print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def selected_slug(explicit: str | None) -> str:
    return explicit or get_current(required=True)


def cmd_doctor(_args: argparse.Namespace) -> int:
    checks = {
        "python": sys.version.split()[0],
        "root": str(ROOT),
        "workflow": (ROOT / "workflows" / "dev-task.json").exists(),
        "claude_settings": (ROOT / ".claude" / "settings.json").exists(),
        "agents": len(list((ROOT / ".claude" / "agents").glob("*.md"))),
        "skills": len(list((ROOT / ".claude" / "skills").glob("*/SKILL.md"))),
        "registry": (ROOT / "config" / "workspace-registry.toml").exists(),
        "profile": _profile_path().exists(),
        "git": shutil.which("git") is not None,
        "claude": shutil.which("claude") is not None,
    }
    checks["ok"] = all([
        checks["workflow"],
        checks["claude_settings"],
        checks["agents"] >= 5,
        checks["skills"] >= 8,
        checks["registry"],
    ])
    print_json(checks)
    return 0 if checks["ok"] else 1


def cmd_init(args: argparse.Namespace) -> int:
    path = create_task(args.seed, args.repo, title=args.title, slug=args.slug)
    state = load_state(path.name)
    print_json({
        "created": str(path),
        "active_task": path.name,
        "working_title": state["title"],
        "repo": state["repo"],
        "state": state["state"],
    })
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    rows = list_tasks()
    print_json({"count": len(rows), "tasks": rows})
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    slug = resolve_task(args.query)
    set_current(slug)
    state = load_state(slug)
    print_json({
        "active_task": slug,
        "title": state["title"],
        "state": state["state"],
    })
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    slug = selected_slug(args.slug)
    state = load_state(slug)
    definition = load_workflow()["states"][state["state"]]
    print_json({
        **state,
        "active_task": get_current(required=False),
        "owner": definition["owner"],
        "expected_artifacts": definition["artifacts"],
        "next_state": definition["next"],
    })
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    slug = selected_slug(args.slug)
    result = validate_task(slug)
    print_json({"task": slug, **result.as_dict()})
    return 0 if result.passed else 2


def cmd_advance(args: argparse.Namespace) -> int:
    slug = selected_slug(args.slug)
    old, new = transition(slug, by=args.by)
    suffix = f" (by {args.by})" if args.by else ""
    print(f"{slug}: {old} -> {new}{suffix}")
    return 0


def cmd_return(args: argparse.Namespace) -> int:
    slug = selected_slug(args.slug)
    old, new = return_to(slug, args.to, args.reason)
    print(f"{slug}: {old} -> {new}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    slug = selected_slug(args.slug)
    summary = run_verify_and_record(slug, args.checks or None, timeout_s=args.timeout)
    print_json(summary)
    # 2 = the checks ran and something failed; distinct from a usage error.
    return 0 if summary["passed"] else 2


def cmd_abandon(args: argparse.Namespace) -> int:
    slug = selected_slug(args.slug)
    old, new = abandon(slug, args.reason)
    print(f"{slug}: {old} -> {new}")
    return 0


def _opt_bool(value: str | None) -> bool | None:
    return None if value is None else value == "true"


def cmd_decision(args: argparse.Namespace) -> int:
    slug = selected_slug(args.slug)
    record_decision(
        slug,
        args.actor,
        args.question,
        args.decision,
        args.confidence,
        args.reason,
        stage=args.stage,
        matched_principles=args.matched_principles,
        ask_user=_opt_bool(args.ask_user),
        risk=args.risk,
        reversible=_opt_bool(args.reversible),
        basis=args.basis,
        kind=args.kind,
    )
    print("Decision recorded")
    return 0


def cmd_event(args: argparse.Namespace) -> int:
    slug = selected_slug(args.slug)
    event: dict = {"type": args.type}
    if args.stage:
        event["stage"] = args.stage
    else:
        try:
            event["stage"] = load_state(slug).get("state")
        except HarnessError:
            pass
    for attr in ("role", "outcome", "note", "model"):
        value = getattr(args, attr)
        if value is not None:
            event[attr] = value
    for attr in ("tokens", "tool_uses", "duration_ms", "attempt"):
        value = getattr(args, attr)
        if value is not None:
            event[attr] = value
    if args.data:
        try:
            extra = json.loads(args.data)
        except json.JSONDecodeError as exc:
            raise HarnessError(f"--data must be valid JSON: {exc}")
        if not isinstance(extra, dict):
            raise HarnessError("--data must be a JSON object")
        event.update(extra)
    record_event(slug, event)
    print("Event recorded")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    slug = selected_slug(args.slug)
    print_json(aggregate_metrics(slug))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from .dashboard import serve
    serve(host=args.host, port=args.port)
    return 0


def cmd_eval(_args: argparse.Namespace) -> int:
    from evals.run_evals import run
    return run()


def add_optional_slug(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slug", help="Internal override. Defaults to the active task.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dev-harness")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("init")
    p.add_argument("--seed", required=True, help="What you want done.")
    p.add_argument("--repo", required=True,
                   help="Repo NAME from the workspace registry (never a path).")
    p.add_argument("--title")
    p.add_argument("--slug")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("list")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("switch")
    p.add_argument("query")
    p.set_defaults(func=cmd_switch)

    p = sub.add_parser("status")
    add_optional_slug(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("validate")
    add_optional_slug(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("advance")
    add_optional_slug(p)
    p.add_argument("--by", help="Who decided the advance (e.g. secretary, user). Recorded in history.")
    p.set_defaults(func=cmd_advance)

    p = sub.add_parser("return")
    add_optional_slug(p)
    p.add_argument("--to", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_return)

    p = sub.add_parser("verify", help=(
        "Run the target repo's own lint/build/test and record verify.json. "
        "The harness runs them itself: a stage that asks the agent how its "
        "work went is not a gate."))
    add_optional_slug(p)
    p.add_argument("--check", action="append", dest="checks",
                   help="Check name from the registry's [repos.verify] "
                        "(repeatable). Omit to run all.")
    p.add_argument("--timeout", type=int, help="Per-check timeout in seconds.")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("abandon", help=(
        "Stop this task for good, with a reason. 'We looked into it and it "
        "should not be done' is a real outcome and belongs on the record."))
    add_optional_slug(p)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_abandon)

    p = sub.add_parser("decision")
    add_optional_slug(p)
    p.add_argument("--actor", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--decision", required=True)
    p.add_argument("--confidence", type=float, required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--stage", help="State the decision was made at. Defaults to current state.")
    p.add_argument("--principle", action="append", dest="matched_principles",
                   help="Judgment axis relied on (repeatable). From the secretary profile.")
    p.add_argument("--risk", choices=["low", "medium", "high"])
    p.add_argument("--reversible", choices=["true", "false"])
    p.add_argument("--ask-user", dest="ask_user", choices=["true", "false"])
    p.add_argument("--basis", help="What the decision was grounded on (required-in-spirit for approvals).")
    p.add_argument("--kind", choices=["judgment", "mechanical"],
                   help="judgment = a real values fork; mechanical = a routine gate pass.")
    p.set_defaults(func=cmd_decision)

    p = sub.add_parser("event", help="Append one observability event (agent cost/duration logged by the orchestrator).")
    add_optional_slug(p)
    p.add_argument("--type", required=True,
                   help="Event type, e.g. agent_run, note.")
    p.add_argument("--stage", help="State the event belongs to. Defaults to current state.")
    p.add_argument("--role", help="Agent role: secretary/researcher/architect/developer/reviewer/user.")
    p.add_argument("--tokens", type=int, help="Subagent tokens consumed.")
    p.add_argument("--tool-uses", dest="tool_uses", type=int, help="Tool calls the agent made.")
    p.add_argument("--duration-ms", dest="duration_ms", type=int, help="Agent wall-clock in ms.")
    p.add_argument("--attempt", type=int, help="1 = fresh spawn; >=2 = resumed/retried (rework).")
    p.add_argument("--outcome", help="completed / returned_midtask / error.")
    p.add_argument("--model", help="Model the agent ran on (for cost attribution).")
    p.add_argument("--note", help="Freeform note.")
    p.add_argument("--data", help="Extra fields as a JSON object, merged into the event.")
    p.set_defaults(func=cmd_event)

    p = sub.add_parser("metrics", help="Aggregate the event + decision logs into per-phase metrics.")
    add_optional_slug(p)
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("dashboard", help="Serve the observability dashboard (0.0.0.0 by default).")
    p.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0 = all interfaces).")
    p.add_argument("--port", type=int, default=None,
                   help="Port. Omit to auto-pick the first free port from 8899 upward.")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("eval")
    p.set_defaults(func=cmd_eval)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except HarnessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
