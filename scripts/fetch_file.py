#!/usr/bin/env python3
"""Fetch one public HTTP(S) file for the File-Transfer workflow.

Downloaded data is treated as opaque bytes. The script never executes, imports,
extracts, or otherwise interprets the payload.
"""

from __future__ import annotations

import email.message
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUEST_MARKER = "FILE_TRANSFER_REQUEST_V1"
DEFAULT_MAX_MB = 450
HARD_MAX_MB = 2048
CHUNK_SIZE = 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) File-Transfer/1.0"
)


class TransferError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def github_output(name: str, value: str | int) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def redact_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return "<redacted-url>"
    if parts.scheme not in {"http", "https"}:
        return "<redacted-url>"
    host = parts.hostname or "unknown-host"
    port = f":{parts.port}" if parts.port else ""
    path = parts.path or "/"
    suffix = "?<redacted>" if parts.query else ""
    return f"{parts.scheme}://{host}{port}{path}{suffix}"


def sanitize_error_text(text: str) -> str:
    pattern = re.compile(r"https?://[^\s\]\[(){}<>\"']+")
    return pattern.sub(lambda m: redact_url(m.group(0)), text)


def load_request() -> dict[str, Any]:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")

    if event_name == "issues":
        body = os.environ.get("ISSUE_BODY", "")
        lines = body.splitlines()
        try:
            marker_index = next(i for i, line in enumerate(lines) if line.strip() == REQUEST_MARKER)
        except StopIteration as exc:
            raise TransferError(f"Issue body must contain {REQUEST_MARKER}") from exc

        raw_json = "\n".join(lines[marker_index + 1 :]).strip()
        if not raw_json:
            raise TransferError("Transfer request JSON is missing")
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise TransferError(f"Invalid request JSON: {exc.msg}") from exc

    elif event_name == "workflow_dispatch":
        data = {
            "url": os.environ.get("INPUT_URL", ""),
            "filename": os.environ.get("INPUT_FILENAME", ""),
            "max_mb": os.environ.get("INPUT_MAX_MB", str(DEFAULT_MAX_MB)),
            "expected_sha256": os.environ.get("INPUT_EXPECTED_SHA256", ""),
        }
    else:
        raise TransferError(f"Unsupported event: {event_name or '<empty>'}")

    if not isinstance(data, dict):
        raise TransferError("Request must be a JSON object")

    url = str(data.get("url", "")).strip()
    if not url:
        raise TransferError("url is required")

    filename = str(data.get("filename", "") or "").strip()

    try:
        max_mb = int(data.get("max_mb", DEFAULT_MAX_MB))
    except (TypeError, ValueError) as exc:
        raise TransferError("max_mb must be an integer") from exc
    if max_mb < 1 or max_mb > HARD_MAX_MB:
        raise TransferError(f"max_mb must be between 1 and {HARD_MAX_MB}")

    expected_sha256 = str(data.get("expected_sha256", "") or "").strip().lower()
    if expected_sha256 and not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise TransferError("expected_sha256 must be exactly 64 hexadecimal characters")

    return {
        "url": url,
        "filename": filename,
        "max_mb": max_mb,
        "expected_sha256": expected_sha256,
    }


