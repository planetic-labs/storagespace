import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
try:
    from fastapi import HTTPException
    from storage import MountedStorage, PersonalStorage
except ModuleNotFoundError:
    HTTPException = MountedStorage = PersonalStorage = None


@unittest.skipIf(PersonalStorage is None, "backend dependencies are not installed")
class PersonalStorageTest(unittest.TestCase):
    def test_private_roots_share_mount_without_sharing_files(self):
        with tempfile.TemporaryDirectory() as root:
            base = MountedStorage("common", "Общее", root, mode="local")
            alice = PersonalStorage(base, "personal", "Персональное", "Users", 11)
            bob = PersonalStorage(base, "personal", "Персональное", "Users", 22)

            alice.mkdir("docs")
            alice.upload("docs/note.txt", io.BytesIO(b"alice"))
            upload_id = "a" * 32
            alice.create_upload(upload_id, "empty.txt")
            alice.finish_upload(upload_id, "empty.txt", 0)

            self.assertEqual([entry["name"] for entry in alice.list_dir("")], ["docs", "empty.txt"])
            self.assertEqual(bob.list_dir(""), [])
            self.assertEqual(alice.info("docs/note.txt")["path"], "docs/note.txt")
            self.assertEqual((Path(root) / "Users/user-11/docs/note.txt").read_bytes(), b"alice")
            self.assertTrue((Path(root) / "Users/user-11/empty.txt").is_file())
            self.assertTrue((Path(root) / "Users/user-22").is_dir())
            with self.assertRaises(HTTPException) as error:
                bob.info("docs/note.txt")
            self.assertEqual(error.exception.status_code, 404)
            with self.assertRaises(HTTPException) as error:
                alice.info("../user-22/docs/note.txt")
            self.assertEqual(error.exception.status_code, 400)

    def test_copy_keeps_source_and_stays_inside_personal_root(self):
        with tempfile.TemporaryDirectory() as root:
            base = MountedStorage("common", "Общее", root, mode="local")
            alice = PersonalStorage(base, "personal", "Персональное", "Users", 11)
            bob = PersonalStorage(base, "personal", "Персональное", "Users", 22)
            alice.mkdir("docs")
            alice.upload("docs/note.txt", io.BytesIO(b"original"))

            alice.copy("docs", "Копия — docs")
            self.assertEqual((Path(root) / "Users/user-11/docs/note.txt").read_bytes(), b"original")
            self.assertEqual((Path(root) / "Users/user-11/Копия — docs/note.txt").read_bytes(), b"original")
            self.assertEqual(bob.list_dir(""), [])

            with self.assertRaises(HTTPException) as conflict:
                alice.copy("docs", "Копия — docs")
            self.assertEqual(conflict.exception.status_code, 409)
            with self.assertRaises(HTTPException) as inside:
                alice.copy("docs", "docs/clone")
            self.assertEqual(inside.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
