"""
Local namespace: `edwh help devdb`
"""

import contextlib
import datetime
import multiprocessing
import shutil
import textwrap
import typing as t
from contextlib import chdir
from pathlib import Path

import edwh
import humanize
import invoke
import tabulate
import tomlkit
from edwh import DOCKER_COMPOSE
from edwh import improved_task as task
from edwh.constants import DEFAULT_TOML_NAME, FALLBACK_TOML_NAME, LEGACY_TOML_NAME
from edwh_files_plugin import files_plugin
from edwh_files_plugin.files_plugin import CliCompressionTypes
from invoke import Context
from termcolor import cprint
from threadful import ThreadWithReturn, animate, threadify

# url shortening via c.meteddie because the Collectives URL is not recognized as a valid url by the terminal,
# so that's annoying to click
COLLECTIVES_URL = "https://c.meteddie.nl/c/odoo/sqZlx4CNRHySxDQwKYFY0w/8336de2ff453a35a3a8d"

MINIMAL_REQUIRED_EDWH_FILES_VERSION = "1.1.0"


def ensure_snapshots_folder(name: str = "snapshot", create: bool = True) -> Path:
    """
    Ensure that the snapshots folder exists.

    If the snapshots folder does not exist, it will be created at the specified path "./migrate/data/snapshot".
    The folder's permissions will be set to 0x777.

    :return: None
    """
    if name != "snapshot" and not name.endswith(".snapshot"):
        name += ".snapshot"

    parent = Path("./migrate/data")
    parent.mkdir(exist_ok=True, parents=True)
    snapshots_folder = parent / name
    if create:
        snapshots_folder.mkdir(exist_ok=True)
    elif not snapshots_folder.exists():
        print(f"Error: snapshot '{name}' not found.")
        exit(1)

    return snapshots_folder


@task(hookable=False)
def setup(c: Context):
    use_default = edwh.get_env_value("ACCEPT_DEFAULTS", "0") == "1"

    postgres_username = edwh.check_env(
        key="POSTGRES_USERNAME",
        default="postgres",
        comment="Username to use with the postgres database",
        force_default=use_default,
    )
    postgres_password = edwh.check_env(
        key="POSTGRES_PASSWORD",
        default="password",
        comment="password for pgpool/postgres to access the backend database",
        force_default=use_default,
    )
    pgpool_port = edwh.check_env(
        key="PGPOOL_PORT",
        default=edwh.tasks.next_value(c, "PGPOOL_PORT", 5432),
        comment="Port to host pgpool on. Avoid collisions when using multiple projects, auto-discovered default",
        force_default=use_default,
    )
    postgres_database = edwh.check_env(
        key="POSTGRES_DATABASE",
        default="backend",
        comment="Name of the database for the backend database",
        force_default=use_default,
    )
    # use these to construct the intern and external URL
    edwh.check_env(
        key="INSIDE_DOCKER_POSTGRES_URI",
        default=f"postgres://{postgres_username}:{postgres_password}@pgpool:5432/{postgres_database}",
        comment="The pydal compatible URI used to connect to the database, WITHIN the docker environment",
        force_default=use_default,
    )
    edwh.check_env(
        key="OUTSIDE_DOCKER_POSTGRES_URI",
        default=f"postgres://{postgres_username}:{postgres_password}@127.0.0.1:{pgpool_port}/{postgres_database}",
        comment="The pydal compatible URI used to connect to the database, WITHIN the docker environment",
        force_default=use_default,
    )
    edwh.check_env(
        key="INSIDE_DOCKER_PG_DUMP_URI",
        default=f"postgres://{postgres_username}:{postgres_password}@pg-0:5432/{postgres_database}",
        comment="The pydal compatible URI used to connect to the database, WITHIN the docker environment",
        force_default=use_default,
    )


"""
Todo:
- don't harcode tables to exclude
- load from `default.toml` or `.toml` (c
"""


def find_devdb_config():
    for toml_name in (DEFAULT_TOML_NAME, FALLBACK_TOML_NAME, LEGACY_TOML_NAME):
        with contextlib.suppress(Exception):
            path = Path(toml_name)
            data = tomlkit.loads(path.read_text())
            return data["devdb"]

    # nothing found :(
    return {}


