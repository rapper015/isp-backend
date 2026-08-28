from django.urls import path

from . import views

urlpatterns = [
    path("<int:document_id>", views.KycDocumentDetailView.as_view()),
    path("<int:document_id>/verify", views.KycDocumentVerifyView.as_view()),
    path("<int:document_id>/reject", views.KycDocumentRejectView.as_view()),
    path("<int:document_id>/file", views.KycDocumentFileView.as_view()),
]
