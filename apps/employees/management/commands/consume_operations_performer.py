import logging

from django.core.management.base import BaseCommand

from apps.employees.operations_consumer import run_consumer

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Consume OMS5 operations.performer.v1 shift facts and update performer funnel stages"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Drain available messages and exit")
        parser.add_argument("--poll-interval", type=float, default=1.0)

    def handle(self, *args, **options):
        interval = max(0.1, options["poll_interval"])
        try:
            processed = run_consumer(once=options["once"], poll_interval=interval)
        except KeyboardInterrupt:
            self.stdout.write("OMS2 operations performer consumer stopped")
            return
        if options["once"]:
            self.stdout.write(str(processed))
            return
        self.stdout.write("OMS2 operations performer consumer started")
