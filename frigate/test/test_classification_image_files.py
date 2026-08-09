"""Tests for classification image file discovery."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from frigate.api.classification import (
    get_classification_dataset,
    get_classification_images,
)
from frigate.util.classification import (
    get_dataset_image_count,
    is_supported_image_file,
)


class TestClassificationImageFiles(unittest.TestCase):
    """Ensure metadata sidecars are never exposed as classification images."""

    def test_supported_image_file_rejects_appledouble_sidecars(self) -> None:
        self.assertTrue(is_supported_image_file("example.webp"))
        self.assertTrue(is_supported_image_file("example.JPEG"))
        self.assertFalse(is_supported_image_file("._example.webp"))
        self.assertFalse(is_supported_image_file(".hidden.png"))
        self.assertFalse(is_supported_image_file("example.json"))

    def test_train_endpoint_ignores_appledouble_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clips_dir = Path(temporary_directory)
            train_dir = clips_dir / "cat_food" / "train"
            train_dir.mkdir(parents=True)
            (train_dir / "example.webp").touch()
            (train_dir / "._example.webp").touch()

            with patch("frigate.api.classification.CLIPS_DIR", str(clips_dir)):
                response = get_classification_images("cat_food")

        self.assertEqual(json.loads(response.body), ["example.webp"])

    def test_dataset_endpoint_and_count_ignore_appledouble_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clips_dir = Path(temporary_directory)
            category_dir = clips_dir / "cat_food" / "dataset" / "eating"
            category_dir.mkdir(parents=True)
            (category_dir / "example.webp").touch()
            (category_dir / "._example.webp").touch()

            with (
                patch("frigate.api.classification.CLIPS_DIR", str(clips_dir)),
                patch("frigate.util.classification.CLIPS_DIR", str(clips_dir)),
            ):
                response = get_classification_dataset("cat_food")
                image_count = get_dataset_image_count("cat_food")

        content = json.loads(response.body)
        self.assertEqual(content["categories"], {"eating": ["example.webp"]})
        self.assertEqual(content["training_metadata"]["current_image_count"], 1)
        self.assertEqual(image_count, 1)


if __name__ == "__main__":
    unittest.main()
