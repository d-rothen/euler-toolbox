"""Top-level Typer application: ``run``, ``schema``, and ``list`` commands."""

from __future__ import annotations

import json
import logging
from importlib.metadata import metadata
from pathlib import Path
from typing import Annotated, Optional

import click
import typer

from euler_toolbox.registry import ParamInfo, ToolInfo, get_tool, list_tools
from euler_toolbox.types import (
    TrackedPath,
    parse_origin_map,
    render_placeholder,
    resolve_origin,
)

# ---------------------------------------------------------------------------
# Typer app (handles ``list`` and ``schema``; ``run`` is added as a Click
# group at invocation time — see ``main()``).
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="euler-toolbox",
    help="Unified CLI for dataset-processing tools.",
    add_completion=False,
    rich_markup_mode=None,
)


def _get_readme() -> str:
    """Return README contents from package metadata, falling back to file."""
    try:
        body = metadata("euler-toolbox").get_payload()  # type: ignore[attr-defined]
        if isinstance(body, str) and body.strip():
            return body
    except Exception:
        pass
    readme_path = Path(__file__).resolve().parent.parent / "README.md"
    return readme_path.read_text(encoding="utf-8")


@app.callback(invoke_without_command=True)
def _main_callback(
    ctx: typer.Context,
    readme: Annotated[
        bool, typer.Option("--readme", help="Print the README and exit.")
    ] = False,
) -> None:
    if readme:
        typer.echo(_get_readme())
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# ``list`` command
# ---------------------------------------------------------------------------


@app.command("list")
def list_cmd() -> None:
    """List all registered tools."""
    for info in list_tools():
        typer.echo(f"{info.name:30s} {info.description}")


# ---------------------------------------------------------------------------
# ``schema`` command
# ---------------------------------------------------------------------------


@app.command("schema")
def schema_cmd(
    tool_name: Annotated[Optional[str], typer.Argument(help="Tool name.")] = None,
    all_tools: Annotated[
        bool, typer.Option("--all", help="Emit schemas for every tool.")
    ] = False,
    fmt: Annotated[
        str,
        typer.Option(
            "--format", help="Output format: json (full schema) or template (invocation string)."
        ),
    ] = "json",
    placeholder_style: Annotated[
        str,
        typer.Option(
            "--placeholder-style", help="Placeholder syntax: mustache, shell, or plain."
        ),
    ] = "mustache",
) -> None:
    """Generate a machine-readable schema for a tool (or all tools)."""
    if all_tools:
        schemas = [_build_schema(t, fmt, placeholder_style) for t in list_tools()]
        typer.echo(json.dumps(schemas, indent=2))
    elif tool_name:
        info = get_tool(tool_name)
        typer.echo(json.dumps(_build_schema(info, fmt, placeholder_style), indent=2))
    else:
        typer.echo("Provide a tool name or --all", err=True)
        raise typer.Exit(1)


def _build_schema(info: ToolInfo, fmt: str, style: str) -> dict:
    params = []
    for p in info.params:
        entry: dict = {
            "name": p.name,
            "cli_name": f"--{p.cli_name}",
            "type": _schema_type(p),
            "required": p.required,
        }
        if p.help:
            entry["help"] = p.help
        if not p.required and p.default is not None:
            entry["default"] = p.default
        if p.placeholder:
            entry["placeholder"] = render_placeholder(p.placeholder, style)
            if p.is_tracked_path or p.is_list_of_tracked_path:
                origin_ph = _derive_origin_placeholder(p.placeholder)
                entry["origin_placeholder"] = render_placeholder(origin_ph, style)
        if p.is_list_of_tracked_path:
            entry["note"] = (
                f"Repeat --{p.cli_name} for each path. "
                f"Optionally repeat --{p.cli_name}-origin in matching order."
            )
        params.append(entry)

    schema: dict = {
        "tool": info.name,
        "description": info.description,
        "params": params,
        "global_options": {
            "origin_map": {
                "cli_name": "--origin-map",
                "placeholder": render_placeholder("origin.path", style),
                "format": "<local_prefix>=<real_prefix>[,...]",
                "help": "Comma-separated prefix rewrite rules for path origins.",
            },
            "log_level": {
                "cli_name": "--log-level",
                "default": "INFO",
                "choices": ["DEBUG", "INFO", "WARNING", "ERROR"],
            },
        },
    }

    if info.sub_schemas:
        schema["sub_schemas"] = info.sub_schemas

    if fmt == "template":
        schema["template"] = _build_template(info, style)

    return schema


def _schema_type(p: ParamInfo) -> str:
    if p.is_list_of_tracked_path:
        return "list[tracked_path]"
    if p.is_tracked_path:
        return "tracked_path"
    if p.is_list:
        return "list"
    ann = p.type_annotation
    if isinstance(ann, type):
        return ann.__name__
    return str(ann)


def _derive_origin_placeholder(placeholder: str) -> str:
    """``dataset.path[]`` -> ``dataset.path[]:origin``.

    The origin is still a path — the ``:origin`` suffix distinguishes it
    from the working-copy placeholder.
    """
    if placeholder.endswith("[]"):
        return placeholder[:-2] + "[]:origin"
    return placeholder + ":origin"


def _build_template(info: ToolInfo, style: str) -> str:
    parts = [f"euler-toolbox run {info.name}"]
    for p in info.params:
        if p.placeholder:
            ph = render_placeholder(p.placeholder, style)
        else:
            ph = f"<{p.name}>"
        if p.is_list or p.is_list_of_tracked_path:
            parts.append(f"--{p.cli_name} '{ph}' [...]")
        else:
            parts.append(f"--{p.cli_name} '{ph}'")
    return " \\\n  ".join(parts)


