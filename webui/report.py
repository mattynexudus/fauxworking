"""
The Results pane's data — assembled entirely from local files, no network.

`report_lib.report_lines()` is the same cumulative "what's in the account"
table `scripts/verify.sh` prints; `last-run-report.txt` is what
`pipeline.run_up_to` writes at the end of a live run; `output/*.csv` is the
per-entity export. Every read is guarded so a missing file yields an empty
section rather than an error.
"""

from __future__ import annotations

import json

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


# last-run-report.txt ends with a full copy of report_lib.report_lines() under
# this header (see report_lib.write_report). The panel already shows that live
# table as the main "Results" block, so the last-run view is trimmed to just
# the run-specific part above it: timestamp, layer failures, this-run reconciliation.
_CUMULATIVE_MARKER = "=== What's in the account now (cumulative, all runs) ==="


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
    # --- cumulative "what's in the account" — structured, plus the raw text ---
    try:
        raw_lines = report_lib.report_lines()
        counts = report_lib.tracked_counts()
    except Exception as e:  # noqa: BLE001
        raw_lines, counts = [f"(could not read tracked records: {e})"], {}

    rows = []
    for entity in sorted(counts):
        target = report_lib.target_for(entity)
        rows.append({
            "entity": entity,
            "created": counts[entity],
            "target": target,
            "short": target is not None and counts[entity] < target,
        })
    with_target = [r for r in rows if r["target"] is not None]
    summary = {
        "total": sum(counts.values()),
        "entities": len(with_target),
        "below_target": sum(1 for r in with_target if r["short"]),
    }

    # --- last live seeding run (run-specific part only) ---
    last_run = {"generated_at": None, "text": None}
    try:
        path = report_lib.REPORT_PATH
        if path.exists():
            text = path.read_text(encoding="utf-8")
            head = text.split(_CUMULATIVE_MARKER, 1)[0].rstrip()
            last_run = {"generated_at": _first_iso(text), "text": head or text}
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
        "report_text": "\n".join(raw_lines),
        "last_run": last_run,
        "outputs": outputs,
        "outputs_summary": outputs_summary,
        # kept for anything still reading the old shape
        "report_lines": raw_lines,
        "last_run_report": last_run["text"],
    }
