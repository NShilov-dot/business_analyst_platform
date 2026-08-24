#!/usr/bin/env python3
"""Post-deploy acceptance check for the deployed GigaAM ASR endpoint.

NOT a pytest test — this file has no `test_` prefix so pytest will not
collect it. Run manually, once, after `modal deploy services/asr/modal_app.py`:

    MODAL_KEY=... MODAL_SECRET=... python3 services/asr/tests/acceptance.py \\
        --endpoint https://<workspace>--bap-asr-asr-web.modal.run \\
        --url "https://<presigned-GET-url-of-a-16kHz-mono-wav>"

Producing a real short ru/uz sample file (operator step, not automated —
this script only ever reads from --url, it does not host files itself):

    say -v Milena -o meeting.aiff "Текст на русском для проверки распознавания..."
    ffmpeg -i meeting.aiff -ac 1 -ar 16000 -c:a pcm_s16le meeting.wav
    # upload meeting.wav somewhere and generate a presigned GET URL for it

Asserts: monotonic/within-duration timestamps (via contracts.validate_monotonic),
non-empty transcribed text. Prints /health (cold-start) and /transcribe latency.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import ContractError, TranscriptionResult, Utterance, Word, validate_monotonic  # noqa: E402


def _headers() -> dict[str, str]:
    key = os.environ.get("MODAL_KEY")
    secret = os.environ.get("MODAL_SECRET")
    if not key or not secret:
        raise SystemExit("MODAL_KEY and MODAL_SECRET env vars are required")
    return {"Modal-Key": key, "Modal-Secret": secret}


def _get_json(url: str, headers: dict[str, str]) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as resp:
        return json.loads(resp.read())


def _post_json(url: str, headers: dict[str, str], body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Surface the server's error detail — a bare traceback hides the reason.
        raise SystemExit(f"HTTP {exc.code} from {url}: {exc.read()[:500]!r}") from exc


def _result_from_json(payload: dict) -> TranscriptionResult:
    utterances = [
        Utterance(
            start_ms=u["start_ms"],
            end_ms=u["end_ms"],
            text=u["text"],
            confidence=u.get("confidence"),
            words=[Word(**w) for w in u["words"]] if u.get("words") else None,
        )
        for u in payload["utterances"]
    ]
    return TranscriptionResult(
        utterances=utterances,
        detected_language=payload.get("detected_language"),
        model_version=payload["model_version"],
        duration_ms=payload["duration_ms"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="deployed ASR web app base URL")
    parser.add_argument("--url", required=True, help="presigned GET URL of a normalized 16kHz mono WAV")
    args = parser.parse_args()

    headers = _headers()
    endpoint = args.endpoint.rstrip("/")

    t0 = time.monotonic()
    health = _get_json(f"{endpoint}/health", headers)
    cold_start_s = time.monotonic() - t0
    assert health.get("status") == "ok", f"unexpected /health response: {health}"
    print(f"cold-start / health latency: {cold_start_s:.1f}s")

    t1 = time.monotonic()
    payload = _post_json(f"{endpoint}/transcribe", headers, {"audio_url": args.url})
    transcribe_s = time.monotonic() - t1
    print(f"/transcribe latency: {transcribe_s:.1f}s")

    result = _result_from_json(payload)
    try:
        validate_monotonic(result)
    except ContractError as exc:
        raise SystemExit(f"FAIL: contract violation: {exc}") from exc

    if not any(u.text.strip() for u in result.utterances):
        raise SystemExit("FAIL: no non-empty utterance text returned")

    print(
        f"OK: {len(result.utterances)} utterance(s), "
        f"duration_ms={result.duration_ms}, model_version={result.model_version}"
    )
    for u in result.utterances[:3]:
        print(f"  [{u.start_ms:>7}ms-{u.end_ms:>7}ms] {u.text[:80]!r}")


if __name__ == "__main__":
    main()
