"""Tests para feedback de usuario y productos no encontrados."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.analysis import (
    FeedbackRequest,
    FeedbackResponse,
    FlaggedItem,
    NotFoundItem,
)


# ---------------------------------------------------------------------------
# FeedbackRequest validation
# ---------------------------------------------------------------------------

class TestFeedbackRequestValidation:
    def test_valid_feedback_types(self):
        for ft in ("incorrect_price", "incorrect_recommendation", "outdated", "missing_data", "other"):
            req = FeedbackRequest(feedback_type=ft)
            assert req.feedback_type == ft

    def test_invalid_feedback_type_rejected(self):
        with pytest.raises(ValidationError, match="feedback_type must be one of"):
            FeedbackRequest(feedback_type="invalid_type")

    def test_comment_optional(self):
        req = FeedbackRequest(feedback_type="other")
        assert req.comment is None

    def test_comment_included(self):
        req = FeedbackRequest(
            feedback_type="incorrect_price",
            comment="El precio real fue $150, no $170",
            actual_sale_price=150.0,
        )
        assert req.comment == "El precio real fue $150, no $170"
        assert req.actual_sale_price == 150.0

    def test_actual_sale_price_optional(self):
        req = FeedbackRequest(feedback_type="outdated")
        assert req.actual_sale_price is None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class TestFeedbackResponse:
    def test_feedback_response_creation(self):
        resp = FeedbackResponse(
            id=1,
            analysis_id=42,
            feedback_type="incorrect_price",
            comment="Precio incorrecto",
            actual_sale_price=150.0,
            created_at=datetime.now(timezone.utc),
        )
        assert resp.analysis_id == 42
        assert resp.feedback_type == "incorrect_price"


class TestNotFoundItem:
    def test_not_found_item_creation(self):
        item = NotFoundItem(
            id=1,
            product_title="Rare Vintage Helmet",
            barcode="123456789",
            keyword="vintage helmet",
            marketplace="ebay",
            cost_price=50.0,
            created_at=datetime.now(timezone.utc),
        )
        assert item.product_title == "Rare Vintage Helmet"
        assert item.barcode == "123456789"

    def test_not_found_item_nullable_fields(self):
        item = NotFoundItem(
            id=2,
            product_title="Unknown Product",
            barcode=None,
            keyword=None,
            marketplace="amazon_fba",
            cost_price=25.0,
            created_at=datetime.now(timezone.utc),
        )
        assert item.barcode is None
        assert item.keyword is None


class TestFlaggedItem:
    def test_flagged_item_creation(self):
        item = FlaggedItem(
            analysis_id=42,
            product_title="Test Helmet",
            marketplace="ebay",
            recommendation="buy",
            flip_score=75,
            net_profit=30.0,
            feedback_type="incorrect_recommendation",
            comment="Debería ser watch, el producto no se vende tan rápido",
            actual_sale_price=None,
            flagged_at=datetime.now(timezone.utc),
        )
        assert item.analysis_id == 42
        assert item.feedback_type == "incorrect_recommendation"

    def test_flagged_item_with_actual_price(self):
        item = FlaggedItem(
            analysis_id=10,
            product_title="Sneakers",
            marketplace="ebay",
            recommendation="buy",
            flip_score=80,
            net_profit=50.0,
            feedback_type="incorrect_price",
            comment="Vendí por $90, no $130",
            actual_sale_price=90.0,
            flagged_at=datetime.now(timezone.utc),
        )
        assert item.actual_sale_price == 90.0


# ---------------------------------------------------------------------------
# Model: AnalysisFeedback
# ---------------------------------------------------------------------------

class TestAnalysisFeedbackModel:
    def test_model_import(self):
        from app.models.analysis import AnalysisFeedback
        assert AnalysisFeedback.__tablename__ == "analysis_feedbacks"

    def test_analysis_has_no_comps_found(self):
        from app.models.analysis import Analysis
        # Verificar que la columna existe en el modelo
        assert hasattr(Analysis, "no_comps_found")

    def test_analysis_has_feedbacks_relationship(self):
        from app.models.analysis import Analysis
        assert hasattr(Analysis, "feedbacks")
