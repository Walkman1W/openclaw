from fastapi import APIRouter

from app.api.v1 import auth, agents, claw

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(agents.router, prefix="/agents", tags=["agents"])
router.include_router(claw.router, prefix="/claw", tags=["claw"])
