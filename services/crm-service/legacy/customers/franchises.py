from django.db import transaction

from resellers.models import Franchise as SystemFranchise

from .models import Franchise as TenantFranchise


@transaction.atomic
def resolve_franchise(franchise_id):
    """Resolve a public reseller franchise ID to the legacy tenant model.

    Imports and NAS records predate the reseller module and retain foreign keys
    to customers.Franchise. This bridge keeps those relationships stable while
    accepting the canonical IDs exposed by /api/v1/franchises.
    """
    system_franchise = SystemFranchise.objects.filter(
        id=franchise_id, deleted_at__isnull=True
    ).first()
    if system_franchise is not None:
        tenant = TenantFranchise.objects.filter(
            reseller_franchise=system_franchise
        ).first()
        if tenant is not None:
            if tenant.name != system_franchise.name:
                tenant.name = system_franchise.name
                tenant.save(update_fields=("name", "normalized_name", "updated_at"))
            return tenant

        tenant = TenantFranchise.objects.filter(
            normalized_name=system_franchise.name.strip().casefold(),
            reseller_franchise__isnull=True,
        ).first()
        if tenant is None:
            tenant = TenantFranchise(name=system_franchise.name)
        tenant.reseller_franchise = system_franchise
        tenant.save()
        return tenant

    # Backward compatibility for installations that have not created reseller
    # records yet. New frontend integrations should always send the public ID.
    return TenantFranchise.objects.filter(id=franchise_id).first()


def public_franchise_id(franchise):
    if franchise is None:
        return None
    return franchise.reseller_franchise_id or franchise.id
