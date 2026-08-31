from django.urls import path

from . import views

urlpatterns = [
    path("", views.PaymentListCreateView.as_view()),
    path("<int:payment_id>", views.PaymentDetailView.as_view()),
]
