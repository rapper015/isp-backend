from django.urls import path
from . import nas_views as views

urlpatterns=[
 path("test-connection/",views.TestConnectionView.as_view()),path("discover/",views.DiscoverView.as_view()),path("",views.NasListCreateView.as_view()),
 path("<uuid:nas_id>/",views.NasDetailView.as_view()),path("<uuid:nas_id>/test-connection/",views.StoredTestView.as_view()),path("<uuid:nas_id>/sync/",views.SyncView.as_view()),
 path("<uuid:nas_id>/configure-radius/",views.ConfigureRadiusView.as_view()),path("<uuid:nas_id>/configuration-preview/",views.RadiusPreviewView.as_view()),path("<uuid:nas_id>/health/",views.CachedHealthView.as_view()),
 path("<uuid:nas_id>/interfaces/",views.InterfacesView.as_view()),path("<uuid:nas_id>/ip-addresses/",views.IpAddressesView.as_view()),path("<uuid:nas_id>/radius/",views.RadiusView.as_view()),
 path("<uuid:nas_id>/pppoe-servers/",views.PppoeView.as_view()),path("<uuid:nas_id>/hotspot-servers/",views.HotspotView.as_view()),path("<uuid:nas_id>/ip-pools/",views.PoolsView.as_view()),
 path("<uuid:nas_id>/active-sessions/",views.ActiveSessionsView.as_view()),path("<uuid:nas_id>/disconnect-session/",views.DisconnectView.as_view()),path("<uuid:nas_id>/audit-logs/",views.AuditView.as_view()),
]
