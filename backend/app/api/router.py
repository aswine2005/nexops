from fastapi import APIRouter

from app.api.routes import agents, analysis, applications, auth, dashboard, deployments, flags, incidents, observability, platform, policies, webhooks

api_router = APIRouter(prefix="/api")
for module in (auth, dashboard, applications, flags, analysis, deployments, incidents, observability, agents, policies, platform, webhooks):
    api_router.include_router(module.router)
