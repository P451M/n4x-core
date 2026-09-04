from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from n4x.cli.oauth import LoginError, login as cli_login, logout as cli_logout
from n4x.cli.packages import PackageTransferError, fetch_archive, stage_archive
from n4x.cli.session import default_cli_store, normalize_origin
from n4x.graph.neo4j import Neo4jConfig, Neo4jConfigError, Neo4jGraph
from n4x.host.http import MCP_HTTP_PATH, RuntimeMode, resolve_worker_public_origin
from n4x.host.paths import default_runtime_root
from n4x.host.worker import WorkerBootError

app = typer.Typer(help="N4X graph-native application runtime")
host_app = typer.Typer(help="Host import, enable, dump, and restore (localhost / CLI).")
package_app = typer.Typer(help="Stage and fetch Package archives over HTTP.")
app.add_typer(host_app, name="host")
app.add_typer(package_app, name="package")


def _neo4j_config_from_env() -> Neo4jConfig:
    try:
        return Neo4jConfig.from_env()
    except Neo4jConfigError as error:
        raise typer.BadParameter(
            str(error), param_hint="N4X_NEO4J_*"
        ) from error


def _neo4j_from_env() -> Neo4jGraph:
    return Neo4jGraph(_neo4j_config_from_env())


@app.command()
def health() -> None:
    """Print local runtime health."""
    typer.echo({"status": "ok", "runtime": "n4x"})


@app.command("login")
def login(
    origin: Annotated[str, typer.Option(help="Instance origin, for example https://box.example")],
) -> None:
    """Store an operator session for Package HTTP and other instance APIs."""
    try:
        session = cli_login(origin, store=default_cli_store())
    except (LoginError, ValueError) as error:
        typer.echo(str(error))
        raise typer.Exit(code=1) from error
    typer.echo({"logged_in": True, "origin": session.origin})


@app.command("logout")
def logout(
    origin: Annotated[str, typer.Option(help="Instance origin previously passed to login")],
) -> None:
    """Delete the stored operator session for an instance origin."""
    try:
        cli_logout(origin, store=default_cli_store())
    except ValueError as error:
        typer.echo(str(error))
        raise typer.Exit(code=1) from error
    typer.echo({"logged_out": True, "origin": normalize_origin(origin)})


@package_app.command("stage")
def package_stage(
    origin: Annotated[str, typer.Option(help="Instance origin")],
    file: Annotated[Path, typer.Option(help="Local .n4xp archive")],
    overwrite: Annotated[bool, typer.Option(help="Replace an existing archive")] = False,
) -> None:
    """Stream a local Package archive to the instance. Does not import."""
    try:
        staged = stage_archive(
            origin,
            file,
            store=default_cli_store(),
            overwrite=overwrite,
        )
    except (PackageTransferError, ValueError) as error:
        typer.echo(str(error))
        raise typer.Exit(code=1) from error
    _echo_json(staged)


@package_app.command("fetch")
def package_fetch(
    origin: Annotated[str, typer.Option(help="Instance origin")],
    archive_name: Annotated[str, typer.Option(help="Archive name ending in .n4xp")],
    output: Annotated[Path, typer.Option(help="Destination file")],
) -> None:
    """Download a staged Package archive."""
    try:
        path = fetch_archive(
            origin,
            archive_name,
            output,
            store=default_cli_store(),
        )
    except (PackageTransferError, ValueError) as error:
        typer.echo(str(error))
        raise typer.Exit(code=1) from error
    typer.echo({"archive_name": archive_name, "path": str(path)})


@app.command("neo4j-health")
def neo4j_health() -> None:
    """Check Neo4j connectivity using N4X_NEO4J_* environment variables."""
    graph = _neo4j_from_env()
    try:
        graph.verify_connectivity()
        typer.echo({"neo4j": "ok"})
    finally:
        graph.close()


@app.command("bootstrap-neo4j")
def bootstrap_neo4j(
    dry_run: Annotated[
        bool, typer.Option(help="Print schema statements without executing.")
    ] = False,
) -> None:
    """Bootstrap Neo4j constraints and indexes."""
    graph = _neo4j_from_env()
    try:
        statements = graph.schema_statements()
        if dry_run:
            for statement in statements:
                typer.echo(statement)
            return
        graph.bootstrap_schema()
        typer.echo({"neo4j_schema": "ok", "statements": len(statements)})
    finally:
        graph.close()


