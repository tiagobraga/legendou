import asyncio
import json
import subprocess
from pathlib import Path
from typing import Callable, Optional, Dict, Any

def get_media_metadata(input_path: Path) -> Dict[str, str]:
    """
    Extrai o máximo de metadados possível da fonte:
    1. Arquivo .info.json gerado pelo yt-dlp (se existir no diretório)
    2. Tags de container lidas via ffprobe
    """
    meta: Dict[str, str] = {}
    
    # 1. Verifica .info.json do yt-dlp
    info_files = list(input_path.parent.glob("*.info.json"))
    if info_files:
        try:
            with open(info_files[0], "r", encoding="utf-8") as f:
                info = json.load(f)
                title = info.get("track") or info.get("title")
                if title:
                    meta["title"] = str(title).strip()
                
                artist = info.get("artist") or info.get("creator") or info.get("uploader") or info.get("channel")
                if artist:
                    meta["artist"] = str(artist).strip()
                    
                album = info.get("album") or info.get("series")
                if album:
                    meta["album"] = str(album).strip()
                    
                date_val = info.get("release_date") or info.get("upload_date") or info.get("release_year")
                if date_val:
                    date_str = str(date_val)
                    meta["date"] = date_str[:4] if len(date_str) >= 4 else date_str
                    
                webpage = info.get("webpage_url")
                if webpage:
                    meta["comment"] = f"Fonte: {webpage} • Processado por Legendou (curio.ia.br)"
        except Exception as e:
            print(f"Aviso ao ler metadados yt-dlp info.json: {e}")

    # 2. Complementa com ffprobe nas tags do arquivo
    try:
        cmd = [
            "ffprobe",
            "-i", str(input_path),
            "-show_entries", "format_tags",
            "-v", "quiet",
            "-of", "json"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            tags = data.get("format", {}).get("tags", {})
            for k, v in tags.items():
                k_low = k.lower()
                val = str(v).strip()
                if not val:
                    continue
                if k_low in ["title", "tit2"] and "title" not in meta:
                    meta["title"] = val
                elif k_low in ["artist", "author", "composer", "tpe1"] and "artist" not in meta:
                    meta["artist"] = val
                elif k_low in ["album", "album_artist", "talb"] and "album" not in meta:
                    meta["album"] = val
                elif k_low in ["date", "year", "tyer", "creation_time"] and "date" not in meta:
                    meta["date"] = val[:4] if len(val) >= 4 else val
                elif k_low in ["comment", "description"] and "comment" not in meta:
                    meta["comment"] = val
    except Exception as e:
        print(f"Aviso ao ler tags ffprobe: {e}")

    if "title" not in meta:
        meta["title"] = input_path.stem
    if "comment" not in meta:
        meta["comment"] = "Processado por Legendou (curio.ia.br)"
        
    return meta

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
    extra_metadata: Optional[Dict[str, str]] = None,
    progress_callback: Optional[Callable[[str], None]] = None
) -> Path:
    if progress_callback:
        progress_callback("Convertendo áudio para MP3 (192kbps) com metadados ID3 completos...")

    meta = get_media_metadata(input_media_path)
    if extra_metadata:
        meta.update(extra_metadata)
        
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_media_path),
        "-vn",
        "-map_metadata", "0",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
    ]

    # Injeta todas as tags de metadados ID3v2
    for key, val in meta.items():
        if val:
            cmd.extend(["-metadata", f"{key}={val}"])
            
    cmd.extend([
        "-id3v2_version", "3",
        "-write_id3v1", "1",
        str(output_mp3_path)
    ])
    
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
