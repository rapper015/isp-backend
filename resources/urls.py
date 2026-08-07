from django.urls import path

from . import views

urlpatterns = [
    path("ip-pools", views.IPPoolListCreateView.as_view()),
    path("ip-pools/<int:pool_id>", views.IPPoolDetailView.as_view()),
    path("ip-pools/<int:pool_id>/populate", views.IPPoolPopulateView.as_view()),
    path("ip-addresses", views.IPAddressListView.as_view()),
    path("vlan-pools", views.VlanPoolListCreateView.as_view()),
    path("vlan-pools/<int:vlan_id>", views.VlanPoolDetailView.as_view()),
]
