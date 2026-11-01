from django.http import JsonResponse
from django.urls import path

from clinic import views

urlpatterns = [
    path("reset", views.reset),
    path("slots", views.slots),
    path("bookings", views.bookings),
    path("bookings/<str:booking_id>", views.booking_detail),
]


def not_found(request, exception=None):
    return JsonResponse({"error": "not found"}, status=404)


def server_error(request):
    return JsonResponse({"error": "internal server error"}, status=500)


handler404 = not_found
handler500 = server_error