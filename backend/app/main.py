"""Application entry point for the pet-retail forecasting API."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.health import router as health_router
from app.api.routes.data import router as data_router
from app.api.routes.database import router as database_router
from app.api.routes.forecasting import router as forecasting_router
from app.api.routes.ml_forecasting import router as ml_forecasting_router
from app.api.routes.inventory import router as inventory_router
from app.api.routes.mlops import router as mlops_router
from app.core.config import APP_NAME, APP_VERSION

app = FastAPI(
    title=APP_NAME,
    description="Forecasting and inventory decision support for a fictional US pet retailer.",
    version=APP_VERSION,
)

# Only local frontend origins are permitted for development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(health_router, prefix="/api/v1", tags=["System"])
app.include_router(data_router, prefix="/api/v1", tags=["Retail Data"])
app.include_router(database_router, prefix="/api/v1", tags=["Database"])
app.include_router(forecasting_router, prefix="/api/v1", tags=["Forecasting Baselines"])
app.include_router(ml_forecasting_router, prefix="/api/v1", tags=["Trained ML Forecasting"])
app.include_router(inventory_router, prefix="/api/v1", tags=["Inventory Decision Support"])
app.include_router(mlops_router, prefix="/api/v1", tags=["MLOps Model Provenance"])


@app.get("/", tags=["System"])
def root() -> dict[str, str]:
    return {"service": APP_NAME, "docs": "/docs", "health": "/api/v1/health"}
