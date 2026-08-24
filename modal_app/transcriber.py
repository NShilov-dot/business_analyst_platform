"""Транскрибация GigaChat3.1-Audio на Modal — ВРЕМЕННЫЙ пилотный хостинг.

Deploy:   modal deploy modal_app/transcriber.py   (печатает URL ASGI-приложения)
Prewarm:  modal run modal_app/transcriber.py      (скачивает ~20 ГБ весов в Volume — ОБЯЗАТЕЛЬНО до первого запроса)
Auth:     Modal Proxy Auth (Settings → Proxy Auth Tokens) — заголовки Modal-Key / Modal-Secret.
Бэкенд ходит сюда через TranscriptionPort; on-prem-переезд удаляет этот файл, порт остаётся.
"""
import subprocess
import tempfile
from pathlib import Path

import modal

MODEL_ID = "ai-sage/GigaChat3.1-Audio-10B-A1.8B"
# Pinned HF commit — trust_remote_code executes repo code, so an unpinned
# revision means the upstream repo can change what runs on our GPU without us
# noticing. This is the snapshot already prefetched into the hf-hub-cache Volume.
MODEL_REVISION: str | None = "bf73d03a43bdf5118f5a4dbdc24ba6f56ac31cfb"
# ponytail: точность «дословно, без комментариев» тюнится только здесь.
ASR_PROMPT = ("Расшифруй эту аудиозапись дословно. "
              "В ответе верни только текст записи, без комментариев и пояснений.")
MAX_NEW_TOKENS = 2048          # ~3 мин русской речи с запасом
MAX_AUDIO_SECONDS = 600        # предохранитель GPU-времени

app = modal.App("bap-transcriber")
image = (modal.Image.debian_slim(python_version="3.12")
         .apt_install("ffmpeg")
         # vllm==0.18.0 (версия из model card) нужен даже для transformers-пути:
         # кастомный код модели (vllm_gigachat_audio.py) импортирует vllm.* на
         # верхнем уровне, и transformers.check_imports требует его наличия.
         # torch приезжает как зависимость vllm (своя запиненная версия).
         .pip_install("vllm==0.18.0", "torchaudio", "transformers>=4.57",
                      "accelerate", "librosa", "soundfile",
                      "fastapi[standard]", "python-multipart", "hf_transfer")
         .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HOME": "/cache"}))
cache = modal.Volume.from_name("hf-hub-cache", create_if_missing=True)


@app.function(image=image, volumes={"/cache": cache}, timeout=3600)
def download() -> None:
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL_ID, revision=MODEL_REVISION)
    cache.commit()


@app.local_entrypoint()
def main() -> None:
    download.remote()


@app.cls(
    image=image,
    gpu="L40S",              # 48 ГБ: 20 ГБ bf16 весов + активации
    timeout=600,             # потолок одного вызова (ограничение расхода)
    scaledown_window=900,    # тёплый 15 мин после последнего запроса
    max_containers=2,        # потолок стоимости при параллельных запросах
    volumes={"/cache": cache},
)
class Transcriber:
    @modal.enter()
    def load(self) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        self.processor = AutoProcessor.from_pretrained(
            MODEL_ID, trust_remote_code=True, revision=MODEL_REVISION)
        self.model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, trust_remote_code=True, dtype=torch.bfloat16, device_map="cuda:0",
            revision=MODEL_REVISION)

    @modal.asgi_app(requires_proxy_auth=True)   # ОДИН URL на оба роута
    def web(self):
        from fastapi import FastAPI, File, HTTPException, UploadFile
        api = FastAPI()

        @api.get("/health")                      # прогрев: контейнер уже прошёл @enter
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @api.post("/transcribe")
        async def transcribe(file: UploadFile = File(...)) -> dict[str, str]:
            raw = await file.read()
            with tempfile.TemporaryDirectory() as td:
                src, wav = Path(td) / "in.bin", Path(td) / "out.wav"
                src.write_bytes(raw)
                # ffmpeg сам определяет контейнер (webm/opus, ogg, mp4/m4a, mp3) по содержимому
                proc = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000",
                     "-t", str(MAX_AUDIO_SECONDS), str(wav)], capture_output=True)
                if proc.returncode != 0 or not wav.exists():
                    print("ffmpeg failed:", proc.stderr.decode(errors="replace")[-500:])
                    raise HTTPException(status_code=415, detail="Could not decode audio")
                messages = [{"role": "user", "content": [
                    {"type": "audio", "path": str(wav)},
                    {"type": "text", "text": ASR_PROMPT}]}]
                inputs = self.processor.prepare_for_inference(messages, device=self.model.device)
                output_ids = self.model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS,
                                                  do_sample=False)
                text = self.processor.decode(output_ids[0, inputs["input_ids"].shape[1]:],
                                              skip_special_tokens=True)
            return {"text": text.strip()}

        return api
