from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PERSONAS_DIR = DATA_DIR / "personas"
PROFILES_DIR = REPO_ROOT / "profiles"
MODELS_DIR = REPO_ROOT / "models"
NLLB_DIR = MODELS_DIR / "nllb-600m-int8"  # converted locally from facebook/nllb-200-distilled-600M

PERSONA_COUNT = 3

# Query languages a persona may use.
LANGUAGES = ("hy", "ru", "en")

# Until traffic goes through Nym, every persona shares the user's IP, so personas
# must plausibly live in the same country. Weights roughly follow population.
CITIES = {
    "Yerevan": 0.60,
    "Gyumri": 0.10,
    "Vanadzor": 0.08,
    "Vagharshapat": 0.06,
    "Abovyan": 0.05,
    "Kapan": 0.04,
    "Hrazdan": 0.04,
    "Dilijan": 0.03,
}

# Broad areas sampled by code so that personas get varied interests; left alone, a
# small LLM derives every interest from the persona's job. The LLM picks the
# concrete topic inside each area, so every install still ends up different.
INTEREST_CATEGORIES = (
    "sports",
    "cooking and food",
    "music",
    "films and TV series",
    "books and reading",
    "travel and places",
    "home and garden",
    "cars and motorcycles",
    "technology and gadgets",
    "history",
    "nature and animals",
    "crafts and DIY",
    "fashion and style",
    "board, video or mobile games",
    "photography and art",
    "science and space",
)

PROJECT_CATEGORIES = (
    "buying a household appliance",
    "buying a phone or computer",
    "planning a trip abroad",
    "planning a weekend trip inside Armenia",
    "renovating or furnishing a room",
    "choosing a gift for someone",
    "learning a new practical skill",
    "organizing a family celebration",
    "buying or fixing something for a car",
    "choosing clothes or shoes for the season",
    "starting a new hobby",
)

# Trackers attribute decoy searches to the real user, so decoys must never touch
# topics that could harm them (insurance, credit, ad profiling, etc.).
EXCLUDED_TOPICS = (
    "health, illness, medication or mental health",
    "sexual content or dating",
    "political extremism or activism",
    "illegal activity or weapons",
    "debt, loans or financial distress",
    "religion or religious practice",
)
