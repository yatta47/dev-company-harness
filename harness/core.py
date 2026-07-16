from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tasks"
WORKFLOW_PATH = ROOT / "workflows" / "dev-task.json"
LOCAL_STATE_DIR = ROOT / ".harness"
CURRENT_PATH = LOCAL_STATE_DIR / "current.json"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class HarnessError(RuntimeError):
    pass


@dataclass
class ValidationResult:
    passed: bool
    state: str
    errors: list[str]
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "state": self.state,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HarnessError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise HarnessError(f"Invalid JSON: {path}: {exc}") from exc


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_workflow() -> dict[str, Any]:
    return load_json(WORKFLOW_PATH)


def task_dir(slug: str) -> Path:
    if not SLUG_RE.fullmatch(slug):
        raise HarnessError("task id must match ^[a-z0-9][a-z0-9-]*$")
    return TASKS / slug


def state_path(slug: str) -> Path:
    return task_dir(slug) / "state.json"


def load_state(slug: str) -> dict[str, Any]:
    return load_json(state_path(slug))


def save_state(slug: str, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    save_json(state_path(slug), state)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Observability
#
# Every phase of a task's life emits an event to a per-task log
# (tasks/<id>/events.jsonl) and a cross-cutting log
# (observability/events.jsonl). Two kinds of producer:
#
#   * Structural events (validate / advance / return) are emitted automatically
#     by the functions in this module -- the Python layer knows timestamps and
#     state transitions.
#   * Cost events (agent_run: tokens, duration, tool calls, retries) are only
#     visible to the orchestrator (the Claude Code loop that spawns subagents),
#     so they are logged from the outside via `cli.py event`.
#
# The aggregate is the data backbone for the future web UI: per-phase duration
# and token cost, how many times a stage was re-entered (rework / 差し戻し), how
# many validate attempts a phase needed, and the secretary judgment trail.
# ---------------------------------------------------------------------------

OBSERVABILITY_DIR = ROOT / "observability"


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def record_event(slug: str, event: dict[str, Any]) -> dict[str, Any]:
    """Append one observability event. Fields in `event` win over the defaults,
    so a backfill can pass its own `at` / `ts_ms`."""
    record: dict[str, Any] = {"at": now_iso(), "ts_ms": now_ms(), "task": slug}
    record.update(event)
    append_jsonl(task_dir(slug) / "events.jsonl", record)
    append_jsonl(OBSERVABILITY_DIR / "events.jsonl", record)
    return record


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def seconds_in_state(state_data: dict[str, Any], state_name: str | None = None) -> float | None:
    """Wall-clock seconds since the task most recently entered `state_name`
    (defaults to the current state). Derived from history timestamps."""
    state_name = state_name or state_data.get("state")
    entered_at = None
    for ev in state_data.get("history", []):
        if ev.get("to") == state_name:
            entered_at = ev.get("at")
    t0 = _parse_iso(entered_at)
    if t0 is None:
        return None
    return round((datetime.now(timezone.utc).astimezone() - t0).total_seconds(), 1)


def load_events(slug: str) -> list[dict[str, Any]]:
    path = task_dir(slug) / "events.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


# Linear order of the pipeline, used to tell whether a return rolled the task
# back to at-or-before where a secretary advance had approved leaving from.
STATE_ORDER = {s: i for i, s in enumerate([
    "dod", "researching", "planning", "implementing",
    "reviewing", "packaging", "approved",
])}


def load_pricing() -> dict[str, Any]:
    """Optional token pricing so metrics can estimate $ cost. Rates live in
    observability/pricing.json; absent/null rate => cost is simply omitted
    (we never guess a provider's price)."""
    path = OBSERVABILITY_DIR / "pricing.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _secretary_calibration(events: list[dict[str, Any]]) -> dict[str, Any]:
    """How often a secretary advance was later overturned -- i.e. a downstream
    return rolled the task back to at-or-before the state that advance had
    approved leaving. This is the post-hoc calibration the harness cares about:
    when did the gate wave something through that the reviewer then bounced."""
    structural = sorted(
        [e for e in events if e.get("type") in ("advance", "return")],
        key=lambda e: e.get("ts_ms", 0),
    )
    sec_advances = [(i, e) for i, e in enumerate(structural)
                   if e["type"] == "advance" and e.get("by") == "secretary"]
    returns = [(i, e) for i, e in enumerate(structural) if e["type"] == "return"]
    detail: list[dict[str, Any]] = []
    overturned = 0
    for i, adv in sec_advances:
        src_order = STATE_ORDER.get(adv.get("from"), -1)
        was = any(j > i and STATE_ORDER.get(r.get("to"), 99) <= src_order
                  for j, r in returns)
        overturned += 1 if was else 0
        detail.append({"advance": f'{adv.get("from")} -> {adv.get("to")}',
                       "at": adv.get("at"), "overturned": was})
    n = len(sec_advances)
    return {
        "secretary_advances": n,
        "overturned": overturned,
        "overturned_rate": round(overturned / n, 3) if n else None,
        "detail": detail,
    }


def _fork_latency(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """For each ask_user stop, wall-clock until the next recorded decision
    resolved it. Shows when the pipeline is actually blocked on a human."""
    rows = sorted([d for d in decisions if d.get("at")], key=lambda d: d["at"])
    stops: list[dict[str, Any]] = []
    for idx, d in enumerate(rows):
        if d.get("ask_user") is not True:
            continue
        nxt = rows[idx + 1] if idx + 1 < len(rows) else None
        resolution_s = None
        resolved_by = None
        if nxt:
            t0, t1 = _parse_iso(d.get("at")), _parse_iso(nxt.get("at"))
            if t0 and t1 and t1 >= t0:
                resolution_s = round((t1 - t0).total_seconds(), 1)
            resolved_by = nxt.get("actor")
        stops.append({"stage": d.get("stage"), "at": d.get("at"),
                      "resolution_s": resolution_s, "resolved_by": resolved_by})
    lat = [s["resolution_s"] for s in stops if s["resolution_s"] is not None]
    return {
        "ask_user_stops": len(stops),
        "avg_resolution_s": round(sum(lat) / len(lat), 1) if lat else None,
        "stops": stops,
    }


def aggregate_metrics(slug: str) -> dict[str, Any]:
    """Roll the raw event + decision logs up into per-phase and whole-task
    metrics. This is what the web UI reads."""
    events = load_events(slug)
    state_data = load_state(slug)

    def new_stage() -> dict[str, Any]:
        return {
            "entries": 0, "agent_runs": 0, "agent_tokens": 0,
            "agent_tool_uses": 0, "agent_duration_ms": 0, "retries": 0,
            "validate_runs": 0, "validate_failures": 0,
            "returns_out": 0, "wall_s": 0.0, "tokens_by_role": {},
        }

    stages: dict[str, dict[str, Any]] = {}

    def bucket(name: str | None) -> dict[str, Any]:
        return stages.setdefault(name or "<none>", new_stage())

    totals = {
        "agent_runs": 0, "agent_tokens": 0, "agent_tool_uses": 0,
        "agent_duration_ms": 0, "retries": 0, "validate_runs": 0,
        "validate_failures": 0, "advances": 0, "returns": 0,
    }
    roles: dict[str, dict[str, Any]] = {}

    def role_bucket(name: str) -> dict[str, Any]:
        return roles.setdefault(name, {"agent_runs": 0, "agent_tokens": 0,
                                       "agent_tool_uses": 0, "agent_duration_ms": 0})

    flow: list[dict[str, Any]] = []

    for e in events:
        etype = e.get("type")
        stage = e.get("stage")
        if etype == "agent_run":
            b = bucket(stage)
            role = e.get("role") or "<unknown>"
            rb = role_bucket(role)
            b["agent_runs"] += 1
            rb["agent_runs"] += 1
            totals["agent_runs"] += 1
            for key, field in (("tokens", "agent_tokens"),
                               ("tool_uses", "agent_tool_uses"),
                               ("duration_ms", "agent_duration_ms")):
                val = int(e.get(key) or 0)
                b[field] += val
                rb[field] += val
                totals[field] += val
            b["tokens_by_role"][role] = b["tokens_by_role"].get(role, 0) + int(e.get("tokens") or 0)
            if int(e.get("attempt") or 1) >= 2:
                b["retries"] += 1
                totals["retries"] += 1
        elif etype == "validate":
            b = bucket(stage)
            b["validate_runs"] += 1
            totals["validate_runs"] += 1
            if not e.get("passed"):
                b["validate_failures"] += 1
                totals["validate_failures"] += 1
        elif etype == "advance":
            totals["advances"] += 1
            wall = e.get("wall_s_in_stage")
            if wall is not None:
                bucket(stage)["wall_s"] = round(bucket(stage)["wall_s"] + wall, 1)
            flow.append({"at": e.get("at"), "kind": "advance",
                         "detail": f'{e.get("from")} -> {e.get("to")}', "by": e.get("by")})
        elif etype == "return":
            totals["returns"] += 1
            b = bucket(stage)
            b["returns_out"] += 1
            wall = e.get("wall_s_in_stage")
            if wall is not None:
                b["wall_s"] = round(b["wall_s"] + wall, 1)
            flow.append({"at": e.get("at"), "kind": "return",
                         "detail": f'{e.get("from")} -> {e.get("to")}',
                         "reason": (e.get("reason") or "")[:100]})

    # How many times each state was entered. entries > 1 == rework / 差し戻し.
    for ev in state_data.get("history", []):
        to = ev.get("to")
        if to:
            bucket(to)["entries"] += 1

    # secretary judgment trail + fork stats, mined from the decision log.
    decisions = _load_jsonl(task_dir(slug) / "decisions.jsonl")
    confidences = [d["confidence"] for d in decisions
                   if isinstance(d.get("confidence"), (int, float))]
    dec_summary = {
        "total": len(decisions),
        "by_actor": {},
        "ask_user_stops": sum(1 for d in decisions if d.get("ask_user") is True),
        "judgment": sum(1 for d in decisions if d.get("kind") == "judgment"),
        "mechanical": sum(1 for d in decisions if d.get("kind") == "mechanical"),
        "confidence_avg": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }
    for d in decisions:
        actor = d.get("actor", "<unknown>")
        dec_summary["by_actor"][actor] = dec_summary["by_actor"].get(actor, 0) + 1

    # --- Cost share + optional $ estimate per role ---
    # $ uses a single blended rate on total subagent tokens (no in/out split is
    # available), so it is an estimate. Rate comes from observability/pricing.json;
    # if unset, cost fields are omitted rather than guessed.
    total_tokens = totals["agent_tokens"] or 1
    pricing = load_pricing()
    rate = pricing.get("default_blended_per_mtok")

    def est_cost(tokens: int):
        return round(tokens / 1_000_000 * rate, 4) if rate else None

    for rb in roles.values():
        rb["token_share"] = round(rb["agent_tokens"] / total_tokens, 3)
        if rate:
            rb["est_cost_usd"] = est_cost(rb["agent_tokens"])

    # --- Active (agent work) vs wall-clock, per stage + totals ---
    total_active_s = 0.0
    total_wall_s = 0.0
    for b in stages.values():
        active_s = round(b["agent_duration_ms"] / 1000, 1)
        b["active_s"] = active_s
        b["overhead_s"] = round(b["wall_s"] - active_s, 1)
        total_active_s += active_s
        total_wall_s += b["wall_s"]
        if rate:
            b["est_cost_usd"] = est_cost(b["agent_tokens"])
    totals["agent_active_s"] = round(total_active_s, 1)
    totals["wall_s"] = round(total_wall_s, 1)
    totals["overhead_s"] = round(total_wall_s - total_active_s, 1)
    if rate:
        totals["est_cost_usd"] = est_cost(totals["agent_tokens"])

    # --- Rework token cost: agent runs that were resumed / re-run (attempt >= 2) ---
    retry_tokens = sum(int(e.get("tokens") or 0) for e in events
                       if e.get("type") == "agent_run" and int(e.get("attempt") or 1) >= 2)
    retry_ms = sum(int(e.get("duration_ms") or 0) for e in events
                   if e.get("type") == "agent_run" and int(e.get("attempt") or 1) >= 2)

    return {
        "task": slug,
        "current_state": state_data.get("state"),
        "revision": state_data.get("revision"),
        "totals": totals,
        "by_stage": stages,
        "by_role": roles,
        "decisions": dec_summary,
        "secretary_calibration": _secretary_calibration(events),
        "fork_latency": _fork_latency(decisions),
        "rework": {
            "returns": totals["returns"],
            "stages_reentered": {k: v["entries"] for k, v in stages.items() if v["entries"] > 1},
            "retry_agent_runs": totals["retries"],
            "retry_tokens": retry_tokens,
            "retry_duration_ms": retry_ms,
        },
        "flow": flow,
    }


def generate_slug(text: str) -> str:
    """Generate a safe fallback id. Claude normally supplies a semantic slug."""
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    meaningful = [w for w in words if len(w) > 1][:6]
    if meaningful:
        base = "-".join(meaningful)
    else:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        base = f"task-{digest}"
    base = re.sub(r"-+", "-", base).strip("-")[:64]
    return base or f"task-{hashlib.sha1(text.encode()).hexdigest()[:8]}"


def unique_slug(preferred: str) -> str:
    base = preferred
    candidate = base
    index = 2
    while (TASKS / candidate).exists():
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def set_current(slug: str) -> None:
    if not state_path(slug).exists():
        raise HarnessError(f"Unknown task: {slug}")
    save_json(CURRENT_PATH, {
        "active_task": slug,
        "updated_at": now_iso(),
    })


def get_current(required: bool = True) -> str | None:
    if CURRENT_PATH.exists():
        data = load_json(CURRENT_PATH)
        slug = data.get("active_task")
        if slug and state_path(slug).exists():
            return slug

    tasks = list_tasks()
    if len(tasks) == 1:
        slug = tasks[0]["id"]
        set_current(slug)
        return slug

    if required:
        if not tasks:
            raise HarnessError("No tasks exist. Run /work-init first.")
        raise HarnessError("No active task. Run /work-list and /work-switch.")
    return None


def list_tasks() -> list[dict[str, Any]]:
    current = None
    if CURRENT_PATH.exists():
        try:
            current = load_json(CURRENT_PATH).get("active_task")
        except HarnessError:
            current = None

    rows: list[dict[str, Any]] = []
    if not TASKS.exists():
        return rows

    for state_file in sorted(TASKS.glob("*/state.json")):
        try:
            state = load_json(state_file)
        except HarnessError:
            continue
        rows.append({
            "id": state.get("id", state_file.parent.name),
            "title": state.get("title", ""),
            "state": state.get("state", "unknown"),
            "revision": state.get("revision", 0),
            "updated_at": state.get("updated_at"),
            "active": state_file.parent.name == current,
        })
    rows.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
    return rows


def resolve_task(query: str) -> str:
    query = query.strip().lower()
    if not query:
        raise HarnessError("A title fragment or task id is required.")

    rows = list_tasks()
    exact = [
        row for row in rows
        if row["id"].lower() == query or row["title"].lower() == query
    ]
    if len(exact) == 1:
        return exact[0]["id"]

    partial = [
        row for row in rows
        if query in row["id"].lower() or query in row["title"].lower()
    ]
    if len(partial) == 1:
        return partial[0]["id"]
    if not partial:
        raise HarnessError(f"No task matched: {query}")

    candidates = ", ".join(f'{row["id"]} ({row["title"]})' for row in partial)
    raise HarnessError(f"Multiple tasks matched. Be more specific: {candidates}")


def create_task(
    seed: str,
    repo: str,
    title: str | None = None,
    slug: str | None = None,
) -> Path:
    """Start a task against a registered repo.

    `repo` is a NAME from the workspace registry, never a path — the same DoD
    has to mean the same thing on your machine and on your work machine, where
    the checkout lives somewhere else entirely. The name is resolved here, at
    creation, so an unknown repo fails immediately rather than three states
    later when a gate finally tries to run something.
    """
    seed = seed.strip()
    if not seed:
        raise HarnessError("What you want done is required.")

    # Fail fast and loudly: a task pinned to a repo that is not registered can
    # never pass a gate, because the gate is that repo's own commands.
    from .registry import RegistryError, get_repo
    try:
        target_repo = get_repo(repo)
    except RegistryError as exc:
        raise HarnessError(str(exc)) from exc
    if not target_repo.writable:
        raise HarnessError(
            f"repo {repo!r} is {target_repo.write!r}; a task needs "
            f"write = \"agent-commit-only\". Registered as role={target_repo.role!r}."
        )

    working_title = (title or seed.splitlines()[0]).strip()[:120]
    preferred = slug or generate_slug(working_title + "\n" + seed)
    preferred = re.sub(r"[^a-z0-9-]+", "-", preferred.lower()).strip("-")
    if not preferred:
        preferred = generate_slug(seed)
    if not SLUG_RE.fullmatch(preferred):
        raise HarnessError("Generated task id is invalid.")

    slug = unique_slug(preferred)
    target = task_dir(slug)
    target.mkdir(parents=True)

    templates = ROOT / "templates"
    for filename in [
        "dod.json", "facts.json", "findings.md", "plan.md",
        "verify.json", "review.json", "review.md", "commit.md",
    ]:
        (target / filename).write_bytes((templates / filename).read_bytes())

    (target / "request.md").write_text(
        "# Request\n\n" + seed + "\n",
        encoding="utf-8",
    )

    dod = load_json(target / "dod.json")
    dod["title"] = working_title
    dod["repo"] = repo
    dod["source_request"] = seed
    save_json(target / "dod.json", dod)

    created = now_iso()
    state = {
        "id": slug,
        "title": working_title,
        "repo": repo,
        "state": "dod",
        "revision": 0,
        "created_at": created,
        "updated_at": created,
        "last_validation": None,
        "history": [{
            "at": created,
            "event": "created",
            "from": None,
            "to": "dod",
            "reason": "task initialized from request"
        }]
    }
    save_json(target / "state.json", state)
    (target / "decisions.jsonl").write_text("", encoding="utf-8")
    set_current(slug)
    return target


def nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    return value is not None


# Phrasing that looks like an acceptance criterion but cannot be observed.
# The DoD gate rejects these. This is the harness holding the user to the
# standard the user holds people to: an expectation that is not an observable
# event is not an expectation, it is a hope, and "it did not feel done" is not
# something an agent — or a colleague — can act on.
#
# Be clear about what this is: a LINT, not a proof. It catches the common
# shapes of vagueness and raises the floor; it cannot tell you that a
# criterion IS observable, and a determined author can phrase mush past it
# ("the output should be of high standard"). Matching is per-word for English
# (so "make it better" and "make better" both land) and substring for
# Japanese, which has no word boundaries to anchor to.
UNOBSERVABLE_JA = [
    "きれい", "適切", "ちゃんと", "しっかり", "いい感じ", "良い感じ",
    "わかりやすく", "使いやすく", "改善する", "最適化する", "考慮する",
]
# Each entry is a sequence of words that must appear in order, but not
# necessarily adjacently: ("make", "better") matches "make it better".
UNOBSERVABLE_EN = [
    ("properly",), ("cleanly",), ("nicely",), ("as", "appropriate"),
    ("make", "better"), ("improve",), ("optimi",), ("clean", "up"),
    ("robust",), ("user", "friendly"),
]


def _unobservable_hits(text: str) -> list[str]:
    low = text.lower()
    hits = [p for p in UNOBSERVABLE_JA if p in low]
    words = re.findall(r"[a-z]+", low)
    for seq in UNOBSERVABLE_EN:
        idx, found = 0, True
        for token in seq:
            # `in` rather than `==` so a stem like "optimi" covers
            # optimise/optimize/optimization without listing each.
            nxt = next((i for i in range(idx, len(words)) if token in words[i]), None)
            if nxt is None:
                found = False
                break
            idx = nxt + 1
        if found:
            hits.append(" ".join(seq))
    return hits


def _verify_artifact(target: Path, state: str) -> dict[str, Any] | None:
    """Read the recorded verify run, if the harness has produced one."""
    path = target / "verify.json"
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except HarnessError:
        return None
    return data if data.get("checks") else None


def validate_task(slug: str) -> ValidationResult:
    state_data = load_state(slug)
    state = state_data["state"]
    target = task_dir(slug)
    errors: list[str] = []
    warnings: list[str] = []

    if state == "dod":
        dod = load_json(target / "dod.json")
        for key in ["title", "repo", "problem", "acceptance_criteria", "verification"]:
            if not nonempty(dod.get(key)):
                errors.append(f"dod.json: {key} is required")

        # The heart of this gate. Everything downstream is checked by machine;
        # this is the one place where a human decides what "done" means, so it
        # is the one place worth being strict about.
        for i, crit in enumerate(dod.get("acceptance_criteria", []) or []):
            text = crit if isinstance(crit, str) else str(crit.get("statement", ""))
            if not text.strip():
                errors.append(f"dod.json: acceptance_criteria[{i}] is empty")
                continue
            vague = _unobservable_hits(text)
            if vague:
                errors.append(
                    f"dod.json: acceptance_criteria[{i}] is not observable "
                    f"({', '.join(vague)}): {text[:60]!r} — say what can be seen "
                    f"to have happened, not how it should feel"
                )

        # Who signed this off. The DoD is the human confirm gate: mode 1 has a
        # person confirm the secretary's draft, mode 2 supplies it outright.
        # Either way something outside this state machine agreed to it.
        if dod.get("confirmed_by") not in {"user", "supplied"}:
            errors.append(
                "dod.json: confirmed_by must be 'user' (a person confirmed the "
                "draft) or 'supplied' (the DoD was handed to the harness). "
                "The secretary cannot confirm its own draft."
            )

    elif state == "researching":
        facts_data = load_json(target / "facts.json")
        facts = facts_data.get("facts", [])
        if not facts:
            errors.append("facts.json: at least one fact is required")
        for fact in facts:
            fid = fact.get("id", "<unknown>")
            if not nonempty(fact.get("statement")):
                errors.append(f"{fid}: statement is required")
            # A claim about a codebase with no file:line behind it is a guess.
            # This is the researcher's equivalent of "cite your source".
            if not fact.get("sources"):
                errors.append(
                    f"{fid}: at least one source is required (file:line, "
                    f"command output, or URL) — an unsourced claim about the "
                    f"code is a guess"
                )
        for q in facts_data.get("unresolved_questions", []):
            if q.get("severity") == "critical" and q.get("status") == "open":
                errors.append(f"{q.get('id', '<question>')}: critical unresolved question remains")
        findings = (target / "findings.md").read_text(encoding="utf-8")
        if len(findings.strip()) < 200:
            errors.append("findings.md: research notes are too short")

    elif state == "planning":
        plan = (target / "plan.md").read_text(encoding="utf-8")
        # Not a word count: these three sections are what makes a plan
        # reviewable and a change recoverable.
        for heading, why in [
            ("## Changes", "which files change and how"),
            ("## Steps", "the order of work"),
            ("## Rollback", "how to get back if this goes wrong"),
        ]:
            if heading not in plan:
                errors.append(f"plan.md: missing section {heading} ({why})")
        for marker in ["[TODO]", "TBD", "要確認", "仮置き"]:
            if marker in plan:
                errors.append(f"plan.md: unresolved marker remains: {marker}")

    elif state == "implementing":
        # The gate is the repo's own commands, run by the harness, recorded to
        # verify.json. Not the agent's opinion of its work.
        verify = _verify_artifact(target, state)
        if verify is None:
            errors.append(
                "verify.json: no verify run recorded. Run "
                "`python3 -m harness.cli verify --check lint --check build`"
            )
        else:
            for check in verify.get("checks", []):
                if not check.get("passed"):
                    errors.append(
                        f"verify: {check.get('check')} failed "
                        f"(exit {check.get('exit_code')}): {check.get('command')}"
                    )
            if not any(c.get("check") in {"lint", "build"} for c in verify.get("checks", [])):
                warnings.append(
                    "verify.json: neither lint nor build ran — is that right "
                    "for this repo?"
                )

    elif state == "reviewing":
        verify = _verify_artifact(target, state)
        if verify is None:
            errors.append("verify.json: no verify run recorded")
        else:
            if not any(c.get("check") == "test" for c in verify.get("checks", [])):
                errors.append(
                    "verify: test has not been run in this state — "
                    "`python3 -m harness.cli verify --check test`"
                )
            for check in verify.get("checks", []):
                if not check.get("passed"):
                    errors.append(
                        f"verify: {check.get('check')} failed "
                        f"(exit {check.get('exit_code')}): {check.get('command')}"
                    )

        review = load_json(target / "review.json")
        if review.get("status") != "pass":
            errors.append(f"review.json: status must be pass, got {review.get('status')!r}")
        for issue in review.get("issues", []):
            if issue.get("severity") in {"critical", "high"} and issue.get("status") == "open":
                errors.append(f"{issue.get('id', '<issue>')}: blocking issue remains open")
        review_md = (target / "review.md").read_text(encoding="utf-8")
        if len(review_md.strip()) < 100:
            warnings.append("review.md is very short")

    elif state == "packaging":
        commit = (target / "commit.md").read_text(encoding="utf-8")
        if len(commit.strip()) < 50:
            errors.append("commit.md: commit message / PR body is too short")
        for marker in ["[TODO]", "TBD", "要確認", "仮置き"]:
            if marker in commit:
                errors.append(f"commit.md: unresolved marker remains: {marker}")

    elif state == "approved":
        # `approved` claims two things about the real repo, so both are checked
        # against the real repo: the work is committed, and it has not left.
        from .registry import RegistryError, get_repo, git_state
        try:
            repo = get_repo(state_data.get("repo", ""))
        except RegistryError as exc:
            errors.append(str(exc))
        else:
            g = git_state(repo)
            if not g.get("is_git_repo"):
                errors.append(f"{repo.name}: not a git repository at {repo.path}")
            else:
                if not g.get("clean"):
                    errors.append(
                        f"{repo.name}: working tree is not clean — approved means "
                        f"the work is committed. Uncommitted: "
                        f"{', '.join(g.get('dirty_files', [])[:5])}"
                    )
                # Not an error: this harness has no push path, so a pushed
                # branch means a human pushed it, which is exactly the intended
                # ending. Saying so is a report, not a complaint.
                if g.get("unpushed_commits") == 0 and g.get("has_upstream"):
                    warnings.append(
                        f"{repo.name}: nothing unpushed — already published by a human?"
                    )

    elif state == "abandoned":
        dod = load_json(target / "dod.json")
        if not nonempty(dod.get("abandon_reason")):
            errors.append(
                "dod.json: abandon_reason is required — a task that was "
                "dropped is only useful later if it says why"
            )
    else:
        errors.append(f"Unknown state: {state}")

    result = ValidationResult(not errors, state, errors, warnings)
    state_data["last_validation"] = {"at": now_iso(), **result.as_dict()}
    save_state(slug, state_data)
    record_event(slug, {
        "type": "validate",
        "stage": state,
        "passed": result.passed,
        "errors": len(errors),
        "warnings": len(warnings),
    })
    return result


# Exactly one accountable role per state. Domain advisors
# (aws-latest-architect, ...) are a different axis entirely: consulted, never
# accountable, and configured per-repo in the workspace registry.
STAGE_OWNER = {
    "dod": "secretary", "researching": "researcher", "planning": "architect",
    "implementing": "developer", "reviewing": "reviewer",
    "packaging": "developer", "approved": "orchestrator",
    "abandoned": "orchestrator",
}


def build_trace(slug: str) -> dict[str, Any]:
    """A span-per-phase trace (Gantt / waterfall): each time the task was in a
    state becomes a span with real wall-clock start/end from history, the token
    cost spent there, and the active agent-time nested inside (so overhead shows).
    Reworked stages appear as multiple spans; returns mark the boundary."""
    state_data = load_state(slug)
    history = state_data.get("history", [])
    metrics = aggregate_metrics(slug)
    by_stage = metrics["by_stage"]
    rate = load_pricing().get("default_blended_per_mtok")

    # Terminal = the task is over. An open span in a terminal state must not
    # keep accruing wall-clock: idle-after-done is not process time.
    # `abandoned` counts — a dropped task is finished, not still running.
    terminal = {"approved", "abandoned"}
    spans: list[dict[str, Any]] = []
    for i, ev in enumerate(history):
        start = ev.get("at")
        is_open = i + 1 == len(history)
        if is_open:
            # The current, still-open state. For an in-progress phase (someone is
            # still working) count time up to now; for a terminal state, don't --
            # idle-after-completion isn't part of the process trace.
            end = start if ev.get("to") in terminal else now_iso()
        else:
            end = history[i + 1]["at"]
        t0, t1 = _parse_iso(start), _parse_iso(end)
        dur = round((t1 - t0).total_seconds(), 1) if (t0 and t1) else None
        nxt = history[i + 1] if i + 1 < len(history) else None
        spans.append({
            "stage": ev.get("to"),
            "owner": STAGE_OWNER.get(ev.get("to")),
            "start": start, "end": end, "dur_s": dur,
            "entered_via": ev.get("event"), "by": ev.get("by"),
            "ended_via": nxt["event"] if nxt else "current",
            "ended_to": nxt.get("to") if nxt else None,
            "returned": bool(nxt and nxt.get("event") == "returned"),
            "reason": nxt.get("reason") if nxt else None,
        })

    # Apportion each stage's tokens / active-time across its occurrences by wall-duration.
    occurrences: dict[str, list[int]] = {}
    for idx, sp in enumerate(spans):
        occurrences.setdefault(sp["stage"], []).append(idx)
    for stage, idxs in occurrences.items():
        bucket = by_stage.get(stage, {})
        total_tokens = bucket.get("agent_tokens", 0)
        total_active = bucket.get("active_s", 0.0)
        durs = [spans[i]["dur_s"] or 0 for i in idxs]
        denom = sum(durs) or len(idxs)
        for k, i in enumerate(idxs):
            frac = 1.0 if len(idxs) == 1 else ((durs[k] / denom) if denom else 1.0 / len(idxs))
            tokens = round(total_tokens * frac)
            spans[i]["tokens"] = tokens
            spans[i]["active_s"] = round(total_active * frac, 1)
            spans[i]["cost"] = round(tokens / 1_000_000 * rate, 4) if rate else None
            spans[i]["apportioned"] = len(idxs) > 1
            spans[i]["roles"] = bucket.get("tokens_by_role", {}) if len(idxs) == 1 else None

    t0 = _parse_iso(spans[0]["start"]) if spans else None
    tN = _parse_iso(spans[-1]["end"]) if spans else None
    for sp in spans:
        s0 = _parse_iso(sp["start"])
        sp["offset_s"] = round((s0 - t0).total_seconds(), 1) if (s0 and t0) else 0.0
    total_s = round((tN - t0).total_seconds(), 1) if (t0 and tN) else None
    total_tokens = metrics["totals"]["agent_tokens"]

    return {
        "task": slug,
        "current_state": state_data.get("state"),
        "start": spans[0]["start"] if spans else None,
        "end": spans[-1]["end"] if spans else None,
        "total_s": total_s,
        "total_agent_active_s": metrics["totals"].get("agent_active_s"),
        "total_tokens": total_tokens,
        "total_cost": round(total_tokens / 1_000_000 * rate, 4) if rate else None,
        "spans": spans,
    }


def transition(slug: str, by: str | None = None) -> tuple[str, str]:
    result = validate_task(slug)
    if not result.passed:
        raise HarnessError("Validation failed; state was not advanced")

    workflow = load_workflow()
    state_data = load_state(slug)
    old = state_data["state"]
    new = workflow["states"][old]["next"]
    if new is None:
        raise HarnessError(f"No next state from {old}")

    wall = seconds_in_state(state_data, old)
    event = {
        "at": now_iso(),
        "event": "advanced",
        "from": old,
        "to": new,
        "reason": "gate passed",
    }
    # Who made the advance decision. For finalizing -> approved this is the
    # layer-2 approver (self-mirror proxy or user); it lets a reader tell a
    # proxy approval apart from a human one. External publish stays human-only.
    if by is not None:
        event["by"] = by
    state_data["state"] = new
    state_data["revision"] += 1
    state_data["history"].append(event)
    save_state(slug, state_data)
    record_event(slug, {
        "type": "advance",
        "stage": old,
        "from": old,
        "to": new,
        "by": by,
        "wall_s_in_stage": wall,
    })
    return old, new


def return_to(slug: str, target_state: str, reason: str) -> tuple[str, str]:
    workflow = load_workflow()
    state_data = load_state(slug)
    old = state_data["state"]
    allowed = workflow.get("allowed_returns", {}).get(old, [])
    if target_state not in allowed:
        raise HarnessError(f"Cannot return from {old} to {target_state}; allowed: {allowed}")

    wall = seconds_in_state(state_data, old)
    state_data["state"] = target_state
    state_data["revision"] += 1
    state_data["history"].append({
        "at": now_iso(),
        "event": "returned",
        "from": old,
        "to": target_state,
        "reason": reason
    })
    save_state(slug, state_data)
    record_event(slug, {
        "type": "return",
        "stage": old,
        "from": old,
        "to": target_state,
        "reason": reason,
        "wall_s_in_stage": wall,
    })
    return old, target_state


# ---------------------------------------------------------------------------
# There is deliberately no publish()/push() here.
#
# The writing harness this was forked from had `publish()`: approved -> published.
# This one stops at `approved`, which means "committed locally, waiting for a
# human". Pushing is not a step the harness declines to take — it is a step the
# harness cannot take, because no code here does it. That is the difference
# between a rule and a structure, and it is the whole point: an agent must not
# be able to decide, alone, to put something into the world.
#
# If you are here to add "just a small push helper": don't. Adding it back is
# not a feature, it is the removal of the only guarantee this harness makes.
# ---------------------------------------------------------------------------


def run_verify_and_record(
    slug: str,
    checks: list[str] | None = None,
    *,
    timeout_s: int | None = None,
) -> dict[str, Any]:
    """Run the target repo's own checks and write verify.json.

    This is the gate's evidence. The harness runs the commands itself and
    records what happened, because a stage that asks the agent whether its work
    is good is not a gate. The agent may read verify.json; it may not write it.
    """
    from . import registry as reg

    state_data = load_state(slug)
    state = state_data["state"]
    repo_name = state_data.get("repo")
    if not repo_name:
        raise HarnessError(f"task {slug} has no repo recorded in state.json")
    try:
        repo = reg.get_repo(repo_name)
    except reg.RegistryError as exc:
        raise HarnessError(str(exc)) from exc

    kwargs = {"timeout_s": timeout_s} if timeout_s else {}
    results = reg.run_verify(repo, checks, **kwargs)
    if not results:
        raise HarnessError(
            f"repo {repo.name}: no verify checks to run"
            + (f" (asked for: {', '.join(checks)})" if checks else "")
        )

    summary = reg.summarize(results)
    summary.update({
        "task": slug,
        "repo": repo.name,
        "stage": state,
        "at": now_iso(),
    })
    save_json(task_dir(slug) / "verify.json", summary)

    for r in results:
        record_event(slug, {
            "type": "verify_check",
            "stage": state,
            "check": r.check,
            "passed": r.passed,
            "exit_code": r.exit_code,
            "duration_ms": r.duration_ms,
            "irreversible_hits": r.irreversible_hits,
        })
    if summary["irreversible"]:
        # Loud on purpose: this is the moment the harness exists for.
        record_event(slug, {
            "type": "irreversible_detected",
            "stage": state,
            "hits": summary["irreversible_hits"],
            "advisors_required": repo.advisors,
        })
    return summary


def abandon(slug: str, reason: str) -> tuple[str, str]:
    """Stop a task for good, on the record.

    The writing harness had no equivalent: an article is always finished. Work
    is not like that — "we looked into it and it should not be done" is a
    normal and valuable outcome. Without this, such a task has nowhere to go
    and rots in `researching` forever, and the reasoning that killed it is lost
    exactly when someone proposes the same thing again next quarter.
    """
    if not reason.strip():
        raise HarnessError("an abandon reason is required")

    state_data = load_state(slug)
    old = state_data["state"]
    workflow = load_workflow()
    if old in workflow.get("terminal_states", []):
        raise HarnessError(f"task is already terminal ({old})")

    wall = seconds_in_state(state_data, old)
    dod_path = task_dir(slug) / "dod.json"
    if dod_path.exists():
        dod = load_json(dod_path)
        dod["abandon_reason"] = reason
        save_json(dod_path, dod)

    state_data["state"] = "abandoned"
    state_data["revision"] += 1
    state_data["history"].append({
        "at": now_iso(),
        "event": "abandoned",
        "from": old,
        "to": "abandoned",
        "reason": reason,
    })
    save_state(slug, state_data)
    record_event(slug, {
        "type": "abandon",
        "stage": old,
        "from": old,
        "to": "abandoned",
        "reason": reason,
        "wall_s_in_stage": wall,
    })
    return old, "abandoned"


def record_decision(
    slug: str,
    actor: str,
    question: str,
    decision: str,
    confidence: float,
    reason: str,
    *,
    stage: str | None = None,
    matched_principles: list[str] | None = None,
    ask_user: bool | None = None,
    risk: str | None = None,
    reversible: bool | None = None,
    basis: str | None = None,
    kind: str | None = None,
) -> None:
    if not 0 <= confidence <= 1:
        raise HarnessError("confidence must be between 0 and 1")
    # Auto-tag the stage the decision was made at, so the judgment trail can be
    # replayed per state (used by the review-time critique / grow loop).
    if stage is None:
        try:
            stage = load_state(slug).get("state")
        except HarnessError:
            stage = None
    record = {
        "at": now_iso(),
        "task": slug,
        "actor": actor,
        "question": question,
        "decision": decision,
        "confidence": confidence,
        "reason": reason,
    }
    if stage is not None:
        record["stage"] = stage
    if matched_principles:
        record["matched_principles"] = matched_principles
    if ask_user is not None:
        record["ask_user"] = ask_user
    if risk is not None:
        record["risk"] = risk
    if reversible is not None:
        record["reversible"] = reversible
    if basis is not None:
        record["basis"] = basis
    # "judgment" = a real fork where the user's values decided; "mechanical" =
    # a gate that only passed a checklist. Keeps the growth log high-signal so
    # real judgment axes can be mined without dilution by routine advances.
    if kind is not None:
        record["kind"] = kind
    append_jsonl(task_dir(slug) / "decisions.jsonl", record)
    if actor == "secretary":
        append_jsonl(ROOT / "secretary" / "decisions.jsonl", record)
