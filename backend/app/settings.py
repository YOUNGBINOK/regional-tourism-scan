from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Only backend reads provider keys; never expose them through React."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://rgap:rgap_local_only@localhost:5432/rgap"
    cors_origins: str = "http://localhost:5173"
    kto_tourism_datalab_api_key: str = Field(default="")
    kto_tourism_datalab_base_url: str = Field(default="https://apis.data.go.kr/B551011/DataLabService")
    kto_tourism_datalab_api_key_param: str = Field(default="serviceKey")
    # These two products publish their operation name separately in the
    # Data.go.kr application detail view. Store just that final path segment
    # here once confirmed; the key and host remain server-side.
    kto_attraction_concentration_endpoint: str = Field(default="")
    kto_tourism_resource_demand_endpoint: str = Field(default="")
    public_data_portal_api_key: str = Field(default="")
    public_data_portal_base_url: str = Field(default="https://apis.data.go.kr")
    public_data_portal_api_key_param: str = Field(default="serviceKey")
    local_finance365_api_key: str = Field(default="")
    local_finance365_base_url: str = Field(default="")
    local_finance365_api_key_param: str = Field(default="serviceKey")
    kosis_api_key: str = Field(default="")
    kosis_base_url: str = Field(default="https://kosis.kr/openapi")
    # Generated KOSIS query strings exclude apiKey; the backend injects it.
    kosis_population_query: str = Field(default="")
    kosis_area_query: str = Field(default="")
    # The approved MOIS service publishes its concrete endpoint in the
    # application detail page. Keep it configurable and server-only.
    mois_tourism_business_base_url: str = Field(default="")
    mois_tourism_business_query: str = Field(default="")

@lru_cache
def get_settings() -> Settings: return Settings()

def cors_origin_list() -> list[str]:
    return [origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()]
