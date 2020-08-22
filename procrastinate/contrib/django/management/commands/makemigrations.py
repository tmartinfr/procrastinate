import pathlib
import sys
from typing import Iterable, Optional, Tuple

import attr
from django.core.management.commands import makemigrations
from django.db import migrations
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader

from procrastinate.contrib.django import utils


class Command(makemigrations.Command):
    def handle(self, *app_labels, **options):
        # To avoid polluting user applications, we're only doing something
        # when explicitely requested.
        if "procrastinate" in app_labels:
            self.make_procrastinate_migrations(options)
            app_labels = list(app_labels)
            app_labels.remove("procrastinate")
        return super().handle(*app_labels, **options)

    def make_procrastinate_migrations(self, options):
        self.verbosity = options["verbosity"]
        self.interactive = options["interactive"]
        self.dry_run = options["dry_run"]
        self.include_header = options["include_header"]

        loader = MigrationLoader(None, ignore_no_migrations=True)

        new_migrations = list(get_missing_migrations(loader=loader))
        if not new_migrations:
            self.stdout.write("No changes detected in procrastinate")
            return

        changes = {"procrastinate": new_migrations}

        self.write_migration_files(changes)
        if migrations and options["check_changes"]:
            sys.exit(1)

        return


def get_max_existing_migration(
    loader: MigrationLoader,
) -> Optional[migrations.Migration]:
    iter_names = list(
        name for app, name in loader.disk_migrations if app == "procrastinate"
    )
    if not iter_names:
        return None
    return loader.disk_migrations[("procrastinate", max(iter_names))]


def get_missing_migrations(loader: MigrationLoader):
    all_migrations = get_all_migrations()
    existing_migrations = set(
        get_existing_migrations(loader=loader, all_migrations=all_migrations)
    )
    # Cannot use a set because we need to keep order.
    missing_migrations = [m for m in all_migrations if m not in existing_migrations]
    previous_migration = get_max_existing_migration(loader=loader)
    for migration in missing_migrations:
        django_migration = make_migration(
            migration=migration, previous_migration=previous_migration,
        )
        previous_migration = django_migration
        yield django_migration


def version_from_string(version_str) -> Tuple:
    return tuple(int(e) for e in version_str.split("."))


@attr.dataclass(frozen=True)
class ProcrastinateMigration:
    filename: str
    name: str
    version: Tuple
    body: str

    @classmethod
    def from_string(cls, path: pathlib.Path) -> "ProcrastinateMigration":
        if path.stem.startswith("baseline"):
            name = "baseline"
            version_str = path.stem.split("-")[1]
        else:
            _, version_str, _, name = path.stem.split("_", 3)
        return cls(
            filename=path.name,
            name=name,
            version=version_from_string(version_str=version_str),
            body=path.read_text(),
        )


def get_all_migrations() -> Iterable[ProcrastinateMigration]:
    all_files = utils.list_migrations()
    migrations = [
        ProcrastinateMigration.from_string(path=e)
        for e in all_files
        if e.suffix == ".sql"
    ]

    return sorted(migrations, key=lambda x: x.version)


def get_existing_migrations(
    loader: MigrationLoader, all_migrations: Iterable[ProcrastinateMigration]
) -> Iterable[ProcrastinateMigration]:
    migration_from_body = {m.body: m for m in all_migrations}
    for (app, name), migration in loader.disk_migrations.items():
        if app != "procrastinate":
            continue
        for operation in migration.operations:
            if not isinstance(operation, migrations.RunSQL):
                continue

            try:
                yield migration_from_body[operation.sql]
            except KeyError:
                print(f"Migration not found ({operation.sql[:100]}...)")
                continue


def make_migration(
    migration: ProcrastinateMigration,
    previous_migration: Optional[migrations.Migration],
):
    class NewMigration(migrations.Migration):
        initial = "baseline" in migration.name
        operations = [utils.RunProcrastinateFile(filename=migration.filename)]
        _name = migration.name

    if previous_migration:
        NewMigration.dependencies = [("procrastinate", previous_migration.name)]
        index = MigrationAutodetector.parse_number(previous_migration.name) + 1

    else:
        index = 1

    name = f"{index:04d}_{migration.name}"

    return NewMigration(name, "procrastinate")
