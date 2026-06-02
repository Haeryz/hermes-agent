import json
import py_compile
import runpy
import subprocess
from pathlib import Path

from tools import putusan_crawler_tool as pct


def _fake_crawler_root(tmp_path: Path) -> Path:
    root = tmp_path / "sinergi"
    (root / "crawler").mkdir(parents=True)
    (root / "crawler" / "cli.py").write_text("", encoding="utf-8")
    (root / "crawler" / "crawler.py").write_text("", encoding="utf-8")
    (root / "main.py").write_text("", encoding="utf-8")
    (root / "crawl-putusan.ps1").write_text("Write-Host ok\n", encoding="utf-8")
    return root


def test_putusan_crawler_download_builds_main_py_command(
    tmp_path, monkeypatch
) -> None:
    root = _fake_crawler_root(tmp_path)
    monkeypatch.setenv("HERMES_PUTUSAN_CRAWLER_ROOT", str(root))
    monkeypatch.setattr(pct.shutil, "which", lambda name: "uv" if name == "uv" else None)

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"downloaded": 2, "target": 2, "output_paths": ["a.pdf"]}\n',
            stderr="",
        )

    monkeypatch.setattr(pct.subprocess, "run", fake_run)

    result = json.loads(
        pct.run_putusan_crawler(
            {
                "action": "download",
                "target_downloads": 2,
                "max_candidates": 5,
                "headless": True,
                "process_timeout_seconds": 45,
            }
        )
    )

    assert result["success"] is True
    assert result["summary"]["downloaded"] == 2
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert kwargs["cwd"] == str(root)
    assert kwargs["timeout"] == 45
    assert cmd[:4] == ["uv", "run", "python", str(root / "main.py")]
    assert cmd[4] == "crawl"
    assert "--json-summary" in cmd
    assert "--plain" in cmd
    assert "--target-downloads" in cmd
    assert cmd[cmd.index("--target-downloads") + 1] == "2"
    assert "--max-candidates" in cmd
    assert cmd[cmd.index("--max-candidates") + 1] == "5"
    assert "--headless" in cmd
    assert "--no-refresh-profile-snapshot" in cmd


def test_putusan_cron_wrapper_delegates_to_main_py_entrypoint(
    tmp_path, monkeypatch
) -> None:
    root = _fake_crawler_root(tmp_path)
    scripts_dir = tmp_path / "scripts"

    script_name = pct.write_putusan_cron_wrapper(
        root,
        {
            "action": "download",
            "target_downloads": 2,
            "max_candidates": 5,
            "out_dir": "downloads/kasus anak",
            "silent_if_unchanged": False,
        },
        scripts_dir,
        script_name="putusan_crawler_test.py",
    )

    wrapper = scripts_dir / script_name
    text = wrapper.read_text(encoding="utf-8")
    assert script_name == "putusan_crawler_test.py"
    assert "uv" in text
    assert "main.py" in text
    assert "VIRTUAL_ENV" in text
    assert "json.loads" in text
    py_compile.compile(str(wrapper), doraise=True)
    config = runpy.run_path(str(wrapper))["CONFIG"]
    assert config["target_downloads"] == "2"
    assert config["max_candidates"] == 5
    assert config["silent_if_unchanged"] is False


def test_putusan_crawler_count_uses_count_only_without_target(
    tmp_path, monkeypatch
) -> None:
    root = _fake_crawler_root(tmp_path)
    monkeypatch.setenv("HERMES_PUTUSAN_CRAWLER_ROOT", str(root))
    monkeypatch.setattr(pct.shutil, "which", lambda name: "uv" if name == "uv" else None)

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"total_downloadable": 10, "remaining": 7}\n',
            stderr="",
        )

    monkeypatch.setattr(pct.subprocess, "run", fake_run)

    result = json.loads(pct.run_putusan_crawler({"action": "count"}))

    assert result["success"] is True
    assert result["summary"]["remaining"] == 7
    cmd = calls[0]
    assert "--count-only" in cmd
    assert "--target-downloads" not in cmd
    assert "--download-all" not in cmd


