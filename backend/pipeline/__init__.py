"""Pipeline modules for the Q&A assistant."""

from .accuracy import check_accuracy
from .claims import extract_claims
from .embeddings import embed_question
from .generation import generate_answer
from .retrieval import collapse_sources, retrieve_documents
from .verification import verify_claims

__all__ = [
    "check_accuracy",
    "collapse_sources",
    "embed_question",
    "extract_claims",
    "generate_answer",
    "retrieve_documents",
    "verify_claims",
]
