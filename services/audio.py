import asyncio
import subprocess
from pathlib import Path
from typing import Callable, Optional

async def extract_and_convert_audio(
    input_media_path: Path,
    output_wav_path: Path,
    progress_callback: Optional[Callable[[str], None]] = None
) -> Path:
    if progress_callback:
        progress_callback("Extraindo e normalizando áudio com FFmpeg (16kHz mono PCM)...")
        
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_media_path),
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_wav_path)
    ]
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Erro no FFmpeg: {stderr.decode('utf-8', errors='ignore')}")
        
    if not output_wav_path.exists() or output_wav_path.stat().st_size == 0:
        raise RuntimeError("Arquivo de áudio extraído está vazio.")
        
    return output_wav_path

async def convert_to_mp3(
    input_media_path: Path,
    output_mp3_path: Path,
    progress_callback: Optional[Callable[[str], None]] = None
) -> Path:
    if progress_callback:
        progress_callback("Convertendo áudio para MP3 (192kbps)...")
        
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_media_path),
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(output_mp3_path)
    ]
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Erro no FFmpeg ao gerar MP3: {stderr.decode('utf-8', errors='ignore')}")
        
    if not output_mp3_path.exists() or output_mp3_path.stat().st_size == 0:
        raise RuntimeError("Arquivo MP3 gerado está vazio.")
        
    return output_mp3_path
