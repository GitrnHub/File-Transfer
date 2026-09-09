# File-Transfer

File-Transfer is a small GitHub Actions bridge for one purpose:

> **When ChatGPT cannot fetch a public direct-download URL because of sandbox/network/tool limitations, use a GitHub-hosted runner to fetch the file and return either the original payload or a compact processed analysis as a short-lived Actions Artifact.**

This repository is intentionally not a file archive. Downloaded payloads are **not committed to Git**.

## Modes

### `[transfer]` — byte-for-byte file relay

```text
User gives ChatGPT a direct URL
          |
          v
ChatGPT creates a [transfer] issue
          |
          v
GitHub Actions (ubuntu-latest)
  - validates URL and redirects
  - downloads without executing it
  - enforces size limit
  - computes SHA-256
          |
          v
short-lived Actions Artifact
          |
          v
workflow comments Artifact ID + metadata on issue
          |
          v
ChatGPT downloads Artifact through GitHub connector
```

The downloaded file is preserved byte-for-byte.

### `[video]` — video preprocessing for AI inspection

Use this when ChatGPT needs to inspect a remote video but fetching/decoding the original video directly is inconvenient or blocked.

```text
public video URL
      |
      v
[video] issue
      |
      v
GitHub-hosted runner downloads source
      |
      +--> ffprobe metadata
      +--> 64 uniformly sampled frames
      +--> up to 64 scene-change frames
      +--> contact sheets
      +--> 16 kHz mono audio extraction
      +--> faster-whisper transcription
      |
      v
processed analysis Artifact
      |
      v
ChatGPT retrieves and inspects the compact result
```

The processed video Artifact intentionally **does not contain the original video**. It contains only analysis outputs, so it is much smaller and easier for an AI sandbox to consume. If the original bytes are also required, create a separate `[transfer]` request using the same URL and preferably the SHA-256 learned from the video-analysis run.

A manual `workflow_dispatch` entry is provided for both modes for human use.

## Why Actions Artifact instead of committing files?

The bridge is for temporary transport, so Git history is the wrong storage layer for downloaded binaries. GitHub Actions Artifacts are designed for passing files produced by workflow runs, support custom retention periods, and are available through the REST API after upload.

Artifacts are retained for **1 day** by default.

GitHub's Artifact transport already provides an archive for download. **Do not manually ZIP a single source file just to move it through this bridge.** The original payload is kept unchanged, including when it is already a `.zip`, `.7z`, `.rar`, `.mp4`, `.img`, `.onnx`, `.engine`, etc. Workflows use Artifact compression level `0` where appropriate to avoid wasting CPU on already-compressed or incompressible data.

References:

- GitHub: Store and share data with workflow artifacts: https://docs.github.com/en/actions/tutorials/store-and-share-data
- GitHub: Actions limits and storage: https://docs.github.com/en/actions/reference/limits
- GitHub: Secure use reference: https://docs.github.com/en/actions/reference/security/secure-use
- `actions/upload-artifact`: https://github.com/actions/upload-artifact
- faster-whisper: https://github.com/SYSTRAN/faster-whisper

## ChatGPT protocol

ChatGPT should use this bridge only when normal file/web/connector access cannot reliably retrieve or efficiently preprocess the remote bytes.

## URL safety

The repository is public, so never publish an authenticated URL that grants access to private data.

Do not use the Issue path for URLs containing account credentials, cookies, bearer tokens, private object-storage credentials, or signatures that expose a private resource.

Some public media CDNs use short-lived query signatures only as anti-hotlink/playback tokens. Those may be used when all of the following are true:

- the user explicitly supplied the URL;
- it points only to media the user is already permitted to access;
- it does not contain account/session credentials;
- publishing the temporary URL does not expose private data.

Treat such URLs as transient: avoid copying them into documentation and close the transfer Issue after the file has been received.

## `[transfer]` request protocol

Title:

```text
[transfer] <short filename or description>
```

Body:

```text
FILE_TRANSFER_REQUEST_V1
{"url":"https://example.com/file.bin","filename":"","max_mb":450,"expected_sha256":""}
```

Fields:

| field | required | meaning |
|---|---:|---|
| `url` | yes | Public HTTP/HTTPS direct-download URL |
| `filename` | no | Override saved filename; blank means auto-detect |
| `max_mb` | no | Maximum accepted payload size in MiB; default 450 |
| `expected_sha256` | no | Optional expected SHA-256 for integrity verification |

Only Issues created by the repository owner and whose title starts with `[transfer]` are accepted by the relay workflow.

On success the workflow posts:

```text
FILE_TRANSFER_RESULT_V1
{"status":"success","run_id":123,"artifact_id":456,"filename":"file.bin","size_bytes":12345,"sha256":"..."}
```

The Artifact root contains:

```text
<downloaded file>
transfer.json
```

`transfer.json` contains size, SHA-256, MIME type, source host and timestamp. Query parameters are deliberately omitted from metadata.

