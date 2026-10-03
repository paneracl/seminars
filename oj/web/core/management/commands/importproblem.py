"""Import a problem package from the command line.

    python3 manage.py importproblem ../problems/sumsub --public
"""

from django.core.management.base import BaseCommand, CommandError

from core.packages import PackageError, import_package


class Command(BaseCommand):
    help = "Import a problem package (.zip or directory)."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--code", default=None,
                            help="override the code in problem.json")
        parser.add_argument("--public", action="store_true",
                            help="make it visible to students immediately")

    def handle(self, *args, **options):
        try:
            problem, warnings, notes = import_package(options["path"],
                                                       code=options["code"])
        except PackageError as exc:
            raise CommandError(str(exc)) from exc

        for note in notes:
            self.stdout.write(f"  {note}")
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"warning: {warning}"))

        if options["public"]:
            problem.is_public = True
            problem.save(update_fields=["is_public"])

        self.stdout.write(self.style.SUCCESS(
            f"{problem.code}: {problem.subtask_count} subtask(s), "
            f"{problem.test_count} test(s), {problem.max_score:g} points"
            f"{'' if problem.is_public else ' (not public yet)'}"))
