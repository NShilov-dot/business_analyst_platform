from fastapi import APIRouter

from app.api.v1 import auth, health, signup
from app.modules.ai_structuring.interface.router import router as intake_chat_router
from app.modules.analytics.interface.router import router as analytics_router
from app.modules.audit.interface.router import router as audit_router
from app.modules.departments.interface.router import router as departments_router
from app.modules.directory.interface.router import router as directory_router
from app.modules.intake_templates.interface.router import router as templates_router
from app.modules.tasks.interface.router import router as tasks_router
from app.modules.tenants.interface.router import router as admin_router
from app.modules.tickets.interface.router import router as tickets_router

router = APIRouter(prefix="/v1")
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(signup.router)
router.include_router(tasks_router)
router.include_router(admin_router)
router.include_router(departments_router)
router.include_router(templates_router)
router.include_router(audit_router)
router.include_router(tickets_router)
router.include_router(intake_chat_router)
router.include_router(analytics_router)
router.include_router(directory_router)