def find_tables_to_exclude(
    exclude: list[str] = (),
):
    return exclude or find_devdb_config().get("exclude") or []


@task(
    help=dict(
        exclude="Tables to exclude (otherwise loaded from devdb.exclude in .toml)",
    ),
    iterable=("exclude",),
    flags={
        "backup_all": ("all", "a"),
    },
    pre=[edwh.tasks.require_sudo],
)
def snapshot(
    ctx: Context,
    exclude: list[str],
    backup_all: bool = False,
    compress: bool = False,
    name: str = "snapshot",
):
    """
    Takes a snapshot of the development database for use with push, pop and recover.
    The intermediate backup is saved in ./migrate/data/snapshot.
    Send this using `ew devdb.push`.
    This file leaves out the largest tables that aren't used regularly when developing ensuring quick processing.

    Args:
        ctx: invoke context
        exclude (list): tables to exclude. If not selected, the `devdb.exclude` value in .toml or default.toml is used.
        backup_all (boolean): ignore 'exclude', backup all tables.
        compress (bool): use lz4 compression? Only supported on Postgres 16 and higher.

    Example:
    server$ ew devdb.snapshot
    # a multithreaded directory backup that should be recovered using the same postgres version is saved
    # in ./migrate/data/snapshot

    server$ ew devdb.push
    # the above folder is packed into a zip file and sent over to https://files.edwh.nl
    # the command to receive this file is printed on stdout.

    developer_machine$ ew devdb.pop <url>
    # Using the output of above, or the given URL the popdb downloads the zip archive
    # and unpacks it to ./migrate/data/snapshot

    developer_machine$ ew wipe-db
    # this regularly clears your postgres database. be cautious!

    developer_machine$ ew up -s db
    # rebuild your postgres cluster. This may take a few seconds.

    developer_machine$ ew devdb.recover
    # use the multithreaded recovery method to quickly restore the database

    developer_machine$ ew migrate
    # make sure all migrations are run and your schema matches your code

    developer_machine$ ew up
    # bring everything up
    """
    snapshots_folder = ensure_snapshots_folder(name)

    ctx.sudo(f"rm -rf {snapshots_folder}")
    ctx.sudo(f"mkdir -p {snapshots_folder}")
    ctx.sudo(f"chown -R 1050:1050 {snapshots_folder}")
    ctx.sudo(f"chmod -R 770 {snapshots_folder}")

    exclude = [] if backup_all else find_tables_to_exclude(exclude)

    excludes = "".join([f" --exclude-table-data={table} " for table in exclude])

    postgres_uri = edwh.get_env_value("INSIDE_DOCKER_PG_DUMP_URI")

    # by default avoid compression for use with Restic
    compress_arg = "--compress=lz4" if compress else "-Z 0"

    cmd = (
        f"{DOCKER_COMPOSE} run -T --rm migrate "  # run within this container, remote docker residue
        "pg_dump "
        "-F d "  # directory format
        f"-j {multiprocessing.cpu_count() - 1} "  # threads
        f"{compress_arg} "
        f"{excludes}"
        f"-f /data/{snapshots_folder.name} "  # in the ./migrate/data folder mounted as /data
        f'"{postgres_uri}"'
    )

    result = run_in_background_with_animation(
        ctx,
        cmd,
        warn=True,
        # echo=True,
    )
    print(f"Ran: `{cmd}`")

    if not result.ok:
        print("-----")
        print("Seeing something like this:")
        print(' ... DETAIL:  kind mismatch among backends. Possible last query was: "SET TRANSACTION SNAPSHOT ....')
        print("if so: try restarting the pg-services: ")
        cprint('$ ew stop -s "pg*" up -s pgpool', color="blue")
    else:
        total_size = sum(f.stat().st_size for f in snapshots_folder.glob("*"))

        print(f"Done, saved {humanize.naturalsize(total_size)} in a snapshot.")
        print("Send it to files for transfering using:")
        cprint("$ ew devdb.push", color="blue")