@app.command("mcp-stdio")
def mcp_stdio(
    neo4j: Annotated[
        bool,
        typer.Option(help="Use the required Neo4j graph storage."),
    ] = True,
) -> None:
    """Run the host lock and one System worker on MCP stdio (no HTTP)."""
    if neo4j:
        _neo4j_config_from_env()
    runtime = _host_from_env()
    raise SystemExit(runtime.run_stdio())


def _run_host_http(
    *,
    bind_host: str,
    port: int,
    mode: RuntimeMode,
    inspector_port: int,
    inspector_proxy_port: int,
    require_neo4j: bool,
) -> None:
    from n4x.host.paths import default_install_root
    from n4x.host.supervisor import Host

    if require_neo4j:
        _neo4j_config_from_env()
    base_url = resolve_worker_public_origin(
        bind_host, port, os.environ.get("N4X_PUBLIC_ORIGIN")
    )
    mcp_url = f"{base_url}{MCP_HTTP_PATH}"
    inspector_enabled = mode == "development"
    runtime = Host(
        default_install_root(),
        worker_env={
            "N4X_HTTP_MODE": mode,
            "N4X_PUBLIC_ORIGIN": base_url,
            "N4X_RUNTIME_ROOT": str(default_runtime_root()),
            "N4X_HOST_CONTROL_ORIGIN": base_url,
            "N4X_INSPECTOR": "1" if inspector_enabled else "0",
            "N4X_INSPECTOR_PORT": str(inspector_port),
            "N4X_INSPECTOR_PROXY_PORT": str(inspector_proxy_port),
        },
    )
    try:
        runtime.boot_active()
    except WorkerBootError as error:
        typer.echo(
            {
                "runtime": "n4x-host",
                "error": "system_worker_unavailable",
                "detail": str(error),
            }
        )
    enabled = runtime.system_graph.enabled_revision()
    typer.echo(
        {
            "runtime": "n4x-host",
            "mode": mode,
            "http": base_url,
            "mcp": mcp_url,
            "control": f"{base_url}/n4x-host/control",
            "revision_id": None if enabled is None else enabled.id,
            "content_root": None if enabled is None else enabled.content_root,
            **(
                {"inspector": f"http://127.0.0.1:{inspector_port}"}
                if inspector_enabled
                else {}
            ),
        }
    )
    try:
        uvicorn.run(runtime.app, host=bind_host, port=port, log_level="info")
    finally:
        runtime.release()


@app.command("serve")
def serve(
    neo4j: Annotated[
        bool,
        typer.Option(help="Use the required Neo4j graph storage."),
    ] = True,
    host: Annotated[
        str, typer.Option(help="Host interface for the local N4X HTTP runtime.")
    ] = "127.0.0.1",
    port: Annotated[
        int, typer.Option(help="Port for the local N4X HTTP runtime.")
    ] = 7744,
    mode: Annotated[
        RuntimeMode,
        typer.Option(
            help=(
                "development starts MCP Inspector; production starts MCP HTTP "
                "without Inspector."
            )
        ),
    ] = "development",
    inspector_port: Annotated[
        int,
        typer.Option(help="MCP Inspector UI port used in development mode."),
    ] = 6274,
    inspector_proxy_port: Annotated[
        int,
        typer.Option(help="MCP Inspector proxy port used in development mode."),
    ] = 6277,
) -> None:
    """Run Surfaces, MCP HTTP, and in development mode MCP Inspector."""
    if not neo4j:
        raise typer.BadParameter("Neo4j is the only production graph store")
    _run_host_http(
        bind_host=host,
        port=port,
        mode=mode,
        inspector_port=inspector_port,
        inspector_proxy_port=inspector_proxy_port,
        require_neo4j=True,
    )


def _host_from_env() -> "Host":
    from n4x.host.paths import default_install_root
    from n4x.host.supervisor import Host

    return Host(default_install_root())


