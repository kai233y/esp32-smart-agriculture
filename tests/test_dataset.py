import sys
from pathlib import Path
import pytest
from PIL import Image
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai.train import validate_dataset


@pytest.fixture
def data(tmp_path):
    for i, split in enumerate(("train", "val", "test")):
        folder = tmp_path / "dataset" / "images" / split
        labels = tmp_path / "dataset" / "labels" / split
        folder.mkdir(parents=True)
        labels.mkdir(parents=True)
        Image.new("RGB", (30, 30), (i * 60, 30, 20)).save(folder / "sample.png")
        (labels / "sample.txt").write_text("0 0.5 0.5 0.4 0.4\n")
    config = tmp_path / "data.yaml"
    config.write_text(yaml.safe_dump(dict(path="dataset", train="images/train",
                                         val="images/val", test="images/test", names={0: "test_class"})))
    return config, tmp_path / "dataset"


def test_valid_dataset(data):
    config, root = data
    resolved, report = validate_dataset(config)
    assert resolved["path"] == str(root)
    assert report["train"]["boxes_per_class"] == [1]


def test_leakage(data):
    config, root = data
    (root / "images/val/sample.png").write_bytes((root / "images/train/sample.png").read_bytes())
    with pytest.raises(ValueError, match="数据泄漏"):
        validate_dataset(config)


def test_invalid_box(data):
    config, root = data
    (root / "labels/train/sample.txt").write_text("0 0.9 0.5 0.5 0.4")
    with pytest.raises(ValueError, match="标注越界"):
        validate_dataset(config)


def test_missing_annotation(data):
    config, root = data
    (root / "labels/train/sample.txt").unlink()
    with pytest.raises(ValueError, match="缺少标注"):
        validate_dataset(config)