@task(
    pre=[edwh.tasks.require_sudo],
)
def snapshot_full(
    ctx: Context,
):
    """
    See devdb.snapshot. This version doesn't exclude any tables.
    """
    return snapshot(
        ctx,
        exclude=[],
        backup_all=True,
    )


@task
def rename(_: Context, name: str, snapshot: str = "snapshot"):
    folder = ensure_snapshots_folder(snapshot, create=False)
    if not any(folder.glob("*")):
        print("Failure: create a snapshot first")
        return

    new_folder_name = folder.parent / f"{name}.snapshot"
    if not new_folder_name.exists():
        folder.rename(new_folder_name)
    else:
        print(f"Failure: {new_folder_name} already exists")


@task(name="list")
def show_list(_: Context):
    """
    List the snapshots in reverse chronological order (by max creation time of the files in the snapshots)

    Local only.
    """
    folder = ensure_snapshots_folder(create=True)
    parent_folder = folder.parent

    snapshot_dirs = [
        d for d in parent_folder.iterdir() if d.is_dir() and d.name.endswith(".snapshot") or d.name == "snapshot"
    ]

    if not snapshot_dirs:
        print("No snapshots found.")
        return

    results = [
        dict(
            folder=d,
            timestamp=(
                datetime.datetime.fromtimestamp(max(f.stat().st_ctime for f in d.glob("*")))
                if list(d.glob("*"))
                else "Empty"
            ),
            size=humanize.naturalsize(sum(f.stat().st_size for f in d.glob("*"))),
        )
        for d in snapshot_dirs
    ]
    results = sorted(results, key=lambda rec: str(rec["timestamp"]), reverse=True)

    print("Snapshot:")
    print(tabulate.tabulate(results, headers="keys"))


@threadify()
def run_in_background(ctx: Context, command: str, **kwargs: t.Any) -> ThreadWithReturn[invoke.Result]:
    return ctx.run(
        command,
        **kwargs,
    )


def run_in_background_with_animation(ctx: Context, command: str, **kwargs: t.Any):
    promise = run_in_background(ctx, command, **kwargs)
    return animate(promise, text=textwrap.shorten(command, 100))


@task()
def recover(ctx: Context, name: str = "snapshot"):
    """
    Recovers the development database from a previously created (popped) snapshot.
    Receive a snapshot using `ew devdb.pop <url>`.

    Run `ew help devdb.snapshot` for more info.

    Example Usage:
    #> ew devdb.recover
    """
    folder = ensure_snapshots_folder(name)
    if not any(folder.glob("*")):
        print("Failure: create a snapshot first")
        return
    print("recovering...")
    postgres_uri = edwh.get_env_value("INSIDE_DOCKER_POSTGRES_URI")
    cmd = (
        f"{DOCKER_COMPOSE} run -T --rm --no-deps migrate "  # run within this container, remote docker residue
        "pg_restore "
        "--no-owner "  # anders verkeerde schema
        "--no-acl "  # schijnbaar ook nodig.
        f"-j {multiprocessing.cpu_count() - 1} "  # threads
        f'-d "{postgres_uri}" '  # target database
        f"/data/{folder.name}"
    )  # in this folder

    result = run_in_background_with_animation(
        ctx,
        cmd,
        warn=True,
    )
    print(f"Ran: `{cmd}`")

    if not (result and result.ok):
        print("In case of a lot of errors:")
        cprint("$ edwh wipe-db up -s pgpool", color="blue")
        print("In case of a connection error, pgpool is probably still rebuilding after your wipe-db")
    else:
        print("Should be fine!")


def create_terminal_link(url: str, text: str, underline: bool = True) -> str:
    """
    Create a clickable terminal hyperlink using ANSI escape sequences

    Args:
        url: The URL to link to
        text: The display text

    Returns:
        Formatted string with terminal hyperlink
    """
    # ANSI escape sequence for hyperlinks: \033]8;;URL\033\\TEXT\033]8;;\033\\
    # Use \x1b instead of \033 for better compatibility
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


