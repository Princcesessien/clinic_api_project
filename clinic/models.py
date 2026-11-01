import uuid

from django.db import models
from django.db.models import Q


def new_id():
    return str(uuid.uuid4())


class Slot(models.Model):
    id = models.CharField(primary_key=True, max_length=100)
    provider = models.CharField(max_length=200)
    start = models.DateTimeField()
    end = models.DateTimeField()


class Booking(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id)
    slot = models.ForeignKey(Slot, on_delete=models.CASCADE, related_name="bookings")
    name = models.CharField(max_length=200)
    email = models.CharField(max_length=254, db_index=True)
    status = models.CharField(max_length=20, default="confirmed")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["slot"],
                condition=Q(status="confirmed"),
                name="one_confirmed_per_slot",
            )
        ]


class IdempotencyRecord(models.Model):
    key = models.CharField(max_length=255, unique=True)
    body_hash = models.CharField(max_length=64)
    status_code = models.IntegerField()
    response = models.JSONField()


class BookingAttempt(models.Model):
    email = models.CharField(max_length=254, db_index=True)
    at = models.DateTimeField(db_index=True)