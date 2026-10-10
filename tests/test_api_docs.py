import re

from fastapi.testclient import TestClient


def test_docs_and_assets_are_same_origin_and_legacy_links_work(client):
    response = client.get("/api/v1/docs")
    assert response.status_code == 200 and "SwaggerUIBundle" in response.text
    assert "/api/v1/openapi.json" in response.text
    assets = re.findall(r'(?:src|href)="([^"]+)"', response.text)
    assert assets and all(url.startswith("/api/v1/docs/assets/") for url in assets)
    for url in assets:
        asset = client.get(url)
        assert asset.status_code == 200 and asset.content
    for legacy, target in [
        ("/docs", "/api/v1/docs"),
        ("/redoc", "/api/v1/docs"),
        ("/openapi.json", "/api/v1/openapi.json"),
    ]:
        redirect = client.get(legacy, follow_redirects=False)
        assert redirect.status_code == 307 and redirect.headers["location"] == target
        assert client.get(legacy).status_code == 200


def test_openapi_describes_all_operations_and_actual_validation_errors(client):
    schema = client.get("/api/v1/openapi.json").json()
    operations = [operation for path in schema["paths"].values() for operation in path.values()]
    assert len(operations) == 29
    for operation in operations:
        assert operation["summary"] and operation["description"] and operation["tags"]
        assert "422" in operation["responses"]
        success = next(value for key, value in operation["responses"].items() if key.startswith("2"))
        assert success["content"]  # response schemas, not empty undocumented success responses
    create = schema["paths"]["/api/v1/analyses"]["post"]
    assert create["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith("TaskAccepted")
    assert "multipart/form-data" in schema["paths"]["/api/v1/sources/excel"]["post"]["requestBody"]["content"]
    assert schema["components"]["schemas"]["MySQLSource"]["properties"]["password"]["writeOnly"]
    assert "awaiting_confirmation" in schema["info"]["description"]
    assert not schema.get("security")  # current service does not implement API authentication
    invalid = client.post("/api/v1/sources/mysql", json={"password": "<PASSWORD_NOT_ECHOED>"})
    assert invalid.status_code == 422 and isinstance(invalid.json()["detail"], list)
    assert "<PASSWORD_NOT_ECHOED>" not in invalid.text
    assert all(set(issue) == {"loc", "type", "msg"} for issue in invalid.json()["detail"])


def test_docs_support_reverse_proxy_root_path_without_external_assets(client):
    with TestClient(client.app, root_path="/gateway") as proxied:
        page = proxied.get("/api/v1/docs")
        assert "/gateway/api/v1/openapi.json" in page.text
        assert "/gateway/api/v1/docs/assets/swagger-ui-bundle.js" in page.text
        assert proxied.get("/docs", follow_redirects=False).headers["location"] == "/gateway/api/v1/docs"
