def test_home_page(client):
    response = client.get('/')
    assert response.status_code == 200
    assert "Hello, Flask!" in response.get_json()["message"]

def test_greet_name(client):
    response = client.get('/greet/World')
    assert response.status_code == 200
    assert "Hello, World!" in response.get_json()["message"]

def test_greet_invalid_route(client):
    response = client.get('/nonexistent_route')
    assert response.status_code == 404
