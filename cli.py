"""CLI entry point for palmos-portal — PalmOS device communication tool."""

from __future__ import annotations

import os
import sys
import logging

import struct

import click

from palm.transport import Connection
from palm.slp import SLPSocket
from palm.padp import PADPConnection
from palm.dlp import DLPClient, DatabaseInfo, DB_MODE_READ
from palm.pdb import PalmDatabase, ATTR_RESOURCE

logger = logging.getLogger(__name__)

# CMP (Connection Management Protocol) constants
CMP_TYPE_WAKEUP = 0x01
CMP_TYPE_INIT = 0x02
_CMP_FORMAT = ">BBBBHI"
_CMP_SIZE = struct.calcsize(_CMP_FORMAT)


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s: %(message)s")


class DeviceSession:
    """Context manager that opens a full HotSync session."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.conn: Connection | None = None
        self.dlp: DLPClient | None = None

    def __enter__(self) -> DLPClient:
        import time
        self.conn = Connection()
        click.echo("Waiting for Visor... press HotSync button.")
        while True:
            try:
                self.conn.open()
                break
            except ConnectionError:
                time.sleep(1)

        slp = SLPSocket(self.conn)
        padp = PADPConnection(slp)

        # CMP handshake: receive and send via PADP
        cmp_data = padp.receive()
        if len(cmp_data) >= _CMP_SIZE:
            cmp_type, flags, ver_major, ver_minor, unused, max_baud = struct.unpack(
                _CMP_FORMAT, cmp_data[:_CMP_SIZE]
            )
            logger.info(f"CMP init: type={cmp_type}, ver={ver_major}.{ver_minor}, max_baud={max_baud}")

            response = struct.pack(
                _CMP_FORMAT,
                CMP_TYPE_INIT, 0x00, ver_major, ver_minor, 0, 0,
            )
            padp.send(response)
            logger.info("CMP handshake complete")

        self.dlp = DLPClient(padp)
        self.dlp.open_conduit()

        return self.dlp

    def __exit__(self, *args) -> None:
        if self.dlp is not None:
            try:
                self.dlp.end_of_sync()
            except Exception:
                pass
        if self.conn is not None:
            self.conn.close()


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Show protocol debug output")
@click.pass_context
def cli(ctx, verbose):
    """palmos-portal — communicate with PalmOS devices over USB."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    setup_logging(verbose)


@cli.command()
@click.pass_context
def sysinfo(ctx):
    """Show device system info."""
    with DeviceSession(verbose=ctx.obj["verbose"]) as dlp:
        info = dlp.read_sys_info()
        rom_major = (info.rom_version >> 24) & 0xFF
        rom_minor = (info.rom_version >> 20) & 0x0F
        device_name = info.name or dlp._padp._slp._stream._dev.product or "Unknown"
        click.echo(f"Device: {device_name}")
        click.echo(f"ROM Version: {rom_major}.{rom_minor}")


@cli.command("list")
@click.option("--rom", is_flag=True, help="Include ROM databases")
@click.option("--ram/--no-ram", default=True, help="Include RAM databases")
@click.pass_context
def list_dbs(ctx, rom, ram):
    """List databases on device."""
    with DeviceSession(verbose=ctx.obj["verbose"]) as dlp:
        databases = dlp.list_databases(ram=ram, rom=rom)
        if not databases:
            click.echo("No databases found.")
            return

        click.echo(f"{'Name':<32} {'Type':<6} {'Creator':<8} {'Flags':<6}")
        click.echo("-" * 54)
        for db in databases:
            flag_str = "R" if db.attributes & ATTR_RESOURCE else "D"
            click.echo(f"{db.name:<32} {db.db_type:<6} {db.creator:<8} {flag_str:<6}")
        click.echo(f"\n{len(databases)} database(s) found.")


@cli.command()
@click.argument("name")
@click.pass_context
def info(ctx, name):
    """Show database header info."""
    with DeviceSession(verbose=ctx.obj["verbose"]) as dlp:
        databases = dlp.list_databases(ram=True, rom=True)
        db_info = next((d for d in databases if d.name == name), None)
        if db_info is None:
            click.echo(f"Database '{name}' not found.", err=True)
            sys.exit(1)

        click.echo(f"Name:    {db_info.name}")
        click.echo(f"Type:    {db_info.db_type}")
        click.echo(f"Creator: {db_info.creator}")
        click.echo(f"Version: {db_info.version}")
        is_res = "Yes (.prc)" if db_info.attributes & ATTR_RESOURCE else "No (.pdb)"
        click.echo(f"Resource DB: {is_res}")

        handle = dlp.open_db(name, DB_MODE_READ)
        try:
            num = dlp.read_open_db_info(handle)
            label = "Resources" if db_info.attributes & ATTR_RESOURCE else "Records"
            click.echo(f"{label}: {num}")
        finally:
            dlp.close_db(handle)


@cli.command()
@click.argument("name")
@click.option("--out", default=None, help="Output file path")
@click.pass_context
def pull(ctx, name, out):
    """Download a database from the device."""
    with DeviceSession(verbose=ctx.obj["verbose"]) as dlp:
        databases = dlp.list_databases(ram=True, rom=True)
        db_info = next((d for d in databases if d.name == name), None)
        if db_info is None:
            click.echo(f"Database '{name}' not found.", err=True)
            sys.exit(1)

        click.echo(f"Downloading '{name}'...")
        db = PalmDatabase.from_device(
            dlp, name=name,
            db_type=db_info.db_type,
            creator=db_info.creator,
            attributes=db_info.attributes,
        )

        if out is None:
            ext = ".prc" if db.is_resource_db else ".pdb"
            out = name + ext

        db.to_file(out)
        items = len(db.resources) if db.is_resource_db else len(db.records)
        label = "resources" if db.is_resource_db else "records"
        click.echo(f"Saved {items} {label} to {out}")


@cli.command()
@click.argument("file", type=click.Path(exists=True))
@click.pass_context
def push(ctx, file):
    """Upload a .prc/.pdb file to the device."""
    db = PalmDatabase.from_file(file)
    items = len(db.resources) if db.is_resource_db else len(db.records)
    label = "resources" if db.is_resource_db else "records"

    click.echo(f"Uploading '{db.name}' ({items} {label})...")

    with DeviceSession(verbose=ctx.obj["verbose"]) as dlp:
        db.to_device(dlp)

    click.echo(f"Done — '{db.name}' installed on device.")


@cli.command()
@click.argument("name")
@click.pass_context
def delete(ctx, name):
    """Delete a database from the device."""
    with DeviceSession(verbose=ctx.obj["verbose"]) as dlp:
        dlp.delete_db(name)
        click.echo(f"Deleted '{name}'.")


@cli.command()
@click.option("--port", default=8000, help="Port to serve on")
def web(port):
    """Launch web dashboard."""
    from web.server import run
    run()


if __name__ == "__main__":
    cli()
