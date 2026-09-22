import httpx

from app.parsers.reducto import ReductoParserAdapter


def test_reducto_async_response_uses_v3_contract_and_normalizes_exact_geometry():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/upload":
            return httpx.Response(
                200,
                json={
                    "file_id": "reducto://file-1",
                    "presigned_url": "https://storage.reducto.test/file-1",
                },
            )
        assert request.url.path == "/parse"
        body = request.content.decode()
        assert "reducto://file-1" in body
        assert "force_url_result" in body
        assert "return_ocr_data" in body
        assert '"async"' in body
        return httpx.Response(
            200,
            json={"job_id": "job-1"},
        )

    def poll_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/job/job-1"
        return httpx.Response(
            200,
            json={
                "status": "Completed",
                "result": {
                    "response_type": "parse",
                    "job_id": "job-1",
                    "result": {
                        "type": "full",
                        "ocr": {
                            "words": [
                                {
                                    "text": "No",
                                    "confidence": 0.99,
                                    "bbox": {"left": 0.2, "top": 0.25, "width": 0.04, "height": 0.03, "page": 1},
                                }
                            ],
                            "lines": [],
                        },
                        "chunks": [
                            {
                                "content": "Given name: No given name",
                                "blocks": [
                                    {
                                        "type": "Key Value",
                                        "content": "Given name: No given name",
                                        "confidence": "high",
                                        "bbox": {
                                            "left": 0.1,
                                            "top": 0.2,
                                            "width": 0.2,
                                            "height": 0.2,
                                            "page": 1,
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                },
            },
        )

    def upload_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://storage.reducto.test/file-1"
        assert request.headers.get("authorization") is None
        assert request.content == b"pdf"
        return httpx.Response(200)

    adapter = ReductoParserAdapter("secret", "https://platform.reducto.test")
    def api_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/job/job-1":
            return poll_handler(request)
        return handler(request)

    adapter.client = httpx.Client(
        transport=httpx.MockTransport(api_handler), base_url="https://platform.reducto.test"
    )
    adapter.upload_client = httpx.Client(transport=httpx.MockTransport(upload_handler))
    job_id = adapter.submit("source.pdf", b"pdf")
    parsed = adapter.poll(job_id)
    assert parsed is not None
    assert parsed.blocks[0]["source"]["rect"] == [0.1, 0.2, 0.30000000000000004, 0.4]
    assert parsed.blocks[0]["source"]["page"] == 1
    assert parsed.blocks[0]["ocr_confidence"] == 0.98
    assert parsed.blocks[0]["ocr_words"][0]["text"] == "No"


def test_reducto_url_response_requires_https():
    adapter = ReductoParserAdapter("secret", "https://platform.reducto.test")
    adapter._submitted["job-2"] = {
        "job_id": "job-2",
        "result": {"type": "url", "url": "http://untrusted.test/result.json"},
    }
    try:
        adapter.poll("job-2")
    except RuntimeError as error:
        assert "HTTPS" in str(error)
    else:
        raise AssertionError("Expected insecure result URL to be rejected")


def test_reducto_url_result_does_not_forward_authorization_header():
    adapter = ReductoParserAdapter("secret", "https://platform.reducto.test")
    adapter._submitted["job-3"] = {
        "status": "Completed",
        "result": {
            "response_type": "parse",
            "result": {"type": "url", "url": "https://storage.reducto.test/result.json"},
        },
    }

    def download_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") is None
        return httpx.Response(200, json={"type": "full", "chunks": []})

    adapter.upload_client = httpx.Client(transport=httpx.MockTransport(download_handler))
    parsed = adapter.poll("job-3")
    assert parsed is not None
    assert parsed.blocks == []