@task()
def push(_: Context, compression: "CliCompressionTypes" = "auto", compression_level: int = 5, name: str = "snapshot"):
    """
    Pushes the local development database to a remote server.

    Args:
        compression: which compression type to use ("auto", "gzip", "zip", "none")
        compression_level: The compression level is a measure of the compression quality (file size; int 0 - 9).

    Run `ew help devdb.snapshot` for more info.

    Example Usage:
    #> ew devdb.push
    """
    folder = ensure_snapshots_folder(name)
    if not any(folder.glob("*")):
        print("Failure: will not push an empty folder")
        return

    response = files_plugin.upload_directory(
        files_plugin.DEFAULT_TRANSFERSH_SERVER,
        filepath=folder,
        compression=compression,
        compression_level=compression_level,
    )

    download_url = response.text.strip()
    delete_url = response.headers.get("x-url-delete")
    print("\ndownload using:")
    cprint(f"$ edwh devdb.pop {download_url}", color="blue")
    print("\ndownload and immediately use using:")
    cprint(f"$ edwh devdb.reset --pop {download_url}", color="blue")
    print("\nDelete using:")
    cprint(f"$ edwh file.delete {delete_url}", color="blue")

    print(
        f"\nVergeet niet om de URL ook bij te werken het overzicht:",
        create_terminal_link(COLLECTIVES_URL, "🔗 Odoo > Kennis > Teddies > Database recover URLs"),
    )


@task(
    aliases=("pull",),
)
def pop(ctx: Context, url: str, yes: bool = False, name: str = "snapshot"):
    """
    Prepares the the development database snapshot from the given URL.

    Args:
        url (str): The URL of the snapshot to download and populate the database with.
                 You get this url after a succesful `ew devdb.push`.
        yes: don't ask permission to overwrite existing
        name: store as a specific name instead of 'snapshot'

    Run `ew help devdb.snapshot` for more info.

    Example Usage:
    #> ew devdb.pop https://example.com/snapshot.zip
    """
    folder = ensure_snapshots_folder(name)
    if any(folder.glob("*")):
        if yes or edwh.confirm("A snapshot already exists. Overwrite? [Yn]", default=True):
            print(f"Flushing {folder}")
            shutil.rmtree(folder)
            folder.mkdir()
        else:
            print("Not overwriting, okay.")
            return

    with chdir(folder.parent):
        ext = url.split(".")[-1]

        if folder.name == "snapshot":
            tmp_file = Path(folder.name).with_suffix(f".{ext}")
        else:
            tmp_file = Path(folder.name).with_suffix(f".snapshot.{ext}")

        files_plugin.download(ctx, url, output_file=tmp_file, unpack=True)

    print("recover using:")
    cprint("$ edwh devdb.reset", color="blue")


@task(flags={"with_pop": ("pop", "p")})
def reset(
    ctx: Context,
    yes: bool = False,
    wait: int = 0,
    skip_up: bool = False,
    name: str = "snapshot",
    with_pop: t.Optional[str] = None,
):
    """
    Reset your database to the latest devdb (wipe, recover, migrate etc.)

    Args:
        ctx: invoke context
        yes: don't ask before wiping database
        wait: deprecated
        skip_up: by default, `edwh up` is called after the process is done. add `--skip-up` to prevent this.
        name: name of the snapshot to use.
        with_pop: snapshot url to download (pop) before restoring
    """
    if wait:
        cprint("Note: --wait is deprecated in favour of health checks!", color="yellow")

    if with_pop:
        pop(ctx, with_pop, yes=yes)

    if name:
        ensure_snapshots_folder(name, create=False)

    edwh.tasks.stop(ctx)
    edwh.tasks.wipe_db(ctx, yes=yes)
    edwh.tasks.up(ctx, service=["db"])

    edwh.tasks.health(
        ctx,
        wait=True,
        service=["pgpool", "pg-0", "pg-1"],
    )

    recover(ctx, name)
    edwh.tasks.migrate(ctx)

    if not skip_up:
        edwh.tasks.up(ctx)
