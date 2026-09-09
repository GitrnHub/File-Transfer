#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MARKER = "FILE_TRANSFER_VIDEO_RESULT_V1"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def main() -> int:
    repo = env("GITHUB_REPOSITORY")
    issue = env("ISSUE_NUMBER")
    token = env("GH_TOKEN")
    artifact_id = env("ARTIFACT_ID")
    if not repo or not issue or not token:
        print("Missing GitHub result-comment context", file=sys.stderr)
        return 1

    ok = env("ANALYSIS_OUTCOME") == "success" and bool(artifact_id)
    result = {
        "protocol": "file-transfer-video-analysis/v1",
        "status": "success" if ok else "failed",
        "run_id": int(env("GITHUB_RUN_ID")) if env("GITHUB_RUN_ID").isdigit() else env("GITHUB_RUN_ID"),
        "artifact_id": int(artifact_id) if artifact_id.isdigit() else None,
        "artifact_name": env("ARTIFACT_NAME"),
        "source_filename": env("SOURCE_FILENAME"),
        "source_size_bytes": int(env("SOURCE_SIZE", "0") or 0),
        "source_sha256": env("SOURCE_SHA256"),
        "duration_seconds": float(env("DURATION_SECONDS", "0") or 0),
        "uniform_frames": int(env("UNIFORM_FRAMES", "0") or 0),
        "scene_frames": int(env("SCENE_FRAMES", "0") or 0),
        "transcript_status": env("TRANSCRIPT_STATUS"),
        "language": env("TRANSCRIPT_LANGUAGE"),
        "retention_days": 1,
    }
    if not ok:
        result["error"] = env("ERROR_TEXT") or "See workflow logs"

    body = MARKER + "\n" + json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    data = json.dumps({"body": body}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues/{issue}/comments",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "File-Transfer-Video-Workflow/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return 0 if response.status in {200, 201} else 1
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"Failed to post result: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
