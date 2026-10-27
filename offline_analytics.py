import argparse
import csv
import gzip
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from sls_analytics import _number, _payload, _timestamp, summarize_logs


TASK_EVENTS = {"transcode_started", "transcode_finished"}
TASK_ID_FIELDS = ("task_id", "job_id", "transcode_id")


def _open_export(path):
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return path.open("r", encoding="utf-8-sig", newline="")


def read_export(path):
    with _open_export(Path(path)) as source:
        for row in csv.DictReader(source):
            overflow = row.pop(None, [])
            extra_values = [row.pop("", ""), *overflow]
            for value in extra_values:
                try:
                    extra = json.loads(value) if value else {}
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(extra, dict):
                    row.update(extra)
            yield row


def _duration_state(payload):
    value = payload.get("transcode_duration_sec")
    if value in (None, ""):
        return "missing"
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return "invalid"
    if not math.isfinite(duration) or duration < 0:
        return "invalid"
    return "valid"


def build_quality_report(all_logs):
    app_events = defaultdict(Counter)
    app_ids = Counter()
    event_types = Counter()
    statuses = Counter()
    timestamps = []
    started_count = 0
    finished_count = 0
    missing_install_ids = 0
    duration_states = Counter()
    task_id_count = 0

    for log in all_logs:
        app_id = str(log.get("app_id") or "(missing)").strip()
        event_type = str(log.get("event_type") or "(missing)").strip()
        app_ids[app_id] += 1
        event_types[event_type] += 1
        timestamp = _timestamp(log)
        if timestamp:
            timestamps.append(timestamp)
        if event_type not in TASK_EVENTS:
            continue

        app_events[app_id][event_type] += 1
        payload = _payload(log)
        if not str(log.get("install_id") or "").strip():
            missing_install_ids += 1
        if any(log.get(field) or payload.get(field) for field in TASK_ID_FIELDS):
            task_id_count += 1
        if event_type == "transcode_started":
            started_count += 1
        else:
            finished_count += 1
            status = str(payload.get("status") or "(empty)").strip().lower()
            statuses[status] += 1
            duration_states[_duration_state(payload)] += 1

    task_event_count = started_count + finished_count
    duration_problem_count = duration_states["missing"] + duration_states["invalid"]
    return {
        "source_log_count": len(all_logs),
        "task_event_count": task_event_count,
        "earliest_log_at": min(timestamps).isoformat() if timestamps else None,
        "latest_log_at": max(timestamps).isoformat() if timestamps else None,
        "app_ids": dict(sorted(app_ids.items())),
        "event_types": dict(sorted(event_types.items())),
        "finished_statuses": dict(sorted(statuses.items())),
        "started_count": started_count,
        "finished_count": finished_count,
        "started_finished_difference": started_count - finished_count,
        "by_application": {
            app_id: {
                "started_count": counts["transcode_started"],
                "finished_count": counts["transcode_finished"],
                "difference": counts["transcode_started"] - counts["transcode_finished"],
            }
            for app_id, counts in sorted(app_events.items())
        },
        "missing_install_id_count": missing_install_ids,
        "missing_install_id_rate": round(missing_install_ids / task_event_count * 100, 2)
        if task_event_count
        else 0,
        "finished_duration": {
            "valid_count": duration_states["valid"],
            "missing_count": duration_states["missing"],
            "invalid_count": duration_states["invalid"],
            "problem_rate": round(duration_problem_count / finished_count * 100, 2) if finished_count else 0,
        },
        "task_id": {
            "recognized_fields": list(TASK_ID_FIELDS),
            "present_count": task_id_count,
            "coverage_rate": round(task_id_count / task_event_count * 100, 2) if task_event_count else 0,
            "reliable_pairing_possible": bool(task_event_count and task_id_count == task_event_count),
        },
    }


def _write_summary_csv(summary, path):
    columns = [
        "scope",
        "app_id",
        "period",
        "user_count",
        "usage_count",
        "task_count",
        "success_count",
        "failure_count",
        "success_rate",
        "failure_rate",
        "saved_seconds",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        groups = [("total", "all", summary["totals"])] + [
            ("application", item["app_id"], item) for item in summary["applications"]
        ]
        for scope, app_id, periods in groups:
            for period_name in ("history", "week"):
                writer.writerow(
                    {"scope": scope, "app_id": app_id, "period": period_name, **periods[period_name]}
                )


def analyze_export(source_path, output_dir, now=None, savings_rates=None):
    all_logs = list(read_export(Path(source_path)))
    task_logs = [log for log in all_logs if log.get("event_type") in TASK_EVENTS]
    summary = summarize_logs(task_logs, now=now, savings_rates=savings_rates)
    quality = build_quality_report(all_logs)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = output_dir / "offline_summary.json"
    csv_path = output_dir / "offline_summary.csv"
    quality_path = output_dir / "data_quality.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    quality_path.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_summary_csv(summary, csv_path)
    return summary_path, csv_path, quality_path


def main():
    parser = argparse.ArgumentParser(description="Analyze an exported Alibaba Cloud SLS CSV locally.")
    parser.add_argument("source", type=Path, help="Path to a .csv or .csv.gz SLS export")
    parser.add_argument("--output-dir", type=Path, default=Path("data/offline"))
    parser.add_argument("--now", type=datetime.fromisoformat, help="Optional ISO timestamp for reproducible weekly data")
    args = parser.parse_args()
    paths = analyze_export(args.source, args.output_dir, now=args.now)
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()