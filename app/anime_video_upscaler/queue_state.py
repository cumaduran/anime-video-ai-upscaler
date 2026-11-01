"""Persist queue snapshots without starting interrupted or completed work."""
from pathlib import Path
import json
import math
import os
import re

from .config import Settings, atomic_json
from .runner import Job

TERMINAL = {"queued", "interrupted", "done", "done_warning", "failed", "cancelled"}
ACTIVE = {"stream", "probe", "subtitle_check", "help", "filters", "encoders", "encoder_check", "normalize", "ai", "ai_frames", "assemble", "verify_ai", "encode", "verify", "verify_source_timing", "resume_count", "resume_decode", "resume_ai", "resume_trim", "resume_verify_new", "resume_verify_existing", "resume_concat"}


def save_queue(path: Path, jobs: list[Job]) -> None:
    if len(jobs) > 10000:
        raise ValueError("Queue has more than 10000 jobs")
    rows = [{"id": job.id, "source": str(job.source), "settings": job.settings.export(),
             "state": job.state, "percent": job.percent, "output": str(job.output) if job.output else "",
             "error": job.error, "settings_locked": job.settings_locked, "output_name": job.output_name}
            for job in jobs]
    document = {"schema": 1, "jobs": rows}
    if len(json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")) > 32_000_000:
        raise ValueError("Queue snapshot is too large")
    atomic_json(path, document)


def load_queue(path: Path) -> list[Job]:
    if path.stat().st_size > 32_000_000:
        raise ValueError("Queue file is too large")
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(raw.get("jobs"), list) or len(raw["jobs"]) > 10000:
        raise ValueError("Invalid queue file")
    jobs, seen = [], set()
    for row in raw["jobs"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid queue entry")
        source, state = row.get("source"), row.get("state")
        if not isinstance(source, str) or not source or not Path(source).is_absolute() or state not in TERMINAL | ACTIVE:
            raise ValueError("Invalid queue source or state")
        key = os.path.normcase(str(Path(source)))
        if key in seen:
            raise ValueError("Duplicate queue source")
        seen.add(key)
        settings = Settings.from_dict(row.get("settings"))
        job_id = row.get("id", "")
        if not isinstance(job_id, str) or (job_id and not re.fullmatch(r"[0-9a-f]{32}", job_id)):
            raise ValueError("Invalid queue job ID")
        locked = row.get("settings_locked", False)
        output_name, output, error = row.get("output_name", ""), row.get("output", ""), row.get("error", "")
        percent = row.get("percent", 0)
        if type(locked) is not bool or not all(isinstance(v, str) for v in (output_name, output, error)) or type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError("Invalid queue fields")
        if output and not Path(output).is_absolute():
            raise ValueError("Invalid queue output")
        if state in ACTIVE:
            state, percent, output = "interrupted", 0, ""
            error = "Previous processing was interrupted. Select Retry to run it again."
        jobs.append(Job(Path(source), settings, id=job_id, state=state, percent=percent, output=Path(output) if output else None,
                        error=error, settings_locked=locked, output_name=output_name))
    return jobs
