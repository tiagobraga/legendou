import os
import json
import uuid
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
from services.audio import extract_and_convert_audio
from services.transcriber import transcribe_audio
from services.translator import translate_segments
from services.subtitle_writer import write_subtitle_files, create_zip_bundle

app = FastAPI(title="Legendou", version="1.0.0")

# Mount static and templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# In-memory job state & event queues
JOBS: Dict[str, Dict[str, Any]] = {}
JOB_QUEUES: Dict[str, List[asyncio.Queue]] = {}

def get_job_file(job_id: str) -> Path:
    return DATA_DIR / job_id / "job.json"

def save_job_state(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    with open(job_dir / "job.json", "w", encoding="utf-8") as f:
        # Save a clean copy without non-serializable objects
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
    model_size: str = DEFAULT_WHISPER_MODEL
):
    target_langs = target_langs or []
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

        JOBS[job_id]["media_filename"] = media_path.name
        save_job_state(job_id)

        # 2. Extract Audio
        await notify_job(job_id, {"status": "extracting_audio", "progress": 25, "message": "Extraindo e normalizando áudio via FFmpeg..."})
        wav_path = job_dir / "audio_extracted.wav"
        await extract_and_convert_audio(media_path, wav_path)

        # 3. Transcribe via Whisper
        await notify_job(job_id, {"status": "transcribing", "progress": 35, "message": f"Iniciando transcrição com Whisper ({model_size}) na GPU..."})

        loop = asyncio.get_event_loop()
        def transcribe_cb(data):
            pct = 35 + int(data.get("progress_percent", 0) * 0.4)
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

        # 4. Translations
        langs_to_translate = [l for l in target_langs if l.lower() != effective_source_lang.lower()]
        if langs_to_translate:
            total_langs = len(langs_to_translate)
            for idx_l, t_lang in enumerate(langs_to_translate, start=1):
                t_name = SUPPORTED_LANGUAGES.get(t_lang, t_lang)
                await notify_job(job_id, {
                    "status": "translating",
                    "progress": 75 + int((idx_l / total_langs) * 20),
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

        # 5. Create ZIP Bundle
        zip_path = create_zip_bundle(job_dir, f"legendas_{base_name}.zip")
        
        # Complete
        await notify_job(job_id, {
            "id": job_id,
            "status": "completed",
            "progress": 100,
            "message": "Processamento concluído com sucesso!",
            "results": sub_results,
            "zip_file": zip_path.name,
            "completed_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        await notify_job(job_id, {
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
        "model_size": model_size,
        "original_name": original_name if not media_url else media_url.split("/")[-1],
        "status": "queued",
        "progress": 0,
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
            
    return templates.TemplateResponse(
        request=request,
        name="job.html",
        context={
            "job": job,
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
        raise HTTPException(status_code=404, detail="Arquivo de mídia físico não encontrado.")
    return FileResponse(media_path)
