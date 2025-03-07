from invoke import Context, task


@task()
def foo(c: Context) -> None:
    c.run("echo Hello World")
