"""Tool registry: ``@tool`` decorator, ``ToolParam``, and introspection."""

from __future__ import annotations

import inspect
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, get_args, get_origin

from euler_toolbox.types import TrackedPath

# ---------------------------------------------------------------------------
# Metadata marker returned by ToolParam()
# ---------------------------------------------------------------------------

_SENTINEL = object()


@dataclass
class _ToolParamMarker:
    """Carried as the default value of a tool-function parameter so that
    the ``@tool`` decorator can extract metadata later."""

    default: Any = _SENTINEL  # _SENTINEL means "required"
    help: str = ""
    placeholder: str | None = None

    @property
    def required(self) -> bool:
        return self.default is _SENTINEL


def ToolParam(
    default: Any = _SENTINEL,
    *,
    help: str = "",
    placeholder: str | None = None,
) -> Any:
    """Declare a tool parameter with metadata.

    When *default* is omitted (or the sentinel ``...`` is passed) the
    parameter is required.
    """
    if default is ...:
        default = _SENTINEL
    return _ToolParamMarker(default=default, help=help, placeholder=placeholder)


# ---------------------------------------------------------------------------
# Introspection results
# ---------------------------------------------------------------------------


@dataclass
class ParamInfo:
    name: str  # Python name, e.g. "rgb_path"
    cli_name: str  # kebab-case, e.g. "rgb-path"
    type_annotation: Any  # raw annotation
    is_tracked_path: bool  # True when type is TrackedPath
    is_list: bool  # True when type is list[X]
    is_list_of_tracked_path: bool
    required: bool
    default: Any
    help: str
    placeholder: str | None


@dataclass
class ToolInfo:
    name: str
    description: str
    func: Callable[..., Any]
    params: list[ParamInfo] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, ToolInfo] = {}


def get_tool(name: str) -> ToolInfo:
    if name not in _TOOL_REGISTRY:
        raise KeyError(f"Unknown tool: {name!r}")
    return _TOOL_REGISTRY[name]


def list_tools() -> list[ToolInfo]:
    return list(_TOOL_REGISTRY.values())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_kebab(name: str) -> str:
    return name.replace("_", "-")


def _is_tracked_path(annotation: Any) -> bool:
    return annotation is TrackedPath


def _is_list_type(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin is list


def _is_list_of_tracked_path(annotation: Any) -> bool:
    if not _is_list_type(annotation):
        return False
    args = get_args(annotation)
    return bool(args) and args[0] is TrackedPath


def _inner_type(annotation: Any) -> Any:
    """For ``list[X]`` return ``X``, otherwise return *annotation*."""
    if _is_list_type(annotation):
        args = get_args(annotation)
        return args[0] if args else typing.Any
    return annotation


def _introspect_params(func: Callable[..., Any]) -> list[ParamInfo]:
    sig = inspect.signature(func)
    hints = typing.get_type_hints(func)
    params: list[ParamInfo] = []

    for pname, p in sig.parameters.items():
        ann = hints.get(pname, str)
        marker: _ToolParamMarker | None = None

        if isinstance(p.default, _ToolParamMarker):
            marker = p.default
        elif p.default is inspect.Parameter.empty:
            # required, no marker
            marker = _ToolParamMarker()

        if marker is None:
            # Plain default (e.g. ``x: int = 5`` without ToolParam)
            marker = _ToolParamMarker(default=p.default)

        params.append(
            ParamInfo(
                name=pname,
                cli_name=_to_kebab(pname),
                type_annotation=ann,
                is_tracked_path=_is_tracked_path(ann),
                is_list=_is_list_type(ann),
                is_list_of_tracked_path=_is_list_of_tracked_path(ann),
                required=marker.required,
                default=marker.default if not marker.required else None,
                help=marker.help,
                placeholder=marker.placeholder,
            )
        )

    return params


# ---------------------------------------------------------------------------
# @tool decorator
# ---------------------------------------------------------------------------


def tool(
    name: str,
    description: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function as a CLI tool."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        desc = description or (func.__doc__ or "").strip().split("\n")[0]
        info = ToolInfo(
            name=name,
            description=desc,
            func=func,
            params=_introspect_params(func),
        )
        _TOOL_REGISTRY[name] = info
        return func

    return decorator
