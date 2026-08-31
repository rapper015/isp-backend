from django.urls import path

from . import views

urlpatterns = [
    path("", views.SubscriberListCreateView.as_view()),
    path("<int:subscriber_id>", views.SubscriberDetailView.as_view()),
]
