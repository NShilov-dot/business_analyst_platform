"""GigaAM-Multilingual ASR on Modal — the only text source for Uzbek meetings
(GigaChat3.1-Audio does semantics but only ru/en). SEPARATE container from
GigaChat3.1-Audio on purpose: this image needs transformers==5.*, that one
needs 4.57.* — never combine (docs/gigaam-api-findings.md §7).

Deploy:   modal deploy services/asr/modal_app.py
Prewarm:  modal run services/asr/modal_app.py    (downloads the pinned GigaAM
          revision AND the gated pyannote/segmentation-3.0 VAD backbone into
          the shared hf-hub-cache Volume — required once before any request,
          needs the `huggingface-secret` Modal secret for the gated pull)
Auth:     Modal Proxy Auth — Modal-Key / Modal-Secret headers.

Stateless by design: presigned URL in, TranscriptionResult JSON out, forget.
No orchestration here — step order (normalize -> transcribe -> persist) is
owned by our backend's Celery pipeline, not this service.

NOTE: no `from __future__ import annotations` here on purpose — PEP 563 turns
annotations into strings, and FastAPI cannot resolve the closure-scoped
TranscribeRequest from the module globals, silently reclassifying the JSON
body as a required query parameter (every request then 422s).
"""
import modal

from config import (
    GPU,
    MODEL_ID,
    MODEL_REVISION,
    PIPER_VOICE_ONNX,
    PIPER_VOICE_REPO,
    PYANNOTE_MODEL_ID,
    VOLUME_NAME,
)

app = modal.App("bap-asr")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install(
        "torch==2.10.*",
        "torchaudio==2.10.*",
        "transformers==5.*",
        "hydra-core",
        "omegaconf",
        "soundfile",
        "sentencepiece",
        "pyannote.audio",
        # pyannote>=4 reads audio via torchcodec (not torchaudio) — without it
        # the longform VAD dies at runtime: "torchcodec is not available".
        # PINNED: torchcodec releases pair 1:1 with torch minors (0.10 <-> 2.10);
        # unpinned pip grabs a build for a newer torch/CUDA 13 whose native lib
        # fails to load (libnvrtc.so.13 / undefined symbol torch_from_blob).
        "torchcodec==0.10.*",
        "huggingface_hub",
        "httpx",
        "fastapi[standard]",
        "piper-tts",  # brings onnxruntime CPU — used for /synthesize
    )
    .env({"HF_HOME": "/cache"})
    .add_local_python_source("config", "contracts", "audio_io", "model", "synth")
)
cache = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
hf_secret = [modal.Secret.from_name("huggingface-secret")]  # HF_TOKEN — needed for the gated pyannote pull


@app.function(image=image, volumes={"/cache": cache}, timeout=1800, secrets=hf_secret)
def prefetch() -> None:
    """One-time warm of the shared Volume: the pinned GigaAM revision, the
    gated pyannote VAD backbone (findings §4), and the Piper TTS voice files
    for /synthesize. Run before the first /transcribe or /synthesize call."""
    from huggingface_hub import hf_hub_download, snapshot_download

    snapshot_download(MODEL_ID, revision=MODEL_REVISION)
    snapshot_download(PYANNOTE_MODEL_ID)
    hf_hub_download(PIPER_VOICE_REPO, PIPER_VOICE_ONNX)
    hf_hub_download(PIPER_VOICE_REPO, PIPER_VOICE_ONNX + ".json")
    cache.commit()


@app.local_entrypoint()
def main() -> None:
    prefetch.remote()


