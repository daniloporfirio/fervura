import argparse
import contextlib
import io
import json
import logging
import logging.handlers
import os
import queue
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    import serial  # noqa: F401
except ImportError:  # pyserial missing (plain Python outside the build venv): minimal stub
    _serial = types.ModuleType("serial")

    class SerialException(OSError):
        pass

    _serial.SerialException = SerialException
    _serial.Serial = None
    _serial.EIGHTBITS, _serial.PARITY_NONE, _serial.STOPBITS_ONE = 8, "N", 1
    _tools = types.ModuleType("serial.tools")
    _list_ports = types.ModuleType("serial.tools.list_ports")
    _list_ports.comports = lambda: []
    _serial.tools, _tools.list_ports = _tools, _list_ports
    sys.modules.update({"serial": _serial, "serial.tools": _tools,
                        "serial.tools.list_ports": _list_ports})
    import serial  # noqa: F811

from fervura import app, device, settings, system
from fervura.app import READ_ERROR_STATUS, Sender, display_value
from fervura.protocol import parse_frame
from fervura.sensors import Snapshot, _pick


class DisplayValueTest(unittest.TestCase):
    def test_sources(self):
        s = Snapshot(cpu_temp=61.4, gpu_temp=72.0)
        self.assertEqual(display_value(s, "cpu"), (61.4, ""))
        self.assertEqual(display_value(s, "gpu"), (72.0, ""))
        self.assertEqual(display_value(s, "max"), (72.0, ""))

    def test_falls_back_to_other_temperature(self):
        value, note = display_value(Snapshot(gpu_temp=50.0), "cpu")
        self.assertEqual(value, 50.0)
        self.assertIn("GPU", note)
        self.assertEqual(display_value(Snapshot(), "max"), (None, ""))


class FakeHub:
    def __init__(self, snap):
        self.snap = snap
        self.closed = False

    def read(self):
        return self.snap

    def close(self):
        self.closed = True


class FailingHub(FakeHub):
    def __init__(self, error=None):
        super().__init__(None)
        self.error = error or RuntimeError("sensor read failed")

    def read(self):
        raise self.error


class FakeDisplay:
    port = "COM9"

    def __init__(self):
        self.frames = []
        self.closed = False

    def send(self, frame):
        self.frames.append(frame)
        return True

    def close(self):
        self.closed = True


class SenderTest(unittest.TestCase):
    def make(self, snap, source):
        s = Sender(FakeHub(snap), None, True, 1.0, source, queue.Queue())
        s.display = FakeDisplay()
        return s

    def test_sends_two_digit_value_in_cpu_slot(self):
        s = self.make(Snapshot(cpu_temp=40.0, gpu_temp=104.6), "max")
        s.tick()
        readings, _ = parse_frame(s.display.frames[0])
        self.assertEqual(readings.cpu_temp, 99)
        self.assertEqual(readings.gpu_temp, 0)
        update = s.updates.get_nowait()
        self.assertEqual((update.shown, update.connected), (99, True))

    def test_nothing_sent_without_temperatures(self):
        s = self.make(Snapshot(), "cpu")
        s.tick()
        self.assertEqual(s.display.frames, [])
        self.assertIsNone(s.updates.get_nowait().shown)

    def test_read_error_publishes_disconnected_update(self):
        s = self.make(Snapshot(cpu_temp=50.0), "cpu")
        s.tick()
        s.updates.get_nowait()  # connected update
        s.hub = FailingHub()
        with self.assertLogs("fervura.app", logging.ERROR):
            s.step()  # must not raise
        update = s.updates.get_nowait()
        self.assertIsNone(update.shown)
        self.assertFalse(update.connected)
        self.assertEqual(update.status, READ_ERROR_STATUS)
        self.assertEqual(update.snapshot, Snapshot())
        self.assertEqual(len(s.display.frames), 1)  # nothing sent on the failed cycle

    def test_repeated_error_logs_traceback_once(self):
        s = self.make(None, "cpu")
        s.hub = FailingHub()
        with self.assertLogs("fervura.app", logging.DEBUG) as logs:
            for _ in range(5):
                s.step()
        errors = [r for r in logs.records if r.levelno >= logging.ERROR]
        self.assertEqual(len(errors), 1)
        self.assertIsNotNone(errors[0].exc_info)
        # a different error gets its own traceback; recovery is logged once
        s.hub = FailingHub(ValueError("other"))
        with self.assertLogs("fervura.app", logging.ERROR):
            s.step()
        s.hub = FakeHub(Snapshot(cpu_temp=50.0))
        with self.assertLogs("fervura.app", logging.INFO) as logs:
            s.step()
        self.assertTrue(any("recovered" in r.getMessage() for r in logs.records))

    def test_shutdown_closes_display_and_sensors(self):
        s = Sender(FakeHub(Snapshot(cpu_temp=50.0)), None, True, 0.01, "cpu", queue.Queue())
        s.display = FakeDisplay()
        s.start()
        self.assertTrue(s.shutdown(3.0))
        self.assertTrue(s.display.closed)
        self.assertTrue(s.hub.closed)

    def test_shutdown_does_not_hang_on_stuck_thread(self):
        release = threading.Event()

        class StuckHub(FakeHub):
            def read(self):
                release.wait(10)
                return Snapshot()

        s = Sender(StuckHub(None), None, True, 0.01, "cpu", queue.Queue())
        s.start()
        try:
            with self.assertLogs("fervura.app", logging.WARNING):
                self.assertFalse(s.shutdown(0.2))
            self.assertTrue(s.daemon)
        finally:
            release.set()
            s.join(3)


