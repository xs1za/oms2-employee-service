import logging
import time

from django.core.management.base import BaseCommand

from apps.employees.outbox import dispatch_outbox_once

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Publish committed OMS2 people integration events from the transactional outbox"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Publish one batch and exit")
        parser.add_argument("--poll-interval", type=float, default=1.0)

    def handle(self, *args, **options):
        if options["once"]:
            self.stdout.write(str(dispatch_outbox_once()))
            return

        interval = max(0.1, options["poll_interval"])
        self.stdout.write("OMS2 people outbox worker started")
        while True:
            try:
                count = dispatch_outbox_once()
                if not count:
                    time.sleep(interval)
            except KeyboardInterrupt:
                self.stdout.write("OMS2 people outbox worker stopped")
                return
            except Exception:
                logger.exception("OMS2 people outbox loop failed")
                time.sleep(interval)
