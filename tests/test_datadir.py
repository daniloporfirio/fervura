"""Protected data folder (%ProgramData%\\Fervura). Runs without administrator rights and
never touches the real folder: everything happens in temporary directories."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fervura import datadir, settings, system

WINDOWS = os.name == "nt"
PROJECT_DIR = Path(__file__).resolve().parent.parent


class ConstantsTest(unittest.TestCase):
    @unittest.skipUnless(WINDOWS, "Windows paths")
    def test_data_dir_is_program_data(self):
        self.assertEqual(datadir.DATA_DIR, system.program_data_dir() / "Fervura")
        self.assertEqual(settings.CONFIG_FILE, datadir.DATA_DIR / "config.json")

    @unittest.skipIf(WINDOWS, "non-Windows behaviour")
    def test_no_data_dir_outside_windows(self):
        self.assertIsNone(datadir.DATA_DIR)
        self.assertIsNone(settings.CONFIG_FILE)

    def test_accepted_parent_owners(self):
        self.assertEqual(set(datadir.PARENT_OWNERS), {
            "S-1-5-18", "S-1-5-32-544",
            "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"})

    def test_descriptor_matches_the_required_permissions(self):
        # owner Administrators; protected (no inheritance); SYSTEM and Administrators full
        # control, Users read & execute; all with (OI)(CI)
        self.assertEqual(datadir.DIR_SDDL,
                         "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)")
        self.assertEqual(set(datadir.TRUSTED_OWNERS), {"S-1-5-32-544", "S-1-5-18"})

    @unittest.skipIf(WINDOWS, "non-Windows behaviour")
    def test_not_windows_is_refused(self):
        self.assertIsNotNone(datadir.prepare(Path(tempfile.gettempdir()) / "x"))


@unittest.skipUnless(WINDOWS, "Windows paths")
class PathWithoutEnvironmentTest(unittest.TestCase):
    """The folder is resolved from the kernel and HKLM only: variables set by the normal user
    (HKCU\\Environment reaches the elevated process) must not move it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "x" / "ProgramData").mkdir(parents=True)
        drive = system.system_windows_dir().drive
        self.expected = Path(drive + "\\") / "ProgramData" / "Fervura"
        self.attacks = {
            "SystemDrive pointing to a user folder": {
                "SystemDrive": str(self.dir / "x"), "ProgramData": str(self.dir),
                "ALLUSERSPROFILE": str(self.dir), "WINDIR": str(self.dir)},
            "invalid SystemDrive": {
                "SystemDrive": "nao-existe", "ProgramData": str(self.dir), "WINDIR": "x"},
        }

    def test_real_value(self):
        self.assertEqual(datadir.DATA_DIR, self.expected)

    def test_environment_is_ignored_in_process(self):
        for name, env in self.attacks.items():
            with self.subTest(name), mock.patch.dict(os.environ, env):
                self.assertEqual(system.program_data_dir() / "Fervura", self.expected)
                self.assertEqual(system.fonts_dir(), system.system_windows_dir() / "Fonts")
                self.assertFalse(str(system.fonts_dir()).startswith(str(self.dir)))

    def test_environment_is_ignored_by_a_new_process(self):
        """Same as the elevated program started with the user's environment: DATA_DIR is
        computed at import."""
        code = "from fervura import datadir, system; print(datadir.DATA_DIR); print(system.fonts_dir())"
        for name, env in self.attacks.items():
            with self.subTest(name):
                result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_DIR,
                                        env={**os.environ, **env}, capture_output=True,
                                        text=True, check=True)
                data_dir, fonts = result.stdout.splitlines()
                self.assertEqual(Path(data_dir), self.expected)
                self.assertNotIn(str(self.dir).lower(), (data_dir + fonts).lower())

    def test_unresolved_means_no_data_folder(self):
        """No fallback to %ProgramData% or C:\\ProgramData: None, and prepare() refuses."""
        with mock.patch.object(system, "_profile_list_program_data", return_value=None), \
                mock.patch.dict(os.environ, {"ProgramData": str(self.dir)}):
            self.assertIsNone(system.program_data_dir())
        with mock.patch.object(system, "system_windows_dir", return_value=None):
            self.assertIsNone(system.program_data_dir())
        with mock.patch.object(datadir, "DATA_DIR", None):
            self.assertIn("could not be resolved", datadir.prepare())


class SafeFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_missing_and_plain_files_are_safe(self):
        f = self.dir / "a.txt"
        self.assertTrue(datadir.is_safe_file(f))
        f.write_text("x", encoding="utf-8")
        self.assertTrue(datadir.is_safe_file(f))

    def test_folder_is_not_a_safe_file(self):
        self.assertFalse(datadir.is_safe_file(self.dir))

    @unittest.skipUnless(hasattr(os, "link"), "hard links not available")
    def test_hard_link_is_unsafe(self):
        a, b = self.dir / "a.txt", self.dir / "b.txt"
        a.write_text("x", encoding="utf-8")
        try:
            os.link(a, b)
        except OSError as e:
            self.skipTest(f"hard link not supported here: {e}")
        self.assertFalse(datadir.is_safe_file(a))
        self.assertFalse(datadir.is_safe_file(b))

    @unittest.skipUnless(WINDOWS, "junctions are a Windows feature")
    def test_junction_is_unsafe(self):
        import _winapi
        target, link = self.dir / "target", self.dir / "link"
        target.mkdir()
        _winapi.CreateJunction(str(target), str(link))
        self.assertFalse(datadir.is_safe_file(link))


@unittest.skipUnless(WINDOWS, "Windows security API")
class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        probe = self.dir / "probe"
        probe.mkdir()
        # whoever runs the tests: the default owner of what this process creates
        self.me = datadir.security_info(probe)[0]
        # same shape as DIR_SDDL, but without fixing the owner (needs elevation) and with
        # full control for the test user so the temporary folder can be cleaned up
        self.sddl = f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{self.me})"
        # the temporary parent belongs to the test user, not to SYSTEM or Administrators
        self.parent = (self.me,)

    def assert_protected(self, path: Path) -> str:
        owner, dacl = datadir.security_info(path)
        self.assertTrue(dacl.startswith("D:P"), dacl)  # inheritance removed
        self.assertNotIn("ID;", dacl)  # nothing inherited from the parent (flag "ID")
        for ace in ("(A;OICI;FA;;;SY)", "(A;OICI;FA;;;BA)"):
            self.assertIn(ace, dacl)
        return owner

    def test_creates_protected_folder(self):
        path = self.dir / "Fervura"
        self.assertIsNone(datadir.prepare(path, self.sddl, (self.me,), self.parent))
        self.assertTrue(path.is_dir())
        self.assertEqual(self.assert_protected(path), self.me)
        # files created inside inherit the protected permissions
        (path / "log.txt").write_text("x", encoding="utf-8")
        _, dacl = datadir.security_info(path / "log.txt")
        self.assertIn(f";FA;;;{self.me})", dacl)
        self.assertNotIn(";;;BU)", dacl)
        self.assertNotIn(";;;AU)", dacl)

    def test_repairs_existing_folder(self):
        path = self.dir / "Fervura"
        path.mkdir()  # permissions inherited from the parent
        (path / "log.txt").write_text("old", encoding="utf-8")  # written before the repair
        self.assertIn("ID;", datadir.security_info(path)[1])
        self.assertIsNone(datadir.prepare(path, self.sddl, (self.me,), self.parent))
        self.assert_protected(path)
        # the old file ends with the same permissions as a file created after the repair
        (path / "new.txt").write_text("new", encoding="utf-8")
        def owner_and_aces(f):  # without the control flags ("AI" after a reset)
            owner, dacl = datadir.security_info(f)
            return owner, dacl[dacl.index("("):]
        self.assertEqual(owner_and_aces(path / "log.txt"), owner_and_aces(path / "new.txt"))
        self.assertIsNone(datadir.prepare(path, self.sddl, (self.me,), self.parent))  # idempotent

    def test_refuses_folder_of_untrusted_owner(self):
        path = self.dir / "Fervura"
        path.mkdir()
        before = datadir.security_info(path)
        if before[0] in datadir.TRUSTED_OWNERS:
            self.skipTest("tests running elevated: the folder is owned by Administrators")
        reason = datadir.prepare(path, self.sddl, parent_owners=self.parent)  # default owners
        self.assertIn("owned by", reason)
        self.assertEqual(datadir.security_info(path), before)  # left untouched

    def test_refuses_junction_and_leaves_target_alone(self):
        import _winapi
        target, link = self.dir / "elsewhere", self.dir / "Fervura"
        target.mkdir()
        (target / "important.txt").write_text("keep", encoding="utf-8")
        _winapi.CreateJunction(str(target), str(link))
        before = datadir.security_info(target)
        reason = datadir.prepare(link, self.sddl, (self.me,), self.parent)
        self.assertIn("link", reason)
        self.assertEqual(datadir.security_info(target), before)
        self.assertEqual((target / "important.txt").read_text(encoding="utf-8"), "keep")
        self.assertTrue(os.path.lexists(link))  # not deleted nor moved

    def test_refuses_file_in_place_of_folder(self):
        path = self.dir / "Fervura"
        path.write_text("x", encoding="utf-8")
        self.assertIn("not a folder", datadir.prepare(path, self.sddl, (self.me,), self.parent))
        self.assertEqual(path.read_text(encoding="utf-8"), "x")

    def test_missing_parent_is_reported(self):
        # the missing parent is now found by the check of the folders above (before creating)
        path = self.dir / "nope" / "Fervura"
        reason = datadir.prepare(path, self.sddl, (self.me,), self.parent)
        self.assertIn("could not check", reason)
        self.assertFalse(os.path.lexists(path.parent))

    # -- folders above the data folder
    def test_refuses_parent_of_untrusted_owner(self):
        """Default parent owners (SYSTEM, TrustedInstaller, Administrators): the temporary
        folder belongs to the test user and is refused, without creating anything."""
        if self.me in datadir.PARENT_OWNERS:
            self.skipTest("tests running elevated: the folder is owned by Administrators")
        path = self.dir / "Fervura"
        reason = datadir.prepare(path, self.sddl, (self.me,))
        self.assertIn("parent folder", reason)
        self.assertIn(self.me, reason)
        self.assertFalse(os.path.lexists(path))

    def test_refuses_junction_above_the_folder(self):
        import _winapi
        target, link = self.dir / "elsewhere", self.dir / "junction"
        target.mkdir()
        _winapi.CreateJunction(str(target), str(link))
        (target / "sub").mkdir()
        # the junction as the parent, and as an ancestor further up
        for path in (link / "Fervura", link / "sub" / "Fervura"):
            with self.subTest(path=path):
                reason = datadir.prepare(path, self.sddl, (self.me,), self.parent)
                self.assertIn("link", reason)
                self.assertIn(str(link), reason)
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["sub"])  # nothing created
        self.assertEqual(list((target / "sub").iterdir()), [])

    def test_refuses_relative_or_network_path(self):
        for path in (Path("Fervura"), Path(r"\\servidor\share\Fervura"),
                     self.dir / "a" / ".." / "Fervura"):
            with self.subTest(path=path):
                reason = datadir.prepare(path, self.sddl, (self.me,), self.parent)
                self.assertIn("not a local absolute path", reason)
        self.assertFalse(os.path.lexists(self.dir / "Fervura"))

    # -- files already inside the folder (written before the folder was repaired)
    def icacls(self, *args) -> None:
        subprocess.run(["icacls", *map(str, args)], check=True, capture_output=True)

    def assert_only_inherited(self, path: Path) -> None:
        _, dacl = datadir.security_info(path)
        self.assertFalse(dacl.startswith("D:P"), dacl)
        aces = dacl[dacl.index("("):].strip("()").split(")(")
        self.assertTrue(aces)
        for ace in aces:
            self.assertIn("ID", ace.split(";")[1], dacl)  # every ACE inherited
        self.assertNotIn(";;;WD)", dacl)  # Everyone

    def test_explicit_aces_of_existing_files_are_removed(self):
        path = self.dir / "Fervura"
        self.assertIsNone(datadir.prepare(path, self.sddl, (self.me,), self.parent))
        config, log = path / "config.json", path / "log.txt.1"
        config.write_text('{"source": "gpu"}', encoding="utf-8")
        log.write_text("x", encoding="utf-8")
        self.icacls(config, "/grant", "*S-1-1-0:F")  # explicit Everyone full control
        self.icacls(log, "/inheritance:r", "/grant", "*S-1-1-0:F", "/grant", f"*{self.me}:F")
        self.assertIn("WD)", datadir.security_info(config)[1])
        self.assertIsNone(datadir.prepare(path, self.sddl, (self.me,), self.parent))
        for f in (config, log):
            with self.subTest(f=f.name):
                self.assert_only_inherited(f)
        self.assertEqual(config.read_text(encoding="utf-8"), '{"source": "gpu"}')

    def test_files_get_the_owner_of_the_descriptor(self):
        """With an owner in the descriptor (O:BA in production) it is applied to the files
        too. Without elevation only the test user can be set as owner."""
        path = self.dir / "Fervura"
        sddl = f"O:{self.me}{self.sddl}"
        self.assertIsNone(datadir.prepare(path, sddl, (self.me,), self.parent))
        config = path / "config.json"
        config.write_text("{}", encoding="utf-8")
        self.icacls(config, "/grant", "*S-1-1-0:F")
        self.assertIsNone(datadir.prepare(path, sddl, (self.me,), self.parent))
        self.assertEqual(datadir.security_info(config)[0], self.me)
        self.assert_only_inherited(config)

    @unittest.skipUnless(hasattr(os, "link"), "hard links not available")
    def test_hard_linked_file_and_subfolder_are_left_alone(self):
        """A hard link shares its permissions with the other name: changing them would
        change the file elsewhere. Such files are skipped (is_safe_file refuses to use them)."""
        path = self.dir / "Fervura"
        self.assertIsNone(datadir.prepare(path, self.sddl, (self.me,), self.parent))
        victim = self.dir / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        self.icacls(victim, "/grant", "*S-1-1-0:R")
        try:
            os.link(victim, path / "config.json")
        except OSError as e:
            self.skipTest(f"hard link not supported here: {e}")
        sub = path / "sub"
        sub.mkdir()
        self.icacls(sub, "/grant", "*S-1-1-0:F")
        before = datadir.security_info(victim), datadir.security_info(sub)
        self.assertIsNone(datadir.prepare(path, self.sddl, (self.me,), self.parent))
        self.assertEqual((datadir.security_info(victim), datadir.security_info(sub)), before)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep")
        self.assertFalse(datadir.is_safe_file(path / "config.json"))

    def test_real_descriptor(self):
        """With the real descriptor: refused without elevation (nothing created);
        elevated, the folder gets owner Administrators and Users read-only."""
        path = self.dir / "Fervura"
        reason = datadir.prepare(path, parent_owners=self.parent)
        if not system.is_admin():
            self.assertIsNotNone(reason)
            self.assertFalse(os.path.lexists(path))
            return
        self.assertIsNone(reason)
        owner = self.assert_protected(path)
        self.assertEqual(owner, "S-1-5-32-544")
        dacl = datadir.security_info(path)[1]
        self.assertIn(";;;BU)", dacl)
        self.assertNotIn(";FA;;;BU)", dacl)


if __name__ == "__main__":
    unittest.main()
