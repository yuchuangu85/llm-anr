from __future__ import annotations

from pathlib import Path
import io
import tarfile
import tempfile
import unittest
import zipfile

from anr_evidence.ai_context import AiContextOptions, build_ai_context_artifacts_from_input_path
from anr_evidence.dumpsys_input_window import parse_dumpsys_input_window
from anr_evidence.sources.shared.detection import detect_source_kind

INPUT_MATCHED = """Input Dispatcher State:
  FocusedWindow: Window{a1b2c3 u0 com.demo/.MainActivity} inputChannelToken=Token{input-1} mDisplayId=0
  FocusedApplication: ActivityRecord{act-1 u0 com.demo/.MainActivity t42}
  PendingEvents: seq=17 target=Window{a1b2c3 u0 com.demo/.MainActivity}
"""
WINDOW_MATCHED = """WINDOW MANAGER WINDOWS (dumpsys window windows)
  mCurrentFocus=Window{a1b2c3 u0 com.demo/.MainActivity}
  mFocusedApp=ActivityRecord{act-1 u0 com.demo/.MainActivity t42}
  Window #0 Window{a1b2c3 u0 com.demo/.MainActivity}:
    mDisplayId=0 rootTaskId=1
    mSession=Session{abcd 1234:u0a123}
    mInputChannelToken=Token{input-1}
    mActivityRecord=ActivityRecord{act-1 u0 com.demo/.MainActivity t42}
    mHasSurface=true mViewVisibility=0x0
"""


