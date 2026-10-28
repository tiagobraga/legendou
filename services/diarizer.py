import json
import httpx
from typing import List, Dict, Any
from config import OLLAMA_HOST, DEFAULT_TRANSLATION_MODEL

async def identify_speakers(
    segments: List[Dict[str, Any]],
    ollama_host: str = OLLAMA_HOST,
    model_name: str = DEFAULT_TRANSLATION_MODEL
) -> List[Dict[str, Any]]:
    """
    Analisa os segmentos de áudio e atribui identificadores de locutor ([Pessoa 1], [Pessoa 2], etc.)
    baseando-se na dinâmica de diálogo, perguntas/respostas e turnos de fala.
    """
    if not segments:
        return segments

    if len(segments) <= 2:
        for s in segments:
            s["speaker"] = "Pessoa 1"
        return segments

    chunk_size = 25
    total = len(segments)
    speaker_map = {}

    async with httpx.AsyncClient(timeout=120.0) as client:
        for i in range(0, total, chunk_size):
            chunk = segments[i:i + chunk_size]
            payload = [{"id": s["id"], "text": s["text"]} for s in chunk]

            system_prompt = (
                "Você é um especialista em transcrições, atas de reuniões e análise de diálogo.\n"
                "Sua tarefa é analisar os turnos de fala e atribuir a cada fala ('id') um locutor: 'Pessoa 1', 'Pessoa 2', 'Pessoa 3', etc.\n"
                "Regras fundamentais:\n"
                "1. Quando a mesma pessoa estiver falando continuamente ou completando uma ideia, mantenha o mesmo locutor.\n"
                "2. Quando houver uma pergunta seguida de resposta, mudança de assunto ou intervenção clara, alterne para outro locutor ('Pessoa 2', 'Pessoa 1', etc.).\n"
                "3. Se for uma palestra, aula ou fala individual de uma única pessoa, mantenha todos como 'Pessoa 1'.\n"
                "4. Retorne OBRIGATORIAMENTE um JSON no formato: {\"speakers\": [{\"id\": 1, \"speaker\": \"Pessoa 1\"}]}\n"
                "Não inclua markdown ou explicações fora do JSON."
            )

            user_prompt = f"Analise os segmentos e identifique as trocas de locutor:\n{json.dumps(payload, ensure_ascii=False)}"

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
                data = resp.json()
                raw_text = data.get("response", "").strip()
                parsed = json.loads(raw_text)

                items = parsed.get("speakers", []) if isinstance(parsed, dict) else parsed
                for item in items:
                    if isinstance(item, dict) and "id" in item and "speaker" in item:
                        speaker_name = str(item["speaker"]).strip()
                        if not speaker_name.lower().startswith("pessoa"):
                            speaker_name = f"Pessoa {speaker_name}"
                        speaker_map[item["id"]] = speaker_name
                        speaker_map[str(item["id"])] = speaker_name
            except Exception as e:
                print(f"Aviso na identificação de locutores do lote {i}: {e}. Mantendo padrão.")

    last_speaker = "Pessoa 1"
    for s in segments:
        spk = speaker_map.get(s["id"], speaker_map.get(str(s["id"])))
        if spk:
            last_speaker = spk
        s["speaker"] = spk or last_speaker

    return segments
