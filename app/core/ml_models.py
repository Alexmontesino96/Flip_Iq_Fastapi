"""Carga y gestión de modelos ML locales (ONNX Runtime).

Los modelos se cargan una vez en startup y se mantienen en memoria.
Si los archivos no existen, los getters retornan None y el pipeline
cae al fallback (LLM o regex).
"""

import json
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_comp_relevance_session = None
_comp_relevance_pipeline = None
_condition_session = None
_condition_pipeline = None
_brand_model_catalog: dict | None = None


def load_models() -> None:
    """Carga modelos ONNX y catálogo brand/model desde disco.

    Llamado una vez desde lifespan() en main.py.
    Silencioso si los archivos no existen.
    """
    global _comp_relevance_session, _comp_relevance_pipeline
    global _condition_session, _condition_pipeline
    global _brand_model_catalog

    models_dir = Path(settings.ml_models_dir)

    # Comp relevance model
    if settings.ml_comp_relevance_enabled:
        _comp_relevance_session, _comp_relevance_pipeline = _load_onnx_model(
            models_dir / "comp_relevance",
            "comp_relevance",
        )

    # Condition classifier
    if settings.ml_condition_enabled:
        _condition_session, _condition_pipeline = _load_onnx_model(
            models_dir / "condition",
            "condition",
        )

    # Brand/model catalog (siempre intentar cargar)
    catalog_path = models_dir / "brand_model_catalog.json"
    if catalog_path.exists():
        try:
            _brand_model_catalog = json.loads(catalog_path.read_text())
            logger.info(
                "Brand/model catalog loaded: %d brands",
                len(_brand_model_catalog.get("brands", {})),
            )
        except Exception as e:
            logger.warning("Failed to load brand/model catalog: %s", e)
            _brand_model_catalog = None


def _load_onnx_model(model_dir: Path, name: str):
    """Carga un modelo ONNX + su pipeline de features (pickle)."""
    onnx_path = model_dir / "model.onnx"
    pipeline_path = model_dir / "pipeline.pkl"

    if not onnx_path.exists():
        logger.info("ML model %s not found at %s, skipping", name, onnx_path)
        return None, None

    try:
        import onnxruntime as ort
        import pickle

        session = ort.InferenceSession(
            str(onnx_path),
            providers=["CPUExecutionProvider"],
        )

        pipeline = None
        if pipeline_path.exists():
            with open(pipeline_path, "rb") as f:
                pipeline = pickle.load(f)

        logger.info("ML model %s loaded from %s", name, onnx_path)
        return session, pipeline

    except ImportError:
        logger.warning("onnxruntime not installed, ML model %s disabled", name)
        return None, None
    except Exception as e:
        logger.warning("Failed to load ML model %s: %s", name, e)
        return None, None


def get_comp_relevance_model():
    """Retorna (onnx_session, feature_pipeline) o (None, None)."""
    if not settings.ml_comp_relevance_enabled:
        return None, None
    return _comp_relevance_session, _comp_relevance_pipeline


def get_condition_model():
    """Retorna (onnx_session, feature_pipeline) o (None, None)."""
    if not settings.ml_condition_enabled:
        return None, None
    return _condition_session, _condition_pipeline


def get_brand_model_catalog() -> dict | None:
    """Retorna el catálogo brand/model o None."""
    return _brand_model_catalog