class PickTest(unittest.TestCase):
    def test_zero_temperature_counts_as_unavailable(self):
        sensors = [{"type": "Temperature", "name": "Core (Tctl/Tdie)", "value": 0.0}]
        self.assertIsNone(_pick(sensors, "Temperature", ("tctl",)))
        sensors.append({"type": "Temperature", "name": "Core (Tctl/Tdie)", "value": 52.5})
        self.assertEqual(_pick(sensors, "Temperature", ("tctl",)), 52.5)


class SettingsLoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "config.json"
        for name, value in (("CONFIG_FILE", self.file), ("storage_ready", True)):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def load_bytes(self, data: bytes) -> dict:
        self.file.write_bytes(data)
        return settings.load()

    def test_deeply_nested_json_falls_back_to_default(self):
        for data in (b"[" * 100000, b'{"a":' * 100000):
            with self.subTest(data=data[:8]):
                self.assertEqual(self.load_bytes(data), {"source": "cpu"})

    def test_unknown_keys_are_dropped_and_not_saved(self):
        conf = self.load_bytes(json.dumps({"source": "gpu", "cmd": "calc.exe"}).encode())
        self.assertEqual(conf, {"source": "gpu"})
        settings.save(conf)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), {"source": "gpu"})
        settings.save({"source": "max", "extra": 1})
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), {"source": "max"})

    def test_oversized_file_falls_back_to_default(self):
        data = b'{"source": "gpu", "pad": "' + b"x" * settings.MAX_CONFIG_BYTES + b'"}'
        self.assertEqual(self.load_bytes(data), {"source": "cpu"})

    def test_storage_not_ready_reads_and_writes_nothing(self):
        self.file.write_text('{"source": "gpu"}', encoding="utf-8")
        with mock.patch.object(settings, "storage_ready", False):
            self.assertEqual(settings.load(), {"source": "cpu"})
            with self.assertLogs("fervura.settings", logging.WARNING):
                settings.save({"source": "max"})
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), {"source": "gpu"})

    @unittest.skipUnless(hasattr(os, "link"), "hard links not available")
    def test_hard_linked_config_is_not_read_nor_written(self):
        other = Path(self.tmp.name) / "other.json"
        other.write_text('{"source": "gpu"}', encoding="utf-8")
        try:
            os.link(other, self.file)
        except OSError as e:
            self.skipTest(f"hard link not supported here: {e}")
        with self.assertLogs("fervura.settings", logging.WARNING):
            self.assertEqual(settings.load(), {"source": "cpu"})
            settings.save({"source": "max"})
        self.assertEqual(other.read_text(encoding="utf-8"), '{"source": "gpu"}')

    def test_non_object_json_falls_back_to_default(self):
        for text in ("[]", "null", '"cpu"', "123", "true", '["gpu"]'):
            with self.subTest(text=text):
                self.assertEqual(self.load_bytes(text.encode()), {"source": "cpu"})

    def test_invalid_source_type_falls_back_to_default(self):
        for source in (["cpu"], {"x": 1}, 1, None, "xyz"):
            with self.subTest(source=source):
                conf = self.load_bytes(json.dumps({"source": source}).encode())
                self.assertEqual(conf["source"], "cpu")

    def test_corrupted_or_missing_file(self):
        self.assertEqual(self.load_bytes(b"{lixo"), {"source": "cpu"})
        self.assertEqual(self.load_bytes(b"\xff\xfe\x00"), {"source": "cpu"})
        self.file.unlink()
        self.assertEqual(settings.load(), {"source": "cpu"})

    def test_utf8_bom_is_accepted(self):
        self.assertEqual(self.load_bytes(b'\xef\xbb\xbf{"source":"gpu"}')["source"], "gpu")

    def test_save_and_load_roundtrip(self):
        settings.save({"source": "max"})
        self.assertEqual(settings.load(), {"source": "max"})


