import os
from dotenv import load_dotenv

load_dotenv()

PARQUET_PATH = os.getenv("PARQUET_PATH", "/data/master_variables.h3.parquet")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
ENV = os.getenv("ENV", "development")

# Keycloak
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "vento")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
