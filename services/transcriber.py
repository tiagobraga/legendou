import os
import wave
import numpy as np
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Any
from faster_whisper import WhisperModel

_MODEL_CACHE: Dict[str, WhisperModel] = {}

def get_whisper_model(model_size: str = "large-v3", device: str = "cuda") -> WhisperModel:
    cache_key = f"{model_size}_{device}"
    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]
        
    compute_type = "float16" if device == "cuda" else "int8"
    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as e:
        print(f"Aviso: Falha ao carregar no dispositivo '{device}': {e}. Tentando fallback em CPU...")
        model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=8)
        
    _MODEL_CACHE[cache_key] = model
    return model

def load_wav_pcm16_mono(path: str) -> np.ndarray:
    with wave.open(path, 'rb') as wf:
        raw = wf.readframes(wf.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

def transcribe_audio(
    wav_path: Path,
    model_size: str = "large-v3",
    language: Optional[str] = None,
    device: str = "cuda",
    progress_callback: Optional[Callable[[dict], None]] = None
) -> Tuple[List[Dict[str, Any]], str]:
    if progress_callback:
        progress_callback({"status": "loading_model", "message": f"Carregando Whisper ({model_size}) na GPU..."})

    audio = load_wav_pcm16_mono(str(wav_path))
    duration = len(audio) / 16000.0
    
    model = get_whisper_model(model_size=model_size, device=device)
    
    if progress_callback:
        progress_callback({
            "status": "transcribing",
            "message": f"Transcrevendo áudio ({duration:.1f} segundos)...",
            "duration": duration,
            "progress_percent": 0
        })

    # Prepare transcribe arguments
    kwargs = {
        "beam_size": 5,
        "vad_filter": True,
        "vad_parameters": dict(min_silence_duration_ms=500),
    }
    if language and language.lower() not in ["auto", ""]:
        kwargs["language"] = language.lower()
        
    segments_generator, info = model.transcribe(audio, **kwargs)
    detected_language = info.language
    
    segments_list = []
    for idx, s in enumerate(segments_generator, start=1):
        text = s.text.strip()
        seg_data = {
            "id": idx,
            "start": round(s.start, 3),
            "end": round(s.end, 3),
            "text": text
        }
        segments_list.append(seg_data)
        
        if progress_callback and duration > 0:
            current_pct = min(100, int((s.end / duration) * 100))
            progress_callback({
                "status": "transcribing",
                "message": f"Transcrevendo: {current_pct}% [{format_time(s.start)} -> {format_time(s.end)}]",
                "progress_percent": current_pct,
                "current_segment": seg_data
            })

    return segments_list, detected_language

def format_time(seconds: float) -> str:
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"
