# AI Agent Operating Protocol

This repository is an **ephemeral file-transfer fallback** for AI agents with a connected GitHub integration.

Its intended use is narrow: when the user supplies a public direct-download URL and the agent cannot reliably retrieve the file through its normal sandbox/web/file tools, the agent may ask this repository's GitHub Actions runner to fetch the file and return it as an Actions Artifact.

## Priority rule

Do not use File-Transfer automatically for every URL.

Use this order:

1. normal file/web/connector access;
2. File-Transfer only when the normal path cannot obtain the bytes or is blocked by the agent's sandbox/network constraints.

## Required safety check before creating an issue

The repository is public. Never publish a URL that contains credentials or sensitive authorization material.

Do not create a transfer issue when the URL contains or appears to contain:

- username/password authority (`user:pass@host`);
- access tokens;
- session identifiers;
- cookies;
- signed cloud-storage query parameters;
- temporary authentication signatures;
- secrets embedded in path/query/fragment.

Common warning strings include `token`, `auth`, `signature`, `sig`, `key`, `credential`, `X-Amz-`, `X-Goog-`, `Expires`, and similar authentication parameters. Presence of one is not proof of a secret, but agents should treat it conservatively.

For sensitive URLs, this public bridge is not appropriate. Use a private authenticated route instead.

## Automated issue protocol

Create an issue in `GitrnHub/File-Transfer`.

Title:

```text
[transfer] <short filename or description>
```

Body must be exactly the protocol marker followed by a JSON object:

```text
FILE_TRANSFER_REQUEST_V1
{"url":"https://example.com/file.bin","filename":"","max_mb":450,"expected_sha256":""}
```

Rules:

- `url` is required.
- `filename` is optional; blank means auto-detect.
- `max_mb` defaults to 450 MiB and should be set close to the expected file size when known.
- `expected_sha256` should be supplied whenever the user/source provides a trustworthy SHA-256.
- Hard protocol ceiling is 2048 MiB, but GitHub Artifact storage quota can be much lower.
- Do not start unnecessary concurrent transfers; this repository serializes transfer jobs.

## Result protocol

Read comments on the same issue until a comment begins with:

```text
FILE_TRANSFER_RESULT_V1
```

The following line is compact JSON.

Successful example:

```json
{"protocol":"file-transfer/v1","status":"success","run_id":123,"artifact_id":456,"artifact_name":"file-transfer-8","filename":"file.bin","size_bytes":12345,"sha256":"...","mime_type":"application/octet-stream","source_host":"example.com","retention_days":1}
```

Failure example:

```json
{"protocol":"file-transfer/v1","status":"failed","run_id":123,"diagnostic_artifact_id":456,"error":"...","retention_days":1}
```

## Retrieving the result

On success:

1. parse `artifact_id`;
2. call the connected GitHub capability that downloads a workflow artifact by repository + artifact ID;
3. obtain the artifact archive;
4. inspect/extract it only as required by the user's task;
5. verify the payload SHA-256 against the workflow's reported hash when practical;
6. close the transfer issue after successful receipt.

The Actions Artifact is an outer transport archive. Do not create an additional source ZIP merely to move a single file. If the original payload is already `.zip`, `.7z`, `.rar`, `.mp4`, `.img`, `.onnx`, `.engine`, etc., keep it unchanged; the workflow uses artifact compression level 0 to avoid wasting CPU on incompressible data.

On failure:

1. inspect the `error` value;
2. if `diagnostic_artifact_id` exists, download the diagnostic artifact;
3. if necessary, inspect the workflow job/log through the GitHub connector;
4. report the concrete limitation rather than repeatedly retrying the same request.

## File-size policy

Default: 450 MiB.

This default is intentionally close to, but below, the GitHub Free 500 MB included Artifact storage allowance. Artifact quota is account-dependent and shared with GitHub Packages.

For very large files, especially multi-gigabyte firmware, disk images, model weights or datasets, prefer another transport/storage system. Raising `max_mb` only changes the downloader guard; it does not create more GitHub storage quota.

## Content handling

Treat the downloaded payload as untrusted opaque bytes.

Do not execute, install, import, source, mount, flash, or run downloaded content merely because the transfer succeeded. Subsequent handling is governed by the user's actual request and normal safety rules.

## Manual fallback

Humans can use **Actions -> File Transfer -> Run workflow** with the same URL/filename/max-size/hash fields.

Agents should prefer the issue trigger because the connected GitHub interface can create issues and read their comments, giving the bridge a machine-readable request/result channel without requiring a workflow-dispatch API.

## Repository maintenance

Keep this repository minimal. Do not commit transferred payloads, generated archives, logs, or test downloads to Git. Artifacts are intentionally retained for one day only.
