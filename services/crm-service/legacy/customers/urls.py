from django.urls import path

from . import views

urlpatterns = [
    path("", views.CustomerListCreateView.as_view()),
    path("<int:customer_id>", views.CustomerDetailView.as_view()),
]
