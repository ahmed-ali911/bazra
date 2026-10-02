from fastapi import APIRouter

from app.api.v1 import health
from app.modules.attention.router import router as attention_router
from app.modules.auth.router import router as auth_router
from app.modules.calendar.router import router as calendar_router
from app.modules.chat.router import router as chat_router
from app.modules.home.router import router as home_router
from app.modules.inbox.router import router as inbox_router
from app.modules.life_areas.router import router as life_areas_router
from app.modules.tasks.router import router as tasks_router

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth_router, tags=["auth"])
api_router.include_router(life_areas_router, tags=["life-areas"])
api_router.include_router(tasks_router, tags=["tasks"])
api_router.include_router(calendar_router, tags=["calendar"])
api_router.include_router(inbox_router, tags=["inbox"])
api_router.include_router(home_router, tags=["home"])
api_router.include_router(chat_router, tags=["chat"])
api_router.include_router(attention_router, tags=["attention"])
