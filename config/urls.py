from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.utils import timezone
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from customers.views import CustomerListCreateView
from leads.views import LeadListCreateView
from payments.views import PaymentListCreateView
from plans.views import PlanListCreateView
from subscribers.views import SubscriberListCreateView


def health(_request):
    return JsonResponse(
        {
            "status": "ok",
            "service": "isp-management-platform",
            "timestamp": timezone.now().isoformat(),
        }
    )


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health", health),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("internal/aaa/", include("aaa.urls")),
    path("api/v1/auth/", include("accounts.urls")),
    # Node/Express routing doesn't distinguish "/api/v1/customers" from
    # "/api/v1/customers/" (non-strict routing). Django does, and unlike GET,
    # a POST/PATCH to the no-slash form can't be transparently redirected
    # (APPEND_SLASH drops the body) - so both exact forms are registered here
    # for every bare collection endpoint, matching the Node reference exactly.
    path("api/v1/customers", CustomerListCreateView.as_view()),
    path("api/v1/customers/", include("customers.urls")),
    path("api/v1/subscribers", SubscriberListCreateView.as_view()),
    path("api/v1/subscribers/", include("subscribers.urls")),
    path("api/v1/plans", PlanListCreateView.as_view()),
    path("api/v1/plans/", include("plans.urls")),
    path("api/v1/", include("aaa.admin_urls")),
    path("api/v1/billing/", include("billing.urls")),
    path("api/v1/payments", PaymentListCreateView.as_view()),
    path("api/v1/payments/", include("payments.urls")),
    path("api/v1/dashboard/", include("dashboard.urls")),
    path("api/v1/network/", include("network.urls")),
    path("api/v1/leads", LeadListCreateView.as_view()),
    path("api/v1/leads/", include("leads.urls")),
]
