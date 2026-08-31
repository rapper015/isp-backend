import ipaddress as ip_module

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView
from aaa.exceptions import AppError
from common.casing import normalize_keys

from .models import IPAddress, IPPool, VlanPool
from .serializers import IPAddressSerializer, IPPoolSerializer, VlanPoolSerializer


class IPPoolListCreateView(AdminAPIView):
    def get(self, request):
        pools = IPPool.objects.filter(deleted_at__isnull=True).order_by("-created_at")
        status_param = request.query_params.get("status")
        if status_param:
            pools = pools.filter(status=status_param)
        return Response(IPPoolSerializer(pools, many=True).data)

    def post(self, request):
        serializer = IPPoolSerializer(data=normalize_keys(request.data))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=HTTP_201_CREATED)


class IPPoolDetailView(AdminAPIView):
    def _get(self, pool_id):
        pool = IPPool.objects.filter(id=pool_id, deleted_at__isnull=True).first()
        if pool is None:
            raise AppError("IP pool not found", 404)
        return pool

    def get(self, request, pool_id):
        return Response(IPPoolSerializer(self._get(pool_id)).data)

    def patch(self, request, pool_id):
        pool = self._get(pool_id)
        serializer = IPPoolSerializer(pool, data=normalize_keys(request.data), partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pool_id):
        pool = self._get(pool_id)
        pool.deleted_at = timezone.now()
        pool.save(update_fields=["deleted_at"])
        return Response(status=HTTP_204_NO_CONTENT)


class IPPoolPopulateView(AdminAPIView):
    """Bulk-creates IPAddress inventory rows for a pool from a CIDR or address range."""

    def post(self, request, pool_id):
        pool = IPPool.objects.filter(id=pool_id, deleted_at__isnull=True).first()
        if pool is None:
            raise AppError("IP pool not found", 404)

        data = normalize_keys(request.data)
        cidr = data.get("cidr")
        start_address = data.get("start_address")
        end_address = data.get("end_address")

        if cidr:
            try:
                network = ip_module.ip_network(cidr, strict=False)
            except ValueError:
                raise AppError("Validation failed", 400, {"invalidFields": ["cidr"]})
            candidates = [str(host) for host in network.hosts()]
        elif start_address and end_address:
            try:
                start = ip_module.ip_address(start_address)
                end = ip_module.ip_address(end_address)
            except ValueError:
                raise AppError(
                    "Validation failed", 400, {"invalidFields": ["startAddress", "endAddress"]}
                )
            if int(end) < int(start):
                raise AppError("endAddress must not be before startAddress", 400)
            candidates = [
                str(ip_module.ip_address(value)) for value in range(int(start), int(end) + 1)
            ]
        else:
            raise AppError(
                "Validation failed", 400, {"missingFields": ["cidr or startAddress/endAddress"]}
            )

        existing = set(
            IPAddress.objects.filter(address__in=candidates).values_list("address", flat=True)
        )
        created = IPAddress.objects.bulk_create(
            [IPAddress(pool=pool, address=addr) for addr in candidates if addr not in existing]
        )
        return Response({"created": len(created), "skipped": len(candidates) - len(created)})


class IPAddressListView(AdminAPIView):
    def get(self, request):
        addresses = IPAddress.objects.select_related("pool").order_by("address")

        pool_id = request.query_params.get("pool")
        if pool_id:
            addresses = addresses.filter(pool_id=pool_id)

        status_param = request.query_params.get("status")
        if status_param:
            addresses = addresses.filter(status=status_param)

        return Response(IPAddressSerializer(addresses, many=True).data)


class VlanPoolListCreateView(AdminAPIView):
    def get(self, request):
        vlans = VlanPool.objects.filter(deleted_at__isnull=True).order_by("vlan_id")
        status_param = request.query_params.get("status")
        if status_param:
            vlans = vlans.filter(status=status_param)
        return Response(VlanPoolSerializer(vlans, many=True).data)

    def post(self, request):
        serializer = VlanPoolSerializer(data=normalize_keys(request.data))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=HTTP_201_CREATED)


class VlanPoolDetailView(AdminAPIView):
    def _get(self, vlan_id):
        vlan = VlanPool.objects.filter(id=vlan_id, deleted_at__isnull=True).first()
        if vlan is None:
            raise AppError("VLAN pool not found", 404)
        return vlan

    def get(self, request, vlan_id):
        return Response(VlanPoolSerializer(self._get(vlan_id)).data)

    def patch(self, request, vlan_id):
        vlan = self._get(vlan_id)
        serializer = VlanPoolSerializer(vlan, data=normalize_keys(request.data), partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, vlan_id):
        vlan = self._get(vlan_id)
        vlan.deleted_at = timezone.now()
        vlan.save(update_fields=["deleted_at"])
        return Response(status=HTTP_204_NO_CONTENT)
