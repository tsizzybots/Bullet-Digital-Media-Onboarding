"""Webhook ingestion package (PandaDoc and future providers)."""

from bullet_api.webhooks.pandadoc import router as pandadoc_router

__all__ = ["pandadoc_router"]
