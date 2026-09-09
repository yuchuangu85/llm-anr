"""Reference-preserving redaction and explicit release-admission regressions."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from urllib.error import HTTPError, URLError

from anr_evidence.evidence_slice import EvidenceSlice
from anr_evidence.redaction import (
    RedactedOutboundAdmission,
    RedactionError,
    build_reference_preserving_redacted_view,
    redacted_slices_for,
    validate_redacted_outbound_admission,
    validate_redacted_view_against_slices,
)


def _slice(
    content: str,
    *,
    group_id: str = "group-a",
    source: str = "logcat",
    line_index: int = 0,
    span_id: str = "span-a",
) -> EvidenceSlice:
    return EvidenceSlice(
        source=source,
        timestamp_iso="2026-04-12T10:00:00.000",
        delta_t_seconds=0.0,
        tag="am_anr",
        content=content,
        importance="critical",
        group_id=group_id,
        line_index=line_index,
        evidence_ref={
            "spanId": span_id,
            "groupId": group_id,
            "artifactId": "artifact-a",
            "sourceKind": "logcat" if source == "anr_manager" else source,
            "sha256": "0" * 64,
            "startByte": 0,
            "endByte": len(content.encode("utf-8")),
            "coordinateSpace": "artifact_byte_offset",
        },
    )


def _admission(view, *, expiry: datetime | None = None, recipient_id: str = "openai:test"):
    return RedactedOutboundAdmission(
        purpose_id="anr-triage-2026-09",
        recipient_id=recipient_id,
        approval_id="approval-123",
        approved_by="privacy-reviewer",
        ruleset_version=view.ruleset_version,
        expires_at=(expiry or (datetime.now(timezone.utc) + timedelta(days=1))).isoformat(),
        view_commitment=view.view_commitment,
        map_commitment=view.map_commitment,
        authorization_token="test-signed-capability-v1",
    )


def _verifier_for(expected: RedactedOutboundAdmission):
    """Deterministic test double; not evidence of a production approval service."""
    def verifier(candidate, view, recipient_id, now):
        return (
            candidate == expected
            and recipient_id == expected.recipient_id
            and view.view_commitment == expected.view_commitment
            and view.map_commitment == expected.map_commitment
            and now.tzinfo is not None
        )
    return verifier


class ReferencePreservingRedactionTests(unittest.TestCase):
    def test_preserves_coordinates_and_scopes_tokens_per_group(self) -> None:
        raw = [
            _slice("pkg=com.example.app email=alice@example.test pid=1234", span_id="span-1"),
            _slice("again com.example.app email=alice@example.test", line_index=1, span_id="span-2"),
            _slice("again com.example.app email=alice@example.test", group_id="group-b", span_id="span-3"),
        ]
        view = build_reference_preserving_redacted_view(raw, view_id="review-view")
        first, second, third = view.redacted_slices

        self.assertEqual(first.group_id, raw[0].group_id)
        self.assertEqual(first.line_index, raw[0].line_index)
        self.assertEqual(first.evidence_ref["spanId"], "span-1")
        self.assertNotIn("com.example.app", first.content)
        self.assertNotIn("alice@example.test", first.content)
        self.assertIn("[[R", first.content)

        replacements = {item.token: item for item in view.replacement_map}
        package_token = next(item.token for item in replacements.values()
                             if item.original_value == "com.example.app" and item.occurrences[0].group_id == "group-a")
        email_token = next(item.token for item in replacements.values()
                           if item.original_value == "alice@example.test" and item.occurrences[0].group_id == "group-a")
        self.assertIn(package_token, first.content)
        self.assertIn(package_token, second.content)
        self.assertIn(email_token, first.content)
        self.assertIn(email_token, second.content)
        self.assertNotIn(package_token, third.content)
        self.assertNotIn(email_token, third.content)
        self.assertNotEqual(first.content, raw[0].content)
        validate_redacted_view_against_slices(view, raw)
        self.assertEqual(redacted_slices_for(view, [raw[1]])[0], second)

    def test_map_and_audit_metadata_never_render_original_values(self) -> None:
        raw_value = "alice@example.test"
        view = build_reference_preserving_redacted_view([_slice(f"contact={raw_value}")], view_id="review-view")
        self.assertNotIn(raw_value, repr(view))
        self.assertNotIn(raw_value, json.dumps(view.audit_metadata(), sort_keys=True))
        replacement = view.replacement_map[0]
        self.assertNotIn(raw_value, repr(replacement))
        self.assertNotIn(raw_value, json.dumps({
            "token": replacement.token,
            "category": replacement.category,
            "hash": replacement.original_sha256,
            "occurrences": [occurrence.__dict__ for occurrence in replacement.occurrences],
        }, sort_keys=True))

    def test_requires_store_verified_references_and_rejects_mutated_view(self) -> None:
        unbound = replace(_slice("alice@example.test"), evidence_ref=None)
        with self.assertRaisesRegex(RedactionError, "verified store references"):
            build_reference_preserving_redacted_view([unbound])

        raw = [_slice("alice@example.test")]
        view = build_reference_preserving_redacted_view(raw, view_id="review-view")
        forged = replace(view, redacted_slices=(replace(view.redacted_slices[0], content="safe"),))
        with self.assertRaisesRegex(RedactionError, "commitment mismatch"):
            validate_redacted_view_against_slices(forged, raw)

    def test_admission_binds_exact_view_recipient_and_expiry(self) -> None:
        view = build_reference_preserving_redacted_view([_slice("alice@example.test")], view_id="review-view")
        admission = _admission(view)
        validate_redacted_outbound_admission(
            view, admission, recipient_id="openai:test", verifier=_verifier_for(admission),
        )
        with self.assertRaisesRegex(RedactionError, "recipient"):
            validate_redacted_outbound_admission(
                view, admission, recipient_id="anthropic:test", verifier=_verifier_for(admission),
            )
        with self.assertRaisesRegex(RedactionError, "expired"):
            validate_redacted_outbound_admission(
                view,
                _admission(view, expiry=datetime.now(timezone.utc) - timedelta(seconds=1)),
                recipient_id="openai:test", verifier=_verifier_for(admission),
            )
        with self.assertRaisesRegex(RedactionError, "view commitment"):
            validate_redacted_outbound_admission(
                view,
                replace(admission, view_commitment="0" * 64),
                recipient_id="openai:test", verifier=_verifier_for(admission),
            )

    def test_private_admission_requires_a_real_verifier_not_just_fields(self) -> None:
        view = build_reference_preserving_redacted_view([_slice("alice@example.test")], view_id="review-view")
        admission = _admission(view)
        with self.assertRaisesRegex(RedactionError, "independent"):
            validate_redacted_outbound_admission(
                view, admission, recipient_id="openai:test", verifier=None,
            )
        with self.assertRaisesRegex(RedactionError, "independently verified"):
            validate_redacted_outbound_admission(
                view, admission, recipient_id="openai:test", verifier=lambda *_: False,
            )

    def test_adversarial_direct_identifier_patterns_are_tokenized_without_replacement_leaks(self) -> None:
        raw_values = (
            "Bearer secret-token-123",
            "api_key=top-secret-456",
            "alice@example.test",
            "https://private.example.test/a?token=xyz",
            "10.0.0.42",
            "2001:db8:0:1::42",
            "aa:bb:cc:dd:ee:ff",
            "/data/user/0/com.private.app/files/account-42",
            r"C:\private\account-42.txt",
            "com.private.customer",
            "deviceId=PRIVATE-DEVICE-42",
            "phone=+8613800138000",
            "pid 1234",
            "sysTid=5678",
            "uid: 10042",
        )
        raw = _slice(" | ".join(raw_values), source="trace")
        view = build_reference_preserving_redacted_view([raw], view_id="adversarial-view")
        self.assertGreaterEqual(len(view.replacement_map), 12)
        self.assertIn("[[R", view.redacted_slices[0].content)
        for raw_value in raw_values:
            self.assertNotIn(raw_value, view.redacted_slices[0].content)
            self.assertNotIn(raw_value, json.dumps(view.audit_metadata(), sort_keys=True))
        validate_redacted_view_against_slices(view, [raw])

    def test_unclassified_free_text_cannot_self_authorize_private_outbound(self) -> None:
        raw = [_slice("opaque free-form customer narrative QX-448 (not a safety certificate)")]
        view = build_reference_preserving_redacted_view(raw, view_id="free-text-view")
        self.assertEqual(view.replacement_map, ())
        # No detector match grants nothing: an admission is still mandatory.
        with self.assertRaisesRegex(RedactionError, "RedactedOutboundAdmission"):
            validate_redacted_outbound_admission(
                view, None, recipient_id="openai:test", verifier=lambda *_: True,
            )  # type: ignore[arg-type]

    def test_existing_raw_slice_is_unchanged_by_view_construction(self) -> None:
        raw = _slice("Cmd line: com.example.app email=alice@example.test", source="trace")
        before = raw.content
        view = build_reference_preserving_redacted_view([raw], view_id="review-view")
        self.assertEqual(raw.content, before)
        self.assertEqual(raw.evidence_ref["spanId"], view.redacted_slices[0].evidence_ref["spanId"])
        self.assertNotEqual(raw.content, view.redacted_slices[0].content)


if __name__ == "__main__":
    unittest.main()

class RedactedPrivateClientBoundaryTests(unittest.TestCase):
    def test_redacted_private_policy_string_cannot_bypass_admission(self) -> None:
        from unittest.mock import patch
        from anr_evidence.ai_agent import LlmClient, LlmRequestError, ProviderConfig, ProviderKind

        client = LlmClient(ProviderConfig(
            kind=ProviderKind.OPENAI, model="test", api_key="test-key",
            outbound_policy="redacted_private",
        ))
        with patch("urllib.request.urlopen") as http:
            with self.assertRaisesRegex(LlmRequestError, "denied") as caught:
                client._post_json("https://example.test", {"canary": "raw-private-value"}, {})
        http.assert_not_called()
        self.assertNotIn("raw-private-value", str(caught.exception))

    def test_synthetic_only_remains_caller_attestation_not_private_admission(self) -> None:
        from unittest.mock import MagicMock, patch
        from anr_evidence.ai_agent import AgentMessage, LlmClient, ProviderConfig, ProviderKind

        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode("utf-8")
        client = LlmClient(ProviderConfig(
            kind=ProviderKind.OPENAI, model="test", api_key="test-key",
            outbound_policy="synthetic_only",
        ))
        # This deliberately has no known-sensitive marker.  The successful call
        # proves only the caller's synthetic declaration is honored; it is not
        # automatic certification that a real log is safe to send.
        with patch("urllib.request.urlopen", return_value=response) as http:
            client.complete([AgentMessage("user", "synthetic fixture opaque-case-QX447")])
        self.assertEqual(http.call_count, 1)
        payload = http.call_args.args[0].data.decode("utf-8")
        self.assertIn("opaque-case-QX447", payload)
        self.assertNotIn("redacted_private", payload)


class RedactedPrivateOutboundIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        import base64
        from pathlib import Path
        import tempfile

        from anr_evidence import AiContextOptions, build_ai_context, prepare_redacted_outbound_view
        from anr_evidence.anr_strategy import strategy_for_package
        from anr_evidence.context_flooding import TruncationConfig, truncate_evidence
        from anr_evidence.evidence_slice import annotate_slices_with_tags, attach_store_references, build_evidence_slices
        from anr_evidence.evidence_store import EvidenceStore
        from anr_evidence.evidence_store_export import export_evidence_store

        def captured(path: str, content: str) -> dict[str, object]:
            return {
                "path": path,
                "content": content,
                "originalArtifacts": [{
                    "path": path,
                    "originalBase64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                    "origin": {"type": "file", "path": f"/private/{path}"},
                }],
            }

        self.package = {
            "package_id": "private-review-fixture",
            "provided_type": "no_focus_window",
            "sources": {
                "trace": captured("trace.txt", "\n".join([
                    "----- pid 1234 at 2026-04-12 10:00:05.100000000+0000 -----",
                    "Cmd line: com.private.customer",
                    '"main" prio=5 tid=1 Native',
                    "  native: #00 pc 0 /system/lib/libbinder.so (android::IPCThreadState::waitForResponse+8)",
                ])),
                "event_log": captured("events.txt", "04-12 10:00:05.000 1234 1234 I am_anr  : [0,1234,com.private.customer,1,Input dispatching timed out]"),
                "logcat": captured("logcat.txt", "04-12 10:00:06.000 1234 1234 W App: account=alice@example.test deviceId=ABC-PRIVATE-123"),
            },
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.groups = build_ai_context(self.package, AiContextOptions()).groups
        self.exported = export_evidence_store(self.package, self.groups, Path(self.tmp.name))
        self.store = EvidenceStore(self.exported["path"])
        self.addCleanup(self.store.close)
        strategy = strategy_for_package(self.package, None)
        slices = annotate_slices_with_tags(
            build_evidence_slices(self.groups),
            event_tags=frozenset(strategy.event_tags),
            logcat_patterns=frozenset(strategy.logcat_patterns),
        )
        slices = attach_store_references(slices, self.store, self.exported["groups"])
        self.raw_slices = truncate_evidence(slices, TruncationConfig()).retained_slices
        self.view = prepare_redacted_outbound_view(
            self.package,
            evidence_store=self.store,
            evidence_references=self.exported["groups"],
            truncation=TruncationConfig(),
            view_id="approved-private-view",
        )
        validate_redacted_view_against_slices(self.view, self.raw_slices)
        self.admission = _admission(self.view)
        self.verifier = _verifier_for(self.admission)

    @staticmethod
    def _response(text: str):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode("utf-8")
        return response

    @staticmethod
    def _report(*, re_probe: dict[str, object] | None = None) -> dict[str, object]:
        return {
            "candidateChains": [], "candidateConclusions": [], "remediationDrafts": [],
            "globalLimitations": ["Private evidence remains tentative"],
            "reProbeRequest": re_probe,
            "finalJudgment": False, "notRootCauseYet": True,
            "requiresHumanConfirmation": True,
        }

    def _config(self):
        from anr_evidence.ai_agent import ProviderConfig, ProviderKind
        return ProviderConfig(
            kind=ProviderKind.OPENAI,
            model="test",
            api_key="test-key",
            outbound_policy="redacted_private",
        )

    def _run(self, *, view=None, admission=None, verifier=None, manager_report=None):
        from unittest.mock import patch
        from anr_evidence.ai_agent import AgentConfig, run_ai_agent_analysis

        cfg = self._config()
        replies = [self._response("{}") for _ in range(3)]
        replies.append(self._response(json.dumps(manager_report or self._report())))
        with patch("anr_evidence.ai_agent._open_private_no_redirect", side_effect=replies) as http:
            result = run_ai_agent_analysis(
                self.package,
                provider_config=cfg,
                agent_config=AgentConfig(provider=cfg, max_iterations=1),
                evidence_store=self.store,
                evidence_references=self.exported["groups"],
                redacted_view=view if view is not None else self.view,
                redacted_outbound_admission=admission if admission is not None else self.admission,
                redacted_admission_verifier=verifier if verifier is not None else self.verifier,
            )
        return result, http

    def test_all_provider_bodies_use_same_redacted_view_and_no_raw_map_or_entity_map(self) -> None:
        result, http = self._run()
        self.assertEqual(http.call_count, 4)
        self.assertEqual(result.errors, [])
        self.assertIs(result.redacted_view, self.view)
        raw_values = tuple(item.original_value for item in self.view.replacement_map)
        request_texts = [call.args[0].data.decode("utf-8") for call in http.call_args_list]
        for request_text in request_texts:
            self.assertIn("[[R", request_text)
            self.assertNotIn("## Entity Map", request_text)
            for raw_value in raw_values:
                self.assertNotIn(raw_value, request_text)
        manager_payload = json.loads(request_texts[-1])
        manager_body = "\n".join(message["content"] for message in manager_payload["messages"])
        catalog = manager_body.split("## Current scoped citation catalog (untrusted evidence)\n", 1)[1]
        catalog_rows = [json.loads(line) for line in catalog.splitlines() if line.startswith("{")]
        catalog_by_span = {row["spanId"]: row for row in catalog_rows}
        for redacted_slice in self.view.redacted_slices:
            row = catalog_by_span[redacted_slice.evidence_ref["spanId"]]
            self.assertEqual(row["content"], redacted_slice.content)
            self.assertEqual(row["groupId"], redacted_slice.group_id)
            self.assertEqual(row["sourceKind"], redacted_slice.evidence_ref["sourceKind"])
        self.assertNotIn("com.private.customer", json.dumps(result.outbound_admission_audit, sort_keys=True))
        self.assertNotIn("replacementMap", json.dumps(result.outbound_admission_audit, sort_keys=True))
        self.assertNotIn(self.admission.authorization_token, json.dumps(result.outbound_admission_audit, sort_keys=True))
        for call in http.call_args_list:
            self.assertNotIn("approval-123", json.dumps(call.args[0].headers, sort_keys=True))
        # The local immutable source remains available for local reference validation.
        trace_ref = next(
            slice_.evidence_ref for slice_ in self.raw_slices
            if slice_.source == "trace" and "com.private.customer" in slice_.content
        )
        original = self.store.read_span(
            trace_ref["spanId"], group_id=trace_ref["groupId"], source_kind="trace",
            max_bytes=65536,
            expected_artifact_commitment=trace_ref.get("artifactCommitment"),
        )
        self.assertIn(b"com.private.customer", original["content"])

    def test_missing_or_forged_private_admission_issues_zero_http(self) -> None:
        from unittest.mock import patch
        from anr_evidence.ai_agent import AgentConfig, run_ai_agent_analysis

        cfg = self._config()
        with patch("anr_evidence.ai_agent._open_private_no_redirect") as http:
            with self.assertRaisesRegex(ValueError, "redacted_view"):
                run_ai_agent_analysis(
                    self.package, provider_config=cfg,
                    agent_config=AgentConfig(provider=cfg, max_iterations=1),
                    evidence_store=self.store, evidence_references=self.exported["groups"],
                )
        http.assert_not_called()

        forged = replace(self.view, view_commitment="0" * 64)
        result, http = self._run(view=forged)
        http.assert_not_called()
        self.assertTrue(any("admission rejected" in error for error in result.errors))

        expired = _admission(self.view, expiry=datetime.now(timezone.utc) - timedelta(seconds=1))
        result, http = self._run(admission=expired)
        http.assert_not_called()
        self.assertTrue(any("admission rejected" in error for error in result.errors))

    def _authorized_client(self, *, verifier=None, max_retries: int = 2):
        from anr_evidence.ai_agent import LlmClient

        client = LlmClient(replace(self._config(), max_retries=max_retries))
        client.authorize_redacted_outbound(
            self.view,
            self.admission,
            verifier=verifier if verifier is not None else self.verifier,
        )
        return client

    def test_missing_or_altered_verifier_authorization_issues_zero_http(self) -> None:
        from unittest.mock import patch
        from anr_evidence.ai_agent import AgentConfig, run_ai_agent_analysis

        cfg = self._config()
        with patch("anr_evidence.ai_agent._open_private_no_redirect") as http:
            with self.assertRaisesRegex(ValueError, "redacted_admission_verifier"):
                run_ai_agent_analysis(
                    self.package,
                    provider_config=cfg,
                    agent_config=AgentConfig(provider=cfg, max_iterations=1),
                    evidence_store=self.store,
                    evidence_references=self.exported["groups"],
                    redacted_view=self.view,
                    redacted_outbound_admission=self.admission,
                )
        http.assert_not_called()

        for field, value in (
            ("purpose_id", "other-purpose"),
            ("approved_by", "forged-reviewer"),
            ("authorization_token", "forged-capability"),
        ):
            with self.subTest(field=field):
                result, http = self._run(admission=replace(self.admission, **{field: value}))
                http.assert_not_called()
                self.assertTrue(any("admission rejected" in error for error in result.errors))

        result, http = self._run(verifier=lambda *_: False)
        http.assert_not_called()
        self.assertTrue(any("admission rejected" in error for error in result.errors))

    def test_private_client_cannot_reuse_view_authorization_for_arbitrary_messages(self) -> None:
        from unittest.mock import patch
        from anr_evidence.ai_agent import AgentMessage, LlmRequestError

        client = self._authorized_client()
        with patch("anr_evidence.ai_agent._open_private_no_redirect") as http:
            with self.assertRaisesRegex(LlmRequestError, "exact redacted request permit"):
                client.complete([AgentMessage("user", "opaque-private-B outside reviewed view")])
        http.assert_not_called()

    def test_private_endpoint_is_fixed_and_redirects_do_not_forward(self) -> None:
        from unittest.mock import patch
        from anr_evidence.ai_agent import AgentMessage, LlmRequestError, _PrivateNoRedirectHandler

        client = self._authorized_client()
        messages = [AgentMessage("user", "review [[R:group-a:package:1]]")]
        normalized = client._normalize_messages(messages)
        permit = client._permit_redacted_messages(messages)
        body = {
            "model": "test",
            "max_tokens": client._config.max_tokens,
            "temperature": client._config.temperature,
            "messages": [{"role": item.role, "content": item.content} for item in normalized],
        }
        client._active_redacted_request_permit = permit
        try:
            with patch("anr_evidence.ai_agent._open_private_no_redirect") as http:
                with self.assertRaisesRegex(LlmRequestError, "endpoint"):
                    client._post_json("https://third-party.invalid/collect", body, {})
            http.assert_not_called()
        finally:
            client._active_redacted_request_permit = None

        handler = _PrivateNoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://third-party.invalid/collect"))
        redirect = HTTPError(
            "https://api.openai.com/v1/chat/completions", 302, "Found", hdrs=None, fp=None,
        )
        with patch("anr_evidence.ai_agent._open_private_no_redirect", side_effect=redirect) as http:
            with self.assertRaisesRegex(LlmRequestError, "HTTP 302"):
                client.complete_with_tokens(
                    messages,
                    _redacted_request_permit=client._permit_redacted_messages(messages),
                )
        self.assertEqual(http.call_count, 1)
        redirect.close()

    def test_private_no_redirect_transport_never_forwards_a_post_body(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from urllib.request import Request
        from anr_evidence.ai_agent import _open_private_no_redirect

        observed = {"redirect": 0, "target": 0}

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - stdlib handler contract
                if self.path == "/redirect":
                    observed["redirect"] += 1
                    self.send_response(307)
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/target")
                    self.end_headers()
                    return
                observed["target"] += 1
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/redirect",
                data=b"redacted-private-body",
                method="POST",
            )
            with self.assertRaises(HTTPError) as caught:
                _open_private_no_redirect(request, timeout=1.0)
            self.assertEqual(caught.exception.code, 307)
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)
        self.assertEqual(observed, {"redirect": 1, "target": 0})

    def test_private_retry_reverifies_admission_before_a_second_http_attempt(self) -> None:
        from unittest.mock import patch
        from anr_evidence.ai_agent import AgentMessage, LlmRequestError

        verifier_calls: list[int] = []
        def revocable_verifier(*_args):
            verifier_calls.append(1)
            # One authorization validation and one pre-first-attempt validation
            # pass; the retry is stopped before it opens a second connection.
            return len(verifier_calls) <= 2

        client = self._authorized_client(verifier=revocable_verifier, max_retries=1)
        messages = [AgentMessage("user", "review [[R:group-a:package:1]]")]
        with patch("anr_evidence.ai_agent._open_private_no_redirect", side_effect=URLError("temporary")) as http, \
                patch("anr_evidence.ai_agent.time.sleep"):
            with self.assertRaisesRegex(LlmRequestError, "admission is no longer valid"):
                client.complete_with_tokens(
                    messages,
                    _redacted_request_permit=client._permit_redacted_messages(messages),
                )
        self.assertEqual(http.call_count, 1)
        self.assertEqual(len(verifier_calls), 3)

    def test_reprobe_never_expands_a_private_approval(self) -> None:
        re_probe = {
            "beforeSeconds": 2,
            "afterSeconds": 2,
            "sourceKinds": ["event_log"],
            "additionalTags": [],
            "reason": "Need another window",
        }
        result, http = self._run(manager_report=self._report(re_probe=re_probe))
        self.assertEqual(http.call_count, 4)
        self.assertEqual(result.integrated_report, {})
        self.assertEqual(result.probe_outcomes[-1].status, "redacted_private_reprobe_requires_new_admission")
        self.assertTrue(any("newly prepared redacted view" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
