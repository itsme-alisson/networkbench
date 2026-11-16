"""Offline tools with shared local workspace files and no authentication."""
import json
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import storage

ORIGINS = set(os.getenv("NETWORKBENCH_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(","))
STATIC_DIR = Path(__file__).parent / "static"
Tool = Literal["jinja", "textfsm", "ttp", "xpath", "json", "json_yaml"]
initialize = storage.initialize


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(title="NetworkBench local API", version="0.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])


@app.middleware("http")
async def local_guard(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if (origin and origin not in ORIGINS) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-origin requests are disabled"}, status_code=403)
    # Bound bodies even for chunked requests; don't trust Content-Length.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            return JSONResponse({"detail": "Request exceeds 1 MiB"}, status_code=413)
    request._body = bytes(body)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Avoid echoing potentially sensitive input in validation errors.
    return JSONResponse({"detail": [{"loc": item["loc"], "msg": item["msg"],
                                    "type": item["type"]} for item in error.errors()]}, status_code=422)


class Workspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Tool
    name: str = Field(min_length=1, max_length=120)
    template: str = Field(default="", max_length=200_000)
    input: str = Field(default="", max_length=200_000)
    options: dict = Field(default_factory=dict)
    result: str = Field(default="", max_length=200_000)


class WorkspaceUpdate(Workspace):
    revision: int = Field(ge=1)


@app.get("/health")
def health():
    if not (storage.DATA_DIR / 'workspaces').is_dir():
        raise HTTPException(503, "Workspace storage unavailable")
    return {"status": "ok"}


@app.get("/api/tools")
def tools():
    return [{"id": tool, "execution_available": True} for tool in ("jinja", "textfsm", "ttp", "xpath", "json", "json_yaml")]


@app.get("/api/workspaces")
def list_workspaces(tool: Tool | None = None, limit: int = 50, offset: int = 0):
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(422, "Invalid pagination")
    return storage.list_all(tool)[offset:offset + limit]


@app.delete("/api/workspaces", status_code=204)
def clean_workspaces():
    storage.delete_all()


@app.post("/api/workspaces", status_code=201)
def create_workspace(workspace: Workspace):
    return storage.create(workspace.model_dump())


@app.get("/api/workspaces/{workspace_id}")
def get_workspace(workspace_id: str):
    return storage.get(workspace_id)


@app.put("/api/workspaces/{workspace_id}")
def save_workspace(workspace_id: str, workspace: WorkspaceUpdate):
    return storage.save(workspace_id, workspace.model_dump())


@app.delete("/api/workspaces/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: str):
    storage.delete(workspace_id)


@app.get("/api/config")
def config():
    return {"authentication": False, "storage": "local-files"}


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


ENGINE_SLOTS = threading.BoundedSemaphore(2)


class Execution(BaseModel):
    conversion_direction: Literal["json_to_yaml", "yaml_to_json"] = "json_to_yaml"
    model_config = ConfigDict(extra="forbid")
    tool: Tool
    template: str = Field(max_length=100_000)
    input: str = Field(max_length=100_000)


@app.post("/api/execute")
def execute_tool(payload: Execution):
    if not ENGINE_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Tools are busy. Try again in a moment.")
    try:
        with tempfile.TemporaryDirectory() as directory:
            try:
                process = subprocess.run(
                    [sys.executable, "-I", str(Path(__file__).parent.resolve() / "engine.py")],
                    input=payload.model_dump_json(), text=True, capture_output=True,
                    timeout=3, cwd=directory,
                    env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "HOME": directory},
                )
            except subprocess.TimeoutExpired:
                raise HTTPException(422, "Execution exceeded the 3-second limit.")
        if process.returncode:
            raise HTTPException(422, "Execution stopped: resource limit or invalid template.")
        try:
            result = json.loads(process.stdout)
        except ValueError:
            raise HTTPException(422, "The parser could not produce a result. Check the template.")
        if "error" in result:
            raise HTTPException(422, result["error"])
        return result
    finally:
        ENGINE_SLOTS.release()


@app.get('/api/jinja/modules')
def jinja_modules():
    """Report actual runtime versions and explicitly exposed Jinja filters."""
    from importlib.metadata import distributions, version
    from jinja2.defaults import DEFAULT_FILTERS
    from .jinja_filters import NETWORK_FILTERS, VLAN_FILTERS, CORE_FILTERS, LIST_FILTERS
    from .custom_filters import FILTERS

    collection_root = Path(__file__).resolve().parent.parent / '.collections' / 'ansible_collections'
    collections = []
    for manifest in sorted(collection_root.glob('*/*/MANIFEST.json')):
        info = json.loads(manifest.read_text())['collection_info']
        collections.append({'name': info['namespace'] + '.' + info['name'], 'version': info['version']})
    packages = sorted(
        ({'name': package.metadata['Name'], 'version': package.version} for package in distributions() if package.metadata['Name']),
        key=lambda item: item['name'].lower(),
    )
    return {
        'filter_groups': [
            {'name': 'Jinja built-ins', 'version': version('Jinja2'), 'filters': sorted(DEFAULT_FILTERS)},
            {'name': 'ansible.utils', 'filters': list(NETWORK_FILTERS)},
            {'name': 'ansible.netcommon', 'filters': list(VLAN_FILTERS)},
            {'name': 'ansible.builtin', 'version': version('ansible-core'), 'filters': list(CORE_FILTERS + LIST_FILTERS)},
            {'name': 'Custom filters', 'filters': sorted(FILTERS)},
        ],
        'collections': collections,
        'packages': packages,
    }
