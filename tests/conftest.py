"""Pytest fixtures for the Eversolo integration tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom integration in every test (required by HA test harness)."""
    yield
