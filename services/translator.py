import json
import httpx
from typing import Callable, Dict, List, Optional, Any
from config import OLLAMA_HOST, DEFAULT_TRANSLATION_MODEL, SUPPORTED_LANGUAGES

async def translate_segments(
    segments: List[Dict[str, Any]],
    source_lang: str,
    target_lang: str,
    ollama_host: str = OLLAMA_HOST,
    model_name: str = DEFAULT_TRANSLATION_MODEL,
    progress_callback: Optional[Callable[[dict], None]] = None
) -> List[Dict[str, Any]]:
    if source_lang.lower() == target_lang.lower():
        return segments

    target_lang_name = SUPPORTED_LANGUAGES.get(target_lang, target_lang)
    source_lang_name = SUPPORTED_LANGUAGES.get(source_lang, source_lang)
    
    total = len(segments)
    chunk_size = 15
    translated_segments: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=180.0) as client:
        for i in range(0, total, chunk_size):
            chunk = segments[i:i + chunk_size]
            current_pct = int(((i + len(chunk)) / total) * 100)
            
            if progress_callback:
                progress_callback({
                    "status": "translating",
                    "message": f"Traduzindo para {target_lang_name}: {current_pct}% ({min(i + chunk_size, total)}/{total} segmentos)...",
                    "target_language": target_lang,
                    "progress_percent": current_pct
                })

            prompt_payload = {
                "translations": [
                    {"id": s["id"], "text": s["text"]}
                    for s in chunk
                ]
            }

            system_prompt = (
                f"You are a professional subtitle translator translating from {source_lang_name} to {target_lang_name}.\n"
                f"Translate each text naturally and accurately for video subtitles.\n"
                f"Preserve terminology, names, institutions and conversational tone.\n"
                f"You must return a JSON object with a single key 'translations' containing an array of objects with keys 'id' and 'text'.\n"
                f"Example: {{\"translations\": [{{\"id\": 1, \"text\": \"translated text here\"}}]}}\n"
                f"Return only valid JSON without markdown wrapping or explanations."
            )

            user_prompt = f"Translate the following subtitle segments to {target_lang_name}:\n{json.dumps(prompt_payload, ensure_ascii=False)}"

            try:
                resp = await client.post(
                    f"{ollama_host}/api/generate",
                    json={
                        "model": model_name,
                        "system": system_prompt,
                        "prompt": user_prompt,
                        "stream": False,
                        "format": "json"
                    }
                )
                resp.raise_for_status()
                response_data = resp.json()
                raw_text = response_data.get("response", "").strip()
                
                parsed = json.loads(raw_text)
                items_list = []
                if isinstance(parsed, dict):
                    if "translations" in parsed and isinstance(parsed["translations"], list):
                        items_list = parsed["translations"]
                    elif "items" in parsed and isinstance(parsed["items"], list):
                        items_list = parsed["items"]
                    elif "id" in parsed and "text" in parsed:
                        items_list = [parsed]
                    else:
                        items_list = [{"id": k, "text": v} for k, v in parsed.items() if isinstance(v, str)]
                elif isinstance(parsed, list):
                    items_list = parsed

                trans_map = {}
                for item in items_list:
                    if isinstance(item, dict) and "id" in item and "text" in item:
                        trans_map[str(item["id"])] = item["text"]
                        try:
                            trans_map[int(item["id"])] = item["text"]
                        except (ValueError, TypeError):
                            pass

                for s in chunk:
                    translated_segments.append({
                        "id": s["id"],
                        "start": s["start"],
                        "end": s["end"],
                        "text": trans_map.get(s["id"], trans_map.get(str(s["id"]), s["text"]))
                    })
            except Exception as e:
                print(f"Aviso: Erro ao traduzir lote ({i}-{i+len(chunk)}): {e}. Mantendo texto original.")
                for s in chunk:
                    translated_segments.append({
                        "id": s["id"],
                        "start": s["start"],
                        "end": s["end"],
                        "text": s["text"]
                    })

    return translated_segments
