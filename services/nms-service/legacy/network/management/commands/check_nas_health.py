import time

from django.core.management.base import BaseCommand

from network.models import NasDevice
from network.nas_services import health_check


class Command(BaseCommand):
    help="Refresh cached health for enabled NAS devices; schedule externally when Celery is unavailable."

    def add_arguments(self,parser):
        parser.add_argument("--stagger-seconds",type=float,default=0.25)

    def handle(self,*args,**options):
        devices=NasDevice.objects.filter(enabled=True,deleted_at__isnull=True).order_by("id")
        for index,nas in enumerate(devices.iterator()):
            health=health_check(nas)
            self.stdout.write(f"{nas.public_id}: {'online' if health['reachable'] else 'offline'}")
            if index+1<devices.count() and options["stagger_seconds"]:time.sleep(options["stagger_seconds"])
