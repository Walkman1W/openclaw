from fastapi import APIRouter

from app.api.v1 import admin, agents, auth, claw, notifications, publishers, tasks

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(agents.router, prefix="/agents", tags=["agents"])
router.include_router(claw.router, prefix="/claw", tags=["claw"])
router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
router.include_router(publishers.router, prefix="/publishers", tags=["publishers"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
