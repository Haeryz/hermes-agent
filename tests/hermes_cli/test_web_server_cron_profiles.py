"""Regression tests for dashboard cron job profile routing."""

import json
import runpy

import pytest
from fastapi import HTTPException


@pytest.fixture()
def isolated_profiles(tmp_path, monkeypatch):
    """Give profile discovery an isolated default home with one named profile."""
    from hermes_cli import profiles

    default_home = tmp_path / ".hermes"
    profiles_root = default_home / "profiles"
    worker_home = profiles_root / "worker_alpha"

    for home in (default_home, worker_home):
        (home / "cron").mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("model: test-model\n", encoding="utf-8")

    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profiles_root)
    return {"default": default_home, "worker_alpha": worker_home}


def test_call_cron_for_profile_routes_storage_and_restores_globals(isolated_profiles):
    from cron import jobs as cron_jobs
    from hermes_cli import web_server

    old_cron_dir = cron_jobs.CRON_DIR
    old_jobs_file = cron_jobs.JOBS_FILE
    old_output_dir = cron_jobs.OUTPUT_DIR

    job = web_server._call_cron_for_profile(
        "worker_alpha",
        "create_job",
        prompt="run scheduled task",
        schedule="every 1h",
        name="worker-alpha-scan",
    )

    assert job["profile"] == "worker_alpha"
    assert job["profile_name"] == "worker_alpha"
    assert job["hermes_home"] == str(isolated_profiles["worker_alpha"])
    assert job["is_default_profile"] is False
    assert (isolated_profiles["worker_alpha"] / "cron" / "jobs.json").exists()
    assert not (isolated_profiles["default"] / "cron" / "jobs.json").exists()

    assert cron_jobs.CRON_DIR == old_cron_dir
    assert cron_jobs.JOBS_FILE == old_jobs_file
    assert cron_jobs.OUTPUT_DIR == old_output_dir


def _fake_putusan_root(tmp_path):
    root = tmp_path / "sinergi"
    (root / "crawler").mkdir(parents=True)
    (root / "crawler" / "cli.py").write_text("", encoding="utf-8")
    (root / "crawler" / "crawler.py").write_text("", encoding="utf-8")
    (root / "main.py").write_text("", encoding="utf-8")
    (root / "crawl-putusan.ps1").write_text("Write-Host ok\n", encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_cron_job_monitor_returns_putusan_stats_without_writing_snapshot(
    isolated_profiles, tmp_path, monkeypatch
):
    from hermes_cli import web_server
    from tools import putusan_crawler_tool

    root = _fake_putusan_root(tmp_path)
    monkeypatch.setattr(
        putusan_crawler_tool,
        "detect_putusan_runtime",
        lambda _root: {"running": False, "processes": []},
    )
    out_dir = root / "downloads" / "kasus anak"
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "case.pdf").write_bytes(b"%PDF-test")
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
        + "\n",
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

    job = web_server._call_cron_for_profile(
        "worker_alpha",
        "create_job",
        prompt=(
            "Run Putusan.\n"
            'Call putusan_crawler with these exact JSON arguments:\n{"action":"download","out_dir":"downloads/kasus anak"}'
        ),
        schedule="0 12 * * *",
        name="Putusan crawler download",
        enabled_toolsets=["putusan_crawler"],
        workdir=str(root),
    )
    broken_wrapper = isolated_profiles["worker_alpha"] / "scripts" / "putusan_crawler_broken.py"
    broken_wrapper.parent.mkdir(parents=True, exist_ok=True)
    broken_wrapper.write_text("CONFIG = {'silent_if_unchanged': false}\n", encoding="utf-8")
    job = web_server._call_cron_for_profile(
        "worker_alpha",
        "update_job",
        job["id"],
        {"script": broken_wrapper.name, "no_agent": True},
    )

    payload = await web_server.get_cron_job_monitor(
        job["id"], profile="worker_alpha"
    )

    assert payload["success"] is True
    assert payload["kind"] == "putusan"
    assert payload["current"]["downloaded_records"] == 1
    assert payload["current"]["pdf_files"] == 1
    assert payload["current"]["skipped_records"] == 1
    assert payload["current"]["latest_downloads"][0]["title"] == "Putusan PN Anak"
    assert payload["current"]["latest_events"][0]["type"] == "skipped"
    assert payload["current"]["files"]["downloaded_jsonl"]["exists"] is True
    assert payload["delta"]["downloaded_records"] == 1
    assert payload["changed"] is True
    assert payload["running"] is False
    assert payload["status"] in {"active", "idle"}
    assert not (out_dir / ".hermes_putusan_monitor.json").exists()


