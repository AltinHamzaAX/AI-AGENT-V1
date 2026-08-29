from fastapi import APIRouter

from app.api.routes import (
    assets,
    campaigns,
    conversations,
    health,
    post_benchmarks,
    posts,
    projects,
)
from app.api.routes.section_conversations import (
    campaigns_conversations_router,
    posts_conversations_router,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(posts_conversations_router, prefix="/posts", tags=["posts"])
api_router.include_router(
    campaigns_conversations_router, prefix="/campaigns", tags=["campaigns"]
)
api_router.include_router(posts.router, prefix="/posts", tags=["posts"])
api_router.include_router(
    post_benchmarks.router,
    prefix="/post-benchmarks",
    tags=["post-benchmarks"],
)
api_router.include_router(campaigns.router, prefix="/campaigns", tags=["campaigns"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(assets.router, prefix="/assets", tags=["assets"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
