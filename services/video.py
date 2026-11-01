import asyncio
import subprocess
from pathlib import Path
from typing import Callable, Optional

def has_video_stream(media_path: Path) -> bool:
    """
    Verifica se o arquivo contém ao menos uma faixa de vídeo válida via ffprobe.
    Retorna False para arquivos puramente de áudio (ex: mp3, wav, flac, aac, m4a sem vídeo).
    """
    if not media_path.exists():
        return False

    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(media_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return "video" in res.stdout.strip().lower()
    except Exception as e:
        print(f"Aviso ao verificar stream de vídeo via ffprobe em {media_path}: {e}")
        return False

async def burn_subtitles_to_video(
    input_video_path: Path,
    subtitle_path: Path,
    output_video_path: Path,
    progress_callback: Optional[Callable[[str], None]] = None
) -> Path:
    """
    Grava/queima as legendas (hardsub / burn-in) diretamente nos quadros do vídeo usando FFmpeg.
    Tenta aceleração por hardware (h264_nvenc) e faz fallback automático para CPU (libx264).
    """
    if progress_callback:
        progress_callback("Iniciando renderização de vídeo com legendas embutidas...")

    work_dir = input_video_path.parent
    sub_rel_name = subtitle_path.name
    input_rel_name = input_video_path.name
    output_rel_name = output_video_path.name

    # Estilização limpa e legível para qualquer fundo (tamanho ideal, contorno preto nítido)
    sub_filter = (
        f"subtitles='{sub_rel_name}':force_style="
        "'FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,Outline=1.5,Shadow=0,MarginV=26'"
    )

    # 1. Tentativa com aceleração GPU NVIDIA (h264_nvenc)
    cmd_gpu = [
        "ffmpeg",
        "-y",
        "-i", input_rel_name,
        "-vf", sub_filter,
        "-c:a", "copy",
        "-c:v", "h264_nvenc",
        "-preset", "p4",
        "-cq", "22",
        output_rel_name
    ]

    # 2. Fallback com CPU (libx264)
    cmd_cpu = [
        "ffmpeg",
        "-y",
        "-i", input_rel_name,
        "-vf", sub_filter,
        "-c:a", "copy",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        output_rel_name
    ]

    use_cmd = cmd_gpu
    proc = await asyncio.create_subprocess_exec(
        *use_cmd,
        cwd=str(work_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()

    # Se NVENC falhar (ex: biblioteca NVENC não compilada no container ou ocupada), tenta CPU
    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="ignore")
        print(f"NVENC não disponível ou falhou ({err_msg[:100]}...). Tentando fallback para CPU libx264...")
        
        proc_cpu = await asyncio.create_subprocess_exec(
            *cmd_cpu,
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout_c, stderr_c = await proc_cpu.communicate()
        if proc_cpu.returncode != 0:
            raise RuntimeError(f"Erro no FFmpeg ao gerar vídeo legendado: {stderr_c.decode('utf-8', errors='ignore')}")

    if not output_video_path.exists() or output_video_path.stat().st_size == 0:
        raise RuntimeError("Arquivo de vídeo legendado gerado está vazio ou não foi criado.")

    if progress_callback:
        progress_callback("Vídeo legendado (.MP4) gerado com sucesso!")

    return output_video_path