@pytest.mark.asyncio
async def test_create_putusan_download_job_writes_script_wrapper(
    isolated_profiles, tmp_path, monkeypatch
):
    from hermes_cli import web_server
    from tools import putusan_crawler_tool

    root = _fake_putusan_root(tmp_path)
    monkeypatch.setattr(putusan_crawler_tool, "_find_crawler_root", lambda: root)

    body = web_server.CronJobCreate(
        prompt=(
            "Run the local Sinergi Putusan MA crawler.\n"
            "```json\n"
            '{"action":"download","target_downloads":2,'
            '"out_dir":"downloads/kasus anak",'
            '"start_url":"https://putusan3.mahkamahagung.go.id/direktori/index/kategori/peradilan-anak-abh-1.html"}'
            "\n```"
        ),
        schedule="0 12 * * *",
        name="Putusan crawler download",
        deliver="local",
        enabled_toolsets=["putusan_crawler"],
    )

    job = await web_server.create_cron_job(body, profile="worker_alpha")

    assert job["no_agent"] is True
    assert job["script"].startswith("putusan_crawler_")
    assert job["script"].endswith(".py")
    assert job["workdir"] == str(root)
    wrapper = isolated_profiles["worker_alpha"] / "scripts" / job["script"]
    assert wrapper.exists()
    text = wrapper.read_text(encoding="utf-8")
    assert "uv" in text
    assert "main.py" in text
    assert "VIRTUAL_ENV" in text
    config = runpy.run_path(str(wrapper))["CONFIG"]
    assert config["target_downloads"] == "2"


@pytest.mark.asyncio
async def test_trigger_putusan_job_starts_process_and_monitor_tails_log(
    isolated_profiles, tmp_path, monkeypatch
):
    from hermes_cli import web_server
    from tools import putusan_crawler_tool

    root = _fake_putusan_root(tmp_path)
    monkeypatch.setattr(putusan_crawler_tool, "_find_crawler_root", lambda: root)
    monkeypatch.setattr(
        putusan_crawler_tool,
        "detect_putusan_runtime",
        lambda _root: {"running": False, "processes": []},
    )

    job = web_server._call_cron_for_profile(
        "worker_alpha",
        "create_job",
        prompt=(
            "Run Putusan.\n"
            'Call putusan_crawler with these exact JSON arguments:\n{"action":"download","target_downloads":2,"out_dir":"downloads/kasus anak"}'
        ),
        schedule="0 12 * * *",
        name="Putusan crawler download",
        enabled_toolsets=["putusan_crawler"],
        workdir=str(root),
    )
    broken_wrapper = isolated_profiles["worker_alpha"] / "scripts" / "putusan_crawler_broken.py"
    broken_wrapper.parent.mkdir(parents=True, exist_ok=True)
    broken_wrapper.write_text("CONFIG = {'silent_if_unchanged': false}\n", encoding="utf-8")
    job = web_server._call_cron_for_profile(
        "worker_alpha",
        "update_job",
        job["id"],
        {"script": broken_wrapper.name, "no_agent": True},
    )

    class FakePopen:
        pid = 4321

        def __init__(self, _cmd, **kwargs):
            self.kwargs = kwargs
            kwargs["stdout"].write(b"crawler live line\n")

        def poll(self):
            return None

    monkeypatch.setattr(web_server.subprocess, "Popen", FakePopen)

    triggered = await web_server.trigger_cron_job(job["id"], profile="worker_alpha")
    payload = await web_server.get_cron_job_monitor(
        job["id"], profile="worker_alpha"
    )

    assert triggered["no_agent"] is True
    assert triggered["script"].startswith("putusan_crawler_")
    assert triggered["script"] != broken_wrapper.name
    assert payload["running"] is True
    assert payload["status"] == "running"
    assert payload["live_log"]["pid"] == 4321
    assert payload["live_log"]["running"] is True
    assert any("crawler live line" in line for line in payload["live_log"]["lines"])


