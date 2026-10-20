"""Optional integration tests using a temporary Docker PostgreSQL instance.

Run with: RUN_DOCKER_TESTS=1 pytest tests/test_integration_docker.py -q
"""

import os
import shutil
import subprocess
import time

import pytest

pytestmark = pytest.mark.docker

PG_PORT = int(os.environ.get("PG_PORT", "55432"))
PG_PASSWORD = "testpw"
CONTAINER = "edwh-devdb-test-pg"
PG_IMAGE = os.environ.get("PG_IMAGE", "postgres:16")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _psql(db, sql, port=PG_PORT):
    return _run(
        ["psql", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "-d", db,
         "-v", "ON_ERROR_STOP=1", "-c", sql],
        env={**os.environ, "PGPASSWORD": PG_PASSWORD},
    )


@pytest.fixture(scope="module")
def pg_container():
    _run(["docker", "rm", "-f", CONTAINER])
    r = _run(["docker", "run", "-d", "--name", CONTAINER,
              "-e", f"POSTGRES_PASSWORD={PG_PASSWORD}",
              "-p", f"{PG_PORT}:5432", PG_IMAGE])
    if r.returncode != 0:
        pytest.skip(f"could not start postgres container: {r.stderr}")
    deadline = time.time() + 60
    ready = False
    while time.time() < deadline:
        rr = _run(["docker", "exec", CONTAINER, "pg_isready", "-U", "postgres"])
        if rr.returncode == 0:
            ready = True
            break
        time.sleep(1)
    if not ready:
        _run(["docker", "rm", "-f", CONTAINER])
        pytest.skip("postgres container did not become ready")
    yield
    _run(["docker", "rm", "-f", CONTAINER])


def _setup_source():
    _psql("postgres", "DROP DATABASE IF EXISTS testsrc;")
    r = _psql("postgres", "CREATE DATABASE testsrc;")
    assert r.returncode == 0, r.stderr
    r = _psql("testsrc", """
        CREATE TABLE public.foo (id int primary key, v text);
        CREATE TABLE public.bar (id int primary key, v text);
        CREATE MATERIALIZED VIEW public.mv AS SELECT count(*) c FROM public.foo;
        INSERT INTO public.foo VALUES (1,'a'),(2,'b');
        INSERT INTO public.bar VALUES (10,'x'),(11,'y');
        REFRESH MATERIALIZED VIEW public.mv;
    """)
    assert r.returncode == 0, r.stderr


def _filter_list(snap, include=None, exclude=None, tmp_path=None):
    include_set = set(include or [])
    exclude_set = set(exclude or [])
    partial = bool(include_set or exclude_set)
    no_lines, mat_lines = [], []
    mat_found = False
    any_sel = False
    out = _run(["pg_restore", "-l", snap], env={**os.environ, "PGPASSWORD": PG_PASSWORD}).stdout
    for raw in out.split("\n"):
        line = raw + "\n"
        is_mat = "MATERIALIZED VIEW DATA" in line
        is_data = ("TABLE DATA" in line) or is_mat
        if partial and is_data:
            toks = line.split()
            schema = name = None
            for i, tok in enumerate(toks):
                if tok == "DATA" and i + 2 < len(toks):
                    schema, name = toks[i + 1], toks[i + 2]
                    break
            keep = True
            if schema is not None and name is not None:
                cands = {name, f"{schema}.{name}"}
                if include_set and not (cands & include_set):
                    keep = False
                if exclude_set and (cands & exclude_set):
                    keep = False
            if not keep:
                continue
            any_sel = True
        if is_mat:
            mat_lines.append(line)
            mat_found = True
        else:
            no_lines.append(line)
    assert partial is False or any_sel, "no matching tables"
    no_path = str(tmp_path / "no_mat.lst")
    mat_path = str(tmp_path / "mat.lst")
    with open(no_path, "w") as f:
        f.write("".join(no_lines))
    with open(mat_path, "w") as f:
        f.write("".join(mat_lines))
    return no_path, mat_path, mat_found