def resolved_public_addresses(hostname: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise TransferError(f"DNS resolution failed for host {hostname!r}: {exc}") from exc

    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise TransferError(f"Host {hostname!r} resolved to no addresses")

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise TransferError(
                f"Refusing non-public destination: {hostname!r} resolved to {address}"
            )

    return addresses


def validate_public_url(url: str) -> urllib.parse.SplitResult:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise TransferError("Malformed URL") from exc

    if parts.scheme not in {"http", "https"}:
        raise TransferError("Only http:// and https:// URLs are allowed")
    if not parts.hostname:
        raise TransferError("URL hostname is missing")
    if parts.username is not None or parts.password is not None:
        raise TransferError("Embedded URL credentials are not allowed")

    hostname = parts.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise TransferError("Local hostnames are not allowed")

    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise TransferError("Invalid URL port") from exc

    resolved_public_addresses(hostname, port)
    return parts


class GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def filename_from_content_disposition(header: str | None) -> str:
    if not header:
        return ""
    msg = email.message.Message()
    msg["content-disposition"] = header
    value = msg.get_filename()
    return value or ""


def sanitize_filename(name: str) -> str:
    name = urllib.parse.unquote(name).strip()
    name = re.sub(r"[\\/\x00-\x1f\x7f]+", "_", name)
    name = name.strip(" .")
    if name in {"", ".", ".."}:
        return ""
    if len(name) > 180:
        stem, suffix = os.path.splitext(name)
        keep = max(1, 180 - len(suffix))
        name = stem[:keep] + suffix[:40]
    return name


def choose_filename(override: str, response, final_url: str) -> str:
    candidates = [
        override,
        filename_from_content_disposition(response.headers.get("Content-Disposition")),
        os.path.basename(urllib.parse.urlsplit(final_url).path),
        "download.bin",
    ]
    for candidate in candidates:
        clean = sanitize_filename(candidate)
        if clean:
            return clean
    return "download.bin"


def download(request_data: dict[str, Any]) -> dict[str, Any]:
    source_url = request_data["url"]
    source_parts = validate_public_url(source_url)
    max_bytes = request_data["max_mb"] * 1024 * 1024

    opener = urllib.request.build_opener(GuardedRedirectHandler())
    req = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
        method="GET",
    )

    try:
        response = opener.open(req, timeout=45)
    except urllib.error.HTTPError as exc:
        raise TransferError(f"HTTP error {exc.code} while fetching {redact_url(source_url)}") from exc
    except urllib.error.URLError as exc:
        reason = sanitize_error_text(str(exc.reason))
        raise TransferError(f"Network error while fetching {redact_url(source_url)}: {reason}") from exc

    with response:
        final_url = response.geturl()
        final_parts = validate_public_url(final_url)

        length_header = response.headers.get("Content-Length")
        if length_header:
            try:
                declared_length = int(length_header)
            except ValueError:
                declared_length = -1
            if declared_length > max_bytes:
                raise TransferError(
                    f"Server declares {declared_length} bytes, exceeding limit {max_bytes} bytes"
                )

        filename = choose_filename(request_data["filename"], response, final_url)
        payload_dir = Path("payload")
        payload_dir.mkdir(parents=True, exist_ok=True)
        file_path = payload_dir / filename

        digest = hashlib.sha256()
        total = 0

        try:
            with file_path.open("wb") as f:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise TransferError(
                            f"Download exceeded configured limit of {max_bytes} bytes"
                        )
                    digest.update(chunk)
                    f.write(chunk)
        except Exception:
            file_path.unlink(missing_ok=True)
            raise

    sha256 = digest.hexdigest()
    expected = request_data["expected_sha256"]
    if expected and sha256 != expected:
        file_path.unlink(missing_ok=True)
        raise TransferError(
            f"SHA-256 mismatch: expected {expected}, received {sha256}"
        )

    mime_type = response.headers.get_content_type() if response.headers else ""
    if not mime_type or mime_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            mime_type = guessed
    if not mime_type:
        mime_type = "application/octet-stream"

    metadata = {
        "protocol": "file-transfer/v1",
        "downloaded_at": utc_now(),
        "filename": filename,
        "size_bytes": total,
        "sha256": sha256,
        "mime_type": mime_type,
        "source": {
            "scheme": source_parts.scheme,
            "host": source_parts.hostname,
        },
        "final_source": {
            "scheme": final_parts.scheme,
            "host": final_parts.hostname,
        },
        "expected_sha256_supplied": bool(expected),
    }

    with (Path("payload") / "transfer.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
        f.write("\n")

    github_output("filename", filename)
    github_output("size_bytes", total)
    github_output("sha256", sha256)
    github_output("mime_type", mime_type)
    github_output("source_host", source_parts.hostname or "")
    github_output("final_host", final_parts.hostname or "")
    return metadata


def write_diagnostic(exc: Exception) -> None:
    diagnostics = Path("diagnostics")
    diagnostics.mkdir(parents=True, exist_ok=True)
    message = sanitize_error_text(str(exc))
    data = {
        "protocol": "file-transfer/v1",
        "status": "failed",
        "failed_at": utc_now(),
        "error_type": type(exc).__name__,
        "message": message,
    }
    with (diagnostics / "transfer-error.json").open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    github_output("error", message.replace("\n", " "))


def main() -> int:
    try:
        request_data = load_request()
        metadata = download(request_data)
        print(
            "Downloaded successfully: "
            f"{metadata['filename']} ({metadata['size_bytes']} bytes, SHA-256 {metadata['sha256']})"
        )
        return 0
    except Exception as exc:
        write_diagnostic(exc)
        print(f"ERROR: {sanitize_error_text(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
