#!/usr/bin/env python3
"""Post a machine-readable File-Transfer result comment to the trigger issue."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

RESULT_MARKER = "FILE_TRANSFER_RESULT_V1"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def main() -> int:
    repository = env("GITHUB_REPOSITORY")
    issue_number = env("ISSUE_NUMBER")
    token = env("GH_TOKEN")
    run_id = env("GITHUB_RUN_ID")
    run_attempt = env("GITHUB_RUN_ATTEMPT", "1")
    fetch_outcome = env("FETCH_OUTCOME")

    if not repository or not issue_number or not token:
        print("Missing repository, issue number, or GitHub token", file=sys.stderr)
        return 1

    success_artifact_id = env("SUCCESS_ARTIFACT_ID")
    diagnostic_artifact_id = env("DIAGNOSTIC_ARTIFACT_ID")

    if fetch_outcome == "success" and success_artifact_id:
        result = {
            "protocol": "file-transfer/v1",
            "status": "success",
            "run_id": int(run_id) if run_id.isdigit() else run_id,
            "run_attempt": int(run_attempt) if run_attempt.isdigit() else run_attempt,
            "artifact_id": int(success_artifact_id),
            "artifact_name": env("SUCCESS_ARTIFACT_NAME"),
            "filename": env("TRANSFER_FILENAME"),
            "size_bytes": int(env("TRANSFER_SIZE", "0") or 0),
            "sha256": env("TRANSFER_SHA256"),
            "mime_type": env("TRANSFER_MIME"),
            "source_host": env("TRANSFER_SOURCE_HOST"),
            "retention_days": 1,
        }
    else:
        result = {
            "protocol": "file-transfer/v1",
            "status": "failed",
            "run_id": int(run_id) if run_id.isdigit() else run_id,
            "run_attempt": int(run_attempt) if run_attempt.isdigit() else run_attempt,
            "diagnostic_artifact_id": (
                int(diagnostic_artifact_id) if diagnostic_artifact_id.isdigit() else None
            ),
            "error": env("TRANSFER_ERROR") or "See workflow logs / diagnostic artifact",
            "retention_days": 1,
        }

    body = RESULT_MARKER + "\n" + json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    payload = json.dumps({"body": body}, ensure_ascii=False).encode("utf-8")
    api_url = f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments"

    request = urllib.request.Request(
        api_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "File-Transfer-Workflow/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in {200, 201}:
                print(f"Unexpected GitHub API status: {response.status}", file=sys.stderr)
                return 1
    except urllib.error.HTTPError as exc:
        print(f"GitHub API error while posting result: HTTP {exc.code}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error while posting result: {exc.reason}", file=sys.stderr)
        return 1

    print(f"Posted File-Transfer result to issue #{issue_number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