@app.cls(
    image=image,
    gpu=GPU,
    timeout=1800,           # hour-long meeting ceiling
    scaledown_window=600,
    max_containers=2,
    volumes={"/cache": cache},
    secrets=hf_secret,
)
class Asr:
    @modal.enter()
    def load(self) -> None:
        from model import load_model, warmup_longform

        self.model = load_model()
        warmup_longform(self.model)

        # Best-effort: a TTS load failure must never crash @enter() and take
        # down ASR with it — /synthesize just 503s if self.tts stays None.
        self.tts = None
        try:
            from huggingface_hub import hf_hub_download

            try:
                from piper import PiperVoice
            except ImportError:
                from piper.voice import PiperVoice

            onnx_path = hf_hub_download(PIPER_VOICE_REPO, PIPER_VOICE_ONNX, local_files_only=True)
            config_path = hf_hub_download(
                PIPER_VOICE_REPO, PIPER_VOICE_ONNX + ".json", local_files_only=True
            )
            self.tts = PiperVoice.load(onnx_path, config_path=config_path)
        except Exception:
            import logging

            logging.getLogger(__name__).warning("piper voice load failed — /synthesize will 503", exc_info=True)

    @modal.asgi_app(requires_proxy_auth=True)  # ONE URL for all routes
    def web(self):
        from fastapi import FastAPI, HTTPException, Request, Response
        from pydantic import BaseModel

        from audio_io import (
            AsrInputError,
            decode_any_to_16k_mono,
            download,
            validate_wav,
            write_temp_wav,
        )
        from contracts import ContractError, validate_monotonic
        from model import transcribe
        from synth import SynthTextError, build_wav_bytes, validate_synth_text

        api = FastAPI()

        class TranscribeRequest(BaseModel):
            audio_url: str

        class SynthesizeRequest(BaseModel):
            text: str

        @api.get("/health")  # prewarm target: container has already run @modal.enter()
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        def _run_model(audio) -> dict:
            with write_temp_wav(audio) as path:
                # Dispatch by sample count — the model's 25 s reject is defined
                # in samples, ms rounding misroutes boundary files (model.py).
                result = transcribe(self.model, path, num_samples=len(audio))
            try:
                validate_monotonic(result)
            except ContractError as exc:
                # a self-check failure here is a bug in this service, not a bad request
                raise HTTPException(status_code=500, detail=f"internal contract violation: {exc}") from exc
            return result.to_dict()

        @api.post("/transcribe")
        async def do_transcribe(request: Request) -> dict:
            content_type = (request.headers.get("content-type") or "").lower()

            if content_type.startswith("multipart/"):
                # Chat-compat mode: mirrors bap-transcriber's contract so the
                # backend's ModalTranscriptionGateway can point here unchanged —
                # a MediaRecorder blob in a `file` field (any container,
                # ffmpeg-decoded), response is {"text": ...}. This is what gives
                # the intake chat Uzbek/Kazakh/Kyrgyz voice input.
                form = await request.form()
                upload = form.get("file")
                if upload is None or isinstance(upload, str):
                    raise HTTPException(status_code=422, detail="multipart field 'file' is required")
                data = await upload.read()
                try:
                    audio = decode_any_to_16k_mono(data)
                except AsrInputError as exc:
                    raise HTTPException(status_code=415, detail=str(exc)) from exc
                result = _run_model(audio)
                text = " ".join(u["text"] for u in result["utterances"]).strip()
                return {"text": text}

            # JSON mode (meeting pipeline): {"audio_url": <presigned>} in,
            # full TranscriptionResult out. Strict 16 kHz mono WAV contract.
            try:
                body = TranscribeRequest.model_validate(await request.json())
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=422, detail="invalid body: expected JSON {\"audio_url\": ...}"
                ) from exc
            try:
                raw = download(body.audio_url)
                audio, _duration_ms = validate_wav(raw)
            except AsrInputError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return _run_model(audio)

        @api.post("/synthesize")
        async def do_synthesize(body: SynthesizeRequest) -> Response:
            if self.tts is None:
                raise HTTPException(status_code=503, detail="TTS voice failed to load")
            try:
                text = validate_synth_text(body.text)
            except SynthTextError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            wav_bytes = build_wav_bytes(self.tts, text)
            return Response(content=wav_bytes, media_type="audio/wav")

        return api
