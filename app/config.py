"""
Application settings loaded from environment variables / .env file.
The OpenRouter API key and Firebase credentials stay server-side only.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    OPENROUTER_API_KEY: str = ""
    LLM_MODEL: str = "openai/gpt-3.5-turbo"
    JEV_MODEL: str = "typesafe/jev-1.13"
    READ_SECONDS: int = 75
    FIREBASE_CREDENTIALS_PATH: str = ""
    FIREBASE_PROJECT_ID: str = "prayas-2ce75"
    FIREBASE_API_KEY: str = "AIzaSyAlxP4PzqJluvo_YxbxGF7Ksoe-VQ9FaIA"
    FIREBASE_AUTH_DOMAIN: str = "prayas-2ce75.firebaseapp.com"
    FIREBASE_STORAGE_BUCKET: str = "prayas-2ce75.firebasestorage.app"
    FIREBASE_MESSAGING_SENDER_ID: str = "551207538440"
    FIREBASE_APP_ID: str = "1:551207538440:web:fea873a3a08f450f9da6bd"
    FIREBASE_MEASUREMENT_ID: str = "G-H08329R5H7"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
