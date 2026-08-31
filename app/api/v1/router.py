from fastapi import APIRouter

from app.api.v1.endpoints import (
    activities,
    ai,
    auth,
    chat_sessions,
    comments,
    health,
    notifications,
    organizations,
    projects,
    tasks,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(comments.router)
api_router.include_router(activities.router)
api_router.include_router(notifications.router)
api_router.include_router(ai.router)
api_router.include_router(chat_sessions.router)
