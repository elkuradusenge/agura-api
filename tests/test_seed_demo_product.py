"""The demo seed exists so the farmer flow can be walked. It must still obey the rule."""

from __future__ import annotations

from app.models.enums import ProductStatus
from app.services import product_service
from scripts.seed_demo_product import DEMO_NOTE, seed_demo_product
from scripts.seed_product import seed_mushroom_product


def test_demo_seed_publishes_a_complete_product(db, seeded_food_items):
    product = seed_demo_product(db)

    assert product.status is ProductStatus.TRACKABLE
    assert product.version == 1
    assert product_service.trackable_blockers(product) == []
    assert product.published_at is not None


def test_demo_seed_is_idempotent(db, seeded_food_items):
    first = seed_demo_product(db)
    second = seed_demo_product(db)

    assert first.id == second.id
    assert second.status is ProductStatus.TRACKABLE


def test_demo_seed_leaves_the_real_mushroom_a_draft(db, seeded_food_items):
    seed_demo_product(db)
    mushroom = seed_mushroom_product(db)

    assert mushroom.status is ProductStatus.DRAFT


def test_every_demo_health_statement_names_itself(db, seeded_food_items):
    product = seed_demo_product(db)

    assert DEMO_NOTE in (product.conditions_diseases or "")
    assert DEMO_NOTE in (product.how_not_to_use or "")
    for nutrient in product.nutrients:
        assert nutrient.source == DEMO_NOTE
