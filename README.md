# File-Transfer

File-Transfer is a GitHub Actions bridge for cases where ChatGPT cannot reliably retrieve or preprocess a public direct-download URL because of sandbox, network, or tool limitations.

Downloaded payloads are temporary transport data and are **never committed to Git**.

## Modes

- **`[transfer]`** — byte-for-byte relay. The runner validates a public HTTP/HTTPS URL, downloads it without executing it, enforces a size limit, computes SHA-256, uploads the unchanged file as a one-day Actions Artifact, and posts the Artifact ID in a machine-readable Issue comment.
- **`[video]`** — remote video preprocessing. The runner downloads the video, runs `ffprobe`, extracts 64 uniform frames plus up to 64 scene-change frames, builds contact sheets, extracts 16 kHz mono audio, transcribes it with `faster-whisper` (`small`, CPU/int8 by default), and uploads only the compact analysis outputs as a one-day Artifact.

Both modes use owner-authored Issues and the same body envelope:

```text
FILE_TRANSFER_REQUEST_V1
{"url":"https://example.com/file.bin","filename":"","max_mb":450,"expected_sha256":""}
```

Use title `[transfer] ...` for the raw relay and `[video] ...` for video preprocessing.

Raw-relay result comments begin with `FILE_TRANSFER_RESULT_V1`; video-analysis result comments begin with `FILE_TRANSFER_VIDEO_RESULT_V1`.

For `[video]`, the original video is not returned in the analysis Artifact. If exact source bytes are later required, create a `[transfer]` request for the same URL and use the source SHA-256 reported by the video run as `expected_sha256`.

## Typical agent flow

```text
normal web/file access
        |
        | unavailable or remote preprocessing is useful
        v
[transfer] or [video] Issue
        v
GitHub-hosted ubuntu-latest runner
        v
short-lived Artifact + result comment
        v
ChatGPT downloads Artifact through GitHub connector
        v
verify / inspect / continue user's task
        v
close Issue
```

See `AGENTS.md` for detailed machine-operating and evidence rules.

## Storage and compression

This repository is a temporary bridge, not a binary archive. Transferred files and analysis outputs are never committed to Git. Artifacts are retained for **1 day**.

Do not manually ZIP one source file just to move it through the bridge: a GitHub Actions Artifact is already downloaded as an outer archive. Existing `.zip`, `.7z`, `.rar`, `.mp4`, `.img`, `.onnx`, `.engine`, etc. should remain byte-for-byte unchanged in `[transfer]` mode.

Default source-size guard: **450 MiB**. Downloader hard ceiling: **2048 MiB**. Multi-gigabyte firmware, disk images, model weights, or datasets should use dedicated object storage / Drive / Releases / LFS instead.

## URL safety

The repository is public. Never publish account credentials, cookies, bearer tokens, private object-storage credentials, or signed URLs exposing private data.

Short-lived query signatures used only by a public media CDN for playback/anti-hotlinking may be used when the user explicitly supplied the URL, it grants no account/session access, the user is permitted to access the media, and publishing the temporary URL does not expose private data. Close such Issues promptly and do not copy the URL into permanent documentation.

The downloader rejects localhost, loopback/private/link-local/reserved destinations, embedded `user:password@host` credentials, and non-HTTP schemes, and validates every redirect destination.

## Security

- GitHub-hosted `ubuntu-latest` runner only.
- Owner-only Issue triggers.
- No repository secrets required.
- User-controlled URL/body fields are passed into Python via environment variables rather than interpolated into shell commands.
- `[transfer]` treats payloads as opaque bytes and never executes/imports/installs/mounts them.
- `[video]` may decode the supplied media with FFmpeg and transcribe extracted audio, but never executes code embedded in the payload.

## Video evidence discipline

For teardown/hardware analysis, frames are primary evidence and speech transcription is supplemental. Model numbers, IC markings, acronyms, and uncommon technical words can be misrecognized. If coarse sampling misses a critical moment, use `[transfer]` to retrieve the original and perform dense local seeking rather than guessing.

## Verified operation

Both paths have been tested end-to-end with the connected GitHub account. The video path successfully processed an approximately 8.5-minute public MP4 that the normal sandbox route could not reliably fetch, generating 64 uniform frames, 64 scene frames, contact sheets, Chinese transcription, and a downloadable processed Artifact.

## Repository files

```text
.github/workflows/file-transfer.yml    raw relay
.github/workflows/video-analysis.yml   remote video preprocessing
scripts/fetch_file.py                  secure downloader / request parser
scripts/comment_issue.py               raw result comment
scripts/analyze_video.py               ffprobe / frames / contact sheets / transcript
scripts/comment_video_result.py        video result comment
AGENTS.md                              AI operating protocol
README.md                              overview
```

## References

- https://docs.github.com/en/actions/tutorials/store-and-share-data
- https://docs.github.com/en/actions/reference/limits
- https://docs.github.com/en/actions/reference/security/secure-use
- https://github.com/actions/upload-artifact
- https://github.com/SYSTRAN/faster-whisper
