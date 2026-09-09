# File-Transfer

File-Transfer is a GitHub Actions bridge for cases where ChatGPT cannot reliably retrieve or preprocess a public direct-download URL because of sandbox, network, or tool limitations.

Downloaded payloads are temporary transport data and are **never committed to Git**.

## Modes

### `[transfer]` — byte-for-byte file relay

ChatGPT creates an owner-authored Issue whose title starts with `[transfer]`. The workflow validates the public HTTP/HTTPS URL and redirects, downloads the file without executing it, enforces a size limit, computes SHA-256, uploads the unchanged payload as a one-day Actions Artifact, and writes a machine-readable result comment containing the Artifact ID and metadata.

Request body:

```text
FILE_TRANSFER_REQUEST_V1
{"url":"https://example.com/file.bin","filename":"","max_mb":450,"expected_sha256":""}
```

Success marker:

```text
FILE_TRANSFER_RESULT_V1
```

The Artifact root contains the downloaded file and `transfer.json`.

### `[video]` — remote video preprocessing

Use this when the source video is easier to analyze on a GitHub-hosted runner than inside the ChatGPT sandbox.

The `[video]` workflow downloads the source and generates a compact analysis Artifact containing:

- `ffprobe.json` media metadata;
- 64 approximately uniform frames;
- up to 64 scene-change frames;
- 4x3 contact sheets;
- `frames_manifest.csv`;
- Chinese/other-language speech transcription through `faster-whisper` (`small`, CPU/int8 by default);
- `analysis_summary.json` and source-transfer metadata.

The original video is deliberately **not** included in the processed Artifact. If exact source bytes are also needed, run a separate `[transfer]` request and use the SHA-256 reported by `[video]` as `expected_sha256`.

Video request body uses the same envelope:

```text
FILE_TRANSFER_REQUEST_V1
{"url":"https://example.com/video.mp4","filename":"video.mp4","max_mb":450,"expected_sha256":""}
```

Success marker:

```text
FILE_TRANSFER_VIDEO_RESULT_V1
```

## Agent workflow

```text
normal web/file access
        |
        | fails or remote preprocessing is useful
        v
create [transfer] or [video] Issue
        |
        v
GitHub-hosted ubuntu-latest runner
        |
        v
short-lived Actions Artifact + machine result comment
        |
        v
ChatGPT downloads Artifact through GitHub connector
        |
        v
verify / inspect / continue user's task
        |
        v
close Issue
```

See `AGENTS.md` for the machine-operating protocol and evidence rules.

## Why Actions Artifact instead of committing files?

This repository is a temporary transport bridge, not a file archive. Git history is inappropriate for repeated binary transfers. GitHub Actions Artifacts provide short-lived workflow output storage and an API-addressable Artifact ID.

Artifacts are retained for **1 day**. Do not manually ZIP one source file merely to move it through the bridge: the GitHub Artifact download is already an outer archive. If the original is `.zip`, `.7z`, `.rar`, `.mp4`, `.img`, `.onnx`, `.engine`, etc., preserve its original bytes.

## URL safety

This repository is public. Never publish URLs containing account credentials, cookies, bearer tokens, private object-storage credentials, or signatures that expose private data.

Some public media CDNs use expiring query signatures purely for playback/anti-hotlink purposes. They may be used only when the user explicitly supplied the URL, it contains no account/session credential, it accesses content the user is permitted to access, and publishing the temporary URL does not expose private data. Such Issues should be closed promptly after receipt and the URL should not be copied into permanent documentation.

The downloader rejects localhost, loopback/private/link-local/reserved destinations, embedded `user:password@host` credentials, and non-HTTP schemes, and re-validates every redirect destination.

## Limits

- Default single-source limit: **450 MiB**.
- Secure-downloader hard ceiling: **2048 MiB**.
- Artifact retention: **1 day**.
- Artifact storage is finite and account-plan dependent. Multi-gigabyte firmware, disk images, model weights, and datasets should use object storage / Drive / Releases / LFS instead.

## Security model

- Standard GitHub-hosted `ubuntu-latest` runner only.
- Owner-only Issue triggers.
- No repository secrets are needed.
- User-controlled URL/body fields are passed into Python via environment variables rather than interpolated into shell commands.
- `[transfer]` treats the payload as opaque bytes and never executes/imports/installs/mounts it.
- `[video]` is allowed to decode the supplied media with FFmpeg and transcribe extracted audio, but does not execute code embedded in the payload.

## Video evidence discipline

For hardware/teardown analysis:

- extracted frames are primary evidence;
- speech transcription is supplemental and can misrecognize IC markings, model numbers, acronyms, or uncommon technical words;
- if 64-frame sampling misses a critical moment, retrieve the original with `[transfer]` and perform dense local seeking around the relevant timestamp rather than guessing.

## Manual use

Humans can use:

- **Actions -> File Transfer -> Run workflow**;
- **Actions -> Video Transfer Analysis -> Run workflow**.

## Verified operation

Both modes have been tested end-to-end with the connected GitHub account. The raw relay successfully returned a payload whose SHA-256 matched the workflow result. The video path successfully processed an approximately 8.5-minute public MP4 that the normal sandbox route could not fetch reliably, generating 64 uniform frames, 64 scene frames, contact sheets, Chinese speech recognition, and a downloadable processed Artifact.

## Repository files

```text
.github/workflows/file-transfer.yml    raw relay entry point
.github/workflows/video-analysis.yml   video preprocessing entry point
scripts/fetch_file.py                  request parser + secure downloader
scripts/comment_issue.py               raw-relay result comment
scripts/analyze_video.py               media probing / frames / transcript pipeline
scripts/comment_video_result.py        video-analysis result comment
AGENTS.md                              AI operating protocol
.gitignore                             transient-output guard
README.md                              human-facing overview
```

## References

- GitHub workflow artifacts: https://docs.github.com/en/actions/tutorials/store-and-share-data
- GitHub Actions limits/storage: https://docs.github.com/en/actions/reference/limits
- GitHub Actions secure use: https://docs.github.com/en/actions/reference/security/secure-use
- actions/upload-artifact: https://github.com/actions/upload-artifact
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
