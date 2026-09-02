"""
The Results pane's data — assembled entirely from local files, no network.

`report_lib.tracked_counts_detail()` is the same cumulative "what's in the
account" tally `scripts/verify.sh` prints as text; `last-run-report.txt` (and
its `last-run.json` sibling — see report_lib.write_run_json) is what
`pipeline.run_up_to` writes at the end of a live run; `last-teardown-report.txt`
(and its `last-teardown.json` sibling — see teardown.py::write_teardown_report)
is the mirror of that for a live `teardown.py` run; `output/*.csv` is the
per-entity export. Every read is guarded so a missing file yields an empty
section rather than an error.

`latest_action` ("run" | "teardown" | None) says which of the two most
recently touched the account, by timestamp — the frontend uses it to decide
whether an entity row's delta column shows the seed's `+N` or the teardown's
`-N`.
"""

from __future__ import annotations

import json
from collections import Counter

import config
import report_lib

# volume key -> the entity apiPath it's tracked under (reverse of report_lib's map)
_ENTITY_BY_KEY = {v: k for k, v in report_lib.TARGET_KEY_BY_ENTITY.items()}


def gather_plan() -> dict:
    """What prebuild has generated (from data/plan-manifest.json, or actual
    file lengths) and what's actually seeded live — powers the guided flow's
    "N seeded, a run only adds new ones" readout. Local reads only."""
    manifest = {}
    try:
        p = config.DATA_DIR / "plan-manifest.json"
        if p.exists():
            manifest = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}

    counts = dict(manifest.get("counts") or {})
    for key, fname in report_lib.DATA_FILE_BY_VOLUME_KEY.items():
        if key not in counts:
            try:
                counts[key] = len(json.loads(
                    (config.DATA_DIR / fname).read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass

    try:
        tracked = report_lib.tracked_counts()
    except Exception:  # noqa: BLE001
        tracked = {}
    seeded = {key: tracked.get(_ENTITY_BY_KEY.get(key), 0)
              for key in report_lib.DATA_FILE_BY_VOLUME_KEY}

    return {
        "seed": manifest.get("seed"),
        "generated_at": manifest.get("generated_at"),
        "counts": counts,
        "seeded": seeded,
    }


def _csv_rows(path) -> int:
    """Data rows in a CSV (line count minus the header), 0 if header-only/empty."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            n = sum(1 for _ in fh)
    except OSError:
        return 0
    return max(0, n - 1)


def _first_iso(text) -> str | None:
    """The '<...> generated <iso>' timestamp from the first line of a last-run
    or last-teardown report ('Report generated ...' / 'Teardown report
    generated ...'), if present."""
    first = text.splitlines()[0] if text else ""
    for prefix in ("Report generated ", "Teardown report generated "):
        if first.startswith(prefix):
            return first[len(prefix):].strip()
    return None


def gather_report() -> dict:
    # --- cumulative "what's in the account" — structured ---
    try:
        counts, malformed_count = report_lib.tracked_counts_detail()
    except Exception:  # noqa: BLE001
        counts, malformed_count = {}, 0

    # --- last live seeding run: structured (last-run.json), for the "last
    # run" delta joined onto each row below, plus a summary for the status
    # strip. Falls back to just a timestamp (scraped from the text report's
    # first line) when the JSON sibling is missing — an older last-run-report.txt
    # written before this file existed, or a JSON write that failed independently
    # of the text one. No JSON at all just means every row's "last run" reads "—".
    run_entities, run_summary = {}, None
    try:
        json_path = report_lib.REPORT_PATH.with_suffix(".json")
        if json_path.exists():
            run_data = json.loads(json_path.read_text(encoding="utf-8"))
            run_entities = run_data.get("entities") or {}
            reasons = Counter()
            for c in run_entities.values():
                reasons.update(c.get("failure_reasons") or {})
            total_failed = sum(c.get("failed", 0) for c in run_entities.values())
            run_summary = {
                "generated_at": run_data.get("generated_at"),
                "layer_failures": run_data.get("layer_failures") or [],
                "total_created": sum(c.get("created", 0) for c in run_entities.values()),
                "total_failed": total_failed,
                "entities_failed": sum(1 for c in run_entities.values() if c.get("failed")),
                # top 3 is plenty for a one-line status strip — the full
                # breakdown is still in the raw report for anyone who needs it.
                "top_failure_reasons": reasons.most_common(3),
            }
    except (OSError, ValueError):
        pass

    # --- last live teardown run: the mirror of the block above, from
    # last-teardown.json (see teardown.py::write_teardown_report). Same
    # shape of use — a per-entity delta joined onto each row, plus a
    # one-line summary for a status strip of its own.
    teardown_entities, teardown_summary = {}, None
    try:
        td_json = report_lib.TEARDOWN_REPORT_PATH.with_suffix(".json")
        if td_json.exists():
            td_data = json.loads(td_json.read_text(encoding="utf-8"))
            teardown_entities = td_data.get("entities") or {}
            totals = td_data.get("totals") or {}
            td_reasons = Counter()
            aborted_entities = []
            for entity_name, c in teardown_entities.items():
                td_reasons.update(c.get("failure_reasons") or {})
                if c.get("aborted"):
                    aborted_entities.append(entity_name)
            teardown_summary = {
                "generated_at": td_data.get("generated_at"),
                "mode": td_data.get("mode"),
                "total_deleted": totals.get("deleted", 0),
                "total_failed": totals.get("failed", 0),
                "total_marked_used": totals.get("marked_used", 0),
                "total_seen": totals.get("seen", 0),
                "entities_failed": sum(1 for c in teardown_entities.values() if c.get("failed")),
                "top_failure_reasons": td_reasons.most_common(3),
                "aborted_entities": aborted_entities,
            }
    except (OSError, ValueError):
        pass

    rows = []
    for entity in sorted(counts):
        target = report_lib.target_for(entity)
        run = run_entities.get(entity)
        td = teardown_entities.get(entity)
        rows.append({
            "entity": entity,
            # The config.VOLUMES key this entity is tracked under, so the
            # frontend can join these rows against gather_plan()'s per-key
            # generated/seeded counts. None for entities with no volume key.
            "key": report_lib.TARGET_KEY_BY_ENTITY.get(entity),
            "created": counts[entity],
            "target": target,
            "short": target is not None and counts[entity] < target,
            # This run's delta for this entity, or None if the last run
            # never touched it (a skipped layer, or an entity another
            # layer owns). Distinct from "short" above, which is lifetime.
            "last_run": {"created": run.get("created", 0), "failed": run.get("failed", 0)} if run else None,
            # The last teardown's delta for this entity, same idea — None
            # if that teardown never processed it. The frontend shows this
            # instead of last_run when latest_action == "teardown".
            "last_teardown": {"deleted": td.get("deleted", 0), "failed": td.get("failed", 0),
                              "marked_used": td.get("marked_used", 0)} if td else None,
        })
    with_target = [r for r in rows if r["target"] is not None]
    summary = {
        "total": sum(counts.values()),
        "entities": len(with_target),
        "below_target": sum(1 for r in with_target if r["short"]),
        "malformed": malformed_count,
    }

    # --- last live seeding run: the raw file, verbatim, for the "raw report"
    # viewer — a QA person opening exactly what write_report() saved to disk.
    last_run = {"generated_at": run_summary["generated_at"] if run_summary else None, "text": None}
    try:
        path = report_lib.REPORT_PATH
        if path.exists():
            text = path.read_text(encoding="utf-8")
            last_run["text"] = text
            if last_run["generated_at"] is None:
                last_run["generated_at"] = _first_iso(text)
    except OSError:
        pass

    # --- last live teardown run: the raw file, verbatim — same "raw report"
    # viewer, shown in place of last_run's text when a teardown was the
    # most recent thing to touch the account.
    teardown_report = {
        "generated_at": teardown_summary["generated_at"] if teardown_summary else None,
        "text": None,
    }
    try:
        td_path = report_lib.TEARDOWN_REPORT_PATH
        if td_path.exists():
            td_text = td_path.read_text(encoding="utf-8")
            teardown_report["text"] = td_text
            if teardown_report["generated_at"] is None:
                teardown_report["generated_at"] = _first_iso(td_text)
    except OSError:
        pass

    # --- which of the two last touched the account? ISO-8601 UTC strings
    # from datetime.now(timezone.utc).isoformat() on both sides (and the
    # scraped-from-text fallback uses the same format), so a lexical
    # compare is a chronological one.
    run_at = last_run["generated_at"]
    td_at = teardown_report["generated_at"]
    if td_at and (not run_at or td_at >= run_at):
        latest_action = "teardown"
    elif run_at:
        latest_action = "run"
    else:
        latest_action = None

    # --- output/*.csv exports ---
    outputs = []
    try:
        out_dir = config.OUTPUT_DIR
        if out_dir.exists():
            for f in sorted(out_dir.glob("*.csv")):
                st = f.stat()
                outputs.append({"name": f.name, "rows": _csv_rows(f),
                                "size": st.st_size, "mtime": st.st_mtime})
    except OSError:
        pass
    outputs_summary = {"files": len(outputs),
                       "with_rows": sum(1 for o in outputs if o["rows"] > 0)}

    return {
        "report": rows,
        "summary": summary,
        "run_summary": run_summary,
        "last_run": last_run,
        "teardown_summary": teardown_summary,
        "teardown_report": teardown_report,
        "latest_action": latest_action,
        "outputs": outputs,
        "outputs_summary": outputs_summary,
    }
