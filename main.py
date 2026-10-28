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
from services.audio import extract_and_convert_audio, convert_to_mp3
from services.transcriber import transcribe_audio
from services.diarizer import identify_speakers
from services.translator import translate_segments
from services.subtitle_writer import write_subtitle_files, create_zip_bundle

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
            for p in list(job_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in [".mov", ".mp4", ".mkv", ".webm", ".avi", ".m4v", ".flv"]:
                    try:
                        p.unlink()
                        deleted_any = True
                    except Exception as e:
                        print(f"Erro ao deletar vídeo expirado {p}: {e}")

            if deleted_any or not data.get("media_deleted"):
                data["media_deleted"] = True
                data["media_deleted_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
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

        JOBS[job_id]["media_filename"] = media_path.name
        JOBS[job_id]["media_exists"] = True
        JOBS[job_id]["media_deleted"] = False
        JOBS[job_id]["file_size_bytes"] = file_size_bytes
        JOBS[job_id]["file_size_formatted"] = file_size_formatted
        JOBS[job_id]["output_formats"] = output_formats
        save_job_state(job_id)

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

        # OPÇÃO 1: Excluir o áudio WAV intermediário imediatamente após a transcrição
        if wav_path.exists():
            try:
                wav_path.unlink()
            except Exception as e:
                print(f"Aviso ao remover wav: {e}")

        # 4. Identificação de locutores para Minutagem/Ata
        await notify_job(job_id, {
            "status": "diarizing",
            "progress": 62,
            "message": "Identificando locutores da reunião/fala ([Pessoa 1], [Pessoa 2])..."
        })
        try:
            segments = await identify_speakers(segments)
        except Exception as e:
            print(f"Aviso na diarização: {e}")
            for s in segments:
                s["speaker"] = "Pessoa 1"

        duration_seconds = segments[-1]["end"] if segments else 0.0
        duration_formatted = format_duration(duration_seconds)
        JOBS[job_id]["duration_seconds"] = duration_seconds
        JOBS[job_id]["duration_formatted"] = duration_formatted

        effective_source_lang = source_lang if (source_lang and source_lang != "auto") else detected_lang
        JOBS[job_id]["detected_language"] = detected_lang
        JOBS[job_id]["effective_source_language"] = effective_source_lang
        
        # Save original subtitles
        base_name = media_path.stem
        sub_results = {}
        
        orig_files = write_subtitle_files(job_dir, base_name, effective_source_lang, segments)
        sub_results[effective_source_lang] = {
            "name": SUPPORTED_LANGUAGES.get(effective_source_lang, effective_source_lang),
            "files": {k: v.name for k, v in orig_files.items()},
            "segments": segments
        }

        # 5. Translations
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
                
                t_files = write_subtitle_files(job_dir, base_name, t_lang, t_segments)
                sub_results[t_lang] = {
                    "name": t_name,
                    "files": {k: v.name for k, v in t_files.items()},
                    "segments": t_segments
                }

        # 6. Converter para MP3 se selecionado
        mp3_file = None
        if "mp3" in [f.lower() for f in output_formats]:
            await notify_job(job_id, {
                "status": "converting_mp3",
                "progress": 90,
                "message": "Gerando arquivo de áudio MP3 (192kbps)..."
            })
            mp3_name = f"{base_name}.mp3"
            mp3_path = job_dir / mp3_name
            await convert_to_mp3(media_path, mp3_path)
            mp3_file = mp3_name
            JOBS[job_id]["mp3_file"] = mp3_file

        # 7. Create ZIP Bundle (limpo, sem arquivos .json)
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
    target_languages: List[str] = Form([]),
    output_formats: List[str] = Form(["timed", "srt", "plain"]),
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

    return templates.TemplateResponse(
        request=request,
        name="job.html",
        context={
            "job": job,
            "media_exists": media_exists,
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
