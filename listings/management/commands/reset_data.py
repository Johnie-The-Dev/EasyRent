from django.core.management import BaseCommand, CommandError, call_command
from django.db import connections


class Command(BaseCommand):
    help = "Delete all Django-managed data from a database while preserving its schema."

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="default",
            help="Database alias to reset (default: default).",
        )
        parser.add_argument(
            "--confirm-database",
            required=True,
            help="The exact configured database name to confirm deletion.",
        )

    def handle(self, *args, **options):
        database = options["database"]
        if database not in connections.databases:
            raise CommandError(f"Unknown database alias: {database}")

        configured_name = connections[database].settings_dict["NAME"]
        if not configured_name:
            raise CommandError(f"Database alias {database!r} has no configured database name.")

        if options["confirm_database"] != configured_name:
            raise CommandError(
                f"Confirmation did not match database {configured_name!r}; no data was deleted."
            )

        self.stdout.write(
            f"Deleting all Django-managed data from database {configured_name!r} "
            f"(alias {database!r}); tables and migrations will be preserved."
        )
        call_command(
            "flush",
            database=database,
            interactive=False,
            verbosity=options["verbosity"],
        )
        self.stdout.write(self.style.SUCCESS("Database data reset complete."))
