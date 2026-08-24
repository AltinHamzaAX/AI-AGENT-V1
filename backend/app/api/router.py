from fastapi import APIRouter

from app.api.routes import assets, campaigns, conversations, health, posts, projects

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(posts.router, prefix="/posts", tags=["posts"])
api_router.include_router(campaigns.router, prefix="/campaigns", tags=["campaigns"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(assets.router, prefix="/assets", tags=["assets"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
