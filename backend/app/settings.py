from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Only backend reads provider keys; never expose them through React."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://rgap:rgap_local_only@localhost:5432/rgap"
    kto_tourism_datalab_api_key: str = Field(default="")
    kto_tourism_datalab_base_url: str = Field(default="")
    kto_tourism_datalab_api_key_param: str = Field(default="serviceKey")
    public_data_portal_api_key: str = Field(default="")
    public_data_portal_base_url: str = Field(default="https://apis.data.go.kr")
    public_data_portal_api_key_param: str = Field(default="serviceKey")
    local_finance365_api_key: str = Field(default="")
    local_finance365_base_url: str = Field(default="")
    local_finance365_api_key_param: str = Field(default="serviceKey")

@lru_cache
def get_settings() -> Settings: return Settings()
