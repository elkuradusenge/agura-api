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


# Notification titles are short labels, not editable copy, so they live here
# rather than in the database.
DEFAULT_TITLES: dict[str, dict[Language, str]] = {
    "task.reminder.morning": {
        Language.EN: "Morning task",
        Language.RW: "Umurimo w'igitondo",
        Language.FR: "Tache du matin",
    },
    "task.reminder.evening": {
        Language.EN: "Evening task",
        Language.RW: "Umurimo w'ikigoroba",
        Language.FR: "Tache du soir",
    },
    "task.missed": {
        Language.EN: "You missed a task",
        Language.RW: "Watsinzwe umurimo",
        Language.FR: "Tache manquee",
    },
    "product.published": {
        Language.EN: "New crop available",
        Language.RW: "Igihingwa gishya kibonetse",
        Language.FR: "Nouvelle culture disponible",
    },
    "analysis.updated": {
        Language.EN: "Province figures updated",
        Language.RW: "Imibare y'intara yavuguruwe",
        Language.FR: "Chiffres de la province actualises",
    },
    "specialist.request.updated": {
        Language.EN: "Specialist request update",
        Language.RW: "Ubutumwa ku busabe bw'umuhanga",
        Language.FR: "Suivi de la demande",
    },
    "funding.interest.updated": {
        Language.EN: "Funding request update",
        Language.RW: "Ubutumwa ku busabe bw'inkunga",
        Language.FR: "Suivi du financement",
    },
}


def title_for(key: str, language: str) -> str | None:
    """The short title for a notification, in the asked language."""
    titles = DEFAULT_TITLES.get(key)
    if titles is None:
        return None
    return titles.get(Language(language)) or titles[Language.EN]


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
