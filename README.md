# File-Transfer

File-Transfer is a small GitHub Actions bridge for one purpose:

> **When ChatGPT cannot fetch a public direct-download URL because of sandbox/network/tool limitations, use a GitHub-hosted runner to fetch the file, upload it as a short-lived Actions Artifact, and hand the Artifact ID back to ChatGPT.**

This repository is intentionally not a file archive. Downloaded payloads are **not committed to Git**.

## Architecture

```text
User gives ChatGPT a direct URL
          |
          v
ChatGPT creates a [transfer] issue
          |
          v
GitHub Actions (ubuntu-latest)
  - validates URL
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
          |
          v
ChatGPT verifies / reads / processes the file
```

A manual `workflow_dispatch` entry is also provided for human use.

## Why Actions Artifact instead of committing the file?

The bridge is for temporary transport, so Git history is the wrong storage layer for downloaded binaries. GitHub Actions Artifacts are designed for passing files produced by workflow runs, can use a custom retention period, and are available through the REST API after upload.

The workflow keeps artifacts for **1 day** by default.

References:

- GitHub: Store and share data with workflow artifacts: https://docs.github.com/en/actions/tutorials/store-and-share-data
- GitHub: Actions limits and storage: https://docs.github.com/en/actions/reference/limits
- GitHub: Secure use reference: https://docs.github.com/en/actions/reference/security/secure-use

## ChatGPT protocol

ChatGPT should use this bridge only when normal file/web access cannot reliably retrieve the URL.

### 1. Safety check

Only use the automated issue path for a **public, non-sensitive direct URL**.

Do **not** put URLs containing credentials, cookies, private tokens, signed query strings, temporary authentication parameters, or other secrets into this public repository. Issue bodies and workflow metadata may be publicly visible.

If the URL is sensitive, do not use the public issue path. A private repository or another authenticated transfer method is required.

### 2. Create a transfer issue

Title:

```text
[transfer] <short descriptive name>
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

Only issues created by the repository owner and whose title starts with `[transfer]` are accepted by the workflow.

### 3. Wait for the machine result comment

On success the workflow posts a comment containing:

```text
FILE_TRANSFER_RESULT_V1
{"status":"success","run_id":123,"artifact_id":456,"artifact_name":"file-transfer-...","filename":"file.bin","size_bytes":12345,"sha256":"..."}
```

On failure it posts:

```text
FILE_TRANSFER_RESULT_V1
{"status":"failed","run_id":123,"diagnostic_artifact_id":456}
```

### 4. Retrieve the artifact

ChatGPT should parse `artifact_id` from the success comment and use the connected GitHub Artifact download capability to retrieve it.

The artifact contains:

```text
payload/
  <downloaded file>
  transfer.json
```

`transfer.json` contains the downloaded file's size, SHA-256, MIME type, source host and timestamp. Query parameters are not copied into the metadata.

### 5. Verify and close

After retrieving the file:

1. verify the original file against the SHA-256 reported by the workflow when practical;
2. continue the user's requested processing;
3. close the transfer issue after the artifact has been successfully received.

## Manual use

Open **Actions -> File Transfer -> Run workflow** and provide:

- URL
- optional filename
- maximum size in MiB
- optional expected SHA-256

`workflow_dispatch` is retained because GitHub officially supports typed manual workflow inputs and it is useful for testing or human operation.

## Limits

### Default size

The default single-file limit is **450 MiB**. The downloader has a hard protocol ceiling of **2048 MiB**, but raising the request limit does not guarantee GitHub Artifact storage is available.

GitHub's included Actions Artifact storage depends on the account plan. For example, GitHub Free includes 500 MB and GitHub Pro includes 1 GB. Public repositories get free standard hosted-runner minutes, but Artifact storage is still a finite resource.

For multi-gigabyte firmware images, disk images, model weights or datasets, use dedicated object storage / Drive / Release / LFS instead of this bridge.

### Retention

Artifacts are retained for **1 day** to keep this repository ephemeral and reduce storage consumption.

### Supported sources

Supported:

- `http://`
- `https://`
- redirects to other public HTTP/HTTPS hosts

Rejected:

- localhost
- loopback/private/link-local/reserved IP addresses
- URLs containing embedded `user:password@host` credentials
- non-HTTP schemes

The downloader validates every redirect destination before following it.

### Authentication

This bridge does not accept cookies, Authorization headers, browser sessions, SSH credentials or custom secrets. That is deliberate: the repository is a transport fallback, not an authenticated scraping service.

### Downloaded content is never executed

The workflow writes the response body to disk and uploads it as an Artifact. It does not import, run, install, source, unzip or otherwise execute downloaded payloads.

## Security model

- Standard GitHub-hosted `ubuntu-latest` runner only.
- Owner-only issue trigger.
- `GITHUB_TOKEN` receives only the permissions needed to read repository content and comment on issues.
- No repository secrets are required.
- Untrusted URL/body values are passed to Python through environment variables instead of interpolated directly into shell commands.
- Public/private/loopback network destinations are rejected before download and on redirects.
- Downloaded files are treated as opaque bytes.

GitHub states that hosted runners execute in ephemeral clean virtual machines. The workflow intentionally does **not** use a self-hosted runner because this bridge handles untrusted remote content.

## Repository files

```text
.github/workflows/file-transfer.yml   GitHub Actions entry point
scripts/fetch_file.py                 request parser + secure downloader
scripts/comment_issue.py              machine-readable result comment
AGENTS.md                             operating instructions for AI agents
README.md                             human-facing protocol
```

## Non-goals

This repository is not intended to:

- permanently host files;
- bypass website authentication;
- scrape websites that do not expose a direct file URL;
- download from private/internal network services;
- execute downloaded code;
- replace large-file storage.
