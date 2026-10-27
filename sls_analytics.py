import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone


DEFAULT_REGION = "cn-hangzhou"
DEFAULT_PROJECT = "zyb-telemetry"
DEFAULT_LOGSTORE = "events"
DEFAULT_HISTORY_DAYS = 3650
DEFAULT_SAVINGS_RATES = {"zipvid": 0.5}
BUSINESS_TIMEZONE = timezone(timedelta(hours=8))
SUCCESS_STATUSES = {"success", "succeeded", "completed", "ok"}


class SLSAnalyticsError(RuntimeError):
    pass


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _payload(log):
    value = log.get("payload_json", {})
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _timestamp(log):
    value = log.get("created_at") or log.get("server_received_at")
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    value = _number(log.get("__time__") or log.get("ts"))
    if value > 10_000_000_000:
        value /= 1000
    return datetime.fromtimestamp(value, timezone.utc) if value else None


def _is_success(payload):
    status = str(payload.get("status") or "").strip().lower()
    return not status or status in SUCCESS_STATUSES


def _empty_period():
    return {
        "install_ids": set(),
        "usage_count": 0,
        "task_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "saved_seconds": 0.0,
    }


def _finalize_period(period):
    finished_count = period["success_count"] + period["failure_count"]
    return {
        "user_count": len(period["install_ids"]),
        "usage_count": period["usage_count"],
        "task_count": period["task_count"],
        "success_count": period["success_count"],
        "failure_count": period["failure_count"],
        "success_rate": round(period["success_count"] / finished_count * 100, 1) if finished_count else 0,
        "failure_rate": round(period["failure_count"] / finished_count * 100, 1) if finished_count else 0,
        "saved_seconds": round(period["saved_seconds"]),
    }


def summarize_logs(logs, now=None, savings_rates=None):
    now = (now or datetime.now(BUSINESS_TIMEZONE)).astimezone(BUSINESS_TIMEZONE)
    savings_rates = savings_rates or DEFAULT_SAVINGS_RATES
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    applications = defaultdict(lambda: {"history": _empty_period(), "week": _empty_period()})
    totals = {"history": _empty_period(), "week": _empty_period()}

    for log in logs:
        app_id = str(log.get("app_id") or "").strip()
        if not app_id:
            continue
        event_type = log.get("event_type")
        if event_type not in {"transcode_started", "transcode_finished"}:
            continue
        periods = [applications[app_id]["history"], totals["history"]]
        timestamp = _timestamp(log)
        if timestamp and week_start <= timestamp <= now:
            periods.extend([applications[app_id]["week"], totals["week"]])
        install_id = str(log.get("install_id") or "").strip()
        payload = _payload(log)

        for period in periods:
            if event_type == "transcode_started":
                if install_id:
                    period["install_ids"].add(install_id)
                period["usage_count"] += 1
                period["task_count"] += 1
            elif _is_success(payload):
                period["success_count"] += 1
                period["saved_seconds"] += (
                    _number(payload.get("transcode_duration_sec")) * _number(savings_rates.get(app_id))
                )
            else:
                period["failure_count"] += 1

    rows = [
        {
            "app_id": app_id,
            "history": _finalize_period(periods["history"]),
            "week": _finalize_period(periods["week"]),
        }
        for app_id, periods in applications.items()
    ]
    rows.sort(key=lambda item: (-item["week"]["task_count"], -item["history"]["task_count"], item["app_id"]))
    return {
        "configured": True,
        "week_start": week_start.strftime("%Y-%m-%d"),
        "totals": {name: _finalize_period(period) for name, period in totals.items()},
        "applications": rows,
    }


def fetch_logs(days=None):
    access_key_id = os.getenv("ALIYUN_ACCESS_KEY_ID", "")
    access_key_secret = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
    if not access_key_id or not access_key_secret:
        raise SLSAnalyticsError("尚未配置阿里云 SLS 访问凭据")

    try:
        from aliyun.log import LogClient
    except ImportError as exc:
        raise SLSAnalyticsError("缺少 aliyun-log-python-sdk 依赖") from exc

    region = os.getenv("ALIYUN_SLS_REGION", DEFAULT_REGION)
    endpoint = os.getenv("ALIYUN_SLS_ENDPOINT", f"{region}.log.aliyuncs.com")
    project = os.getenv("ALIYUN_SLS_PROJECT", DEFAULT_PROJECT)
    logstore = os.getenv("ALIYUN_SLS_LOGSTORE", DEFAULT_LOGSTORE)
    end_time = int(time.time())
    history_days = int(os.getenv("ALIYUN_SLS_HISTORY_DAYS", str(DEFAULT_HISTORY_DAYS)))
    requested_days = history_days if days is None else int(days)
    start_time = end_time - max(1, requested_days) * 86400

    client = LogClient(endpoint, access_key_id, access_key_secret, region=region)
    try:
        responses = client.get_log_all_v2(
            project,
            logstore,
            start_time,
            end_time,
            query="event_type: transcode_started OR event_type: transcode_finished",
            forward=True,
        )
        return [dict(item.get_contents()) for response in responses for item in response.get_logs()]
    except Exception as exc:
        raise SLSAnalyticsError(f"读取阿里云 SLS 失败：{exc}") from exc


def load_dashboard():
    configured_rates = os.getenv("SOFTWARE_SAVINGS_RATES_JSON", "")
    try:
        savings_rates = json.loads(configured_rates) if configured_rates else DEFAULT_SAVINGS_RATES
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SLSAnalyticsError("SOFTWARE_SAVINGS_RATES_JSON 不是有效的 JSON 对象") from exc
    if not isinstance(savings_rates, dict):
        raise SLSAnalyticsError("SOFTWARE_SAVINGS_RATES_JSON 必须是 JSON 对象")
    return summarize_logs(fetch_logs(), savings_rates=savings_rates)
