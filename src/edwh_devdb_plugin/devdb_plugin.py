"""
Local namespace: `edwh help devdb`
"""

import datetime
import shutil
import textwrap
import typing as t
from contextlib import chdir
from pathlib import Path

import edwh
import humanize
import invoke
import tabulate
from edwh import DOCKER_COMPOSE
from edwh import improved_task as task
from edwh_files_plugin import files_plugin
from edwh_files_plugin.files_plugin import CliCompressionTypes
from invoke import Context
from termcolor import cprint
from threadful import ThreadWithReturn, animate, threadify

# url shortening via c.meteddie because the Collectives URL is not recognized as a valid url by the terminal,
# so that's annoying to click
COLLECTIVES_URL = "https://c.meteddie.nl/c/nextcloud/sqZlx4CNRHySxDQwKYFY0w/8336de2ff453a35a3a8d"

MINIMAL_REQUIRED_EDWH_FILES_VERSION = "1.1.0"


def ensure_snapshots_folder(name: str = "snapshot") -> Path:
    """
    Ensure that the snapshots folder exists.

    If the snapshots folder does not exist, it will be created at the specified path "./migrate/data/snapshot".
    The folder's permissions will be set to 0x777.

    :return: None
    """
    if name != "snapshot" and not name.endswith(".snapshot"):
        name += ".snapshot"
    snapshots_folder = Path(f"./migrate/data/{name}")
    snapshots_folder.mkdir(exist_ok=True)
    return snapshots_folder


@task()
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


@task(
    help=dict(
        without_api_activity="Ignore deprecated yet historicly relevant api_activity table",
        without_applog="Ignore the currently used signal, evidence and sesssion tables",
        without_event_stream="Ignore deprecated yet historicly relevant event_stream",
        without_cache="Ignore the cache",
    ),
    pre=[edwh.tasks.require_sudo],
)
def snapshot(
    ctx: Context,
    without_api_activity=True,
    without_applog=True,
    without_event_stream=True,
    # without_session=True,
    without_cache=True,
):
    """
    Takes a snapshot of the development database for use with push, pop and recover.
    The intermediate backup is saved in ./migrate/data/snapshot.
    Send this using `ew devdb.push`.
    This file leaves out the largest tables that aren't used regularly when developing ensuring quick processing.

    Optional parameters:
    - without_api_activity (bool): If `True`, ignores the deprecated yet historically relevant api_activity table. Default is `True`.
    - without_applog (bool): If `True`, ignores the currently used signal, evidence, and session tables. Default is `True`.
    - without_event_stream (bool): If `True`, ignores the deprecated yet historically relevant event_stream table. Default is `True`.
    - without_session (bool): If `True`, ignores the session table. Default is `True`.
    - without_cache (bool): If `True`, ignores the cache table. Default is `True`.

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
    snapshots_folder = Path("./migrate/data/snapshot")

    ctx.sudo(f"rm -rf {snapshots_folder}")
    ctx.sudo(f"mkdir -p {snapshots_folder}")
    ctx.sudo(f"chown -R 1050:1050 {snapshots_folder}")
    ctx.sudo(f"chmod -R 770 {snapshots_folder}")

    excludes = "".join(
        [
            (" --exclude-table-data=public.api_activity " if without_api_activity else " "),
            (" --exclude-table-data=public.evidence " if without_applog else " "),
            (" --exclude-table-data=signal.signal " if without_applog else " "),
            (" --exclude-table-data=signal.evidence " if without_applog else " "),
            (" --exclude-table-data=public.signal " if without_applog else " "),
            (" --exclude-table-data=public.event_stream " if without_event_stream else " "),
            (" --exclude-table-data=public.session " if without_applog else ""),
            (" --exclude-table-data=public.cache " if without_cache else ""),
        ]
    )
    postgres_uri = edwh.get_env_value("INSIDE_DOCKER_PG_DUMP_URI")

    cmd = (
        f"{DOCKER_COMPOSE} run -T --rm migrate "  # run within this container, remote docker residue
        "pg_dump "
        "-F d "  # directory format
        "-j 3 "  # threads
        "-Z 0 "  # avoid compression for use with Restic
        f"{excludes}"
        "-f /data/snapshot "  # in the ./migrate/data folder mounted as /data
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
        without_api_activity=False,
        without_applog=False,
        without_cache=False,
        without_event_stream=False,
    )


@task
def rename(ctx: Context, name: str):
    folder = ensure_snapshots_folder()
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
    folder = ensure_snapshots_folder()
    parent_folder = folder.parent
    if snapshot_dirs := [
        d for d in parent_folder.iterdir() if d.is_dir() and d.name.endswith(".snapshot") or d.name == "snapshot"
    ]:
        print("Snapshot:")
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
        results = sorted(results, key=(lambda rec: str(rec["timestamp"])), reverse=True)
        print(tabulate.tabulate(results, headers="keys"))
    else:
        print("No snapshots found.")


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
        "-j 3 "  # threads
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


@task()
def push(_: Context, compression: "CliCompressionTypes" = "auto", compression_level: int = 5):
    """
    Pushes the local development database to a remote server.

    Args:
        compression: which compression type to use ("auto", "gzip", "zip", "none")
        compression_level: The compression level is a measure of the compression quality (file size; int 0 - 9).

    Run `ew help devdb.snapshot` for more info.

    Example Usage:
    #> ew devdb.push
    """
    folder = ensure_snapshots_folder()
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
    print("\ndownload/prepare using:")
    cprint(f"$ edwh devdb.pop {download_url}", color="blue")
    print("\nDelete using:")
    cprint(f"$ edwh file.delete {delete_url}", color="blue")

    print(f"\nVergeet niet om de URL ook bij te werken op collectives: {COLLECTIVES_URL}")


@task()
def pop(ctx: Context, url: str):
    """
    Prepares the the development database snapshot from the given URL.

    Parameters:
    - url (str): The URL of the snapshot to download and populate the database with.
                 You get this url after a succesful `ew devdb.push`.

    Run `ew help devdb.snapshot` for more info.

    Example Usage:
    #> ew devdb.pop https://example.com/snapshot.zip
    """
    folder = ensure_snapshots_folder()
    if any(folder.glob("*")):
        if not edwh.confirm("A snapshot already exists. Overwrite? [Yn]", default=True):
            print("Not overwriting, okay.")
            return
        else:
            print(f"Flushing {folder}")
            shutil.rmtree(folder)
            folder.mkdir()

    with chdir(folder.parent):
        files_plugin.download(ctx, url, unpack=True)

    print("recover using:")
    cprint("$ ew devdb.recover # or devdb.reset", color="blue")


@task()
def reset(
    ctx: Context,
    yes: bool = False,
    wait: int = 0,
    skip_up: bool = False,
    name: str = "snapshot",
):
    """
    Reset your database to the latest devdb (wipe, recover, migrate etc.)
    """
    if wait:
        cprint("Note: --wait is deprecated in favour of health checks!", color="yellow")

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
