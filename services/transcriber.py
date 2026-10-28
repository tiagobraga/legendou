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

    # Prepare transcribe arguments - word_timestamps ativado para sincronização milimétrica
    # vad_filter desativado para garantir que introduções musicais não cortem o áudio
    kwargs = {
        "beam_size": 5,
        "word_timestamps": True,
        "vad_filter": False,
        "condition_on_previous_text": False,
    }
    if language and language.lower() not in ["auto", ""]:
        kwargs["language"] = language.lower()
        
    segments_generator, info = model.transcribe(audio, **kwargs)
    detected_language = info.language
    
    segments_list = []
    for idx, s in enumerate(segments_generator, start=1):
        text = s.text.strip()
        # Refina o início e fim com base na primeira e última palavra pronunciada
        start_time = s.words[0].start if (s.words and len(s.words) > 0) else s.start
        end_time = s.words[-1].end if (s.words and len(s.words) > 0) else s.end

        seg_data = {
            "id": idx,
            "start": round(start_time, 2),
            "end": round(end_time, 2),
            "text": text
        }
        segments_list.append(seg_data)
        
        if progress_callback and duration > 0:
            current_pct = min(100, int((end_time / duration) * 100))
            progress_callback({
                "status": "transcribing",
                "message": f"Transcrevendo: {current_pct}% [{format_time(start_time)} -> {format_time(end_time)}]",
                "progress_percent": current_pct,
                "current_segment": seg_data
            })

    return segments_list, detected_language

def format_time(seconds: float) -> str:
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"
