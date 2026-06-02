"""Hermes wrapper for the Sinergi Putusan MA crawler."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.registry import registry, tool_error, tool_result


ALLOWED_HOST = "putusan3.mahkamahagung.go.id"
DEFAULT_START_URL = (
    "https://putusan3.mahkamahagung.go.id/direktori/index/"
    "kategori/peradilan-anak-abh-1.html"
)
DEFAULT_OUT_DIR = "downloads/kasus anak"
DEFAULT_CASE_TITLE_PREFIX = "Putusan PN"
DEFAULT_SCRIPT_NAME = "crawl-putusan.ps1"
DEFAULT_ENTRYPOINT_NAME = "main.py"
DEFAULT_PROCESS_TIMEOUT_SECONDS = 1800
VALID_ACTIONS = {"count", "download", "stats", "monitor", "schedule"}
VALID_BROWSER_BACKENDS = {
    "managed-chrome",
    "undetected-chrome",
    "playwright",
    "playwright-cdp",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _candidate_roots() -> list[Path]:
    candidates: list[Path] = []
    env_root = os.getenv("HERMES_PUTUSAN_CRAWLER_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser())

    hermes_root = _repo_root()
    candidates.extend(
        [
            hermes_root,
            hermes_root.parent,
            Path.cwd(),
            Path.cwd().parent,
        ]
    )

    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _is_crawler_root(path: Path) -> bool:
    return (
        (path / "crawler" / "cli.py").is_file()
        and (path / "crawler" / "crawler.py").is_file()
        and (path / "main.py").is_file()
    )


def _find_crawler_root() -> Path | None:
    for candidate in _candidate_roots():
        if _is_crawler_root(candidate):
            return candidate
    return None


def _find_crawler_script(crawler_root: Path) -> Path | None:
    script = crawler_root / DEFAULT_SCRIPT_NAME
    return script if script.is_file() else None


def _find_crawler_entrypoint(crawler_root: Path) -> Path | None:
    entrypoint = crawler_root / DEFAULT_ENTRYPOINT_NAME
    return entrypoint if entrypoint.is_file() else None


def check_putusan_crawler_requirements() -> bool:
    return _find_crawler_root() is not None and shutil.which("uv") is not None


def _validate_listing_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != ALLOWED_HOST
        or not parsed.path.startswith("/direktori/index/")
        or not parsed.path.lower().endswith(".html")
    ):
        raise ValueError(
            "start_url must be a Putusan MA listing URL under "
            f"https://{ALLOWED_HOST}/direktori/index/"
        )
    return url


def _validate_detail_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != ALLOWED_HOST
        or not parsed.path.startswith("/direktori/putusan/")
        or not parsed.path.lower().endswith(".html")
    ):
        raise ValueError(
            "detail_urls must be Putusan MA detail URLs under "
            f"https://{ALLOWED_HOST}/direktori/putusan/"
        )
    return url


def _optional_positive_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be greater than 0")
    return parsed


def _optional_nonnegative_float(value: Any, field: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be zero or greater")
    return parsed


def _bool_arg(args: dict[str, Any], field: str, default: bool = False) -> bool:
    value = args.get(field, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _coerce_detail_urls(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("detail_urls must be a list of URL strings")
    return [_validate_detail_url(str(item).strip()) for item in value if str(item).strip()]


def _append_option(cmd: list[str], flag: str, value: Any) -> None:
    if value not in (None, ""):
        cmd.extend([flag, str(value)])


def _resolve_out_dir(crawler_root: Path, raw_out_dir: Any) -> Path:
    raw = str(raw_out_dir or DEFAULT_OUT_DIR).strip() or DEFAULT_OUT_DIR
    path = Path(raw)
    if not path.is_absolute():
        path = crawler_root / path
    resolved_root = crawler_root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("out_dir must resolve inside the Putusan crawler root") from exc
    return resolved_path


def _out_dir_arg(crawler_root: Path, raw_out_dir: Any) -> str:
    out_dir = _resolve_out_dir(crawler_root, raw_out_dir)
    try:
        return str(out_dir.relative_to(crawler_root.resolve()))
    except ValueError:
        return str(out_dir)


def _download_target_arg(args: dict[str, Any]) -> str:
    if _bool_arg(args, "download_all"):
        return "all"
    return str(
        _optional_positive_int(args.get("target_downloads"), "target_downloads")
        or 10
    )


def write_putusan_cron_wrapper(
    crawler_root: Path,
    args: dict[str, Any],
    scripts_dir: Path,
    *,
    script_name: str | None = None,
) -> str:
    """Write a Hermes cron-safe Python wrapper for the Putusan crawler.

    Cron scripts are intentionally restricted to HERMES_HOME/scripts. The real
    Sinergi crawler entry point for this checkout is ``main.py``; the
    ``sinergi`` console script points at a package name that is not present in
    the local tree. Scheduled dashboard jobs therefore use a small trusted
    wrapper inside the allowed scripts directory.
    """
    action = str(args.get("action") or "download").strip().lower()
    if action != "download":
        raise ValueError("Putusan cron wrapper only supports action='download'")

    start_url = _validate_listing_url(str(args.get("start_url") or DEFAULT_START_URL))
    out_dir = _out_dir_arg(crawler_root, args.get("out_dir"))
    entrypoint = _find_crawler_entrypoint(crawler_root)
    if entrypoint is None:
        raise FileNotFoundError(f"Could not find {DEFAULT_ENTRYPOINT_NAME} in {crawler_root}")

    max_candidates = _optional_positive_int(args.get("max_candidates"), "max_candidates")
    config = {
        "crawler_root": str(crawler_root.resolve()),
        "entrypoint": str(entrypoint.resolve()),
        "start_url": start_url,
        "target_downloads": _download_target_arg(args),
        "out_dir": out_dir,
        "timeout_seconds": _optional_positive_int(args.get("timeout_seconds"), "timeout_seconds") or 120,
        "manual_clearance_timeout_seconds": (
            _optional_positive_int(
                args.get("manual_clearance_timeout_seconds"),
                "manual_clearance_timeout_seconds",
            )
            or 120
        ),
        "max_candidates": max_candidates or 0,
        "silent_if_unchanged": _bool_arg(args, "silent_if_unchanged", default=False),
    }

    scripts_dir.mkdir(parents=True, exist_ok=True)
    safe_name = script_name or f"putusan_crawler_{uuid.uuid4().hex}.py"
    if Path(safe_name).name != safe_name or not safe_name.endswith(".py"):
        raise ValueError("script_name must be a plain .py filename")
    wrapper_path = scripts_dir / safe_name
    wrapper_path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import json",
                "import os",
                "import subprocess",
                "import sys",
                "from pathlib import Path",
                "",
                f"CONFIG = json.loads({json.dumps(json.dumps(config, indent=2))})",
                "",
                "",
                "def line_count(path: Path) -> int:",
                "    try:",
                '        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())',
                "    except OSError:",
                "        return 0",
                "",
                "",
                "def snapshot_counts() -> dict[str, int]:",
                '    out_dir = Path(CONFIG["crawler_root"]) / CONFIG["out_dir"]',
                '    pdf_dir = out_dir / "pdfs"',
                "    return {",
                '        "downloaded_lines": line_count(out_dir / "downloaded.jsonl"),',
                '        "skipped_lines": line_count(out_dir / "skipped.jsonl"),',
                '        "pdf_files": len(list(pdf_dir.glob("*.pdf"))) if pdf_dir.exists() else 0,',
                "    }",
                "",
                "",
                "def main() -> int:",
                "    before = snapshot_counts()",
                "    command = [",
                '        "uv",',
                '        "run",',
                '        "python",',
                '        CONFIG["entrypoint"],',
                '        "crawl",',
                '        "--start-url",',
                '        CONFIG["start_url"],',
                '        "--out-dir",',
                '        CONFIG["out_dir"],',
                '        "--timeout-seconds",',
                '        str(CONFIG["timeout_seconds"]),',
                '        "--manual-clearance-timeout-seconds",',
                '        str(CONFIG["manual_clearance_timeout_seconds"]),',
                '        "--case-title-prefix",',
                f'        "{DEFAULT_CASE_TITLE_PREFIX}",',
                '        "--no-refresh-profile-snapshot",',
                '        "--headed",',
                "    ]",
                '    if int(CONFIG.get("max_candidates") or 0) > 0:',
                '        command.extend(["--max-candidates", str(CONFIG["max_candidates"])])',
                '    target = str(CONFIG["target_downloads"])',
                '    if target.lower() == "all":',
                '        command.append("--download-all")',
                "    else:",
                '        command.extend(["--target-downloads", target])',
                "    env = os.environ.copy()",
                '    env.pop("VIRTUAL_ENV", None)',
                '    env.pop("VIRTUAL_ENV_PROMPT", None)',
                '    env.setdefault("PYTHONIOENCODING", "utf-8")',
                "    completed = subprocess.run(",
                "        command,",
                '        cwd=CONFIG["crawler_root"],',
                "        capture_output=True,",
                "        text=True,",
                '        encoding="utf-8",',
                '        errors="replace",',
                "        check=False,",
                "        env=env,",
                "    )",
                "    after = snapshot_counts()",
                '    if completed.returncode == 0 and CONFIG.get("silent_if_unchanged") and before == after:',
                "        return 0",
                "    if completed.stdout:",
                "        print(completed.stdout, end=\"\")",
                "    if completed.stderr:",
                "        print(completed.stderr, end=\"\", file=sys.stderr)",
                "    return int(completed.returncode)",
                "",
                "",
                'if __name__ == "__main__":',
                "    raise SystemExit(main())",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return safe_name


def _build_command(args: dict[str, Any]) -> tuple[list[str], Path, int]:
    crawler_root = _find_crawler_root()
    if crawler_root is None:
        searched = ", ".join(str(path) for path in _candidate_roots())
        raise FileNotFoundError(
            "Could not find the Putusan crawler root. Set "
            f"HERMES_PUTUSAN_CRAWLER_ROOT. Searched: {searched}"
        )

    uv = shutil.which("uv")
    if uv is None:
        raise FileNotFoundError("uv is required to run the Putusan crawler")

    action = str(args.get("action") or "download").strip().lower()
    if action not in VALID_ACTIONS:
        raise ValueError("action must be 'count' or 'download'")

    browser_backend = str(args.get("browser_backend") or "managed-chrome").strip()
    if browser_backend not in VALID_BROWSER_BACKENDS:
        raise ValueError(
            "browser_backend must be one of: "
            + ", ".join(sorted(VALID_BROWSER_BACKENDS))
        )

    start_url = _validate_listing_url(str(args.get("start_url") or DEFAULT_START_URL))
    detail_urls = _coerce_detail_urls(args.get("detail_urls"))

    process_timeout = (
        _optional_positive_int(
            args.get("process_timeout_seconds"), "process_timeout_seconds"
        )
        or DEFAULT_PROCESS_TIMEOUT_SECONDS
    )

    entrypoint = _find_crawler_entrypoint(crawler_root)
    if entrypoint is None:
        raise FileNotFoundError(f"Could not find {DEFAULT_ENTRYPOINT_NAME} in {crawler_root}")

    cmd = [
        uv,
        "run",
        "python",
        str(entrypoint),
        "crawl",
        "--json-summary",
        "--plain",
        "--start-url",
        start_url,
        "--out-dir",
        _out_dir_arg(crawler_root, args.get("out_dir")),
        "--browser-backend",
        browser_backend,
    ]

    _append_option(
        cmd,
        "--timeout-seconds",
        _optional_positive_int(args.get("timeout_seconds"), "timeout_seconds") or 120,
    )
    _append_option(
        cmd,
        "--manual-clearance-timeout-seconds",
        _optional_positive_int(
            args.get("manual_clearance_timeout_seconds"),
            "manual_clearance_timeout_seconds",
        )
        or 120,
    )
    _append_option(
        cmd,
        "--retry-attempts",
        _optional_positive_int(args.get("retry_attempts"), "retry_attempts") or 3,
    )
    _append_option(
        cmd,
        "--parallel-downloads",
        _optional_positive_int(args.get("parallel_downloads"), "parallel_downloads")
        or 1,
    )
    _append_option(
        cmd,
        "--count-parallel-pages",
        _optional_positive_int(args.get("count_parallel_pages"), "count_parallel_pages")
        or 16,
    )
    _append_option(
        cmd,
        "--fast-fetch-timeout-seconds",
        _optional_positive_int(
            args.get("fast_fetch_timeout_seconds"), "fast_fetch_timeout_seconds"
        )
        or 15,
    )
    _append_option(
        cmd,
        "--delay-seconds",
        _optional_nonnegative_float(args.get("delay_seconds"), "delay_seconds") or 0.0,
    )
    _append_option(cmd, "--max-candidates", _optional_positive_int(args.get("max_candidates"), "max_candidates"))
    _append_option(cmd, "--chrome-profile", args.get("chrome_profile") or "Profile 4")
    _append_option(cmd, "--case-title-prefix", args.get("case_title_prefix") or DEFAULT_CASE_TITLE_PREFIX)

    for detail_url in detail_urls:
        cmd.extend(["--detail-url", detail_url])

    if _bool_arg(args, "no_listing"):
        cmd.append("--no-listing")
    if _bool_arg(args, "include_unpublished_listing_items"):
        cmd.append("--include-unpublished-listing-items")
    if not _bool_arg(args, "refresh_profile_snapshot", default=False):
        cmd.append("--no-refresh-profile-snapshot")
    cmd.append("--headless" if _bool_arg(args, "headless") else "--headed")

    if action == "count":
        cmd.append("--count-only")
    else:
        if _bool_arg(args, "download_all"):
            cmd.append("--download-all")
        else:
            cmd.extend(["--target-downloads", _download_target_arg(args)])

    return cmd, crawler_root, process_timeout


def _tail(text: str | bytes | None, limit: int = 12000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text[-limit:]


def _parse_json_summary(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0

    records: list[dict[str, Any]] = []
    invalid = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            invalid += 1
    return records, invalid


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    except OSError:
        return None


def _file_state(path: Path) -> dict[str, Any]:
    exists = path.exists()
    stat = None
    if exists:
        try:
            stat = path.stat()
        except OSError:
            stat = None
    return {
        "path": str(path),
        "exists": exists,
        "size": stat.st_size if stat else 0,
        "mtime": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat() if stat else None,
    }


def _latest_pdf_state(pdf_dir: Path) -> dict[str, Any] | None:
    if not pdf_dir.exists():
        return None
    try:
        pdfs = [path for path in pdf_dir.glob("*.pdf") if path.is_file()]
    except OSError:
        return None
    if not pdfs:
        return None
    latest = max(pdfs, key=lambda path: path.stat().st_mtime)
    return _file_state(latest)


def _record_output_path(out_dir: Path, record: dict[str, Any]) -> Path | None:
    raw_path = str(record.get("output_path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return path
    if path.exists():
        return path
    candidates = [
        out_dir / path,
        out_dir.parent / path,
        out_dir.parent.parent / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _day_from_timestamp(record: dict[str, Any]) -> str:
    timestamp = str(record.get("timestamp") or "").strip()
    if len(timestamp) >= 10:
        return timestamp[:10]
    return "unknown"


def _timestamp_sort_key(record: dict[str, Any]) -> str:
    return str(record.get("timestamp") or "")


def _record_event(
    record: dict[str, Any],
    *,
    event_type: str,
    out_dir: Path,
) -> dict[str, Any]:
    output_path = _record_output_path(out_dir, record)
    return {
        "type": event_type,
        "status": record.get("status"),
        "timestamp": record.get("timestamp"),
        "title": record.get("title"),
        "detail_url": record.get("detail_url"),
        "output_path": record.get("output_path"),
        "error": record.get("error"),
        "output_exists": bool(output_path and output_path.exists()),
    }


def _build_local_stats(crawler_root: Path, args: dict[str, Any]) -> dict[str, Any]:
    out_dir = _resolve_out_dir(crawler_root, args.get("out_dir"))
    downloaded, invalid_downloaded = _read_jsonl(out_dir / "downloaded.jsonl")
    skipped, invalid_skipped = _read_jsonl(out_dir / "skipped.jsonl")

    downloaded_records = [
        record for record in downloaded if record.get("status") == "downloaded"
    ]
    skipped_by_status = Counter(str(record.get("status") or "unknown") for record in skipped)
    skipped_errors = Counter(
        str(record.get("error") or "unknown")[:240] for record in skipped
    )
    downloaded_by_day = Counter(_day_from_timestamp(record) for record in downloaded_records)

    output_paths: set[Path] = set()
    missing_output_paths = 0
    for record in downloaded_records:
        path = _record_output_path(out_dir, record)
        if path is None:
            missing_output_paths += 1
            continue
        if path.exists():
            output_paths.add(path.resolve())
        else:
            missing_output_paths += 1

    pdf_dir = out_dir / "pdfs"
    pdf_files = sorted(pdf_dir.glob("*.pdf")) if pdf_dir.exists() else []
    for path in pdf_files:
        output_paths.add(path.resolve())

    total_pdf_bytes = 0
    for path in output_paths:
        try:
            total_pdf_bytes += path.stat().st_size
        except OSError:
            missing_output_paths += 1

    latest_downloads = []
    latest_limit = _optional_positive_int(args.get("latest_limit"), "latest_limit") or 10
    for record in sorted(downloaded_records, key=_timestamp_sort_key, reverse=True)[
        :latest_limit
    ]:
        latest_downloads.append(
            {
                "timestamp": record.get("timestamp"),
                "title": record.get("title"),
                "detail_url": record.get("detail_url"),
                "output_path": record.get("output_path"),
            }
        )

    latest_events = [
        _record_event(record, event_type="downloaded", out_dir=out_dir)
        for record in downloaded_records
    ]
    latest_events.extend(
        _record_event(record, event_type="skipped", out_dir=out_dir)
        for record in skipped
    )
    latest_events = sorted(latest_events, key=_timestamp_sort_key, reverse=True)[
        :latest_limit
    ]

    file_states = {
        "downloaded_jsonl": _file_state(out_dir / "downloaded.jsonl"),
        "skipped_jsonl": _file_state(out_dir / "skipped.jsonl"),
        "pdf_dir": _file_state(pdf_dir),
        "latest_pdf": _latest_pdf_state(pdf_dir),
    }
    latest_activity_at = max(
        (
            str(item.get("mtime") or "")
            for item in file_states.values()
            if isinstance(item, dict) and item.get("mtime")
        ),
        default=None,
    )

    return {
        "out_dir": str(out_dir),
        "downloaded_records": len(downloaded_records),
        "unique_detail_urls": len(
            {
                str(record.get("detail_url"))
                for record in downloaded_records
                if record.get("detail_url")
            }
        ),
        "skipped_records": len(skipped),
        "invalid_downloaded_lines": invalid_downloaded,
        "invalid_skipped_lines": invalid_skipped,
        "pdf_files": len(output_paths),
        "total_pdf_bytes": total_pdf_bytes,
        "total_pdf_mb": round(total_pdf_bytes / (1024 * 1024), 3),
        "missing_output_paths": missing_output_paths,
        "downloaded_by_day": dict(sorted(downloaded_by_day.items())),
        "skipped_by_status": dict(sorted(skipped_by_status.items())),
        "top_errors": [
            {"error": error, "count": count}
            for error, count in skipped_errors.most_common(5)
        ],
        "latest_downloads": latest_downloads,
        "latest_events": latest_events,
        "files": file_states,
        "latest_activity_at": latest_activity_at,
    }


def detect_putusan_runtime(crawler_root: Path) -> dict[str, Any]:
    """Best-effort process detection for the local Putusan crawler."""
    root_text = str(crawler_root).lower()
    needles = [
        DEFAULT_SCRIPT_NAME.lower(),
        DEFAULT_ENTRYPOINT_NAME.lower(),
        "sinergi crawl",
        "crawler.cli",
        "putusan_crawler_",
        root_text,
    ]

    if sys.platform == "win32":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            return {"running": False, "processes": [], "error": "powershell not found"}
        command = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -and "
            "($_.CommandLine -match 'crawl-putusan\\.ps1|main\\.py|sinergi crawl|crawler\\.cli|putusan_crawler_') } | "
            "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
        )
        argv = [powershell, "-NoProfile", "-Command", command]
    else:
        ps = shutil.which("ps")
        if not ps:
            return {"running": False, "processes": [], "error": "ps not found"}
        argv = [ps, "-eo", "pid=,args="]

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=False,
        )
    except Exception as exc:
        return {"running": False, "processes": [], "error": str(exc)}

    processes: list[dict[str, Any]] = []
    if sys.platform == "win32":
        text = (completed.stdout or "").strip()
        if text:
            try:
                value = json.loads(text)
                items = value if isinstance(value, list) else [value]
            except json.JSONDecodeError:
                items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                cmdline = str(item.get("CommandLine") or "")
                if "Get-CimInstance Win32_Process" in cmdline:
                    continue
                if any(needle in cmdline.lower() for needle in needles):
                    processes.append(
                        {
                            "pid": item.get("ProcessId"),
                            "command": cmdline[:500],
                        }
                    )
    else:
        for line in (completed.stdout or "").splitlines():
            lower = line.lower()
            if any(needle in lower for needle in needles):
                parts = line.strip().split(None, 1)
                processes.append(
                    {
                        "pid": int(parts[0]) if parts and parts[0].isdigit() else None,
                        "command": (parts[1] if len(parts) > 1 else line.strip())[:500],
                    }
                )

    return {"running": bool(processes), "processes": processes[:5]}


def _monitor_state_path(out_dir: Path) -> Path:
    return out_dir / ".hermes_putusan_monitor.json"


def _monitor_stats(crawler_root: Path, args: dict[str, Any]) -> dict[str, Any]:
    stats = _build_local_stats(crawler_root, args)
    out_dir = Path(stats["out_dir"])
    snapshot_path = _monitor_state_path(out_dir)
    previous: dict[str, Any] | None = None
    if snapshot_path.exists():
        try:
            previous_value = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if isinstance(previous_value, dict):
                previous = previous_value
        except (OSError, json.JSONDecodeError):
            previous = None

    comparable_keys = [
        "downloaded_records",
        "unique_detail_urls",
        "skipped_records",
        "pdf_files",
        "total_pdf_bytes",
        "missing_output_paths",
    ]
    previous_stats = previous.get("stats", {}) if previous else {}
    delta = {
        key: int(stats.get(key, 0) or 0) - int(previous_stats.get(key, 0) or 0)
        for key in comparable_keys
    }
    changed = previous is None or any(value != 0 for value in delta.values())

    snapshot = {
        "updated_at": datetime.now(UTC).isoformat(),
        "stats": {key: stats.get(key) for key in comparable_keys},
    }
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    return {
        "changed": changed,
        "delta": delta,
        "current": stats,
        "previous": previous_stats or None,
        "snapshot_path": str(snapshot_path),
    }


def _cron_payload_for_schedule(args: dict[str, Any], crawler_root: Path) -> dict[str, Any]:
    schedule = str(args.get("schedule") or "").strip()
    if not schedule:
        raise ValueError("schedule is required when action='schedule'")

    scheduled_action = str(args.get("scheduled_action") or args.get("run_action") or "download").strip().lower()
    if scheduled_action not in {"count", "download", "monitor", "stats"}:
        raise ValueError("scheduled_action must be one of: count, download, monitor, stats")

    run_args = dict(args)
    for key in {
        "action",
        "schedule",
        "scheduled_action",
        "run_action",
        "name",
        "repeat",
        "deliver",
        "silent_if_unchanged",
    }:
        run_args.pop(key, None)
    run_args["action"] = scheduled_action
    run_args.setdefault("out_dir", DEFAULT_OUT_DIR)

    silent_if_unchanged = _bool_arg(args, "silent_if_unchanged", default=True)
    run_args["silent_if_unchanged"] = silent_if_unchanged
    run_args_json = json.dumps(run_args, ensure_ascii=False, sort_keys=True)
    if scheduled_action == "download":
        from hermes_constants import get_hermes_home

        wrapper = write_putusan_cron_wrapper(
            crawler_root,
            run_args,
            get_hermes_home() / "scripts",
        )
        return {
            "action": "create",
            "schedule": schedule,
            "prompt": (
                "Run the local Sinergi Putusan MA crawler via "
                f"{DEFAULT_SCRIPT_NAME}."
            ),
            "name": str(args.get("name") or "Putusan crawler download"),
            "repeat": args.get("repeat"),
            "deliver": args.get("deliver"),
            "script": wrapper,
            "no_agent": True,
            "enabled_toolsets": ["putusan_crawler"],
            "workdir": str(crawler_root),
        }

    prompt = (
        "Run the local Sinergi Putusan MA crawler on this schedule.\n\n"
        f"1. Call putusan_crawler with these exact JSON arguments:\n{run_args_json}\n"
        "2. After the crawl/count/stat action, call putusan_crawler again with "
        "action='monitor' and the same out_dir to collect aggregate totals, "
        "deltas, PDF byte totals, skipped/error counts, and latest downloads.\n"
        "3. Report a concise operational summary with the run result and the "
        "aggregate monitoring data. Include failures and remaining/count data "
        "when available."
    )
    if silent_if_unchanged:
        prompt += (
            "\n4. If the run succeeds, the monitor delta shows no changes, and "
            "there are no new failures, respond with exactly [SILENT]."
        )

    return {
        "action": "create",
        "schedule": schedule,
        "prompt": prompt,
        "name": str(args.get("name") or f"Putusan crawler {scheduled_action}"),
        "repeat": args.get("repeat"),
        "deliver": args.get("deliver"),
        "enabled_toolsets": ["putusan_crawler"],
        "workdir": str(crawler_root),
    }


def _create_cronjob(payload: dict[str, Any]) -> str:
    from tools.cronjob_tools import cronjob

    return cronjob(**payload)


def _schedule_crawler(args: dict[str, Any]) -> str:
    scheduled_action = str(
        args.get("scheduled_action") or args.get("run_action") or "download"
    ).strip().lower()
    crawler_root = _find_crawler_root()
    if crawler_root is None:
        searched = ", ".join(str(path) for path in _candidate_roots())
        return tool_error(
            "Could not find the Putusan crawler root. Set "
            f"HERMES_PUTUSAN_CRAWLER_ROOT. Searched: {searched}",
            success=False,
        )
    try:
        payload = _cron_payload_for_schedule(args, crawler_root)
        created = json.loads(_create_cronjob(payload))
    except Exception as exc:
        return tool_error(str(exc), success=False)

    if not created.get("success"):
        return json.dumps(created, indent=2)
    return tool_result(
        success=True,
        cron_job=created.get("job") or created,
        schedule=payload["schedule"],
        scheduled_action=scheduled_action,
        enabled_toolsets=payload["enabled_toolsets"],
        workdir=payload["workdir"],
    )


def run_putusan_crawler(args: dict[str, Any]) -> str:
    action = str(args.get("action") or "download").strip().lower()
    if action not in VALID_ACTIONS:
        return tool_error(
            "action must be one of: " + ", ".join(sorted(VALID_ACTIONS)),
            success=False,
        )

    if action == "schedule":
        return _schedule_crawler(args)

    crawler_root = _find_crawler_root()
    if action in {"stats", "monitor"}:
        if crawler_root is None:
            searched = ", ".join(str(path) for path in _candidate_roots())
            return tool_error(
                "Could not find the Putusan crawler root. Set "
                f"HERMES_PUTUSAN_CRAWLER_ROOT. Searched: {searched}",
                success=False,
            )
        try:
            if action == "stats":
                return tool_result(success=True, stats=_build_local_stats(crawler_root, args))
            return tool_result(success=True, monitor=_monitor_stats(crawler_root, args))
        except Exception as exc:
            return tool_error(str(exc), success=False)

    try:
        cmd, crawler_root, process_timeout = _build_command(args)
    except Exception as exc:
        return tool_error(str(exc), success=False)

    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.pop("VIRTUAL_ENV_PROMPT", None)
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(crawler_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=process_timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return tool_error(
            f"Putusan crawler timed out after {process_timeout}s",
            success=False,
            cwd=str(crawler_root),
            command=cmd,
            stdout_tail=_tail(exc.stdout),
            stderr_tail=_tail(exc.stderr),
        )
    except OSError as exc:
        return tool_error(str(exc), success=False, cwd=str(crawler_root), command=cmd)

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    summary = _parse_json_summary(stdout)
    return tool_result(
        success=completed.returncode == 0,
        returncode=completed.returncode,
        cwd=str(crawler_root),
        command=cmd,
        summary=summary,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
    )


PUTUSAN_CRAWLER_SCHEMA = {
    "name": "putusan_crawler",
    "description": (
        "Run the local Sinergi Putusan MA crawler as a controlled subagent tool. "
        "Use action='count' to inventory matching cases, action='download' to "
        "download PDFs, action='stats'/'monitor' for aggregate local crawl data, "
        "or action='schedule' to create a recurring crawler cron job."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(VALID_ACTIONS),
                "description": "count/download run the crawler, stats aggregates local state, monitor records deltas, schedule creates a cron job.",
            },
            "schedule": {
                "type": "string",
                "description": "Cron schedule for action='schedule', e.g. 'every 6h' or '0 9 * * *'.",
            },
            "scheduled_action": {
                "type": "string",
                "enum": ["count", "download", "monitor", "stats"],
                "description": "Crawler action the cron job should run when action='schedule'. Default: download.",
            },
            "name": {
                "type": "string",
                "description": "Human-friendly cron job name when action='schedule'.",
            },
            "repeat": {
                "type": "integer",
                "description": "Optional cron repeat count when action='schedule'. Omit for forever on recurring schedules.",
            },
            "deliver": {
                "type": "string",
                "description": "Optional cron delivery target when action='schedule'. Omit to auto-deliver to the origin chat.",
            },
            "silent_if_unchanged": {
                "type": "boolean",
                "description": "For scheduled jobs, tell the agent to emit [SILENT] when monitor deltas do not change.",
            },
            "target_downloads": {
                "type": "integer",
                "description": "Number of new PDFs to download when action='download'.",
            },
            "download_all": {
                "type": "boolean",
                "description": "Download all matching new PDFs instead of a target count.",
            },
            "start_url": {
                "type": "string",
                "description": "Putusan MA listing URL to start from.",
            },
            "out_dir": {
                "type": "string",
                "description": f"Output directory inside the crawler root. Default: {DEFAULT_OUT_DIR}.",
            },
            "latest_limit": {
                "type": "integer",
                "description": "Number of latest downloads to include for action='stats' or action='monitor'.",
            },
            "max_candidates": {
                "type": "integer",
                "description": "Stop after inspecting this many listing candidates.",
            },
            "detail_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific Putusan MA detail URLs to process before listing traversal.",
            },
            "no_listing": {
                "type": "boolean",
                "description": "Only process detail_urls and skip listing traversal.",
            },
            "headless": {
                "type": "boolean",
                "description": "Run browser automation headlessly. Default opens visible Chrome for manual clearance.",
            },
            "browser_backend": {
                "type": "string",
                "enum": sorted(VALID_BROWSER_BACKENDS),
                "description": "Crawler browser backend. managed-chrome is best for Cloudflare clearance.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Per-browser-operation timeout in seconds.",
            },
            "manual_clearance_timeout_seconds": {
                "type": "integer",
                "description": "Seconds to wait for manual Cloudflare clearance.",
            },
            "process_timeout_seconds": {
                "type": "integer",
                "description": "Overall subprocess timeout in seconds.",
            },
            "case_title_prefix": {
                "type": "string",
                "description": "Only queue listing items whose visible title starts with this prefix.",
            },
            "refresh_profile_snapshot": {
                "type": "boolean",
                "description": "Refresh the managed Chrome profile snapshot before running.",
            },
            "include_unpublished_listing_items": {
                "type": "boolean",
                "description": "Include listing items marked Unpublish.",
            },
            "parallel_downloads": {
                "type": "integer",
                "description": "Concurrent PDF downloads after browser clearance.",
            },
            "count_parallel_pages": {
                "type": "integer",
                "description": "Listing pages to fetch concurrently in count mode.",
            },
            "chrome_profile": {
                "type": "string",
                "description": "Chrome profile directory used by managed-chrome.",
            },
        },
        "required": ["action"],
    },
}


registry.register(
    name="putusan_crawler",
    toolset="putusan_crawler",
    schema=PUTUSAN_CRAWLER_SCHEMA,
    handler=lambda args, **kw: run_putusan_crawler(args),
    check_fn=check_putusan_crawler_requirements,
    max_result_size_chars=50_000,
)
