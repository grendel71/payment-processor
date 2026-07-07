"""Tests for the /metrics Prometheus endpoint."""
from fastapi.testclient import TestClient

from app.main import app


def test_metrics_endpoint_returns_200():
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")


def test_metrics_contains_http_requests_total():
    client = TestClient(app)
    client.get("/healthz")
    response = client.get("/metrics")
    assert "http_requests" in response.text


def test_metrics_contains_payments_created_counter():
    client = TestClient(app)
    response = client.get("/metrics")
    assert "payments_created_total" in response.text
