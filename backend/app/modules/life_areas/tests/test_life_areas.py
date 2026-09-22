from fastapi.testclient import TestClient


def test_list_life_areas_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/life-areas").status_code == 401


def test_list_life_areas_returns_the_migration_seeded_rows(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/v1/life-areas")
    assert response.status_code == 200
    slugs = {area["slug"] for area in response.json()}
    assert slugs == {"work", "personal", "learning", "career", "projects", "ideas", "teaching"}
