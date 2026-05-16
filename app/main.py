from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "llm_services"


settings = Settings()

app = FastAPI(title=settings.app_name)


@app.get("/health")
def health():
    return {"status": "ok"}
