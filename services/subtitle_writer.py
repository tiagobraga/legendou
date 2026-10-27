import json
import zipfile
from pathlib import Path
from typing import Dict, List, Any

def format_timestamp(seconds: float, srt: bool = True) -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    separator = "," if srt else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"

def format_short_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"

def write_subtitle_files(
    output_dir: Path,
    base_name: str,
    lang_code: str,
    segments: List[Dict[str, Any]]
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{lang_code}" if lang_code else ""
    
    srt_path = output_dir / f"{base_name}{suffix}.srt"
    vtt_path = output_dir / f"{base_name}{suffix}.vtt"
    timed_txt_path = output_dir / f"{base_name}{suffix}_com_tempo.txt"
    plain_txt_path = output_dir / f"{base_name}{suffix}_texto_corrido.txt"
    json_path = output_dir / f"{base_name}{suffix}.json"

    # 1. SRT
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, s in enumerate(segments, start=1):
            start = format_timestamp(s["start"], srt=True)
            end = format_timestamp(s["end"], srt=True)
            text = s["text"].strip()
            f.write(f"{idx}\n{start} --> {end}\n{text}\n\n")

    # 2. VTT
    with open(vtt_path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for idx, s in enumerate(segments, start=1):
            start = format_timestamp(s["start"], srt=False)
            end = format_timestamp(s["end"], srt=False)
            text = s["text"].strip()
            f.write(f"{idx}\n{start} --> {end}\n{text}\n\n")

    # 3. Timed Transcript
    with open(timed_txt_path, "w", encoding="utf-8") as f:
        for s in segments:
            time_str = format_short_time(s["start"])
            text = s["text"].strip()
            f.write(f"[{time_str}] {text}\n")

    # 4. Plain Text
    with open(plain_txt_path, "w", encoding="utf-8") as f:
        current_paragraph = []
        for s in segments:
            text = s["text"].strip()
            current_paragraph.append(text)
            if text.endswith((".", "!", "?")) and len(current_paragraph) >= 3:
                f.write(" ".join(current_paragraph) + "\n\n")
                current_paragraph = []
        if current_paragraph:
            f.write(" ".join(current_paragraph) + "\n")

    # 5. JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)

    return {
        "srt": srt_path,
        "vtt": vtt_path,
        "timed_txt": timed_txt_path,
        "plain_txt": plain_txt_path,
        "json": json_path
    }

def create_zip_bundle(output_dir: Path, zip_name: str = "legendas_completas.zip") -> Path:
    zip_path = output_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file in output_dir.glob("*"):
            if file.is_file() and file.name != zip_name and file.suffix in [".srt", ".vtt", ".txt", ".json"]:
                zipf.write(file, arcname=file.name)
    return zip_path
