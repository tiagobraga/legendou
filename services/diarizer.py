import json
import httpx
from typing import List, Dict, Any, Optional
from config import OLLAMA_HOST, DEFAULT_TRANSLATION_MODEL

async def detect_opening_transition(
    segments: List[Dict[str, Any]],
    client: httpx.AsyncClient,
    ollama_host: str,
    model_name: str
) -> Dict[str, Any]:
    """
    Analisa as falas iniciais (até 15) para detectar se há um apresentador ou anfitrião
    passando a palavra para o palestrante/convidado principal.
    """
    if len(segments) <= 3:
        return {"has_intro": False}

    first_15 = [{"id": s["id"], "text": s["text"]} for s in segments[:15]]
    prompt = (
        "Analise as falas iniciais desta gravação e verifique se há uma introdução/abertura feita por um Apresentador ou Anfitrião passando a palavra para o Palestrante/Convidado principal.\n"
        f"Segmentos:\n{json.dumps(first_15, ensure_ascii=False, indent=2)}\n\n"
        "Regras:\n"
        "1. Se houver um apresentador abrindo o evento ou passando a palavra (ex: 'agradeço a presença', 'passo a palavra', e o convidado responde 'obrigado pelo convite', 'bom dia a todos'), retorne has_intro: true e o id exato da primeira frase dita pelo convidado/palestrante principal.\n"
        "2. Se o palestrante já começa expondo o conteúdo sozinho desde o início, retorne has_intro: false.\n"
        "Retorne OBRIGATORIAMENTE um JSON: {\"has_intro\": true, \"main_speaker_start_id\": 4}"
    )

    try:
        resp = await client.post(
            f"{ollama_host}/api/generate",
            json={
                "model": model_name,
                "prompt": prompt,
                "format": "json",
                "stream": False
            },
            timeout=45.0
        )
        resp.raise_for_status()
        data = resp.json()
        parsed = json.loads(data.get("response", "{}"))
        if isinstance(parsed, dict) and "has_intro" in parsed:
            return parsed
    except Exception as e:
        print(f"Aviso na detecção de abertura de locutor: {e}")

    return {"has_intro": False}

async def detect_mid_interjections(
    segments: List[Dict[str, Any]],
    main_speaker_start_id: int,
    client: httpx.AsyncClient,
    ollama_host: str,
    model_name: str
) -> List[int]:
    """
    Localiza pequenas interrupções (respostas a perguntas de tempo, intervenções curtas da mesa, encerramento)
    ao longo da fala do orador principal.
    """
    if len(segments) <= 15:
        return []

    candidate_windows = []
    total = len(segments)
    host_ids = set()

    for i in range(1, total - 1):
        s = segments[i]
        text = s.get("text", "").strip()
        words = text.split()
        prev_text = segments[i - 1].get("text", "").strip()
        
        # Heurística direta para respostas curtas de tempo
        clean_t = text.lower().rstrip(".,!?")
        if ("?" in prev_text or "minutos" in prev_text.lower()) and clean_t in [
            "dois", "três", "quatro", "cinco", "dez", "dois minutos", "três minutos", "cinco minutos", "tempo", "acabou"
        ]:
            host_ids.add(s["id"])
            continue

        # Candidatos para verificação com LLM
        is_reply_to_question = "?" in prev_text and len(words) <= 5
        has_time_cue = any(cue in text.lower() for cue in ["minuto", "tempo", "encerrar", "concluir", "passo a palavra"]) and len(words) <= 8
        is_closing_cue = i >= total - 3 and any(cue in text.lower() for cue in ["obrigado", "agradeço", "palmas", "pergunta"]) and len(words) <= 6

        if is_reply_to_question or has_time_cue or is_closing_cue:
            w_start = max(0, i - 2)
            w_end = min(total, i + 3)
            window = [{"id": seg["id"], "text": seg["text"]} for seg in segments[w_start:w_end]]
            candidate_windows.append((s["id"], window))

    for target_id, window in candidate_windows[:15]:
        if target_id in host_ids:
            continue
        prompt = (
            "Nesta gravação, o palestrante principal fala a maior parte do tempo ('Pessoa 2'), "
            "enquanto a mesa/moderador/anfitrião é 'Pessoa 1'.\n"
            f"Analise o seguinte trecho de conversa:\n{json.dumps(window, ensure_ascii=False, indent=2)}\n\n"
            f"Verifique se a fala de id {target_id} foi dita pela mesa/moderador/anfitrião (Pessoa 1) ou pelo palestrante (Pessoa 2).\n"
            "Atenção: Avisos ou respostas sobre tempo restante (ex: 'Dois', 'Dois minutos', 'Tempo') ou intervenções externas são da Mesa (Pessoa 1).\n"
            "Retorne um JSON no formato: {\"is_host_table\": true} ou {\"is_host_table\": false}"
        )
        try:
            resp = await client.post(
                f"{ollama_host}/api/generate",
                json={
                    "model": model_name,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False
                },
                timeout=25.0
            )
            resp.raise_for_status()
            parsed = json.loads(resp.json().get("response", "{}"))
            if parsed.get("is_host_table"):
                host_ids.add(target_id)
        except Exception:
            pass

    return list(host_ids)

