import httpx

from app.parsers.reducto import ReductoParserAdapter


def test_reducto_url_result_is_fetched_and_normalized():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/parse":
            assert request.headers["authorization"] == "Bearer secret"
            return httpx.Response(
                200,
                json={
                    "job_id": "job-123",
                    "result": {"type": "url", "url": "https://results.example/result.json"},
                },
            )
        return httpx.Response(
            200,
            json={
                "chunks": [
                    {
                        "type": "text",
                        "content": "Applicant name",
                        "page": 1,
                        "bounding_box": [10, 20, 100, 30],
                        "coordinate_system": "pdf-top-left",
                    }
                ]
            },
        )

    adapter = ReductoParserAdapter("secret", "https://api.reducto.ai/v1")
    adapter.client = httpx.Client(
        base_url="https://api.reducto.ai/v1",
        headers={"Authorization": "Bearer secret"},
        transport=httpx.MockTransport(handler),
    )
    parsed = adapter.parse("form.pdf", b"pdf")
    assert parsed.provider_job_id == "job-123"
    assert parsed.blocks == [
        {
            "type": "text",
            "text": "Applicant name",
            "source": {
                "page": 1,
                "rect": [10, 20, 100, 30],
                "coordinate_system": "pdf-top-left",
            },
        }
    ]
