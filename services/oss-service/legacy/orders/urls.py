from django.urls import path

from . import views

urlpatterns = [
    path("orders", views.OrderListCreateView.as_view()),
    path("orders/<int:order_id>", views.OrderDetailView.as_view()),
    path("orders/<int:order_id>/events", views.OrderEventListView.as_view()),
    path("orders/<int:order_id>/activate", views.OrderActivateView.as_view()),
    path("orders/<int:order_id>/cancel", views.OrderCancelView.as_view()),
]