class SettingsMigrationTest(unittest.TestCase):
    """First run with %ProgramData%: "source" is copied from the old %APPDATA% file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "new").mkdir()
        (root / "old").mkdir()
        self.new = root / "new" / "config.json"
        self.old = root / "old" / "config.json"
        for name, value in (("CONFIG_FILE", self.new), ("LEGACY_CONFIG_FILE", self.old),
                            ("storage_ready", True)):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_migrates_only_source_and_keeps_old_file(self):
        old_bytes = b'\xef\xbb\xbf{"source": "gpu", "x": [1, 2]}'
        self.old.write_bytes(old_bytes)
        settings.migrate_legacy()
        self.assertEqual(json.loads(self.new.read_text(encoding="utf-8")), {"source": "gpu"})
        self.assertEqual(settings.load(), {"source": "gpu"})
        self.assertEqual(self.old.read_bytes(), old_bytes)  # never deleted nor changed
        self.assertEqual(sorted(p.name for p in self.old.parent.iterdir()), ["config.json"])

    def test_existing_new_file_wins(self):
        self.new.write_text('{"source": "max"}', encoding="utf-8")
        self.old.write_text('{"source": "gpu"}', encoding="utf-8")
        settings.migrate_legacy()
        self.assertEqual(settings.load(), {"source": "max"})

    def test_invalid_or_missing_old_file_writes_nothing(self):
        settings.migrate_legacy()
        self.assertFalse(self.new.exists())
        for data in (b"{lixo", b"[" * 100000, b'{"source": "xyz"}', b"[]"):
            with self.subTest(data=data[:8]):
                self.old.write_bytes(data)
                settings.migrate_legacy()
                self.assertFalse(self.new.exists())
                self.assertEqual(settings.load(), {"source": "cpu"})

    def test_write_error_does_not_stop_the_start(self):
        self.old.write_text('{"source": "gpu"}', encoding="utf-8")
        self.new.parent.rmdir()
        with self.assertLogs("fervura.settings", logging.WARNING):
            settings.migrate_legacy()
        self.assertFalse(self.new.exists())

    def test_no_migration_without_storage(self):
        self.old.write_text('{"source": "gpu"}', encoding="utf-8")
        with mock.patch.object(settings, "storage_ready", False):
            settings.migrate_legacy()
        self.assertFalse(self.new.exists())


class AutostartXmlTest(unittest.TestCase):
    """The temporary task XML lives in the protected data folder and is deleted afterwards."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.calls = []
        for target, name, value in (
                (settings, "CONFIG_FILE", self.dir / "config.json"),
                (settings, "storage_ready", True),
                (settings, "autostart_enabled", lambda: False),
                (settings, "_schtasks", self.fake_schtasks),
                (system, "current_user", lambda: "PC\\fulano"),
                (system, "self_command", lambda: (r"C:\Program Files\Fervura\Fervura.exe", []))):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_schtasks(self, *args):
        xml = None
        if "/XML" in args:
            path = Path(args[args.index("/XML") + 1])
            xml = (path, path.read_text(encoding="utf-16"))
        self.calls.append((args, xml))
        return mock.Mock(returncode=0, stdout="", stderr="")

    def test_xml_written_in_data_folder_and_removed(self):
        settings.set_autostart(True)
        args, (path, xml) = self.calls[-1]
        self.assertEqual(args[:3], ("/Create", "/TN", "Fervura"))
        self.assertEqual(path.parent, self.dir)
        self.assertIn("<Arguments>--minimized</Arguments>", xml)
        self.assertIn("HighestAvailable", xml)
        self.assertFalse(path.exists())

    def test_user_comes_from_the_token_not_the_environment(self):
        with mock.patch.dict(os.environ, {"USERNAME": "intruso", "USERDOMAIN": "OUTRO"}):
            settings.set_autostart(True)
        xml = self.calls[-1][1][1]
        self.assertEqual(xml.count("<UserId>PC\\fulano</UserId>"), 2)  # trigger and principal
        self.assertNotIn("intruso", xml)

    def test_leftover_plain_xml_is_replaced(self):
        (self.dir / "autostart-task.xml").write_text("velho", encoding="utf-8")
        settings.set_autostart(True)
        self.assertIn("<Task", self.calls[-1][1][1])

    def test_refused_without_protected_folder(self):
        with mock.patch.object(settings, "storage_ready", False):
            with self.assertRaises(OSError):
                settings.set_autostart(True)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.dir.iterdir()), [])

    @unittest.skipUnless(hasattr(os, "link"), "hard links not available")
    def test_refused_when_xml_is_hard_link(self):
        victim = self.dir / "victim.txt"
        victim.write_text("original", encoding="utf-8")
        try:
            os.link(victim, self.dir / "autostart-task.xml")
        except OSError as e:
            self.skipTest(f"hard link not supported here: {e}")
        with self.assertRaises(OSError):
            settings.set_autostart(True)
        self.assertEqual(self.calls, [])
        self.assertEqual(victim.read_text(encoding="utf-8"), "original")