def test_full_backup_and_restore_all_options(pg_container, tmp_path):  # noqa: ARG001
    dump_dir = tmp_path / "snap"
    dump_dir.mkdir()
    snap = str(dump_dir)

    _setup_source()

    r = _run(["pg_dump", "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", "postgres",
              "-F", "d", "-f", snap, "testsrc"],
             env={**os.environ, "PGPASSWORD": PG_PASSWORD})
    assert r.returncode == 0, r.stderr

    _psql("postgres", "DROP DATABASE IF EXISTS testtgt;")
    _psql("postgres", "CREATE DATABASE testtgt;")
    _psql("testtgt", """
        CREATE TABLE public.foo (id int primary key, v text);
        CREATE TABLE public.bar (id int primary key, v text);
        CREATE MATERIALIZED VIEW public.mv AS SELECT count(*) c FROM public.foo;
        INSERT INTO public.bar VALUES (99,'preexisting');
    """)

    # 1) Partial data-only restore of foo only.
    no_path, mat_path, mat_found = _filter_list(snap, include=["public.foo"], tmp_path=tmp_path)
    r = _run(["pg_restore", "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", "postgres",
              "-d", "testtgt", "--data-only", "--no-owner", "--no-acl",
              "-L", no_path, snap],
             env={**os.environ, "PGPASSWORD": PG_PASSWORD})
    assert r.returncode == 0, r.stderr
    r = _psql("testtgt", "SELECT count(*) FROM foo;")
    assert "2" in r.stdout
    r = _psql("testtgt", "SELECT count(*) FROM bar;")
    assert "1" in r.stdout

    # 2) Exclude bar from a full restore using clean.
    _psql("postgres", "DROP DATABASE IF EXISTS testtgt2;")
    _psql("postgres", "CREATE DATABASE testtgt2;")
    _psql("testtgt2", """
        CREATE TABLE public.foo (id int primary key, v text);
        CREATE TABLE public.bar (id int primary key, v text);
        CREATE MATERIALIZED VIEW public.mv AS SELECT count(*) c FROM public.foo;
    """)
    no_path, mat_path, mat_found = _filter_list(snap, exclude=["public.bar"], tmp_path=tmp_path)
    r = _run(["pg_restore", "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", "postgres",
              "-d", "testtgt2", "--clean", "--if-exists", "--no-owner", "--no-acl",
              "-L", no_path, snap],
             env={**os.environ, "PGPASSWORD": PG_PASSWORD})
    assert r.returncode == 0, r.stderr
    if mat_found:
        r = _run(["pg_restore", "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", "postgres",
                  "-d", "testtgt2", "--clean", "--if-exists", "--no-owner", "--no-acl",
                  "-L", mat_path, snap],
                 env={**os.environ, "PGPASSWORD": PG_PASSWORD})
        assert r.returncode == 0, r.stderr
    r = _psql("testtgt2", "SELECT count(*) FROM foo;")
    assert "2" in r.stdout
    r = _psql("testtgt2", "SELECT count(*) FROM bar;")
    assert "0" in r.stdout

    # 3) disable-triggers path (data-only).
    no_path, mat_path, mat_found = _filter_list(snap, include=["public.foo"], tmp_path=tmp_path)
    r = _run(["pg_restore", "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", "postgres",
              "-d", "testtgt", "--data-only", "--disable-triggers", "--no-owner", "--no-acl",
              "-L", no_path, snap],
             env={**os.environ, "PGPASSWORD": PG_PASSWORD})
    assert "Usage:" not in (r.stderr or "") and "invalid" not in (r.stderr or "").lower()

    _psql("postgres", "DROP DATABASE IF EXISTS testsrc;")
    _psql("postgres", "DROP DATABASE IF EXISTS testtgt;")
    _psql("postgres", "DROP DATABASE IF EXISTS testtgt2;")
    shutil.rmtree(snap, ignore_errors=True)
