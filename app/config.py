import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).resolve()
DB = DATA / 'library.sqlite3'
LLM_URL = os.getenv('LLM_URL', 'http://127.0.0.1:8080').rstrip('/')
LLM_MODEL = os.getenv('LLM_MODEL', 'qwen2.5-3b-instruct-q4_k_m')
WHISPER_MODEL = os.getenv('WHISPER_MODEL', 'small')
WHISPER_DEVICE = os.getenv('WHISPER_DEVICE', 'cpu')
MAX_DISCOVERY = 500

# Must not exceed the context configured on the inference server.
LLM_CONTEXT_TOKENS = int(os.getenv('LLM_CONTEXT_TOKENS', '8192'))
