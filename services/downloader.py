import os
import re
import asyncio
import subprocess
from pathlib import Path
from typing import Callable, Optional
import httpx

def is_nextcloud_link(url: str) -> bool:
    return bool(re.search(r'/s/[a-zA-Z0-9]+', url))

def get_nextcloud_download_url(url: str) -> str:
    url = url.strip()
    if url.endswith("/download"):
        return url
    if "/s/" in url:
        return url.rstrip("/") + "/download"
    return url

async def download_file_http(
    url: str,
    dest_path: Path,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> Path:
    target_url = get_nextcloud_download_url(url)
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=600.0) as client:
        async with client.stream("GET", target_url) as response:
            response.raise_for_status()
            total_bytes = int(response.headers.get("content-length", 0))
            
            # Extract filename from Content-Disposition if present
            disp = response.headers.get("content-disposition", "")
            filename = None
            if "filename*=" in disp:
                match = re.search(r"filename\*=UTF-8''([^;\n]+)", disp)
                if match:
                    filename = match.group(1)
            elif "filename=" in disp:
                match = re.search(r'filename="?([^";\n]+)"?', disp)
                if match:
                    filename = match.group(1)
            
            if filename:
                import urllib.parse
                filename = urllib.parse.unquote(filename)
                # Clean any quotes or leading/trailing whitespace
                filename = filename.strip('"\' ')
                dest_path = dest_path.parent / filename
            
            downloaded = 0
            with open(dest_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_bytes > 0:
                            percent = int((downloaded / total_bytes) * 100)
                            progress_callback(downloaded, total_bytes, f"Baixando: {percent}% ({downloaded // (1024*1024)}MB / {total_bytes // (1024*1024)}MB)")

    return dest_path

async def download_with_ytdlp(
    url: str,
    output_dir: Path,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> Path:
    out_template = str(output_dir / "%(title).50s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestaudio/best",
        "-o", out_template,
        url
    ]
    
    if progress_callback:
        progress_callback(0, 100, "Iniciando download com yt-dlp...")
        
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Falha no download via yt-dlp: {stderr.decode('utf-8', errors='ignore')}")
    
    # Locate the downloaded file
    files = list(output_dir.glob("*"))
    files = [f for f in files if f.is_file() and not f.name.endswith(".part")]
    if not files:
        raise FileNotFoundError("Nenhum arquivo encontrado após download com yt-dlp.")
    
    # Return newest file
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return files[0]

async def download_media(
    url: str,
    dest_dir: Path,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> Path:
    url = url.strip()
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    if is_nextcloud_link(url) or re.search(r'\.(mp4|mov|mkv|webm|avi|mp3|wav|m4a|ogg|aac|flac)(\?|$)', url, re.IGNORECASE):
        # Direct HTTP/Nextcloud stream
        temp_dest = dest_dir / "media_file"
        return await download_file_http(url, temp_dest, progress_callback)
    else:
        # Fallback to yt-dlp for video platforms
        return await download_with_ytdlp(url, dest_dir, progress_callback)
