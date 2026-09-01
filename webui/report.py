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


def gather_report() -> dict:
    try:
        lines = report_lib.report_lines()
    except Exception as e:  # noqa: BLE001
        lines = [f"(could not read tracked records: {e})"]

    last_run_report = None
    try:
        path = report_lib.REPORT_PATH
        if path.exists():
            last_run_report = path.read_text(encoding="utf-8")
    except OSError:
        last_run_report = None

    outputs = []
    try:
        out_dir = config.OUTPUT_DIR
        if out_dir.exists():
            for f in sorted(out_dir.glob("*.csv")):
                st = f.stat()
                outputs.append({"name": f.name, "size": st.st_size, "mtime": st.st_mtime})
    except OSError:
        pass

    return {"report_lines": lines, "last_run_report": last_run_report, "outputs": outputs}
