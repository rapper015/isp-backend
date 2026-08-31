from django.urls import path

from . import views

urlpatterns = [
    path("nas", views.NasDeviceListView.as_view()),
    path("nas/<int:device_id>", views.NasDeviceDetailView.as_view()),
]
