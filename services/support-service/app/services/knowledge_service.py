"""Knowledge base and runbooks: versioned articles with tenant visibility,
approval, search and usage tracking. Knowledge suggestions never automatically
execute operational actions; they only recommend."""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ValidationError
from ..enums import KB_STATUSES, KB_VISIBILITIES
from ..models import KnowledgeArticle, KnowledgeUsage, Ticket
from ..services.audit_service import audit, correlation


def create_article(session: Session, tenant_id, *, slug: str, title: str, body: str,
                   category_id: uuid.UUID | None = None, visibility: str = "INTERNAL",
                   status: str = "DRAFT", tags: list | None = None, author: str | None = None,
                   actor: str | None = None) -> KnowledgeArticle:
    slug = slug.strip().lower().replace(" ", "-")
    visibility = visibility.upper()
    status = status.upper()
    if visibility not in KB_VISIBILITIES:
        raise ValidationError(f"invalid visibility {visibility!r}")
    if status not in KB_STATUSES:
        raise ValidationError(f"invalid status {status!r}")
    article = KnowledgeArticle(
        tenant_id=tenant_id, slug=slug, title=title.strip(), body=body,
        category_id=category_id, visibility=visibility, status=status,
        tags=tags or [], author=author or actor,
    )
    session.add(article)
    session.flush()
    audit(session, tenant_id, "support.kb.article_created", "kb_article", str(article.id), actor=actor,
          correlation_id=correlation(None), safe_after={"slug": slug, "visibility": visibility, "status": status})
    return article


def update_article(session: Session, tenant_id, article_id: uuid.UUID, *, title: str | None = None,
                   body: str | None = None, tags: list | None = None, actor: str | None = None) -> KnowledgeArticle:
    article = session.get(KnowledgeArticle, article_id)
    if article is None or article.tenant_id != tenant_id:
        raise NotFoundError("knowledge article not found")
    if body is not None:
        article.body = body
        article.version += 1
        article.status = "DRAFT"  # edits require re-approval
        article.approved_by = None
    if title:
        article.title = title
    if tags is not None:
        article.tags = tags
    session.flush()
    audit(session, tenant_id, "support.kb.article_updated", "kb_article", str(article.id), actor=actor,
          correlation_id=correlation(None), safe_after={"version": article.version, "status": article.status})
    return article


def publish_article(session: Session, tenant_id, article_id: uuid.UUID, *, actor: str | None = None) -> KnowledgeArticle:
    article = session.get(KnowledgeArticle, article_id)
    if article is None or article.tenant_id != tenant_id:
        raise NotFoundError("knowledge article not found")
    article.status = "ACTIVE"
    article.approved_by = actor
    session.flush()
    audit(session, tenant_id, "support.kb.article_published", "kb_article", str(article.id), actor=actor,
          correlation_id=correlation(None), safe_after={"status": "ACTIVE"})
    return article


def search_articles(session: Session, tenant_id, *, query: str | None = None, category_id: uuid.UUID | None = None,
                    visibility: str | None = None, status: str = "ACTIVE") -> list[KnowledgeArticle]:
    stmt = select(KnowledgeArticle).where(
        or_(KnowledgeArticle.tenant_id == tenant_id, KnowledgeArticle.tenant_id.is_(None)),
        KnowledgeArticle.status == status,
    )
    if visibility:
        stmt = stmt.where(KnowledgeArticle.visibility == visibility)
    if category_id:
        stmt = stmt.where(KnowledgeArticle.category_id == category_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(KnowledgeArticle.title.ilike(like), KnowledgeArticle.body.ilike(like),
                              KnowledgeArticle.slug.ilike(like)))
    return list(session.scalars(stmt.order_by(KnowledgeArticle.usage_count.desc())))


def suggest_for_ticket(session: Session, tenant_id, ticket: Ticket, *, limit: int = 5) -> list[dict]:
    """Suggest active internal articles matching the ticket category/type/text."""
    stmt = select(KnowledgeArticle).where(
        or_(KnowledgeArticle.tenant_id == tenant_id, KnowledgeArticle.tenant_id.is_(None)),
        KnowledgeArticle.status == "ACTIVE",
        KnowledgeArticle.visibility == "INTERNAL",
    )
    if ticket.category_id:
        stmt = stmt.where(or_(KnowledgeArticle.category_id == ticket.category_id, KnowledgeArticle.category_id.is_(None)))
    articles = list(session.scalars(stmt.limit(50)))
    subject_tokens = {w.lower() for w in ticket.subject.split() if len(w) > 3}
    scored = []
    for article in articles:
        haystack = f"{article.title} {article.slug} {' '.join(article.tags or [])}".lower()
        score = sum(1 for token in subject_tokens if token in haystack)
        scored.append((score, article))
    scored.sort(key=lambda pair: (-pair[0], -pair[1].usage_count))
    return [{"id": str(a.id), "title": a.title, "slug": a.slug, "usage_count": a.usage_count} for _, a in scored[:limit]]


def record_usage(session: Session, tenant_id, article_id: uuid.UUID, *, ticket_id: uuid.UUID | None = None,
                 used_by: str | None = None) -> None:
    article = session.get(KnowledgeArticle, article_id)
    if article is None or (article.tenant_id and article.tenant_id != tenant_id):
        raise NotFoundError("knowledge article not found")
    article.usage_count += 1
    session.add(KnowledgeUsage(tenant_id=tenant_id, article_id=article.id, ticket_id=ticket_id, used_by=used_by))
    session.flush()
