import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meowkeeper.core.identity.pet_registry import PetRegistry


class PetRegistrySafetyTests(unittest.TestCase):
    def test_invalid_ids_are_rejected_before_filesystem_read(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PetRegistry(directory)
            for invalid in ("../outside", "..", "C:\\private", "a/b", "a\\b", "", None):
                for method in (registry.get_pet, registry.get_embeddings):
                    with self.subTest(value=invalid, method=method.__name__), self.assertRaises(ValueError):
                        method(invalid)

    def test_reference_name_cannot_escape_or_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PetRegistry(directory)
            pet = registry.register_pet("測試寵物")
            original = registry.add_reference_image(pet.pet_id, b"first", filename="image.jpg")
            with self.assertRaises(FileExistsError):
                registry.add_reference_image(pet.pet_id, b"second", filename="image.jpg")
            for name in ("../image.jpg", "..\\image.jpg", "profile.json", "C:\\image.jpg"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    registry.add_reference_image(pet.pet_id, b"bad", filename=name)
            self.assertEqual(original.read_bytes(), b"first")
            self.assertEqual(len(registry.get_pet(pet.pet_id).reference_images), 1)

    def test_failed_atomic_replace_keeps_existing_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PetRegistry(directory)
            pet = registry.register_pet("原名稱")
            pet.display_name = "新名稱"
            with patch("meowkeeper.core.identity.pet_registry.os.replace", side_effect=OSError("injected")), self.assertRaises(OSError):
                registry._write_profile(pet)
            self.assertEqual(registry.get_pet(pet.pet_id).display_name, "原名稱")
            self.assertEqual(list(Path(directory).rglob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
