import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
OPENAI_MODEL_TRANSLATOR = os.getenv("OPENAI_MODEL_TRANSLATOR", "deepseek-v4-flash")
OPENAI_MODEL_SUMMARIZER = os.getenv("OPENAI_MODEL_SUMMARIZER", "deepseek-v4-pro")
