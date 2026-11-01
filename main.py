import os
import json
import uuid
import time
import shutil
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from fastapi import FastAPI, Request, BackgroundTasks, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import (
    BASE_DIR, DATA_DIR, SUPPORTED_LANGUAGES, WHISPER_MODELS,
    DEFAULT_WHISPER_MODEL, DEFAULT_DEVICE
)
from services.downloader import download_media
from services.audio import extract_and_convert_audio, convert_to_mp3, get_media_duration
from services.transcriber import transcribe_audio
from services.diarizer import identify_speakers
from services.translator import translate_segments
from services.subtitle_writer import write_subtitle_files, create_zip_bundle
from services.video import has_video_stream, burn_subtitles_to_video
from services.discourse import generate_discourse_text

app = FastAPI(title="Legendou", version="1.0.0")

# Mount static and templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# In-memory job state & event queues
JOBS: Dict[str, Dict[str, Any]] = {}
JOB_QUEUES: Dict[str, List[asyncio.Queue]] = {}

def format_file_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024**3):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024**2):.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"

def format_duration(seconds: float) -> str:
    secs = int(round(seconds))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    elif m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"

def format_elapsed(seconds: float) -> str:
    secs = int(round(seconds))
    if secs < 60:
        return f"{secs} segundos"
    m = secs // 60
    s = secs % 60
    return f"{m}m {s}s"

def get_job_file(job_id: str) -> Path:
    return DATA_DIR / job_id / "job.json"

