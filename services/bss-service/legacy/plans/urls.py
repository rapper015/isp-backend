from django.urls import path

from . import views

urlpatterns = [
    path("", views.PlanListCreateView.as_view()),
    path("<int:plan_id>", views.PlanDetailView.as_view()),
]
