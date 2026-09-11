import json
import tempfile
import threading
import unittest
from pathlib import Path

from voicenote_box.store import exclusive_lock, read_json, write_json


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "nested" / "document.json"

    def tearDown(self):
        self.directory.cleanup()

    def test_a_missing_document_returns_the_default_without_creating_it(self):
        self.assertEqual(read_json(self.path, {"empty": True}), {"empty": True})
        self.assertFalse(self.path.parent.exists())

    def test_a_written_document_reads_back(self):
        write_json(self.path, {"recipients": ["oma"]})

        self.assertEqual(read_json(self.path), {"recipients": ["oma"]})

    def test_a_corrupt_document_raises_instead_of_looking_empty(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken")

        with self.assertRaises(ValueError):
            read_json(self.path, {})

    def test_a_failed_write_leaves_the_previous_document_intact(self):
        write_json(self.path, {"revision": 1})

        class Unserialisable:
            pass

        with self.assertRaises(TypeError):
            write_json(self.path, {"value": Unserialisable()})

        self.assertEqual(read_json(self.path), {"revision": 1})
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_the_lock_serialises_read_modify_write_cycles(self):
        write_json(self.path, {"count": 0})
        ready = threading.Barrier(4)

        def increment():
            # Start together so the threads genuinely contend for the lock.
            ready.wait(timeout=10)
            for _ in range(25):
                with exclusive_lock(self.path):
                    document = read_json(self.path)
                    document["count"] += 1
                    write_json(self.path, document)

        threads = [threading.Thread(target=increment) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())

        self.assertEqual(json.loads(self.path.read_text())["count"], 100)



if __name__ == "__main__":
    unittest.main()
