from django.urls import path

from . import views

urlpatterns = [
    path("authenticate", views.AuthenticateView.as_view()),
    path("authorize", views.AuthorizeView.as_view()),
    path("accounting", views.AccountingView.as_view()),
    path("post-auth", views.PostAuthView.as_view()),
    path("disconnect", views.DisconnectView.as_view()),
    path("coa", views.CoaView.as_view()),
    path("nas-client", views.NasClientLookupView.as_view()),
]
