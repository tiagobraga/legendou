import json
import re
import httpx
from typing import List, Dict, Any, Callable, Optional
from config import OLLAMA_HOST, DEFAULT_TRANSLATION_MODEL

SYSTEM_DISCOURSE_PROMPT_PT = """Você é um editor literário e de discursos de alto nível.
Sua tarefa é converter uma transcrição literal de áudio em um DISCURSO formal, elegante, fluido e agradável para leitura pública.

Diretrizes estritas:
1. Mantenha integralmente todos os fatos, nomes próprios, números, dados, conceitos e argumentos do orador. Não invente nada.
2. Elimine todos os maneirismos, cacoetes e vícios da fala oral (ex: "né", "tipo", "aí então", "como se diz", "tá?", repetições involuntárias).
3. Elimine autocorreções e falsos começos da fala oral (ex: "tentar deslegitimar, legitimizar a ciência" -> "tentar deslegitimar a ciência").
4. Corrija a sintaxe oral redundante para a norma culta escrita (ex: "Borco, que na década de 60, ele define" -> "Borco, que na década de 60, definiu").
5. Ajuste tempos verbais e concordâncias para uma narrativa escrita fluida e natural (ex: "a gente pensa, não, precisamos" -> "pensou-se: 'precisamos'").
6. Aplique pontuação expressiva e editorial (dois-pontos em listas explicativas, aspas em pensamentos e citações, travessões).
7. Estruture o texto em parágrafos temáticos naturais, prontos para publicação ou leitura como artigo/discurso.
Retorne APENAS o texto revisado do discurso, sem introduções, saudações ou comentários sobre as alterações feitas."""

SYSTEM_DISCOURSE_PROMPT_EN = """You are a high-level speech and literary editor.
Your task is to convert a raw literal audio transcript into a formal, elegant, fluid, and well-structured SPEECH in ENGLISH, ready for reading or publication.

Strict guidelines:
1. Preserve all facts, names, numbers, data, and arguments. Do not invent anything.
2. Remove verbal crutches, hesitation markers, repetitions, and spoken mannerisms (e.g., "you know", "like", "um", "uh", "so yeah").
3. Correct spoken grammar and syntax into clear, eloquent written English prose.
4. Structure the text into natural paragraphs ready for reading or publication.
Output ONLY the revised speech in English, with no preamble, greetings, or commentary."""

PREAMBLE_PATTERN = re.compile(r'^(?:Eis\s+a\s+vers[aã]o|Aqui\s+est[aá]|Segue\s+a\s+vers[aã]o|Vers[aã]o\s+revisada|Here\s+is\s+the|Revised\s+speech).*?:\s*\n*', re.IGNORECASE)

async def generate_discourse_text(
    segments: List[Dict[str, Any]],
    lang_code: str = "pt",
    ollama_host: str = OLLAMA_HOST,
    model_name: str = DEFAULT_TRANSLATION_MODEL,
    progress_callback: Optional[Callable[[str], None]] = None
) -> str:
    """
    Processa os segmentos transcritos e gera uma versão em 'Discurso' editorial,
    livre de vícios orais e repetições, estruturada em parágrafos de alta legibilidade.
    Suporta múltiplos idiomas (pt, en).
    """
    if not segments:
        return ""

    chunks = []
    current_chunk = []
    current_word_count = 0

    for s in segments:
        text = s.get("text", "").strip()
        if not text:
            continue
        current_chunk.append(s)
        current_word_count += len(text.split())

        if current_word_count >= 280 and text.endswith((".", "!", "?")):
            chunks.append(current_chunk)
            current_chunk = []
            current_word_count = 0

    if current_chunk:
        chunks.append(current_chunk)

    total_chunks = len(chunks)
    discourse_paragraphs = []

    is_english = (lang_code or "pt").lower().startswith("en")
    system_prompt = SYSTEM_DISCOURSE_PROMPT_EN if is_english else SYSTEM_DISCOURSE_PROMPT_PT
    prompt_prefix = "Convert the following transcript into clean, eloquent speech format in English:\n\n" if is_english else "Converta a seguinte transcrição em formato de discurso limpo e fluido:\n\n"

    async with httpx.AsyncClient(timeout=180.0) as client:
        for idx, chunk in enumerate(chunks, start=1):
            if progress_callback:
                progress_callback(f"Editando discurso ({idx}/{total_chunks})...")

            raw_block = " ".join(s.get("text", "").strip() for s in chunk)
            
            if len(raw_block.split()) < 6:
                discourse_paragraphs.append(raw_block)
                continue

            try:
                resp = await client.post(
                    f"{ollama_host}/api/generate",
                    json={
                        "model": model_name,
                        "system": system_prompt,
                        "prompt": f"{prompt_prefix}{raw_block}",
                        "stream": False
                    }
                )
                resp.raise_for_status()
                data = resp.json()
                refined = data.get("response", "").strip()
                if refined:
                    refined = PREAMBLE_PATTERN.sub('', refined).strip()
                    discourse_paragraphs.append(refined)
                else:
                    discourse_paragraphs.append(raw_block)
            except Exception as e:
                print(f"Aviso na geração de discurso do bloco {idx}: {e}")
                discourse_paragraphs.append(raw_block)

    return "\n\n".join(discourse_paragraphs)