class SetSourceSaveErrorTest(unittest.TestCase):
    """Data folder deleted with the program open: the source still changes for the session,
    the error goes to the log and stays in the status line until a save works."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "apagada" / "config.json"  # folder does not exist
        for name, value in (("CONFIG_FILE", self.file), ("storage_ready", True)):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = app.App.__new__(app.App)  # without Tk: only what set_source/render use
        self.app.conf = {"source": "cpu"}
        self.app.save_problem = ""
        self.app.sender = types.SimpleNamespace(source="cpu")
        self.app.source_var, self.app.status_var = mock.Mock(), mock.Mock()

    def test_save_error_is_logged_and_shown(self):
        with self.assertLogs("fervura.app", logging.WARNING):
            self.app.set_source("gpu")
        self.assertEqual(self.app.conf["source"], "gpu")
        self.assertEqual(self.app.sender.source, "gpu")
        self.assertIn(app.SAVE_ERROR_STATUS, self.app.save_problem)
        self.app.status_var.set.assert_called_with(self.app.save_problem)
        # the next status refresh keeps showing it
        text = self.app.status_text("conectado em COM3", "")
        self.assertTrue(text.startswith("conectado em COM3 — "), text)
        self.assertIn(app.SAVE_ERROR_STATUS, text)

    def test_successful_save_clears_the_error(self):
        with self.assertLogs("fervura.app", logging.WARNING):
            self.app.set_source("gpu")
        self.file.parent.mkdir()
        self.app.set_source("max")
        self.assertEqual(self.app.save_problem, "")
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), {"source": "max"})
        self.assertEqual(self.app.status_text("ok", "nota"), "ok — nota")


class WarningsTextTest(unittest.TestCase):
    def test_cpu_warning_points_to_program_data(self):
        text = app.warnings_text(Snapshot(), True)
        self.assertEqual(text, app.CPU_WARNING)
        self.assertIn("%ProgramData%\\Fervura", text)
        self.assertNotIn("APPDATA", text)
        self.assertEqual(app.warnings_text(Snapshot(cpu_temp=50.0), True), "")

    def test_storage_warning(self):
        self.assertEqual(app.warnings_text(Snapshot(cpu_temp=50.0), False), app.STORAGE_WARNING)
        both = app.warnings_text(Snapshot(), False)
        self.assertIn(app.CPU_WARNING, both)
        self.assertIn(app.STORAGE_WARNING, both)


class DataPathsTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_config_and_log_live_in_program_data(self):
        self.assertEqual(settings.CONFIG_FILE.parent, system.program_data_dir() / "Fervura")
        self.assertNotIn("roaming", str(settings.CONFIG_FILE).lower())

    def test_expand_only_system_drive(self):
        self.assertEqual(system.expand_system_drive(r"%SystemDrive%\ProgramData", "C:"),
                         r"C:\ProgramData")
        self.assertEqual(system.expand_system_drive(r"%SYSTEMDRIVE%\PD", "D:"), r"D:\PD")
        self.assertEqual(system.expand_system_drive(r"E:\Dados", "C:"), r"E:\Dados")
        for text in (r"%ProgramData%", r"%USERPROFILE%\x", r"%SystemDrive%\%x%", "50%"):
            with self.subTest(text=text):
                self.assertIsNone(system.expand_system_drive(text, "C:"))

    def test_program_data_from_profile_list(self):
        """Registry value + drive of the Windows folder; anything odd gives None (no fallback)."""
        cases = {
            r"%SystemDrive%\ProgramData": Path(r"D:\ProgramData"),
            r"E:\Dados": Path(r"E:\Dados"),
            r"%ALLUSERSPROFILE%": None, "relativo": None, r"\\servidor\share": None,
            r"C:\a\..\b": None, "": None,
        }
        with mock.patch.object(system, "system_windows_dir", return_value=Path(r"D:\Windows")):
            for value, expected in cases.items():
                with self.subTest(value=value), mock.patch.object(
                        system, "_profile_list_program_data", return_value=value):
                    self.assertEqual(system.program_data_dir(), expected)
            with mock.patch.object(system, "_profile_list_program_data", return_value=None):
                self.assertIsNone(system.program_data_dir())

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_system_folders_resolve(self):
        windows = system.system_windows_dir()
        self.assertTrue(system.is_local_absolute(str(windows)))
        self.assertTrue((windows / "System32").is_dir())
        self.assertEqual(system.fonts_dir(), windows / "Fonts")
        self.assertTrue(system.fonts_dir().is_dir())
        self.assertIsNotNone(system._profile_list_program_data())

    def test_local_absolute(self):
        for text in (r"C:\ProgramData", "c:\\"):
            self.assertTrue(system.is_local_absolute(text), text)
        for text in ("ProgramData", r"\\srv\share", r"C:\a\..\b", r"%X%\a", "C:", r"\\?\C:\x"):
            self.assertFalse(system.is_local_absolute(text), text)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_current_user_comes_from_the_token(self):
        real = system.current_user()
        self.assertIn("\\", real)
        with mock.patch.dict(os.environ, {"USERNAME": "intruso", "USERDOMAIN": "OUTRO"}):
            self.assertEqual(system.current_user(), real)
        self.assertNotIn("intruso", real.lower())


class TrayFontTest(unittest.TestCase):
    """The tray font is loaded by absolute path, never from the current directory."""

    fonts = Path(tempfile.gettempdir()).resolve() / "Fonts"

    def import_tray(self, truetype):
        image_font = types.SimpleNamespace(truetype=truetype,
                                           load_default=mock.Mock(return_value="default"))
        pil = types.ModuleType("PIL")
        pil.Image, pil.ImageDraw, pil.ImageFont = mock.Mock(), mock.Mock(), image_font
        stubs = {"pystray": types.ModuleType("pystray"), "PIL": pil}
        saved = sys.modules.pop("fervura.tray", None)
        self.addCleanup(lambda: sys.modules.__setitem__("fervura.tray", saved) if saved
                        else sys.modules.pop("fervura.tray", None))
        with mock.patch.dict(sys.modules, stubs):
            import fervura.tray as tray
        return tray, image_font

    def test_absolute_font_path(self):
        truetype = mock.Mock(return_value="font")
        with mock.patch.object(system, "fonts_dir", return_value=self.fonts):
            tray, _ = self.import_tray(truetype)
        path = truetype.call_args_list[0].args[0]
        self.assertTrue(Path(path).is_absolute(), path)
        self.assertEqual(Path(path), self.fonts / "segoeuib.ttf")
        self.assertEqual(tray._FONT, "font")

    def test_fallback_to_default_font(self):
        truetype = mock.Mock(side_effect=OSError("no font"))
        with mock.patch.object(system, "fonts_dir", return_value=self.fonts):
            tray, image_font = self.import_tray(truetype)
        tried = [Path(c.args[0]) for c in truetype.call_args_list]
        self.assertEqual(tried, [self.fonts / "segoeuib.ttf", self.fonts / "arialbd.ttf"])
        self.assertEqual(tray._FONT, "default")

    def test_without_fonts_dir_uses_default_font(self):
        truetype = mock.Mock(return_value="font")
        with mock.patch.object(system, "fonts_dir", return_value=None):
            tray, _ = self.import_tray(truetype)
        truetype.assert_not_called()  # never a bare name (current directory)
        self.assertEqual(tray._FONT, "default")

    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_fonts_dir_ignores_windir(self):
        real = system.fonts_dir()
        with mock.patch.dict(os.environ, {"WINDIR": r"D:\Win", "SystemRoot": r"D:\Win"}):
            self.assertEqual(system.fonts_dir(), real)
        with mock.patch.object(system, "system_windows_dir", return_value=None):
            self.assertIsNone(system.fonts_dir())


class ParseVersionTest(unittest.TestCase):
    def test_accepted_versions(self):
        for text in ("2", "2.0", "2.0.0", "2.0.0-beta", "2.2.0", " 2.2.0 ", "10.1", "2.0.0.1"):
            with self.subTest(text=text):
                v = system.parse_version(text)
                self.assertIsNotNone(v)
                self.assertGreaterEqual(v, system.PAWNIO_MIN_VERSION)

    def test_old_or_invalid_versions(self):
        for text in ("1.9.9", "1", "0.9", "1.99-rc"):
            with self.subTest(text=text):
                self.assertLess(system.parse_version(text), system.PAWNIO_MIN_VERSION)
        for text in ("", "abc", "v2.0", None):
            with self.subTest(text=text):
                self.assertIsNone(system.parse_version(text))

    def test_padding_and_prefix(self):
        self.assertEqual(system.parse_version("2"), (2, 0, 0))
        self.assertEqual(system.parse_version("2.1"), (2, 1, 0))
        self.assertEqual(system.parse_version("2.0.0-beta"), (2, 0, 0))
        self.assertEqual(system.parse_version("2.3b.7"), (2, 3, 0))

    def test_only_ascii_digits(self):
        for text in ("٢.٠.٠", "２.０", "²", "2.٠", "٢"):
            with self.subTest(text=text):
                v = system.parse_version(text)
                self.assertTrue(v is None or v == (2, 0, 0), v)
        self.assertIsNone(system.parse_version("٢.٠.٠"))
        self.assertIsNone(system.parse_version("２"))
        self.assertEqual(system.parse_version("2.٠"), (2, 0, 0))  # stops at the odd part

    def test_huge_numbers_give_none_without_exception(self):
        for text in ("9" * 5000, "2." + "1" * 5000, "1234567890", "2.0." + "9" * 10):
            with self.subTest(text=text[:12]):
                self.assertIsNone(system.parse_version(text))
        self.assertEqual(system.parse_version("123456789"), (123456789, 0, 0))

    def test_odd_registry_values(self):
        for value in (b"\x00\x01", 2, ["2"], "\x00", "2\x00"):
            with self.subTest(value=value):
                system.parse_version(value)  # never raises

    def test_pawnio_version_never_raises(self):
        fake = types.ModuleType("winreg")
        fake.HKEY_LOCAL_MACHINE, fake.KEY_READ = 0, 0
        fake.KEY_WOW64_64KEY, fake.KEY_WOW64_32KEY = 1, 2
        fake.OpenKey = mock.MagicMock()
        for text in ("9" * 5000, "٢.٠", "abc", ""):
            with self.subTest(text=text[:8]):
                fake.QueryValueEx = mock.Mock(return_value=(text, 1))
                with mock.patch.dict(sys.modules, {"winreg": fake}):
                    self.assertIsNone(system.pawnio_version())
        fake.QueryValueEx = mock.Mock(side_effect=ValueError("odd"))
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertIsNone(system.pawnio_version())

    def test_pawnio_ok_uses_parsed_version(self):
        with mock.patch.object(system, "pawnio_version", return_value=system.parse_version("2.0")):
            self.assertTrue(system.pawnio_ok())
        with mock.patch.object(system, "pawnio_version", return_value=system.parse_version("1.9.9")):
            self.assertFalse(system.pawnio_ok())
        with mock.patch.object(system, "pawnio_version", return_value=None):
            self.assertFalse(system.pawnio_ok())


class IntervalArgTest(unittest.TestCase):
    def test_valid_values(self):
        self.assertEqual(app._parse_args([]).interval, 1.0)
        self.assertEqual(app._parse_args(["--interval", "2"]).interval, 2.0)
        self.assertEqual(app._parse_args(["--interval", "0.1"]).interval, 0.1)

    def test_invalid_values_are_rejected(self):
        for text in ("0", "-1", "0.05", "nan", "inf", "-inf", "abc", ""):
            with self.subTest(text=text):
                with self.assertRaises(argparse.ArgumentTypeError):
                    app._interval(text)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    app._parse_args(["--interval", text])


class LoggingSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)  # runs last, after the log file is closed
        self.dir = Path(self.tmp.name)
        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        root.handlers.clear()

        def restore():
            for h in root.handlers:
                h.close()
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)

        self.addCleanup(restore)

    def file_handlers(self):
        return [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]

    def test_log_file_rotates(self):
        self.assertIsNone(app._setup_logging(False, self.dir))
        files = self.file_handlers()
        self.assertEqual(len(files), 1)
        handler = files[0]
        self.assertIsInstance(handler, logging.handlers.RotatingFileHandler)
        self.assertEqual(handler.maxBytes, app.LOG_MAX_BYTES)
        self.assertEqual(handler.backupCount, app.LOG_BACKUPS)
        self.assertEqual(handler.encoding, "utf-8")
        self.assertEqual(Path(handler.baseFilename), self.dir / "log.txt")

    def test_no_data_folder_means_no_log_file(self):
        self.assertIsNone(app._setup_logging(False, None))
        self.assertEqual(self.file_handlers(), [])
        self.assertTrue(logging.getLogger().handlers)  # console or NullHandler
        self.assertEqual(list(self.dir.iterdir()), [])

    @unittest.skipUnless(hasattr(os, "link"), "hard links not available")
    def test_hard_linked_log_or_rotation_disables_file_log(self):
        victim = self.dir / "victim.txt"
        victim.write_text("original", encoding="utf-8")
        for name in ("log.txt", "log.txt.3"):
            with self.subTest(name=name):
                try:
                    os.link(victim, self.dir / name)
                except OSError as e:
                    self.skipTest(f"hard link not supported here: {e}")
                problem = app._setup_logging(False, self.dir)
                self.assertIn(name, problem)
                self.assertEqual(self.file_handlers(), [])
                self.assertEqual(victim.read_text(encoding="utf-8"), "original")
                (self.dir / name).unlink()
                for h in logging.getLogger().handlers[:]:
                    logging.getLogger().removeHandler(h)


class DisplayLogTest(unittest.TestCase):
    """Connection problems are logged once per state, not on every retry."""

    def warnings(self, logs):
        return [r.getMessage() for r in logs.records if r.levelno == logging.WARNING]

    def test_busy_port_logged_once_until_success(self):
        d = device.Display("COM5")
        busy = serial.SerialException("access denied")
        with mock.patch.object(device.serial, "Serial", side_effect=busy, create=True):
            with self.assertLogs("fervura.device", logging.WARNING) as logs:
                for _ in range(5):
                    self.assertFalse(d.connect())
        self.assertEqual(self.warnings(logs), ["could not open COM5: access denied"])

        fake = mock.MagicMock(port="COM5", is_open=True)
        with mock.patch.object(device.serial, "Serial", return_value=fake, create=True):
            self.assertTrue(d.connect())
        d.ser = None  # connection lost: the next failure is logged again
        with mock.patch.object(device.serial, "Serial", side_effect=busy, create=True):
            with self.assertLogs("fervura.device", logging.WARNING) as logs:
                d.connect()
                d.connect()
        self.assertEqual(self.warnings(logs), ["could not open COM5: access denied"])

    def test_different_error_is_logged(self):
        d = device.Display("COM5")
        errors = [serial.SerialException("access denied"), serial.SerialException("not found")]
        with mock.patch.object(device.serial, "Serial", side_effect=errors, create=True):
            with self.assertLogs("fervura.device", logging.WARNING) as logs:
                d.connect()
                d.connect()
        self.assertEqual(self.warnings(logs), ["could not open COM5: access denied",
                                               "could not open COM5: not found"])

    def test_display_not_found_logged_once(self):
        d = device.Display()
        with mock.patch.object(device, "find_port", return_value=None):
            with self.assertLogs("fervura.device", logging.WARNING) as logs:
                for _ in range(5):
                    self.assertFalse(d.connect())
        messages = self.warnings(logs)
        self.assertEqual(len(messages), 1)
        self.assertIn("display not found", messages[0])
        self.assertIn("1F3A:0008", messages[0])
        self.assertIn("1A86:484A", messages[0])


if __name__ == "__main__":
    unittest.main()
