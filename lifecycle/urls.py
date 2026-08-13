from django.urls import path

from . import views

urlpatterns = [
    path("transition", views.CustomerLifecycleTransitionView.as_view()),
    path("events", views.CustomerLifecycleEventListView.as_view()),
    path("risk-profile", views.CustomerRiskProfileView.as_view()),
]
