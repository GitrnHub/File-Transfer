import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

API = "https://api-drive.mypikpak.com"
RESULT_PATH = "pikpak_result.txt"

def publish(status, **details):
    payload = {"status": status, **details}
    message = "PIKPAK_RESULT_V1\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        f.write(message + "\n")
    print(message, flush=True)

def infohash_from_torrent(data):
    if len(data) > 256 * 1024 or not data.startswith(b"d"):
        raise ValueError("unexpected_torrent_metadata")
    info_raw = None

    def parse_string(pos):
        colon = data.find(b":", pos, min(pos + 12, len(data)))
        if colon < 0:
            raise ValueError("invalid_bencode_string")
        length = int(data[pos:colon])
        end = colon + 1 + length
        if length < 0 or end > len(data):
            raise ValueError("invalid_bencode_length")
        return data[colon + 1:end], end

    def skip(pos, depth=0):
        nonlocal info_raw
        if depth > 100 or pos >= len(data):
            raise ValueError("invalid_bencode_depth")
        typ = data[pos:pos + 1]
        if typ == b"d":
            pos += 1
            while pos < len(data) and data[pos:pos + 1] != b"e":
                key, pos = parse_string(pos)
                start = pos
                pos = skip(pos, depth + 1)
                if depth == 0 and key == b"info":
                    info_raw = data[start:pos]
            return pos + 1
        if typ == b"l":
            pos += 1
            while pos < len(data) and data[pos:pos + 1] != b"e":
                pos = skip(pos, depth + 1)
            return pos + 1
        if typ == b"i":
            end = data.find(b"e", pos + 1)
            if end < 0:
                raise ValueError("invalid_bencode_integer")
            int(data[pos + 1:end])
            return end + 1
        _, end = parse_string(pos)
        return end

    end = skip(0)
    if end != len(data) or not info_raw:
        raise ValueError("invalid_torrent_info")
    return hashlib.sha1(info_raw).hexdigest()

def get_json(path, pat, method="GET", data=None):
    payload = None if data is None else json.dumps(data).encode("utf-8")
    request = urllib.request.Request(
        API + path,
        data=payload,
        headers={
            "Authorization": "Bearer " + pat,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "PikPak-Offline-Bridge/1.0",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=18) as resp:
            return json.load(resp)
    except HTTPError as err:
        # Never print response bodies, authorization headers, or token strings.
        raise RuntimeError("pikpak_http_" + str(err.code)) from None
    except URLError:
        raise RuntimeError("pikpak_network_error") from None

def main():
    pat = os.environ.get("PIKPAK_PAT", "")
    if not pat:
        publish("blocked", reason="PIKPAK_PAT_unavailable_in_this_repository")
        return

    raw_issue = os.environ.get("ISSUE_BODY", "")
    url = os.environ.get("INPUT_TORRENT_URL", "").strip()
    if raw_issue:
        try:
            prefix = "PIKPAK_OFFLINE_REQUEST_V1\n"
            if not raw_issue.startswith(prefix):
                raise ValueError("invalid_issue_protocol")
            data = json.loads(raw_issue[len(prefix):])
            url = data["torrent_url"]
        except (ValueError, TypeError, KeyError):
            publish("blocked", reason="invalid_issue_request")
            return

    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "archive.org" or not parts.path.startswith("/download/") or not parts.path.endswith(".torrent"):
        publish("blocked", reason="only_public_archive_org_torrents_allowed")
        return

    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 File-Transfer/1.0",
            "Accept": "*/*",
        })
        with urllib.request.urlopen(request, timeout=45) as resp:
            torrent = resp.read(256 * 1024 + 1)
        btih = infohash_from_torrent(torrent)
        magnet = "magnet:?xt=urn:btih:" + btih
    except HTTPError as err:
        publish("blocked", reason="torrent_http_" + str(err.code))
        return
    except ValueError as err:
        publish("blocked", reason=str(err))
        return
    except Exception:
        publish("blocked", reason="torrent_metadata_network_or_parse_error")
        return

    try:
        task_resp = get_json("/drive/v1/files", pat, "POST", {
            "kind": "drive#file",
            "name": "",
            "upload_type": "UPLOAD_TYPE_URL",
            "url": {"url": magnet},
            "folder_type": "DOWNLOAD",
        })
    except RuntimeError as err:
        publish("failed", reason=str(err), infohash=btih)
        return

    if task_resp.get("error") or task_resp.get("error_code"):
        publish("failed", reason="pikpak_api_rejected", infohash=btih)
        return

    task = task_resp.get("task") or {}
    task_id = str(task.get("id", ""))
    if not task_id:
        publish("submitted_unverified", reason="api_no_task_id", infohash=btih)
        return

    phase = task.get("phase") or "UNKNOWN"
    progress = task.get("progress", None)
    for iteration in range(4):
        if phase == "PHASE_TYPE_ERROR":
            publish("failed", reason="pikpak_task_error", infohash=btih, phase=phase)
            return
        if phase == "PHASE_TYPE_COMPLETE" and (progress is None or progress == 100):
            publish("completed", infohash=btih, phase=phase)
            return
        if iteration == 3:
            break
        time.sleep(10)
        try:
            query = urllib.parse.urlencode({
                "type": "offline", "limit": "100", "page_token": "",
                "thumbnail_size": "SIZE_SMALL", "filters": "{}",
            })
            task_list = get_json("/drive/v1/tasks?" + query, pat)
            matches = [t for t in (task_list.get("tasks") or []) if str(t.get("id", "")) == task_id]
            if matches:
                phase = matches[0].get("phase") or phase
                progress = matches[0].get("progress", progress)
        except RuntimeError:
            # Submission was accepted, but status verification is unavailable.
            publish("submitted_unverified", reason="pikpak_task_query_unavailable", infohash=btih)
            return

    publish("pending", reason="not_complete_after_30_seconds", infohash=btih, phase=str(phase))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        publish("failed", reason="unexpected_bridge_error")
