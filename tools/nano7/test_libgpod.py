"""Exercise a compiled libgpod-roundtrip helper on temporary synthetic devices."""
import contextlib
import os
import plistlib
from pathlib import Path
import re
import resource
import signal
import sqlite3
import struct
import subprocess
import tempfile
import unittest

from build import build, ROOT
from probe import HashAB, guid_bytes, verify_cbk, verify_cdb

CAPTURE = ROOT.parents[1] / "docs/sysinfo-captures/nano-7g-16gb-usb.xml"
POST_PROCESS = "com.apple.mobile.iTunes.SQLMusicLibraryPostProcessCommands"
APPLE_COLUMNS = {
    "item": {"is_home_video", "is_tone", "is_book", "is_itunes_u", "is_digital_booklet",
             "chosen_by_auto_fill", "collection_description", "copyright"},
    "avformat_info": {"channels"},
    "video_info": {"is_hd"},
    "store_info": {"xid", "flavor"},
    "container": {"smart_evaluation_order"},
}


def rows(path, sql, params=()):
    with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        return db.execute(sql, params).fetchall()


def columns(path, table):
    return {row[1] for row in rows(path, f"PRAGMA table_info({table})")}


def post_process_commands():
    # The capture is not strictly valid XML elsewhere; take only this dictionary.
    text = CAPTURE.read_text()
    start = text.index("<dict>", text.index(f"<key>{POST_PROCESS}</key>"))
    depth = 0
    for match in re.finditer(r"<dict>|</dict>", text[start:]):
        depth += 1 if match[0] == "<dict>" else -1
        if depth == 0:
            body = text[start:start + match.end()]
            break
    return plistlib.loads(f'<?xml version="1.0"?><plist version="1.0">{body}</plist>'.encode())


class LibgpodRoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = Path(os.environ["LIBGPOD_ROUNDTRIP"]).resolve()
        if not cls.helper.is_file():
            raise FileNotFoundError(cls.helper)
        cls.build_dir = tempfile.TemporaryDirectory()
        cls.hasher = HashAB(build(Path(cls.build_dir.name) / "hashab.so"))
        cls.commands = post_process_commands()

    @classmethod
    def tearDownClass(cls):
        cls.build_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mount = Path(self.temp.name)
        self.control = self.mount / "iPod_Control"
        self.bundle = self.control / "iTunes/iTunes Library.itlp"
        for name in ("Device", "Artwork", "iTunes", "Music/F00"):
            (self.control / name).mkdir(parents=True)
        (self.control / "Music/F00/TEST.mp3").write_bytes(bytes(32000))

    def db(self, name):
        return self.bundle / f"{name}.itdb"

    def setup_nano(self, guid="0123456789ABCDEF"):
        # Keep the device's unusual plist arrays verbatim, including AlbumArt2.
        capture = re.sub(r"(<key>FireWireGUID</key>\s*<string>)[^<]+",
                         lambda m: m[1] + guid, CAPTURE.read_text())
        (self.control / "Device/SysInfoExtended").write_text(capture)
        (self.control / "Device/SysInfo").write_text("ModelNumStr: MD480\n")

    def setup_synthetic(self, model, db_version, commands=None):
        # Synthetic capabilities: no Nano 5/6 SysInfoExtended capture is available.
        properties = {"FireWireGUID": "0123456789ABCDEF", "SQLiteDB": True, "DBVersion": db_version}
        if commands is not None:
            properties[POST_PROCESS] = commands
        (self.control / "Device/SysInfoExtended").write_bytes(plistlib.dumps(properties))
        (self.control / "Device/SysInfo").write_text(f"ModelNumStr: {model}\n")

    def run_helper(self, operation, success=True, preexec_fn=None):
        result = subprocess.run([str(self.helper), str(self.mount), operation],
                                capture_output=True, text=True, preexec_fn=preexec_fn)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def check_library(self, count):
        bundle = self.control / "iTunes/iTunes Library.itlp"
        for name in ("Library", "Locations", "Dynamic", "Extras", "Genius"):
            with sqlite3.connect(f"file:{bundle / (name + '.itdb')}?mode=ro", uri=True) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        guid = guid_bytes("0123456789ABCDEF")
        result = verify_cbk((bundle / "Locations.itdb").read_bytes(),
                            (bundle / "Locations.itdb.cbk").read_bytes(), guid, self.hasher)
        self.assertTrue(result["success"], result)
        cdb = verify_cdb((self.control / "iTunes/iTunesCDB").read_bytes(), guid, self.hasher)
        self.assertEqual((cdb["cdbScheme"], cdb["cdbHashAB"]), (3, True), cdb)
        with sqlite3.connect(f"file:{bundle / 'Library.itdb'}?mode=ro", uri=True) as db:
            self.assertEqual(db.execute("SELECT title FROM item").fetchall(),
                             [("Synthetic track",)] * count)

    def check_schema(self):
        """iTunes' columns and version markers."""
        library = self.db("Library")
        for table, names in APPLE_COLUMNS.items():
            self.assertLessEqual(names, columns(library, table), table)
        self.assertEqual(rows(library, "PRAGMA user_version"), [(26,)])
        self.assertEqual(rows(self.db("Genius"), "PRAGMA user_version"), [(2,)])
        self.assertEqual(rows(library, "SELECT * FROM version_info"), [(1, 1, 117, 0, 0, 1109, 1)])

    def test_nano_6_create_resave_delete(self):
        self.setup_synthetic("MC526", 5, self.commands)
        for operation, count in (("create", 1), ("resave", 1), ("delete", 0)):
            self.run_helper(operation)
            self.check_library(count)
            self.check_schema()

    def test_nano_7_create_resave_delete(self):
        self.setup_nano()
        for operation, count in (("create", 1), ("resave", 1), ("delete", 0)):
            self.run_helper(operation)
            self.check_library(count)
            self.check_schema()

    def snapshot(self):
        files = {path.name: path.read_bytes() for path in self.bundle.iterdir()}
        files["iTunesCDB"] = (self.control / "iTunes/iTunesCDB").read_bytes()
        return files

    def test_nano_post_process_commands_are_required(self):
        sets, sql = self.commands["UserVersionCommandSets"], self.commands["SQLCommands"]
        named_26 = [26 if name == "FakePragmaITunes26" else name for name in sets["25"]["Commands"]]
        cases = {
            "none": None,
            "only set 26": dict(self.commands, UserVersionCommandSets={"26": sets["26"]}),
            "missing command": dict(self.commands, SQLCommands={
                key: value for key, value in sql.items() if key != "FakePragmaITunes26"}),
            "failing command": dict(self.commands, SQLCommands=dict(
                sql, CreateIsHomeVideo="ALTER TABLE no_such_table ADD COLUMN x INTEGER;")),
            "non-string command": dict(self.commands, UserVersionCommandSets=dict(
                sets, **{"25": dict(sets["25"], Commands=named_26)})),
        }
        self.setup_synthetic("MC526", 5, self.commands)
        self.run_helper("create")
        library = self.snapshot()
        for name, commands in cases.items():
            with self.subTest(name):
                self.setup_synthetic("MC526", 5, commands)
                result = self.run_helper("resave", success=False)
                self.assertIn("post-process commands", result.stderr)
                self.assertEqual(self.snapshot(), library)

    def test_nano_runs_the_first_genre_count_command(self):
        # Set 25 lists AddGenreMapCompilationCount twice; only the repeat is skipped.
        sql = dict(self.commands["SQLCommands"])
        sql["AddGenreMapCompilationCount"] += "CREATE TABLE genre_count_added (x INTEGER);"
        self.setup_synthetic("MC526", 5, dict(self.commands, SQLCommands=sql))
        self.run_helper("create")
        self.assertEqual(rows(self.db("Library"), "SELECT name FROM sqlite_master "
                                                  "WHERE name = 'genre_count_added'"),
                         [("genre_count_added",)])

    def test_nano_failed_insert_or_commit_fails_the_write(self):
        # A playlist with the master playlist's pid breaks a key; a file size
        # limit makes the COMMIT of Library.itdb fail, as on a full disk.
        self.setup_nano()
        self.run_helper("create")
        library = self.snapshot()
        limit = self.db("Library").stat().st_size + 65536

        def full_disk():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

        for operation, preexec_fn, generated in (("clash", None, "Dynamic.itdb"),
                                                 ("grow", full_disk, "Library.itdb")):
            with self.subTest(operation):
                result = self.run_helper(operation, success=False, preexec_fn=preexec_fn)
                self.assertIn(f"during {generated} generation", result.stderr)
                self.assertEqual(self.snapshot(), library)

    def test_nano_5g_keeps_libgpod_layout(self):
        # SQLite with HASH72 instead of HashAB: none of the nano 6G/7G layout.
        self.setup_synthetic("MC027", 4, self.commands)
        (self.control / "Device/HashInfo").write_bytes(
            b"HASHv0" + bytes.fromhex("0123456789ABCDEF").ljust(20, b"\0") + bytes(range(28)))
        self.run_helper("create")
        library = self.db("Library")
        self.assertIn("date_backed_up", columns(library, "item"))
        for table, names in APPLE_COLUMNS.items():
            self.assertFalse(names & columns(library, table), table)
        self.assertEqual(rows(library, "PRAGMA user_version"), [(0,)])
        self.assertEqual(rows(self.db("Genius"), "PRAGMA user_version"), [(0,)])
        self.assertEqual(rows(library, "SELECT major, minor FROM version_info"), [(2, 48)])

    def test_invalid_or_ios_guid_is_rejected(self):
        for guid in ("", "0123456789ABCDEG", "0123456789ABCDE", "01" * 20):
            with self.subTest(guid=guid):
                self.setup_nano(guid)
                result = self.run_helper("create", success=False)
                self.assertIn("GUID", result.stderr)

    def test_prefixed_guid(self):
        self.setup_nano("0x0123456789ABCDEF")
        self.run_helper("create")
        self.check_library(1)

    def test_video_5g_remains_unsigned(self):
        (self.control / "Device/SysInfo").write_text("ModelNumStr: MA002\n")
        self.run_helper("create")
        data = (self.control / "iTunes/iTunesDB").read_bytes()
        self.assertEqual(data[:4], b"mhbd")
        self.assertEqual(struct.unpack_from("<H", data, 0x30)[0], 0)
        self.assertFalse((self.control / "iTunes/iTunes Library.itlp").exists())


if __name__ == "__main__":
    unittest.main()
