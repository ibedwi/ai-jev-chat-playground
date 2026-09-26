"""Settings loaded from environment variables (and backend/.env if present)."""

import os

from dotenv import load_dotenv

load_dotenv()

TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# "real" calls TypeSafe; "mock" uses a keyword-based stand-in so you can work on the UI
# without a key. Defaults to mock when no key is set.
JEV_MODE = os.getenv("JEV_MODE", "real" if TYPESAFE_API_KEY else "mock")

# Blank Anthropic key => fake LLM that echoes, so only Jev costs money while tuning.
USE_FAKE_LLM = not ANTHROPIC_API_KEY
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5")

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.6"))
SLOT_CONFIDENCE = float(os.getenv("SLOT_CONFIDENCE", "0.5"))

# Timezone used to resolve "today" / "thursday" into dates.
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Jakarta")

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
