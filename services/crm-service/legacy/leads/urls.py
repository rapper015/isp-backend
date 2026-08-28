from django.urls import path

from . import views

urlpatterns = [
    path("", views.LeadListCreateView.as_view()),
    path("<int:lead_id>", views.LeadDetailView.as_view()),
]