## `[video]` request protocol

Title:

```text
[video] <short description>
```

Body uses the same request envelope:

```text
FILE_TRANSFER_REQUEST_V1
{"url":"https://example.com/video.mp4","filename":"video.mp4","max_mb":450,"expected_sha256":""}
```

The video workflow currently uses:

- `ffprobe` for stream/container metadata;
- FFmpeg for frame and audio extraction;
- 64 approximately uniform frames across the full duration;
- up to 64 scene-change frames;
- 4x3 contact sheets;
- `faster-whisper` `small` on CPU/int8 by default, with VAD enabled.

The machine result begins with:

```text
FILE_TRANSFER_VIDEO_RESULT_V1
```

and reports the source size/SHA-256, duration, frame counts, transcript status/language and the processed Artifact ID.

The processed Artifact contains files such as:

```text
analysis_summary.json
ffprobe.json
source_transfer.json
frames_manifest.csv
frames/
  uniform/
  scene/
contact_sheets/
transcript.txt
transcript.json
```

If there is no audio stream, `transcript.txt` records that fact. If transcription fails, frame extraction can still be diagnostically useful and the error is recorded.

### Video-analysis performance note

Speech recognition is normally the slowest stage, especially on a CPU-only hosted runner and on the first run when a model must be downloaded. Hardware/teardown analysis often benefits primarily from the extracted frames; transcript text should be treated as supplemental evidence and may contain recognition errors in model numbers or IC markings.

## Result retrieval and cleanup

For either mode:

1. read the machine-readable result comment;
2. parse `artifact_id`;
3. download that Artifact through the connected GitHub integration;
4. verify SHA-256 where practical;
5. inspect/process the result for the user's actual task;
6. close the Issue after successful receipt.

## Manual use

Humans can run:

- **Actions -> File Transfer -> Run workflow** for a raw relay;
- **Actions -> Video Transfer Analysis -> Run workflow** for remote video preprocessing.

## Limits

### Default size

The default single-source limit is **450 MiB**. The secure downloader has a hard protocol ceiling of **2048 MiB**, but raising the request limit does not guarantee sufficient GitHub Artifact storage.

Artifact storage is account-plan dependent and finite. For multi-gigabyte firmware images, disk images, model weights or datasets, use dedicated object storage / Drive / Release / LFS instead of this bridge.

### Retention

Artifacts are retained for **1 day** to keep the bridge ephemeral and reduce storage consumption.

### Supported sources

Supported:

- `http://`
- `https://`
- redirects to other public HTTP/HTTPS hosts

Rejected:

- localhost;
- loopback/private/link-local/reserved IP addresses;
- URLs containing embedded `user:password@host` credentials;
- non-HTTP schemes.

The downloader validates every redirect destination before following it.

### Authentication

This bridge does not accept cookies, Authorization headers, browser sessions, SSH credentials or custom secrets. It is a transport fallback, not an authenticated scraping service.

## Security model

- Standard GitHub-hosted `ubuntu-latest` runner only.
- Owner-only Issue triggers.
- No repository secrets are required.
- Untrusted URL/body values are passed to Python through environment variables instead of interpolated into shell commands.
- Public/private/loopback network destinations are rejected before download and on redirects.
- The raw relay treats downloaded files as opaque bytes and never executes them.
- Video mode decodes the supplied media with FFmpeg and passes extracted audio to the speech model, but it never executes code contained in the downloaded file.
- Transferred payloads and analysis outputs are not committed to Git.

## Verified tests

### Raw relay

The Issue-triggered path has been verified end-to-end:

```text
GitHub-connected agent -> Issue -> GitHub Actions -> Artifact -> GitHub connector download
```

The received payload SHA-256 matched the value reported by the workflow.

### Remote video analysis

The `[video]` path has also been exercised end-to-end on an approximately 8.5-minute public MP4 that the normal sandbox path could not fetch reliably. The GitHub-hosted runner downloaded the source, generated 64 uniform + 64 scene frames, produced contact sheets, completed Chinese speech recognition, uploaded the processed Artifact, and the connected agent retrieved it successfully.

## Repository files

```text
.github/workflows/file-transfer.yml    raw relay entry point
.github/workflows/video-analysis.yml   video preprocessing entry point
scripts/fetch_file.py                  request parser + secure downloader
scripts/comment_issue.py               raw-relay result comment
scripts/analyze_video.py               ffprobe/frame/contact-sheet/transcript pipeline
scripts/comment_video_result.py        video-analysis result comment
AGENTS.md                              operating instructions for AI agents
.gitignore                             prevents accidental transient-output commits
README.md                              human-facing protocol
```

## Non-goals

This repository is not intended to:

- permanently host files;
- bypass website authentication or access controls;
- download from private/internal network services;
- execute downloaded code;
- replace large-file storage;
- provide forensic-grade speech recognition or video interpretation by itself.
