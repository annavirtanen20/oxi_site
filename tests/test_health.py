from unittest import mock

import pytest
from django.db import OperationalError

pytestmark = pytest.mark.django_db


def test_health_ok(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "no-cache" in response["Cache-Control"]


def test_health_reports_database_down(client):
    with mock.patch("config.health.connection.cursor", side_effect=OperationalError("down")):
        response = client.get("/health/")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"
