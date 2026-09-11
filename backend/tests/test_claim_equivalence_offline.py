"""
Pure-logic half of `app.services.claim_equivalence` -- `classify_claim_relation`
needs no database. Proves the honest-abstention discipline: no client, an
API error, or a malformed/out-of-vocabulary response all degrade to
{"relation": "unknown", "confidence": 0.0} -- never a fabricated relation.
"""
import json

from app.services.claim_equivalence import RELATIONS, classify_claim_relation


def test_no_client_abstains():
    result = classify_claim_relation("A", "B", client=None)
    assert result == {"relation": "unknown", "confidence": 0.0}


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})]


class _FakeClient:
    def __init__(self, content: str):
        self._content = content
        self.chat = type("Chat", (), {})()
        self.chat.completions = type("Completions", (), {})()
        self.chat.completions.create = self._create

    def _create(self, **kwargs):
        return _FakeResponse(self._content)


def test_valid_response_is_classified():
    client = _FakeClient(json.dumps({"relation": "equivalent", "confidence": 0.92}))
    result = classify_claim_relation("Retries help.", "Retrying improves outcomes.", client=client)
    assert result == {"relation": "equivalent", "confidence": 0.92}


def test_confidence_is_clamped_to_0_1():
    client = _FakeClient(json.dumps({"relation": "contradicts", "confidence": 5.0}))
    result = classify_claim_relation("A", "B", client=client)
    assert result["relation"] == "contradicts"
    assert result["confidence"] == 1.0


def test_malformed_json_abstains():
    client = _FakeClient("not json at all")
    result = classify_claim_relation("A", "B", client=client)
    assert result == {"relation": "unknown", "confidence": 0.0}


def test_out_of_vocabulary_relation_abstains():
    client = _FakeClient(json.dumps({"relation": "definitely_true", "confidence": 0.9}))
    result = classify_claim_relation("A", "B", client=client)
    assert result == {"relation": "unknown", "confidence": 0.0}


def test_client_raising_abstains():
    class _RaisingClient:
        def __init__(self):
            self.chat = type("Chat", (), {})()
            self.chat.completions = type("Completions", (), {})()
            self.chat.completions.create = self._raise

        def _raise(self, **kwargs):
            raise RuntimeError("upstream boom")

    result = classify_claim_relation("A", "B", client=_RaisingClient())
    assert result == {"relation": "unknown", "confidence": 0.0}


def test_relations_vocabulary_is_the_documented_four():
    assert RELATIONS == ("equivalent", "contradicts", "related", "unrelated")
