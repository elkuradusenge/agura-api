"""Admin editable message templates with variables such as {product} and {time}."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import Language
from app.models.farming import MessageTemplate

DEFAULT_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "key": "task.reminder.morning",
        "in_app": "Good morning. Today: {task} for {product}. Due in the morning.",
        "sms": "AGUra: {task} for {product} this morning. Reply when done.",
        "variables": ["task", "product"],
    },
    {
        "key": "task.reminder.evening",
        "in_app": "Good evening. Today: {task} for {product}. Due in the evening.",
        "sms": "AGUra: {task} for {product} this evening. Reply when done.",
        "variables": ["task", "product"],
    },
    {
        "key": "task.missed",
        "in_app": "You missed a task yesterday: {task} for {product}. Do it today if you can.",
        "sms": "AGUra: missed {task} for {product}. Please catch up today.",
        "variables": ["task", "product"],
    },
    {
        "key": "product.published",
        "in_app": "A new product is available to plant: {product}.",
        "sms": None,
        "variables": ["product"],
    },
    {
        "key": "analysis.updated",
        "in_app": "New census analysis is available. Your province figures were refreshed.",
        "sms": None,
        "variables": [],
    },
    {
        "key": "specialist.request.updated",
        "in_app": "Your specialist request is now {status}.",
        "sms": "AGUra: your specialist request is now {status}.",
        "variables": ["status"],
    },
    {
        "key": "funding.interest.updated",
        "in_app": "Your funding interest is now {status}.",
        "sms": "AGUra: your funding interest is now {status}.",
        "variables": ["status"],
    },
)

DRAFT_SUFFIX = {
    Language.RW: " [TODO: human review of Kinyarwanda]",
    Language.FR: " [TODO: human review of French]",
}


def render(template: str, params: dict[str, Any]) -> str:
    """Fill {placeholders}. Unknown placeholders are left alone, never crash a send."""
    rendered = template
    for key, value in params.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def get_template(db: Session, key: str, language: str) -> MessageTemplate | None:
    return db.scalar(
        select(MessageTemplate).where(
            MessageTemplate.key == key, MessageTemplate.language == language
        )
    )


def body_for(
    db: Session, key: str, language: str, params: dict[str, Any], *, sms: bool = False
) -> str | None:
    """The rendered text, falling back to the English template."""
    template = get_template(db, key, language) or get_template(db, key, Language.EN.value)
    if template is None:
        return None
    source = template.sms_body if sms else template.body
    if not source:
        return None
    return render(source, params)


def seed_templates(db: Session, *, overwrite: bool = False) -> int:
    written = 0
    for language in Language:
        for template in DEFAULT_TEMPLATES:
            existing = get_template(db, template["key"], language.value)
            if existing is not None and not overwrite:
                continue
            body = template["in_app"]
            sms_body = template["sms"]
            if language is not Language.EN:
                # A first draft only. The TODO marker makes the review step explicit.
                body = body + DRAFT_SUFFIX[language]
                if sms_body is not None:
                    sms_body = sms_body + DRAFT_SUFFIX[language]
            if existing is None:
                db.add(
                    MessageTemplate(
                        key=template["key"],
                        language=language.value,
                        body=body,
                        sms_body=sms_body,
                        variables=list(template["variables"]),
                    )
                )
            else:
                existing.body = body
                existing.sms_body = sms_body
                existing.variables = list(template["variables"])
            written += 1
    db.commit()
    return written
