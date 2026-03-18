import logging

from fastapi import FastAPI

from deploy_app.config import DB_ROOT, DEPLOY_ROOT
from deploy_app.db import create_db_and_seed
from deploy_app.routers.admin import router as admin_router
from deploy_app.routers.auth import router as auth_router
from deploy_app.routers.databases import router as databases_router
from deploy_app.routers.deployments import router as deployments_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Deploy API", version="2.0.0")


@app.on_event("startup")
def on_startup() -> None:
    DEPLOY_ROOT.mkdir(parents=True, exist_ok=True)
    DB_ROOT.mkdir(parents=True, exist_ok=True)
    create_db_and_seed(logger)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(admin_router, include_in_schema=False)
app.include_router(auth_router)
app.include_router(deployments_router)
app.include_router(databases_router)
