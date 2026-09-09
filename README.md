# File-Transfer

File-Transfer is a small GitHub Actions bridge for one purpose:

> **When ChatGPT cannot fetch a public direct-download URL because of sandbox/network/tool limitations, use a GitHub-hosted runner to fetch the file and return either the original payload or a compact processed analysis as a short-lived Actions Artifact.**

This repository is intentionally not a file archive. Downloaded payloads are **not committed to Git**.

See `AGENTS.md` for the AI operating protocol. Supported automated modes are `[transfer]` for a byte-for-byte relay and `[video]` for video probing, frame extraction/contact sheets and speech transcription. Both return short-lived GitHub Actions Artifacts; transferred payloads and generated analysis outputs are never committed to Git.
