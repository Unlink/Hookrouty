from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .auth import current_principal
from .hooks import safe_hook
from .models import RouterError
from .users import require_admin, require_hook_access

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


@router.get("/login")
async def login_page(request: Request):
    return TEMPLATES.TemplateResponse(
        request, "app.html", {"page": "login", "user": None, "hook": None}
    )


@router.get("/app/{path:path}")
async def page(request: Request, path: str):
    try:
        actor = await current_principal(request)
    except RouterError:
        return RedirectResponse("/login", status_code=303)
    hook, rid = None, None
    parts = path.split("/")
    if path == "users":
        require_admin(actor)
        view = "users"
    elif path in {"hooks", "executions", "profile"}:
        view = path
    elif path == "hooks/new":
        view = "editor"
    elif len(parts) == 3 and parts[0] == "hooks" and parts[2] in {"edit", "test"}:
        row = await require_hook_access(request.app.state.db, actor, parts[1])
        hook = safe_hook(row, request.app.state.box)
        view = "editor" if parts[2] == "edit" else "test"
    elif len(parts) == 2 and parts[0] == "executions":
        await request.app.state.history.detail(actor, parts[1])
        view, rid = "detail", parts[1]
    else:
        raise RouterError(404, "PAGE_NOT_FOUND", "Page not found.")
    return TEMPLATES.TemplateResponse(
        request,
        "app.html",
        {
            "page": view,
            "user": actor,
            "hook": hook,
            "execution_id": rid,
            "app_url": request.app.state.settings.app_url,
        },
    )
