import httpx


def TestClient(application):
    """Exercise the actual Django WSGI handler, including middleware and streams."""
    return httpx.Client(transport=httpx.WSGITransport(app=application), base_url='http://testserver')
