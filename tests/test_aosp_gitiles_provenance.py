from __future__ import annotations

import base64
import hashlib
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from anr_evidence.aosp_contract.gitiles import (
    GitilesProvenanceError,
    fetch_source_record,
    immutable_file_url,
    revision_from_tag_json,
    tag_ref_url,
)

REPOSITORY = "platform/frameworks/native"
TAG = "android-15.0.0_r1"
REVISION = "f727a40b31f2bbf05670680ff8a887cafd2943bf"
PATH = "services/inputflinger/dispatcher/InputDispatcher.cpp"
SYMBOL = "InputDispatcher::onAnrLocked"


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *unused: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class GitilesProvenanceTests(unittest.TestCase):
    def test_fetches_tag_then_immutable_raw_bytes_and_hashes_decoded_content(self) -> None:
        source = b"// exact upstream bytes\r\nvoid onAnrLocked();\r\n"
        tag_payload = b")]}'\n" + json.dumps({"id": REVISION}).encode()
        encoded = base64.b64encode(source)
        with patch(
            "anr_evidence.aosp_contract.gitiles.urlopen",
            side_effect=[_Response(tag_payload), _Response(encoded)],
        ) as request:
            record = fetch_source_record(
                release_tag=TAG,
                repository=REPOSITORY,
                path=PATH,
                symbol=SYMBOL,
            )

        expected_url = immutable_file_url(REPOSITORY, REVISION, PATH)
        self.assertEqual(record.url, expected_url)
        self.assertEqual(record.revision, REVISION)
        self.assertEqual(record.sha256, hashlib.sha256(source).hexdigest())
        self.assertEqual(record.byte_length, len(source))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0].full_url, tag_ref_url(REPOSITORY, TAG))
        self.assertEqual(request.call_args_list[1].args[0].full_url, expected_url + "?format=TEXT")

    def test_parses_peeled_ref_and_rejects_conflicting_representations(self) -> None:
        ref = {f"refs/tags/{TAG}": {"value": "a" * 40, "peeled": REVISION}}
        self.assertEqual(revision_from_tag_json(json.dumps(ref).encode(), TAG), REVISION)
        with self.assertRaisesRegex(GitilesProvenanceError, "conflicting"):
            revision_from_tag_json(json.dumps({"id": REVISION, f"refs/tags/{TAG}": {"value": "0" * 40}}).encode(), TAG)

    def test_rejects_invalid_inputs_before_network_access(self) -> None:
        invalid = [
            {"repository": "https://evil.example/a"},
            {"release_tag": "refs/tags/android-15.0.0_r1"},
            {"path": "../InputDispatcher.cpp"},
            {"symbol": "\x00"},
        ]
        for overrides in invalid:
            values = dict(release_tag=TAG, repository=REPOSITORY, path=PATH, symbol=SYMBOL)
            values.update(overrides)
            with self.subTest(overrides=overrides), patch("anr_evidence.aosp_contract.gitiles.urlopen") as request:
                with self.assertRaises(GitilesProvenanceError):
                    fetch_source_record(**values)
                request.assert_not_called()

    def test_rejects_malformed_or_missing_or_nonimmutable_tag_revision(self) -> None:
        bad_payloads = [
            b"not-json",
            json.dumps({}).encode(),
            json.dumps({"id": "not-a-commit"}).encode(),
            json.dumps({"id": "A" * 40}).encode(),
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload), self.assertRaises(GitilesProvenanceError):
                revision_from_tag_json(payload, TAG)

    def test_rejects_invalid_base64_and_network_errors_without_partial_record(self) -> None:
        tag_payload = json.dumps({"commit": REVISION}).encode()
        with patch(
            "anr_evidence.aosp_contract.gitiles.urlopen",
            side_effect=[_Response(tag_payload), _Response(b"this is not base64!")],
        ):
            with self.assertRaisesRegex(GitilesProvenanceError, "Base64"):
                fetch_source_record(release_tag=TAG, repository=REPOSITORY, path=PATH, symbol=SYMBOL)
        with patch("anr_evidence.aosp_contract.gitiles.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(GitilesProvenanceError, "request failed"):
                fetch_source_record(release_tag=TAG, repository=REPOSITORY, path=PATH, symbol=SYMBOL)


if __name__ == "__main__":
    unittest.main()
