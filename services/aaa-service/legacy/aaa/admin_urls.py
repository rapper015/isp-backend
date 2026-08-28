from django.urls import path

from . import admin_views

urlpatterns = [
    path("sessions/active", admin_views.ActiveSessionListView.as_view()),
    path("sessions/<str:session_id>/disconnect", admin_views.SessionDisconnectView.as_view()),
    path("sessions/<str:session_id>", admin_views.SessionDetailView.as_view()),
    path("accounting/records", admin_views.AccountingRecordListView.as_view()),
    path("accounting/records/<int:record_id>", admin_views.AccountingRecordDetailView.as_view()),
]
