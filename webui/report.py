"""
The Results pane's data — assembled entirely from local files, no network.

`report_lib.tracked_counts_detail()` is the same cumulative "what's in the
account" tally `scripts/verify.sh` prints as text; `last-run-report.txt` (and
its `last-run.json` sibling — see report_lib.write_run_json) is what
`pipeline.run_up_to` writes at the end of a live run; `output/*.csv` is the
per-entity export. Every read is guarded so a missing file yields an empty
section rather than an error.
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
    """The 'Report generated <iso>' timestamp from a last-run report, if present."""
    first = text.splitlines()[0] if text else ""
    return first.split("generated ", 1)[1].strip() if first.startswith("Report generated ") else None


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

    rows = []
    for entity in sorted(counts):
        target = report_lib.target_for(entity)
        run = run_entities.get(entity)
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
        "outputs": outputs,
        "outputs_summary": outputs_summary,
    }
