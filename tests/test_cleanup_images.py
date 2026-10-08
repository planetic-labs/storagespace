import unittest

from scripts.cleanup_images import versions_to_delete


def version(version_id, *tags):
    return {"id": version_id, "metadata": {"container": {"tags": list(tags)}}}


class CleanupImagesTest(unittest.TestCase):
    def test_only_versions_with_obsolete_release_tags_are_deleted(self):
        versions = [
            version(1, "v2026.10.01"),
            version(2, "v2026.10.08"),
            version(3, "v2026.10.01", "v2026.10.08"),
            version(4),
            version(5, "latest"),
            version(6, "v2026.10.01-patch1"),
        ]

        self.assertEqual(
            list(versions_to_delete(versions, {"v2026.10.08"})),
            [(1, ["v2026.10.01"]), (6, ["v2026.10.01-patch1"])],
        )


if __name__ == "__main__":
    unittest.main()
