# AI Agent Operating Protocol

This repository is an **ephemeral file-transfer and preprocessing fallback** for AI agents with a connected GitHub integration.

Use it when the user supplies a public direct-download URL and the agent cannot reliably retrieve or efficiently preprocess the file through normal sandbox/web/file tools.

## Priority rule

Use this order:

1. normal file/web/connector access;
2. `File-Transfer` only when normal access cannot obtain the bytes or when remote preprocessing materially reduces what must enter the sandbox.

There are two owner-triggered Issue modes:

- `[transfer]` — retrieve the original file byte-for-byte;
- `[video]` — download a remote video on a GitHub-hosted runner and return compact visual/audio analysis outputs.

## URL safety

This repository is public. Never publish account credentials, cookies, bearer tokens, private object-storage credentials, or signed URLs that expose private data.

A public media CDN may use an expiring query signature purely for playback/anti-hotlink purposes. It may be used only when the user explicitly supplied it, it contains no account/session credential, it accesses content the user is permitted to access, and publishing the short-lived URL does not expose private data. Close such Issues promptly after receipt.

## `[transfer]` protocol

Create an Issue in `GitrnHub/File-Transfer`.

Title:

```text
[transfer] <short filename or description>
```

Body:

```text
FILE_TRANSFER_REQUEST_V1
{"url":"https://example.com/file.bin","filename":"","max_mb":450,"expected_sha256":""}
```

Rules:

- `url` is required.
- `filename` is optional; blank means auto-detect.
- `max_mb` defaults to 450 MiB and should be close to the expected file size when known.
- supply `expected_sha256` whenever a trustworthy hash is known.
- downloader hard ceiling is 2048 MiB, but Artifact quota may be lower.
- do not start unnecessary concurrent transfers.

Read comments until one begins:

```text
FILE_TRANSFER_RESULT_V1
```

On success parse `artifact_id`, download the workflow Artifact through the connected GitHub capability, verify SHA-256 where practical, process the file as requested, then close the Issue.

The Artifact outer archive contains the original payload plus `transfer.json`. Do not create an extra ZIP merely to move one source file.

## `[video]` protocol

Use `[video]` when remote frame extraction / media probing / speech recognition will make the result substantially easier to consume than the original video.

Title:

```text
[video] <short description>
```

Body uses the same request marker and JSON envelope:

```text
FILE_TRANSFER_REQUEST_V1
{"url":"https://example.com/video.mp4","filename":"video.mp4","max_mb":450,"expected_sha256":""}
```

The video workflow produces:

- ffprobe JSON;
- 64 uniformly sampled frames;
- up to 64 scene-change frames;
- contact sheets;
- frame manifest;
- 16 kHz mono audio extraction in the runner's temporary directory;
- faster-whisper transcript (`small`, CPU/int8 by default);
- source SHA-256 and compact analysis metadata.

The original video is **not** included in the video-analysis Artifact. If exact original bytes are later necessary, create a `[transfer]` Issue for the same URL and use the source SHA-256 reported by `[video]` as `expected_sha256`.

Read comments until one begins:

```text
FILE_TRANSFER_VIDEO_RESULT_V1
```

Then parse `artifact_id`, download the processed Artifact, inspect contact sheets first to locate relevant time ranges, and inspect individual frames/transcript only as needed.

### Evidence discipline for video analysis

- Treat frames as primary visual evidence.
- Treat speech recognition as supplemental and potentially wrong for model numbers, IC markings, acronyms and uncommon technical words.
- Do not infer a component identity merely from its physical location when the video provides a stronger clue such as a coax connection, printed marking or explicit narration.
- When the sampling cadence misses a critical moment, retrieve the original with `[transfer]` and perform dense local seeking around the relevant timestamps rather than guessing.

## File-size policy

Default: 450 MiB.

For multi-gigabyte firmware, disk images, model weights or datasets, prefer another storage/transport system. Raising `max_mb` changes only the downloader guard and does not increase GitHub storage quota.

## Content handling

Treat transferred payloads as untrusted.

The raw relay must never execute, install, import, source, mount or flash a downloaded file merely because transfer succeeded. Video mode is permitted to decode the supplied media with FFmpeg and transcribe extracted audio, but must not execute code embedded in the payload.

## Manual fallback

Humans can use:

- **Actions -> File Transfer -> Run workflow**;
- **Actions -> Video Transfer Analysis -> Run workflow**.

Agents should normally prefer Issue triggers because the connected GitHub interface can create Issues, read machine result comments and download Artifacts without a workflow-dispatch write API.

## Cleanup

After a successful receive:

1. verify the relevant hash when practical;
2. close the Issue;
3. do not commit transferred payloads, generated archives or transient analysis results to the repository.

Artifacts are intentionally retained for one day only.