# ---------------------------------------------------------------------------
# ``run`` — Click group with dynamically generated subcommands
# ---------------------------------------------------------------------------


def _build_run_group() -> click.Group:
    """Create a Click ``Group`` containing one command per registered tool."""
    group = click.Group("run", help="Run a registered tool.")
    for info in list_tools():
        group.add_command(_make_run_command(info), info.name)
    return group


def _make_run_command(info: ToolInfo) -> click.Command:
    """Build a ``click.Command`` that resolves TrackedPaths and delegates
    to the tool function."""
    click_params: list[click.Parameter] = []
    tracked_names: list[str] = []
    list_tracked_names: list[str] = []

    # --- Global options ---------------------------------------------------
    click_params.append(
        click.Option(
            ["--origin-map"],
            type=str,
            default=None,
            help="Comma-separated local=real prefix pairs for origin rewriting.",
        )
    )
    click_params.append(
        click.Option(
            ["--log-level"],
            type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
            default="INFO",
            help="Logging level.",
        )
    )

    # --- Tool-specific params ---------------------------------------------
    for p in info.params:
        if p.is_list_of_tracked_path:
            list_tracked_names.append(p.name)
            click_params.append(
                click.Option(
                    [f"--{p.cli_name}"],
                    type=click.Path(),
                    multiple=True,
                    required=p.required,
                    help=p.help,
                )
            )
            click_params.append(
                click.Option(
                    [f"--{p.cli_name}-origin"],
                    type=click.Path(),
                    multiple=True,
                    required=False,
                    default=(),
                    help=f"Override origin paths for {p.name} (positional match).",
                )
            )
        elif p.is_tracked_path:
            tracked_names.append(p.name)
            click_params.append(
                click.Option(
                    [f"--{p.cli_name}"],
                    type=click.Path(),
                    required=p.required,
                    default=p.default if not p.required else None,
                    help=p.help,
                )
            )
            click_params.append(
                click.Option(
                    [f"--{p.cli_name}-origin"],
                    type=click.Path(),
                    required=False,
                    default=None,
                    help=f"Override origin path for {p.name}.",
                )
            )
        elif p.is_list:
            click_params.append(
                click.Option(
                    [f"--{p.cli_name}"],
                    type=_click_scalar_type(p),
                    multiple=True,
                    required=p.required,
                    default=tuple(p.default) if p.default else (),
                    help=p.help,
                )
            )
        else:
            click_params.append(
                click.Option(
                    [f"--{p.cli_name}"],
                    type=_click_scalar_type(p),
                    required=p.required,
                    default=p.default if not p.required else None,
                    help=p.help,
                )
            )

    # --- Callback ---------------------------------------------------------
    def _make_callback(
        tool_info: ToolInfo,
        _tracked: list[str],
        _list_tracked: list[str],
    ):
        def callback(**kwargs: object) -> None:
            origin_map_raw = kwargs.pop("origin_map", None)
            log_level = kwargs.pop("log_level", "INFO")

            logging.basicConfig(
                level=getattr(logging, str(log_level).upper(), logging.INFO),
                format="%(levelname)s %(name)s: %(message)s",
                force=True,
            )

            origin_map = parse_origin_map(str(origin_map_raw)) if origin_map_raw else None

            resolved: dict[str, object] = {}
            for key, value in list(kwargs.items()):
                if key.endswith("_origin"):
                    continue

                if key in _tracked:
                    working = Path(str(value))
                    explicit_key = f"{key}_origin"
                    explicit_raw = kwargs.get(explicit_key)
                    explicit = Path(str(explicit_raw)) if explicit_raw else None
                    origin = resolve_origin(
                        working, explicit_origin=explicit, origin_map=origin_map
                    )
                    resolved[key] = TrackedPath(working=working, origin=origin)

                elif key in _list_tracked:
                    working_paths = [Path(str(v)) for v in value]  # type: ignore[union-attr]
                    origin_values = kwargs.get(f"{key}_origin", ())
                    tracked_list = []
                    for i, wp in enumerate(working_paths):
                        explicit = (
                            Path(str(origin_values[i]))  # type: ignore[index]
                            if i < len(origin_values)  # type: ignore[arg-type]
                            else None
                        )
                        orig = resolve_origin(
                            wp, explicit_origin=explicit, origin_map=origin_map
                        )
                        tracked_list.append(TrackedPath(working=wp, origin=orig))
                    resolved[key] = tracked_list

                else:
                    if isinstance(value, tuple):
                        resolved[key] = list(value)
                    else:
                        resolved[key] = value

            tool_info.func(**resolved)

        return callback

    return click.Command(
        name=info.name,
        params=click_params,
        callback=_make_callback(info, tracked_names, list_tracked_names),
        help=info.description,
    )


def _click_scalar_type(p: ParamInfo) -> click.types.ParamType:
    """Map a Python type annotation to a Click param type."""
    from typing import get_args

    ann = p.type_annotation
    if p.is_list:
        args = get_args(ann)
        ann = args[0] if args else str

    if ann is int:
        return click.INT
    if ann is float:
        return click.FLOAT
    if ann is bool:
        return click.BOOL
    return click.STRING


# ---------------------------------------------------------------------------
# Bootstrap: discover tools
# ---------------------------------------------------------------------------

from euler_toolbox.tools import discover_tools  # noqa: E402

discover_tools()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Build the final Click group (Typer commands + our ``run`` group)
    and invoke it."""
    group = typer.main.get_group(app)
    group.add_command(_build_run_group(), "run")
    group(standalone_mode=True)
