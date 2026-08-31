from django.urls import path

from . import views

urlpatterns = [
    path("overview", views.DashboardOverviewView.as_view()),
]