async def identify_speakers(
    segments: List[Dict[str, Any]],
    speaker_mode: str = "auto",
    ollama_host: str = OLLAMA_HOST,
    model_name: str = DEFAULT_TRANSLATION_MODEL
) -> List[Dict[str, Any]]:
    """
    Analisa os segmentos de áudio e atribui identificadores de locutor ([Pessoa 1], [Pessoa 2], etc.)
    respeitando o modo configurado:
    - 'single': Força todos os segmentos como 'Pessoa 1' (sem custo de inferência).
    - 'auto': Analisa abertura e interrupções. Se houver apresentador/mesa introduzindo,
              separa em Pessoa 1 (Apresentador) e Pessoa 2 (Palestrante), mantendo a estabilidade do orador principal.
    - 'dialogue': Força análise de múltiplos locutores com regras estritas contra falsos positivos.
    """
    if not segments:
        return segments

    # Modo 1: Forçado orador único
    if speaker_mode == "single" or len(segments) <= 2:
        for s in segments:
            s["speaker"] = "Pessoa 1"
        return segments

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Modo 2: Automático com detecção de Abertura / Apresentador e Interrupções
        if speaker_mode == "auto":
            open_info = await detect_opening_transition(segments, client, ollama_host, model_name)
            
            if open_info.get("has_intro"):
                start_id = open_info.get("main_speaker_start_id", 4)
                for s in segments:
                    if s["id"] < start_id:
                        s["speaker"] = "Pessoa 1"
                    else:
                        s["speaker"] = "Pessoa 2"

                inter_ids = await detect_mid_interjections(segments, start_id, client, ollama_host, model_name)
                for i_id in inter_ids:
                    for s in segments:
                        if s["id"] == i_id:
                            s["speaker"] = "Pessoa 1"

                return segments
            else:
                for s in segments:
                    s["speaker"] = "Pessoa 1"
                return segments

        # Modo 3: Diálogo confirmado (Processamento em janelas com memória contínua)
        chunk_size = 20
        total = len(segments)
        speaker_map = {}
        last_known_speaker = "Pessoa 1"

        for i in range(0, total, chunk_size):
            chunk = segments[i:i + chunk_size]
            payload = [{"id": s["id"], "text": s["text"]} for s in chunk]

            system_prompt = (
                "Você é um especialista em transcrições, atas de reuniões e análise de diálogo.\n"
                "Sua tarefa é analisar os turnos de fala e atribuir a cada fala ('id') um locutor: 'Pessoa 1', 'Pessoa 2', 'Pessoa 3', etc.\n"
                f"Contexto anterior: o locutor que estava falando logo antes deste bloco era '{last_known_speaker}'.\n"
                "Regras fundamentais contra erros:\n"
                "1. Quando a mesma pessoa estiver falando continuamente ou completando um pensamento, MANTENHA o mesmo locutor.\n"
                "2. Perguntas retóricas, pausas ou exclamações de um mesmo orador NUNCA devem virar outro locutor.\n"
                "3. Alterne o locutor SOMENTE quando houver uma intervenção clara, resposta explícita ou voz diferente interagindo.\n"
                "4. Retorne OBRIGATORIAMENTE um JSON no formato: {\"speakers\": [{\"id\": 1, \"speaker\": \"Pessoa 1\"}]}\n"
                "Não inclua texto ou markdown fora do JSON."
            )

            user_prompt = f"Analise os segmentos e identifique as trocas reais de locutor:\n{json.dumps(payload, ensure_ascii=False)}"

            try:
                resp = await client.post(
                    f"{ollama_host}/api/generate",
                    json={
                        "model": model_name,
                        "system": system_prompt,
                        "prompt": user_prompt,
                        "format": "json",
                        "stream": False
                    }
                )
                resp.raise_for_status()
                data = resp.json()
                parsed = json.loads(data.get("response", "{}"))
                
                spk_list = parsed.get("speakers", [])
                for item in spk_list:
                    speaker_map[item["id"]] = item.get("speaker", last_known_speaker)
                    last_known_speaker = item.get("speaker", last_known_speaker)
            except Exception as e:
                print(f"Aviso no reconhecimento de locutores do bloco {i}: {e}")
                for s in chunk:
                    speaker_map[s["id"]] = last_known_speaker

        for s in segments:
            s["speaker"] = speaker_map.get(s["id"], "Pessoa 1")

        return segments