def save_job_state(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    with open(job_dir / "job.json", "w", encoding="utf-8") as f:
        clean_copy = {k: v for k, v in job.items() if k not in ["queues"]}
        json.dump(clean_copy, f, ensure_ascii=False, indent=2)

def load_past_jobs() -> List[Dict[str, Any]]:
    past_jobs = []
    if not DATA_DIR.exists():
        return past_jobs
    for job_dir in sorted(DATA_DIR.iterdir(), key=lambda p: p.stat().st_mtime if p.is_dir() else 0, reverse=True):
        if job_dir.is_dir():
            meta = job_dir / "job.json"
            if meta.exists():
                try:
                    with open(meta, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        past_jobs.append(data)
                except Exception:
                    pass
    return past_jobs

def cleanup_expired_media(max_age_seconds: int = 3600):
    """
    Remove arquivos de áudio WAV temporários e exclui os vídeos originais
    com mais de 60 minutos (3600s) após a conversão para liberar espaço em disco.
    """
    if not DATA_DIR.exists():
        return
    now = time.time()
    for job_dir in DATA_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        meta_path = job_dir / "job.json"
        data = {}
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
            except Exception:
                data = {}

        # 1. Deletar qualquer WAV intermediário imediatamente
        wav_file = job_dir / "audio_extracted.wav"
        if wav_file.exists():
            try:
                wav_file.unlink()
            except Exception:
                pass

        # 2. Checar idade para remoção do vídeo original (60 min)
        # Só remove se o job já completou com sucesso há mais de max_age_seconds
        completed_ts = data.get("completed_timestamp")
        if not completed_ts and data.get("status") == "completed":
            try:
                completed_ts = job_dir.stat().st_mtime
            except Exception:
                completed_ts = None

        if completed_ts and (now - completed_ts) >= max_age_seconds:
            deleted_any = False

            # 1. Deletar arquivo de mídia original enviado/baixado (vídeo ou áudio pesado: .ogg, .mp4, .mov, etc.)
            orig_name = data.get("media_filename")
            if orig_name:
                orig_file = job_dir / orig_name
                # Preserva apenas se o arquivo for o MP3 final gerado
                if orig_file.exists() and orig_file.is_file() and orig_file.name != data.get("mp3_file"):
                    try:
                        orig_file.unlink()
                        deleted_any = True
                    except Exception as e:
                        print(f"Erro ao deletar mídia original expirada {orig_file}: {e}")

            # 2. Deletar arquivos de mídia temporários pesados (.mov, .mp4, .mkv, .ogg, etc.) mantendo .mp3 e textos
            for p in list(job_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in [".mov", ".mp4", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ogg", ".wav"]:
                    if p.name != data.get("mp3_file") and p.suffix.lower() not in [".srt", ".vtt", ".txt", ".json", ".zip"]:
                        try:
                            p.unlink()
                            deleted_any = True
                        except Exception as e:
                            print(f"Erro ao deletar arquivo de mídia expirado {p}: {e}")

            if deleted_any or not data.get("media_deleted"):
                data["media_deleted"] = True
                data["media_deleted_at"] = data.get("media_deleted_at") or datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                if meta_path.exists():
                    try:
                        with open(meta_path, "w", encoding="utf-8") as jf:
                            json.dump(data, jf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
                if data.get("id") in JOBS:
                    JOBS[data["id"]]["media_deleted"] = True
                    JOBS[data["id"]]["media_exists"] = False

@app.on_event("startup")
async def startup_event():
    try:
        cleanup_expired_media(3600)
    except Exception as e:
        print(f"Aviso na limpeza inicial: {e}")

    async def periodic_cleanup():
        while True:
            await asyncio.sleep(180)
            try:
                cleanup_expired_media(3600)
            except Exception as e:
                print(f"Aviso na limpeza periódica: {e}")
    asyncio.create_task(periodic_cleanup())

async def notify_job(job_id: str, event_data: dict):
    if "id" not in event_data:
        event_data["id"] = job_id
    if job_id in JOBS:
        JOBS[job_id].update(event_data)
        save_job_state(job_id)
    if job_id in JOB_QUEUES:
        for q in list(JOB_QUEUES[job_id]):
            await q.put(dict(event_data))

async def process_job(
    job_id: str,
    media_url: Optional[str] = None,
    uploaded_file_path: Optional[Path] = None,
    source_lang: Optional[str] = None,
    speaker_mode: str = "auto",
    target_langs: List[str] = None,
    output_formats: Optional[List[str]] = None,
    model_size: str = DEFAULT_WHISPER_MODEL
):
    target_langs = target_langs or []
    output_formats = output_formats or ["timed", "srt", "plain"]
    start_time = time.time()
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Download or locate media
        await notify_job(job_id, {"status": "downloading", "progress": 5, "message": "Obtendo arquivo de mídia..."})
        
        if uploaded_file_path and uploaded_file_path.exists():
            media_path = uploaded_file_path
        elif media_url:
            def dl_cb(down, total, msg):
                asyncio.run_coroutine_threadsafe(
                    notify_job(job_id, {"status": "downloading", "progress": 10, "message": msg}),
                    asyncio.get_event_loop()
                )
            media_path = await download_media(media_url, job_dir, progress_callback=dl_cb)
        else:
            raise ValueError("Nenhum link ou arquivo fornecido.")

        file_size_bytes = media_path.stat().st_size
        file_size_formatted = format_file_size(file_size_bytes)

        # Resolução condicional dos formatos de saída selecionados
        selected_formats = set(f.lower() for f in output_formats)
        needs_transcription = bool(selected_formats & {"timed", "srt", "plain", "discourse", "video"})
        needs_diarization = "timed" in selected_formats
        needs_discourse = "discourse" in selected_formats
        needs_mp3 = "mp3" in selected_formats
        media_has_video = has_video_stream(media_path)
        needs_video = "video" in selected_formats and media_has_video
        base_name = media_path.stem

        JOBS[job_id]["media_filename"] = media_path.name
        JOBS[job_id]["media_exists"] = True
        JOBS[job_id]["media_deleted"] = False
        JOBS[job_id]["has_video"] = media_has_video
        JOBS[job_id]["file_size_bytes"] = file_size_bytes
        JOBS[job_id]["file_size_formatted"] = file_size_formatted
        JOBS[job_id]["output_formats"] = output_formats
        save_job_state(job_id)

        mp3_file = None
        video_file = None
        video_skipped_reason = None
        sub_results = {}

        # =====================================================================
        # CASO 1: CAMINHO RÁPIDO (Apenas Áudio MP3, sem transcrição textual)
        # =====================================================================
        if not needs_transcription:
            dur = get_media_duration(media_path)
            duration_formatted = format_duration(dur)
            JOBS[job_id]["duration_seconds"] = dur
            JOBS[job_id]["duration_formatted"] = duration_formatted
            JOBS[job_id]["detected_language"] = source_lang if (source_lang and source_lang != "auto") else "auto"
            JOBS[job_id]["effective_source_language"] = JOBS[job_id]["detected_language"]

            if needs_mp3:
                await notify_job(job_id, {
                    "status": "converting_mp3",
                    "progress": 50,
                    "message": "Extraindo áudio MP3 (192kbps) diretamente via FFmpeg..."
                })
                mp3_name = f"{base_name}.mp3"
                mp3_path = job_dir / mp3_name
                extra_meta = {
                    "title": JOBS[job_id].get("original_name") or base_name,
                    "comment": f"Tarefa #{job_id} • Legendou (curio.ia.br)"
                }
                if JOBS[job_id].get("media_url"):
                    extra_meta["comment"] = f"Fonte: {JOBS[job_id]['media_url']} • Legendou (curio.ia.br)"
                await convert_to_mp3(media_path, mp3_path, extra_metadata=extra_meta)
                mp3_file = mp3_name
                JOBS[job_id]["mp3_file"] = mp3_file

            zip_path = create_zip_bundle(job_dir, f"legendas_{base_name}.zip")
            elapsed_seconds = round(time.time() - start_time, 1)
            processing_time_formatted = format_elapsed(elapsed_seconds)
            completed_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            completed_ts = time.time()

            await notify_job(job_id, {
                "id": job_id,
                "status": "completed",
                "progress": 100,
                "message": "Extração de áudio concluída com sucesso!",
                "results": {},
                "zip_file": zip_path.name,
                "mp3_file": mp3_file,
                "video_file": None,
                "video_exists": False,
                "video_skipped_reason": "Processamento concluído: Áudio extraído diretamente sem geração de legendas textuais.",
                "media_exists": media_path.exists(),
                "media_deleted": False,
                "file_size_formatted": file_size_formatted,
                "duration_formatted": duration_formatted,
                "processing_time_formatted": processing_time_formatted,
                "elapsed_seconds": elapsed_seconds,
                "completed_at": completed_at,
                "completed_timestamp": completed_ts
            })
            return

        # =====================================================================
        # CASO 2: PIPELINE COM TRANSCRIÇÃO (Whisper GPU + Textos/Legendas)
        # =====================================================================

        # 2. Extract Audio for Whisper
        await notify_job(job_id, {"status": "extracting_audio", "progress": 20, "message": "Extraindo e normalizando áudio via FFmpeg..."})
        wav_path = job_dir / "audio_extracted.wav"
        await extract_and_convert_audio(media_path, wav_path)

        # 3. Transcribe via Whisper
        await notify_job(job_id, {"status": "transcribing", "progress": 30, "message": f"Iniciando transcrição com Whisper ({model_size}) na GPU..."})

        loop = asyncio.get_event_loop()
        def transcribe_cb(data):
            pct = 30 + int(data.get("progress_percent", 0) * 0.3)
            asyncio.run_coroutine_threadsafe(
                notify_job(job_id, {
                    "status": "transcribing",
                    "progress": pct,
                    "message": data.get("message", "Transcrevendo..."),
                    "current_segment": data.get("current_segment")
                }),
                loop
            )

        segments, detected_lang = await loop.run_in_executor(
            None,
            lambda: transcribe_audio(
                wav_path,
                model_size=model_size,
                language=source_lang,
                device=DEFAULT_DEVICE,
                progress_callback=transcribe_cb
            )
        )

        if wav_path.exists():
            try:
                wav_path.unlink()
            except Exception as e:
                print(f"Aviso ao remover wav: {e}")

        # 4. Identificação de locutores (apenas se Minutagem/Ata 'timed' estiver selecionada)
        if needs_diarization:
            await notify_job(job_id, {
                "status": "diarizing",
                "progress": 62,
                "message": "Identificando locutores da fala ([Pessoa 1], [Pessoa 2])..."
            })
            try:
                segments = await identify_speakers(segments, speaker_mode=speaker_mode)
            except Exception as e:
                print(f"Aviso na diarização: {e}")
                for s in segments:
                    s["speaker"] = "Pessoa 1"
        else:
            for s in segments:
                s["speaker"] = "Pessoa 1"

        duration_seconds = segments[-1]["end"] if segments else 0.0
        duration_formatted = format_duration(duration_seconds)
        JOBS[job_id]["duration_seconds"] = duration_seconds
        JOBS[job_id]["duration_formatted"] = duration_formatted

        effective_source_lang = source_lang if (source_lang and source_lang != "auto") else detected_lang
        JOBS[job_id]["detected_language"] = detected_lang
        JOBS[job_id]["effective_source_language"] = effective_source_lang
        
        # 5. Formato Discurso (apenas se 'discourse' estiver selecionado)
        orig_discourse = None
        if needs_discourse:
            await notify_job(job_id, {
                "status": "generating_discourse",
                "progress": 68,
                "message": "Gerando formato Discurso (redação editorial limpa sem vícios de fala)..."
            })
            try:
                orig_discourse = await generate_discourse_text(segments, lang_code=effective_source_lang)
            except Exception as e:
                print(f"Aviso na geração de discurso: {e}")

        # Gravar arquivos da língua original de acordo com os formatos selecionados
        orig_files = write_subtitle_files(
            job_dir,
            base_name,
            effective_source_lang,
            segments,
            discourse_text=orig_discourse,
            selected_formats=output_formats
        )
        sub_results[effective_source_lang] = {
            "name": SUPPORTED_LANGUAGES.get(effective_source_lang, effective_source_lang),
            "files": {k: v.name for k, v in orig_files.items()},
            "segments": segments,
            "discourse": orig_discourse
        }

        # 6. Traduções (apenas se houver idiomas alvos diferentes e formatos textuais solicitados)
        langs_to_translate = [l for l in target_langs if l.lower() != effective_source_lang.lower()]
        if langs_to_translate:
            total_langs = len(langs_to_translate)
            for idx_l, t_lang in enumerate(langs_to_translate, start=1):
                t_name = SUPPORTED_LANGUAGES.get(t_lang, t_lang)
                await notify_job(job_id, {
                    "status": "translating",
                    "progress": 70 + int((idx_l / total_langs) * 15),
                    "message": f"Traduzindo legendas para {t_name}..."
                })
                
                t_segments = await translate_segments(
                    segments,
                    source_lang=effective_source_lang,
                    target_lang=t_lang
                )

                t_discourse = None
                if needs_discourse:
                    try:
                        t_discourse = await generate_discourse_text(t_segments, lang_code=t_lang)
                    except Exception as e:
                        print(f"Aviso na geração de discurso traduzido: {e}")
                
                t_files = write_subtitle_files(
                    job_dir,
                    base_name,
                    t_lang,
                    t_segments,
                    discourse_text=t_discourse,
                    selected_formats=output_formats
                )
                sub_results[t_lang] = {
                    "name": t_name,
                    "files": {k: v.name for k, v in t_files.items()},
                    "segments": t_segments,
                    "discourse": t_discourse
                }

        # 7. Converter para MP3 se selecionado
        if needs_mp3:
            await notify_job(job_id, {
                "status": "converting_mp3",
                "progress": 88,
                "message": "Gerando arquivo de áudio MP3 (192kbps)..."
            })
            mp3_name = f"{base_name}.mp3"
            mp3_path = job_dir / mp3_name
            extra_meta = {
                "title": JOBS[job_id].get("original_name") or base_name,
                "comment": f"Tarefa #{job_id} • Legendou (curio.ia.br)"
            }
            if JOBS[job_id].get("media_url"):
                extra_meta["comment"] = f"Fonte: {JOBS[job_id]['media_url']} • Legendou (curio.ia.br)"
            await convert_to_mp3(media_path, mp3_path, extra_metadata=extra_meta)
            mp3_file = mp3_name
            JOBS[job_id]["mp3_file"] = mp3_file

        # 8. Gerar Vídeo Legendado (.MP4) se selecionado
        if "video" in selected_formats:
            if media_has_video:
                await notify_job(job_id, {
                    "status": "generating_video",
                    "progress": 94,
                    "message": "Renderizando vídeo com legendas embutidas (hardsub)..."
                })
                video_name = f"{base_name}_legendado.mp4"
                video_path = job_dir / video_name
                try:
                    srt_for_video = orig_files.get("srt")
                    if not srt_for_video or not srt_for_video.exists():
                        srt_for_video = job_dir / f"{base_name}_{effective_source_lang}.srt"
                    await burn_subtitles_to_video(
                        media_path,
                        srt_for_video,
                        video_path
                    )
                    video_file = video_name
                    JOBS[job_id]["video_file"] = video_file
                    JOBS[job_id]["video_exists"] = True
                    if effective_source_lang in sub_results:
                        sub_results[effective_source_lang]["files"]["video"] = video_file
                except Exception as e:
                    print(f"Erro ao gerar vídeo legendado: {e}")
                    video_skipped_reason = f"Erro na renderização do vídeo: {str(e)}"
                    JOBS[job_id]["video_skipped_reason"] = video_skipped_reason
            else:
                video_skipped_reason = "A mídia enviada é apenas áudio; a geração de vídeo foi dispensada."
                JOBS[job_id]["video_skipped_reason"] = video_skipped_reason

        # 8. Create ZIP Bundle (limpo, sem arquivos .json ou vídeos pesados)
        zip_path = create_zip_bundle(job_dir, f"legendas_{base_name}.zip")
        
        elapsed_seconds = round(time.time() - start_time, 1)
        processing_time_formatted = format_elapsed(elapsed_seconds)
        completed_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        completed_ts = time.time()

        # Complete
        await notify_job(job_id, {
            "id": job_id,
            "status": "completed",
            "progress": 100,
            "message": "Processamento concluído com sucesso!",
            "results": sub_results,
            "zip_file": zip_path.name,
            "mp3_file": mp3_file,
            "video_file": video_file,
            "video_exists": bool(video_file and (job_dir / video_file).exists()),
            "video_skipped_reason": video_skipped_reason,
            "media_exists": media_path.exists(),
            "media_deleted": False,
            "file_size_formatted": file_size_formatted,
            "duration_formatted": duration_formatted,
            "processing_time_formatted": processing_time_formatted,
            "elapsed_seconds": elapsed_seconds,
            "completed_at": completed_at,
            "completed_timestamp": completed_ts
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        await notify_job(job_id, {
            "id": job_id,
            "status": "failed",
            "progress": 100,
            "message": f"Erro durante o processamento: {str(e)}"
        })

# Routes
@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon():
    fav_path = BASE_DIR / "static" / "favicon.png"
    if fav_path.exists():
        return FileResponse(fav_path)
    raise HTTPException(status_code=404)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def index_page(request: Request):
    past_jobs = load_past_jobs()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "languages": SUPPORTED_LANGUAGES,
            "models": WHISPER_MODELS,
            "default_model": DEFAULT_WHISPER_MODEL,
            "past_jobs": past_jobs[:15]
        }
    )

@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    media_url: Optional[str] = Form(None),
    source_language: str = Form("auto"),
    speaker_mode: str = Form("auto"),
    target_languages: List[str] = Form([]),
    output_formats: List[str] = Form(["timed", "srt", "plain", "discourse", "video"]),
    model_size: str = Form(DEFAULT_WHISPER_MODEL),
    file: Optional[UploadFile] = File(None)
):
    if not media_url and not (file and file.filename):
        raise HTTPException(status_code=400, detail="Forneça um link de mídia ou faça upload de um arquivo.")

    job_id = str(uuid.uuid4())[:8]
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    uploaded_path = None
    original_name = "midia"
    if file and file.filename:
        original_name = file.filename
        uploaded_path = job_dir / file.filename
        with open(uploaded_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

    JOBS[job_id] = {
        "id": job_id,
        "created_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "media_url": media_url,
        "source_language": source_language,
        "speaker_mode": speaker_mode,
        "target_languages": target_languages,
        "output_formats": output_formats,
        "model_size": model_size,
        "original_name": original_name if not media_url else media_url.split("/")[-1],
        "status": "queued",
        "progress": 0,
        "media_exists": bool(uploaded_path and uploaded_path.exists()),
        "media_deleted": False,
        "message": "Tarefa criada e enfileirada."
    }
    save_job_state(job_id)

    background_tasks.add_task(
        process_job,
        job_id=job_id,
        media_url=media_url,
        uploaded_file_path=uploaded_path,
        source_lang=source_language,
        speaker_mode=speaker_mode,
        target_langs=target_languages,
        output_formats=output_formats,
        model_size=model_size
    )

    return {"job_id": job_id, "redirect_url": f"/jobs/{job_id}"}

@app.api_route("/jobs/{job_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def job_detail_page(request: Request, job_id: str):
    job = JOBS.get(job_id)
    if not job:
        job_file = get_job_file(job_id)
        if job_file.exists():
            with open(job_file, "r", encoding="utf-8") as f:
                job = json.load(f)
                JOBS[job_id] = job
        else:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")

    # Verifica existência física do arquivo de mídia original
    media_exists = False
    if job.get("media_filename") and not job.get("media_deleted"):
        media_path = DATA_DIR / job_id / job["media_filename"]
        media_exists = media_path.exists()
    job["media_exists"] = media_exists

    # Verifica existência física do vídeo legendado gerado
    video_exists = False
    if job.get("video_file") and not job.get("media_deleted"):
        video_path = DATA_DIR / job_id / job["video_file"]
        video_exists = video_path.exists()
    job["video_exists"] = video_exists

    return templates.TemplateResponse(
        request=request,
        name="job.html",
        context={
            "job": job,
            "media_exists": media_exists,
            "video_exists": video_exists,
            "languages": SUPPORTED_LANGUAGES
        }
    )

@app.get("/api/jobs/{job_id}/stream")
async def job_stream(job_id: str):
    if job_id not in JOB_QUEUES:
        JOB_QUEUES[job_id] = []
        
    queue = asyncio.Queue()
    JOB_QUEUES[job_id].append(queue)

    async def event_generator():
        try:
            # Send initial state
            if job_id not in JOBS:
                job_file = get_job_file(job_id)
                if job_file.exists():
                    try:
                        with open(job_file, "r", encoding="utf-8") as jf:
                            JOBS[job_id] = json.load(jf)
                    except Exception:
                        pass
            if job_id in JOBS:
                yield f"data: {json.dumps(JOBS[job_id], ensure_ascii=False)}\n\n"
            while True:
                data = await queue.get()
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                if data.get("status") in ["completed", "failed"]:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            if job_id in JOB_QUEUES and queue in JOB_QUEUES[job_id]:
                JOB_QUEUES[job_id].remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/jobs/{job_id}/download/{filename}")
async def download_job_file(job_id: str, filename: str):
    import urllib.parse
    file_path = DATA_DIR / job_id / filename
    if not file_path.exists():
        quoted_path = DATA_DIR / job_id / urllib.parse.quote(filename)
        if quoted_path.exists():
            file_path = quoted_path
        else:
            unquoted_path = DATA_DIR / job_id / urllib.parse.unquote(filename)
            if unquoted_path.exists():
                file_path = unquoted_path
            else:
                raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    return FileResponse(file_path, filename=filename)

@app.get("/api/jobs/{job_id}/media")
async def stream_job_media(job_id: str):
    job = JOBS.get(job_id)
    if not job or "media_filename" not in job:
        raise HTTPException(status_code=404, detail="Mídia não encontrada.")
    media_path = DATA_DIR / job_id / job["media_filename"]
    if not media_path.exists():
        raise HTTPException(status_code=410, detail="Mídia original expirada e removida (política de 60 minutos).")
    return FileResponse(media_path)