class DumpsysInputWindowAdapterTests(unittest.TestCase):
    def test_exact_token_display_and_window_identity_create_observation(self) -> None:
        result = parse_dumpsys_input_window(
            dumpsys_input=INPUT_MATCHED,
            dumpsys_window=WINDOW_MATCHED,
        )

        self.assertEqual(result["schemaVersion"], "dumpsys-input-window-adapter-v1")
        self.assertEqual(result["input"]["status"], "observed")
        self.assertEqual(result["input"]["queue"]["status"], "pending")
        self.assertEqual(result["window"]["status"], "observed")
        self.assertTrue(result["window"]["windows"][0]["visible"])
        self.assertEqual(result["transfer"], {
            "status": "matched",
            "inputLineIndex": 1,
            "windowLineIndex": 3,
            "windowId": "a1b2c3",
            "inputChannelToken": "Token{input-1}",
            "displayId": "0",
            "pid": "1234",
            "processName": "com.demo",
        })
        self.assertIn("dumpsys_capture_time_unavailable", result["gaps"])
        self.assertFalse(result["finalJudgment"])

    def test_token_or_display_mismatch_rejects_transfer_without_repair(self) -> None:
        result = parse_dumpsys_input_window(
            dumpsys_input=INPUT_MATCHED.replace("mDisplayId=0", "mDisplayId=1"),
            dumpsys_window=WINDOW_MATCHED,
        )

        self.assertEqual(result["transfer"]["status"], "rejected")
        self.assertIn("input_window_transfer_rejected", result["gaps"])

    def test_missing_explicit_transfer_fields_remain_unknown(self) -> None:
        result = parse_dumpsys_input_window(
            dumpsys_input="""Input Dispatcher State:
  FocusedWindow: Window{a1b2c3 u0 com.demo/.MainActivity}
""",
            dumpsys_window=WINDOW_MATCHED,
        )

        self.assertEqual(result["transfer"]["status"], "unknown")
        self.assertIn("input_window_transfer_unavailable", result["gaps"])

    def test_unrecognised_content_is_unsupported_not_parsed_as_a_dump(self) -> None:
        result = parse_dumpsys_input_window(
            dumpsys_input="FocusedWindow: Window{a1b2c3 u0 com.demo/.MainActivity}",
            dumpsys_window="mCurrentFocus=Window{a1b2c3 u0 com.demo/.MainActivity}",
        )

        self.assertEqual(result["input"]["status"], "unsupported")
        self.assertEqual(result["window"]["status"], "unsupported")
        self.assertIn("input_dispatcher_header_missing", result["gaps"])
        self.assertIn("window_manager_header_missing", result["gaps"])

    def test_default_directory_route_delivers_snapshot_to_existing_lifecycle_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "bugreport"
            (root / "events").mkdir(parents=True)
            (root / "logs").mkdir()
            (root / "dumpsys").mkdir()
            (root / "events" / "events.txt").write_text(
                "04-12 10:00:05.000 1000 1000 I am_anr  : [0,1234,com.demo,1,no focused window]\n",
                encoding="utf-8",
            )
            (root / "logs" / "logcat.txt").write_text(
                "04-12 10:00:04.900 E InputDispatcher: Application does not have a focused window for com.demo pid=1234\n",
                encoding="utf-8",
            )
            (root / "dumpsys" / "input.txt").write_text(INPUT_MATCHED, encoding="utf-8")
            (root / "dumpsys" / "window.txt").write_text(WINDOW_MATCHED, encoding="utf-8")
            output = root / "anr_ai_context"

            index = build_ai_context_artifacts_from_input_path(
                root,
                AiContextOptions(out_dir=output, package_name="com.demo", anr_type="no_focus_window"),
            )
            analysis = (output / index["groups"][0]["id"] / "anr_analysis.md").read_text(encoding="utf-8")

        self.assertEqual(index["inputRouting"]["mode"], "store_backed_default")
        self.assertIn("Dumpsys snapshot: input=`observed`, window=`observed`, queue=`pending`, token/display transfer=`matched`, blame transfer=`unqualified`", analysis)
        self.assertIn("cannot establish blame", analysis)

    def test_default_zip_route_keeps_path_selected_dumpsys_candidates_for_strict_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive_path = root / "bugreport.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("events/events.txt", "04-12 10:00:05.000 1000 1000 I am_anr  : [0,1234,com.demo,1,no focused window]\n")
                archive.writestr("logs/logcat.txt", "04-12 10:00:04.900 E InputDispatcher: Application does not have a focused window for com.demo pid=1234\n")
                archive.writestr("dumpsys/input.txt", INPUT_MATCHED)
                archive.writestr("dumpsys/window.txt", WINDOW_MATCHED)
            output = root / "context"

            index = build_ai_context_artifacts_from_input_path(
                archive_path,
                AiContextOptions(out_dir=output, package_name="com.demo", anr_type="no_focus_window"),
            )
            analysis = (output / index["groups"][0]["id"] / "anr_analysis.md").read_text(encoding="utf-8")

        self.assertEqual(index["inputRouting"]["inputKind"], "archive")
        self.assertIn("Dumpsys snapshot: input=`observed`, window=`observed`, queue=`pending`, token/display transfer=`matched`, blame transfer=`unqualified`", analysis)

    def test_default_tar_route_keeps_path_selected_dumpsys_candidates_for_strict_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive_path = root / "bugreport.tar"
            with tarfile.open(archive_path, "w") as archive:
                for name, content in {
                    "events/events.txt": "04-12 10:00:05.000 1000 1000 I am_anr  : [0,1234,com.demo,1,no focused window]\n",
                    "logs/logcat.txt": "04-12 10:00:04.900 E InputDispatcher: Application does not have a focused window for com.demo pid=1234\n",
                    "dumpsys/input.txt": INPUT_MATCHED,
                    "dumpsys/window.txt": WINDOW_MATCHED,
                }.items():
                    encoded = content.encode("utf-8")
                    member = tarfile.TarInfo(name)
                    member.size = len(encoded)
                    archive.addfile(member, io.BytesIO(encoded))
            output = root / "context"

            index = build_ai_context_artifacts_from_input_path(
                archive_path,
                AiContextOptions(out_dir=output, package_name="com.demo", anr_type="no_focus_window"),
            )
            analysis = (output / index["groups"][0]["id"] / "anr_analysis.md").read_text(encoding="utf-8")

        self.assertEqual(index["inputRouting"]["inputKind"], "archive")
        self.assertIn("Dumpsys snapshot: input=`observed`, window=`observed`, queue=`pending`, token/display transfer=`matched`, blame transfer=`unqualified`", analysis)

    def test_detector_requires_dump_structure_not_filename_alone(self) -> None:
        self.assertEqual(detect_source_kind(Path("dumpsys/input.txt"), INPUT_MATCHED), "dumpsys_input")
        self.assertEqual(detect_source_kind(Path("dumpsys/window.txt"), WINDOW_MATCHED), "dumpsys_window")
        self.assertIsNone(detect_source_kind(Path("dumpsys/input.txt"), "ordinary text"))


if __name__ == "__main__":
    unittest.main()
