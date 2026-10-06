"""Staging and CORS settings used for Vercel handover deploys."""

import os

import pytest

from app.config import ConfigurationError, Settings


def test_staging_allows_demo_mode_without_debug(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("DEMO_MODE", "true")
    settings = Settings.from_environment()
    assert settings.app_env == "staging"
    assert settings.demo_mode is True
    assert settings.debug is False


def test_staging_rejects_debug(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DEBUG", "true")
    with pytest.raises(ConfigurationError, match="DEBUG"):
        Settings.from_environment()


def test_cors_origins_parsed(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://a.test, http://localhost:3000 ")
    settings = Settings.from_environment()
    assert settings.cors_allow_origins == ("https://a.test", "http://localhost:3000")
