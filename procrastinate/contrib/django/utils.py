import pathlib
from typing import Iterable

import importlib_resources
from django.db import migrations


def get_sql(filename) -> str:
    return importlib_resources.read_text("procrastinate.sql.migrations", filename)


def list_migrations() -> Iterable[pathlib.Path]:
    return importlib_resources.files("procrastinate.sql.migrations").iterdir()


class RunProcrastinateFile(migrations.RunSQL):
    def __init__(self, filename, **kwargs):
        self.filename = filename
        self.sql = get_sql(filename=filename)
        super().__init__(sql=self.sql, **kwargs)

    def deconstruct(self):
        qualname, args, kwargs = super().deconstruct()
        kwargs.pop("sql")
        kwargs["filename"] = self.filename
        return qualname, args, kwargs

    def describe(self):
        return f"Procrastinate SQL migration: {self.filename}"
