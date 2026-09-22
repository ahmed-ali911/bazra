from fastapi import APIRouter

from app.api.v1 import health
from app.modules.auth.router import router as auth_router
from app.modules.life_areas.router import router as life_areas_router
from app.modules.tasks.router import router as tasks_router

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth_router, tags=["auth"])
api_router.include_router(life_areas_router, tags=["life-areas"])
api_router.include_router(tasks_router, tags=["tasks"])
