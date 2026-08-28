from fastapi.testclient import TestClient
from app.main import app


def test_private_metrics_and_correlation_header_are_available_without_sensitive_labels():
    headers = {"X-AAA-Service-Key": "test-internal-key", "X-Correlation-Id": "metric-correlation"}
    with TestClient(app) as client:
        response = client.get("/health", headers=headers)
        assert response.headers["X-Correlation-Id"] == "metric-correlation"
        metrics = client.get("/internal/radius/v1/metrics", headers=headers)
        assert metrics.status_code == 200
        assert "aaa_http_2xx_total" in metrics.json()["metrics"]
        assert "username" not in str(metrics.json()).lower()