def test_putusan_crawler_stats_aggregates_local_crawl_state(
    tmp_path, monkeypatch
) -> None:
    root = _fake_crawler_root(tmp_path)
    monkeypatch.setenv("HERMES_PUTUSAN_CRAWLER_ROOT", str(root))

    out_dir = root / "downloads" / "kasus anak"
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True)
    pdf_path = pdf_dir / "case.pdf"
    pdf_path.write_bytes(b"%PDF-123456")
    (out_dir / "downloaded.jsonl").write_text(
        json.dumps(
            {
                "status": "downloaded",
                "detail_url": "https://putusan3.mahkamahagung.go.id/direktori/putusan/abc.html",
                "output_path": "pdfs/case.pdf",
                "title": "Putusan PN Anak",
                "timestamp": "2026-06-01T01:02:03+00:00",
            }
        )
        + "\n"
        + "{bad json}\n",
        encoding="utf-8",
    )
    (out_dir / "skipped.jsonl").write_text(
        json.dumps(
            {
                "status": "failed",
                "detail_url": "https://putusan3.mahkamahagung.go.id/direktori/putusan/def.html",
                "error": "network timeout",
                "timestamp": "2026-06-01T02:02:03+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = json.loads(
        pct.run_putusan_crawler(
            {"action": "stats", "out_dir": "downloads/kasus anak"}
        )
    )

    assert result["success"] is True
    stats = result["stats"]
    assert stats["downloaded_records"] == 1
    assert stats["unique_detail_urls"] == 1
    assert stats["skipped_records"] == 1
    assert stats["invalid_downloaded_lines"] == 1
    assert stats["pdf_files"] == 1
    assert stats["total_pdf_bytes"] == len(b"%PDF-123456")
    assert stats["downloaded_by_day"] == {"2026-06-01": 1}
    assert stats["top_errors"] == [{"error": "network timeout", "count": 1}]
    assert stats["latest_downloads"][0]["title"] == "Putusan PN Anak"


def test_putusan_crawler_monitor_reports_delta_and_persists_snapshot(
    tmp_path, monkeypatch
) -> None:
    root = _fake_crawler_root(tmp_path)
    monkeypatch.setenv("HERMES_PUTUSAN_CRAWLER_ROOT", str(root))

    out_dir = root / "downloads" / "kasus anak"
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "case.pdf").write_bytes(b"%PDF-a")
    (out_dir / "downloaded.jsonl").write_text(
        json.dumps(
            {
                "status": "downloaded",
                "detail_url": "https://putusan3.mahkamahagung.go.id/direktori/putusan/abc.html",
                "output_path": "pdfs/case.pdf",
                "timestamp": "2026-06-01T01:02:03+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    first = json.loads(
        pct.run_putusan_crawler(
            {"action": "monitor", "out_dir": "downloads/kasus anak"}
        )
    )
    second = json.loads(
        pct.run_putusan_crawler(
            {"action": "monitor", "out_dir": "downloads/kasus anak"}
        )
    )

    assert first["success"] is True
    assert first["monitor"]["changed"] is True
    assert second["monitor"]["changed"] is False
    assert second["monitor"]["delta"]["downloaded_records"] == 0
    assert Path(second["monitor"]["snapshot_path"]).exists()


def test_putusan_crawler_schedule_creates_constrained_cron_job(
    tmp_path, monkeypatch
) -> None:
    import hermes_constants

    root = _fake_crawler_root(tmp_path)
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_PUTUSAN_CRAWLER_ROOT", str(root))
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: hermes_home)

    captured = {}

    def fake_create(payload):
        captured.update(payload)
        return json.dumps(
            {
                "success": True,
                "job": {
                    "job_id": "job-123",
                    "name": payload["name"],
                    "schedule": payload["schedule"],
                },
            }
        )

    monkeypatch.setattr(pct, "_create_cronjob", fake_create)

    result = json.loads(
        pct.run_putusan_crawler(
            {
                "action": "schedule",
                "schedule": "every 6h",
                "scheduled_action": "download",
                "target_downloads": 3,
                "max_candidates": 25,
                "headless": True,
                "name": "ABH crawler",
            }
        )
    )

    assert result["success"] is True
    assert result["cron_job"]["job_id"] == "job-123"
    assert captured["action"] == "create"
    assert captured["schedule"] == "every 6h"
    assert captured["name"] == "ABH crawler"
    assert captured["enabled_toolsets"] == ["putusan_crawler"]
    assert captured["workdir"] == str(root)
    assert captured["script"].startswith("putusan_crawler_")
    assert captured["script"].endswith(".py")
    assert captured["no_agent"] is True
    wrapper = hermes_home / "scripts" / captured["script"]
    assert wrapper.exists()
    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert "main.py" in wrapper_text
    assert "VIRTUAL_ENV" in wrapper_text


def test_putusan_crawler_rejects_non_putusan_start_url(tmp_path, monkeypatch) -> None:
    root = _fake_crawler_root(tmp_path)
    monkeypatch.setenv("HERMES_PUTUSAN_CRAWLER_ROOT", str(root))
    monkeypatch.setattr(pct.shutil, "which", lambda name: "uv" if name == "uv" else None)

    result = json.loads(
        pct.run_putusan_crawler(
            {"action": "count", "start_url": "https://example.com/index.html"}
        )
    )

    assert result["success"] is False
    assert "start_url must be a Putusan MA listing URL" in result["error"]


def test_putusan_crawler_registered_in_toolsets() -> None:
    from toolsets import TOOLSETS, _HERMES_CORE_TOOLS
    from tools.delegate_tool import _expand_parent_toolsets
    from tools.registry import registry

    assert "putusan_crawler" in _HERMES_CORE_TOOLS
    assert TOOLSETS["putusan_crawler"]["tools"] == ["putusan_crawler"]
    assert "putusan_crawler" in _expand_parent_toolsets({"hermes-cli"})
    assert registry.get_entry("putusan_crawler") is not None
