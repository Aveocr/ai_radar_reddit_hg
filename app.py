from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from admin.router import router as admin_router
from middleware import AdminAuthMiddleware, ProtectedStaticFiles
from routes import register_routes
from vector.router import router as vector_router

app = FastAPI(
    title="AI Radar",
    description="Automated AI innovation monitoring system",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(AdminAuthMiddleware)

app.mount("/app/static", StaticFiles(directory="static/app/static"), name="app_static")
app.mount(
    "/admin/static",
    ProtectedStaticFiles(directory="static/admin/static"),
    name="admin_static",
)

app.include_router(admin_router)
app.include_router(vector_router)
register_routes(app)
