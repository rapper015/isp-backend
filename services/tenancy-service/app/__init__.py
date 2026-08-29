"""Tenancy Service (Milestone 8) — Franchise & Multi-Tenant Management.

Central control plane for tenant registry/lifecycle, tenant configuration,
organization hierarchy, partners/franchises, scoped RBAC, commissions,
settlements and tenant-aware reporting. Strict isolation: tenant-owned data is
only reachable through a validated TenantContext; missing context fails closed.
"""
