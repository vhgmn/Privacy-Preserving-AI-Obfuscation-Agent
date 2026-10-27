"""Local machine translation with NLLB-200 (CTranslate2, CPU).

The small LLM writes good English and Russian but cannot write Armenian, so
Armenian queries are written in English and translated here, fully offline.
"""
from functools import lru_cache

from agent import config

NLLB_CODES = {"en": "eng_Latn", "hy": "hye_Armn", "ru": "rus_Cyrl"}


class TranslatorUnavailable(RuntimeError):
    pass


def is_available() -> bool:
    return (config.NLLB_DIR / "model.bin").exists()


@lru_cache(maxsize=1)
def _load():
    if not is_available():
        raise TranslatorUnavailable(f"NLLB model not found in {config.NLLB_DIR} (see README: Armenian queries)")
    import ctranslate2  # imported lazily: optional dependency, slow to load
    import sentencepiece

    translator = ctranslate2.Translator(str(config.NLLB_DIR), device="cpu", compute_type="int8")
    tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(config.NLLB_DIR / "sentencepiece.bpe.model"))
    return translator, tokenizer


def translate(texts: list[str], target: str, source: str = "en") -> list[str]:
    if not texts:
        return []
    translator, tokenizer = _load()
    batch = [[NLLB_CODES[source]] + tokenizer.encode(text, out_type=str) + ["</s>"] for text in texts]
    results = translator.translate_batch(
        batch, target_prefix=[[NLLB_CODES[target]]] * len(texts), beam_size=4, max_decoding_length=64)
    # each hypothesis starts with the target language token
    return [tokenizer.decode(r.hypotheses[0][1:]) for r in results]
