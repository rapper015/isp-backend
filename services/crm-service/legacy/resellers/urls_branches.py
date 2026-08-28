from django.urls import path

from . import views

urlpatterns = [
    path("", views.BranchListCreateView.as_view()),
    path("<int:branch_id>", views.BranchDetailView.as_view()),
]
