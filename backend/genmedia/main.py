from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from .api import router
from .workspace import router as workspace_router
from .config import settings
from .search import SearchError


@asynccontextmanager
async def lifespan(app):
    settings().prepare()
    yield


app = FastAPI(title="GenMedia Vault", version="0.2.0", lifespan=lifespan)
app.include_router(router)
app.include_router(workspace_router)


@app.exception_handler(SearchError)
async def search_error(request, exc):
    return JSONResponse({"detail": str(exc), "position": exc.position}, status_code=422)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return JSONResponse({"detail": "记录已存在或被并发修改，请刷新重试"}, status_code=409)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        if urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


static = settings().frontend_dir
if (static / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="static")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "接口不存在"}, status_code=404)
    if (static / "index.html").is_file():
        return FileResponse(static / "index.html")
    return JSONResponse({"message": "API 已启动。请构建 frontend 或运行 npm run dev。"})
