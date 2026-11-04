"""Shared mock API transport; tests never contact the configured LLM."""

import json

import httpx


def transport_with(outputs, seen=None):
    responses = iter(outputs)

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "test-model"}]})
        if seen is not None:
            seen.append(json.loads(request.content))
        output = next(responses)
        content = json.dumps(output, ensure_ascii=False) if not isinstance(output, str) else output
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        )

    return httpx.MockTransport(handler)