def _echo_json(payload: object) -> None:
    import json

    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _running_host_json(
    method: str, path: str, payload: dict[str, object] | None = None
) -> dict[str, object] | None:
    import httpx

    origin = os.environ.get("N4X_HOST_CONTROL_ORIGIN", "http://127.0.0.1:7744")
    try:
        with httpx.Client(timeout=120.0) as client:
            kwargs: dict[str, object] = {}
            if payload is not None:
                kwargs["json"] = payload
            response = client.request(method, f"{origin.rstrip('/')}{path}", **kwargs)
    except httpx.ConnectError:
        return None
    try:
        body = response.json()
    except ValueError:
        body = {"error": response.text}
    if response.status_code >= 400:
        typer.echo(f"host {path} failed ({response.status_code}): {body}")
        raise typer.Exit(code=1)
    if not isinstance(body, dict):
        typer.echo(f"host {path} returned a non-object")
        raise typer.Exit(code=1)
    return body


@host_app.command("enable")
def host_enable(
    revision_id: Annotated[str, typer.Argument(help="SystemRevision id to enable.")],
) -> None:
    """Enable a System revision. Rematerializes current SourceFiles and restarts the worker."""
    running = _running_host_json(
        "POST",
        "/n4x-host/enable",
        {"revision_id": revision_id},
    )
    if running is not None:
        _echo_json(running)
        return
    runtime = _host_from_env()
    try:
        runtime.acquire()
        runtime.enable_revision(revision_id)
        enabled = runtime.system_graph.enabled_revision()
        _echo_json(
            {
                "enabled": revision_id,
                "content_root": None if enabled is None else enabled.content_root,
            }
        )
    finally:
        runtime.release()


@host_app.command("dump")
def host_dump(
    output: Annotated[Path, typer.Option(help="Destination .n4xi bundle.")],
    neo4j_dump: Annotated[
        Path | None,
        typer.Option(help="Existing Neo4j dump file. Or set N4X_NEO4J_DUMP_COMMAND."),
    ] = None,
) -> None:
    """Write an instance bundle (ciphertext, runtime, Neo4j dump). No plaintext secrets."""
    running = _running_host_json(
        "POST",
        "/n4x-host/dump",
        {
            "output": str(output.expanduser().resolve()),
            "neo4j_dump": None if neo4j_dump is None else str(neo4j_dump),
        },
    )
    if running is not None:
        _echo_json(running)
        return
    runtime = _host_from_env()
    try:
        runtime.acquire()
        _echo_json(runtime.dump_instance(output, neo4j_dump=neo4j_dump))
    finally:
        runtime.release()


@host_app.command("restore")
def host_restore(
    archive: Annotated[Path, typer.Argument(help="Instance bundle to restore.")],
) -> None:
    """Restore an instance bundle onto this box."""
    running = _running_host_json(
        "POST",
        "/n4x-host/restore",
        {"input": str(archive.expanduser().resolve())},
    )
    if running is not None:
        _echo_json(running)
        return
    runtime = _host_from_env()
    try:
        runtime.acquire()
        _echo_json(runtime.restore_instance(archive))
    finally:
        runtime.release()


@host_app.command("import")
def host_import(
    archive: Annotated[
        Path | None,
        typer.Option(help="Official System zip with manifest.json."),
    ] = None,
    check: Annotated[
        bool, typer.Option("--check", help="Compare the running revision to the index.")
    ] = False,
) -> None:
    """Import an official zip into the graph. Does not enable."""
    if check or archive is None:
        running = _running_host_json("GET", "/n4x-host/release")
        if running is not None:
            _echo_json(running)
            return
        runtime = _host_from_env()
        try:
            _echo_json(runtime.release_status())
        finally:
            runtime.release()
        return
    running = _running_host_json(
        "POST",
        "/n4x-host/import",
        {"archive": str(archive.expanduser().resolve())},
    )
    if running is not None:
        _echo_json(running)
        return
    runtime = _host_from_env()
    try:
        runtime.acquire()
        _echo_json(runtime.import_official_archive(archive))
    finally:
        runtime.release()


if __name__ == "__main__":
    app()
