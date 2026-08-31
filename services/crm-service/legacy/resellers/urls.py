from django.urls import path

from . import views

urlpatterns = [
    path("", views.FranchiseListCreateView.as_view()),
    path("<int:franchise_id>", views.FranchiseDetailView.as_view()),
]
