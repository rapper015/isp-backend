from django.urls import path

from . import import_views as views

urlpatterns=[
    path("",views.ImportListView.as_view()), path("validate/",views.ImportValidateView.as_view()),
    path("<uuid:import_id>/",views.ImportDetailView.as_view()), path("<uuid:import_id>/commit/",views.ImportCommitView.as_view()),
    path("<uuid:import_id>/rows/",views.ImportRowsView.as_view()), path("<uuid:import_id>/errors/download/",views.ImportErrorsDownloadView.as_view()),
    path("<uuid:import_id>/retry/",views.ImportRetryView.as_view()), path("<uuid:import_id>/cancel/",views.ImportCancelView.as_view()),
]
