from __future__ import annotations

import inspect
import json
import logging
import math
from pathlib import Path
from typing import Any, get_args, get_origin
from urllib.parse import unquote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from trackio.exceptions import TrackioAPIError

logger = logging.getLogger("trackio.asgi_app")

_PACKAGE_JSON_PATH = Path(__file__).parent / "package.json"
_TRACKIO_PACKAGE_VERSION = json.loads(_PACKAGE_JSON_PATH.read_text())["version"]


def _normalize_allowed_file_roots(
    allowed_file_roots: list[str | Path] | None,
) -> tuple[Path, ...]:
    roots = []
    for root in allowed_file_roots or []:
        roots.append(Path(root).resolve())
    return tuple(roots)


def _is_allowed_file_path(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    resolved_path = path.resolve(strict=False)
    for root in allowed_roots:
        try:
            resolved_path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _json_safe(data: Any) -> Any:
    if data is None or isinstance(data, (str, bool, int)):
        return data
    if isinstance(data, float):
        return data if math.isfinite(data) else None
    if isinstance(data, dict):
        return {k: _json_safe(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_json_safe(v) for v in data]
    if hasattr(data, "item"):
        try:
            return _json_safe(data.item())
        except Exception:
            pass
    return str(data)


def _invoke_handler(
    fn: Any,
    request: Request,
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
) -> Any:
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    positional_args: list[Any] = []
    keyword_args: dict[str, Any] = {}
    args = list(args or [])
    kwargs = dict(kwargs or {})
    data_index = 0

    for param in params:
        if param.name == "request":
            keyword_args["request"] = request
        elif param.kind == inspect.Parameter.VAR_POSITIONAL:
            positional_args.extend(args[data_index:])
            data_index = len(args)
        elif param.kind == inspect.Parameter.VAR_KEYWORD:
            keyword_args.update(kwargs)
            kwargs.clear()
        elif param.name in kwargs:
            keyword_args[param.name] = kwargs.pop(param.name)
        elif param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ) and data_index < len(args):
            positional_args.append(args[data_index])
            data_index += 1
        elif param.default is inspect.Signature.empty and param.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise TrackioAPIError(f"Missing required parameter: {param.name}")

    return fn(*positional_args, **keyword_args)


async def version_handler(request: Request) -> Response:
    mcp_enabled = bool(getattr(request.app.state, "mcp_enabled", False))
    return JSONResponse(
        {
            "version": _TRACKIO_PACKAGE_VERSION,
            "mcp_enabled": mcp_enabled,
            "mcp_path": "/mcp" if mcp_enabled else None,
        }
    )


def _json_schema_and_python_type(annotation: Any) -> tuple[dict[str, Any], str]:
    if annotation is inspect.Parameter.empty:
        return {"type": "object"}, "Any"
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is not None and args:
        non_none = tuple(a for a in args if a is not type(None))
        if len(non_none) == 1 and len(args) > 1:
            return _json_schema_and_python_type(non_none[0])
        if origin is list:
            inner, py_inner = _json_schema_and_python_type(
                args[0] if args else inspect.Parameter.empty
            )
            return {"type": "array", "items": inner}, f"list[{py_inner}]"
        if origin is dict:
            return {"type": "object"}, "dict"
    if annotation in (str, bytes):
        return {"type": "string"}, "str"
    if annotation is int:
        return {"type": "integer"}, "int"
    if annotation is float:
        return {"type": "number"}, "float"
    if annotation is bool:
        return {"type": "boolean"}, "bool"
    name = getattr(annotation, "__name__", None)
    if name:
        return {"type": "object"}, name
    return {"type": "object"}, "Any"


def build_api_info(api_registry: dict[str, Any]) -> dict[str, Any]:
    named_endpoints: dict[str, Any] = {}
    for name in sorted(api_registry.keys()):
        fn = api_registry[name]
        if not callable(fn):
            continue
        sig = inspect.signature(fn)
        parameters: list[dict[str, Any]] = []
        for pname, param in sig.parameters.items():
            if pname == "request":
                continue
            jtype, pytype = _json_schema_and_python_type(param.annotation)
            has_default = param.default is not inspect.Parameter.empty
            parameters.append(
                {
                    "label": pname,
                    "parameter_name": pname,
                    "parameter_has_default": has_default,
                    "parameter_default": None if not has_default else param.default,
                    "type": jtype,
                    "python_type": {"type": pytype, "description": ""},
                    "component": "Api",
                    "example_input": None,
                }
            )
        ret_ann = sig.return_annotation
        if ret_ann is inspect.Signature.empty:
            ret_ann = Any
        rjtype, rpytype = _json_schema_and_python_type(ret_ann)
        returns = [
            {
                "label": "result",
                "type": rjtype,
                "python_type": {"type": rpytype, "description": ""},
                "component": "Api",
            }
        ]
        named_endpoints[f"/{name}"] = {
            "parameters": parameters,
            "returns": returns,
            "api_visibility": "public",
        }
    return {"named_endpoints": named_endpoints, "unnamed_endpoints": {}}


async def run_api_request(request: Request, api_name: str) -> Response:
    api_registry = request.app.state.api_registry
    fn = api_registry.get(api_name)
    if fn is None:
        return JSONResponse({"error": f"Unknown API: {api_name}"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}

    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    if isinstance(body, dict):
        if "args" in body or "kwargs" in body:
            args = body.get("args") or []
            kwargs = body.get("kwargs") or {}
        elif "data" in body and isinstance(body["data"], list):
            args = body["data"]
        else:
            kwargs = body
    elif isinstance(body, list):
        args = body
    elif body is not None:
        args = [body]

    if not isinstance(args, list):
        args = [args]
    if not isinstance(kwargs, dict):
        kwargs = {}

    try:
        result = _invoke_handler(fn, request, args=args, kwargs=kwargs)
        return JSONResponse({"data": _json_safe(result)})
    except TrackioAPIError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_handler(request: Request) -> Response:
    return await run_api_request(request, request.path_params["api_name"])


async def api_info_handler(request: Request) -> Response:
    return JSONResponse(
        build_api_info(request.app.state.api_registry),
        headers={"Cache-Control": "no-store"},
    )


_DISALLOWED_FILE_SUFFIXES = frozenset(
    {".db", ".db-journal", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"}
)


async def file_handler(request: Request) -> Response:
    fs_path = request.query_params.get("path")
    if fs_path is None:
        return Response("Missing path", status_code=400)
    fp = Path(unquote(fs_path)).resolve(strict=False)
    if fp.suffix.lower() in _DISALLOWED_FILE_SUFFIXES:
        return Response("Not found", status_code=404)
    allowed_roots = getattr(request.app.state, "allowed_file_roots", ())
    if fp.is_file() and _is_allowed_file_path(fp, allowed_roots):
        return FileResponse(str(fp))
    return Response("Not found", status_code=404)


async def artifact_blob_handler(request: Request) -> Response:
    from trackio import cas  # noqa: PLC0415

    digest = request.path_params.get("digest")
    try:
        digest = cas.validate_digest(digest)
    except ValueError:
        return Response("Not found", status_code=404)
    project_dir = request.app.state.project_dir
    blob = cas.blob_path(project_dir, digest)
    if not blob.is_file():
        return Response("Not found", status_code=404)
    return FileResponse(str(blob))


def create_trackio_starlette_app(
    api_registry: dict[str, Any],
    project_dir: str | Path,
    extra_routes: list[Any] | None = None,
    mcp_lifespan: Any = None,
    mcp_enabled: bool = False,
    allowed_file_roots: list[str | Path] | None = None,
) -> Starlette:
    routes: list[Any] = [
        Route("/version", endpoint=version_handler, methods=["GET"]),
        Route("/api_info", endpoint=api_info_handler, methods=["GET"]),
        Route("/api/{api_name:str}", endpoint=api_handler, methods=["POST"]),
        Route("/file", endpoint=file_handler, methods=["GET"]),
        Route(
            "/artifact_blob/{digest:str}",
            endpoint=artifact_blob_handler,
            methods=["GET"],
        ),
    ]
    routes.extend(extra_routes or [])
    app = Starlette(routes=routes, lifespan=mcp_lifespan)
    app.state.api_registry = api_registry
    app.state.mcp_enabled = mcp_enabled
    app.state.project_dir = Path(project_dir).expanduser().resolve()
    app.state.allowed_file_roots = _normalize_allowed_file_roots(allowed_file_roots)
    return app
