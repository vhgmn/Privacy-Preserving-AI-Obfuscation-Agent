import httpx

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:3b"


def generate(prompt: str, model: str = DEFAULT_MODEL, json_mode: bool = False,
             options: dict | None = None) -> str:
    payload = {"model": model, "prompt": prompt, "stream": False}
    if json_mode:
        payload["format"] = "json"
    if options:
        payload["options"] = options
    resp = httpx.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=600)
    resp.raise_for_status()
    return resp.json()["response"]