@pytest.mark.asyncio
async def test_list_cron_jobs_all_includes_default_and_named_profiles(isolated_profiles):
    from hermes_cli import web_server

    default_job = web_server._call_cron_for_profile(
        "default",
        "create_job",
        prompt="default heartbeat",
        schedule="every 2h",
        name="default-heartbeat",
    )
    worker_job = web_server._call_cron_for_profile(
        "worker_alpha",
        "create_job",
        prompt="worker heartbeat",
        schedule="every 3h",
        name="worker-alpha-heartbeat",
    )

    jobs = await web_server.list_cron_jobs(profile="all")
    by_id = {job["id"]: job for job in jobs}

    assert set(by_id) >= {default_job["id"], worker_job["id"]}
    assert by_id[default_job["id"]]["profile"] == "default"
    assert by_id[default_job["id"]]["is_default_profile"] is True
    assert by_id[default_job["id"]]["hermes_home"] == str(isolated_profiles["default"])
    assert by_id[worker_job["id"]]["profile"] == "worker_alpha"
    assert by_id[worker_job["id"]]["is_default_profile"] is False
    assert by_id[worker_job["id"]]["hermes_home"] == str(isolated_profiles["worker_alpha"])


@pytest.mark.asyncio
async def test_list_cron_jobs_specific_profile_filters_results(isolated_profiles):
    from hermes_cli import web_server

    web_server._call_cron_for_profile(
        "default",
        "create_job",
        prompt="default only",
        schedule="every 2h",
        name="default-only",
    )
    worker_job = web_server._call_cron_for_profile(
        "worker_alpha",
        "create_job",
        prompt="worker only",
        schedule="every 3h",
        name="worker-only",
    )

    jobs = await web_server.list_cron_jobs(profile="worker_alpha")

    assert [job["id"] for job in jobs] == [worker_job["id"]]
    assert jobs[0]["profile"] == "worker_alpha"


@pytest.mark.asyncio
async def test_cron_mutation_without_profile_finds_named_profile_job(isolated_profiles):
    from hermes_cli import web_server

    worker_job = web_server._call_cron_for_profile(
        "worker_alpha",
        "create_job",
        prompt="managed by named profile",
        schedule="every 1h",
        name="named-profile-job",
    )

    paused = await web_server.pause_cron_job(worker_job["id"])
    assert paused["profile"] == "worker_alpha"
    assert paused["enabled"] is False

    default_jobs = await web_server.list_cron_jobs(profile="default")
    worker_jobs = await web_server.list_cron_jobs(profile="worker_alpha")

    assert default_jobs == []
    assert len(worker_jobs) == 1
    assert worker_jobs[0]["id"] == worker_job["id"]
    assert worker_jobs[0]["enabled"] is False


@pytest.mark.asyncio
async def test_update_cron_job_rejects_id_mutation(isolated_profiles):
    """Dashboard surfaces a 400 (not a 500 or silent rename) when an
    id-mutation attempt is rejected by cron/jobs.update_job."""
    from hermes_cli import web_server

    worker_job = web_server._call_cron_for_profile(
        "worker_alpha",
        "create_job",
        prompt="managed by named profile",
        schedule="every 1h",
        name="immutable-id-job",
    )

    with pytest.raises(HTTPException) as exc:
        await web_server.update_cron_job(
            worker_job["id"],
            web_server.CronJobUpdate(updates={"id": "../escape"}),
            profile="worker_alpha",
        )

    assert exc.value.status_code == 400
    assert "id" in exc.value.detail
    worker_jobs = await web_server.list_cron_jobs(profile="worker_alpha")
    assert [job["id"] for job in worker_jobs] == [worker_job["id"]]


@pytest.mark.asyncio
async def test_cron_delete_with_profile_deletes_only_target_profile(isolated_profiles):
    from hermes_cli import web_server

    default_job = web_server._call_cron_for_profile(
        "default",
        "create_job",
        prompt="same-ish default",
        schedule="every 1h",
        name="shared-name",
    )
    worker_job = web_server._call_cron_for_profile(
        "worker_alpha",
        "create_job",
        prompt="same-ish worker",
        schedule="every 1h",
        name="shared-name-worker",
    )

    deleted = await web_server.delete_cron_job(worker_job["id"], profile="worker_alpha")
    assert deleted == {"ok": True}

    remaining_default = await web_server.list_cron_jobs(profile="default")
    remaining_worker = await web_server.list_cron_jobs(profile="worker_alpha")
    assert [job["id"] for job in remaining_default] == [default_job["id"]]
    assert remaining_worker == []


@pytest.mark.asyncio
async def test_cron_profile_validation_errors(isolated_profiles):
    from hermes_cli import web_server

    with pytest.raises(HTTPException) as bad_name:
        await web_server.list_cron_jobs(profile="../bad")
    assert bad_name.value.status_code == 400

    with pytest.raises(HTTPException) as missing:
        await web_server.list_cron_jobs(profile="missing_profile")
    assert missing.value.status_code == 404
