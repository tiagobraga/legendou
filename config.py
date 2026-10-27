import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://curio-ollama:11434")
DEFAULT_WHISPER_MODEL = os.getenv("DEFAULT_WHISPER_MODEL", "large-v3")
DEFAULT_TRANSLATION_MODEL = os.getenv("DEFAULT_TRANSLATION_MODEL", "qwen2.5:14b")
DEFAULT_DEVICE = os.getenv("DEFAULT_DEVICE", "cuda")
DEFAULT_COMPUTE_TYPE = os.getenv("DEFAULT_COMPUTE_TYPE", "float16")

SUPPORTED_LANGUAGES = {
    "pt": "Português",
    "en": "Inglês",
    "es": "Espanhol",
    "fr": "Francês",
    "de": "Alemão",
    "it": "Italiano",
    "ru": "Russo",
    "zh": "Chinês",
    "ja": "Japonês"
}

WHISPER_MODELS = [
    {"id": "large-v3", "name": "Large v3 (Máxima Precisão - Recomendado GPU)"},
    {"id": "medium", "name": "Medium (Rápido e Equilibrado)"},
    {"id": "small", "name": "Small (Ultrarrápido)"},
    {"id": "base", "name": "Base (Leve)"}
]
