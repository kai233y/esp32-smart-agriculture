"""Validate YOLO annotations and split leakage; train and evaluate a local model."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import yaml


def validate_dataset(config):
    config = Path(config).resolve()
    data = yaml.safe_load(config.read_text())
    root = (config.parent / data["path"]).resolve()
    data["path"] = str(root)
    names = data["names"]
    if not isinstance(names, (list, dict)) or not names:
        raise ValueError("names必须包含至少一个类别")
    if isinstance(names, dict) and sorted(names) != list(range(len(names))):
        raise ValueError("类别 ID 必须从0连续编号")
    seen, report = {}, {}
    for split in ("train", "val", "test"):
        folder = root / data[split]
        images = sorted(p for p in folder.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        if not images:
            raise ValueError(f"{split} 无图片：{folder}；请先准备真实数据")
        count = [0] * len(names)
        for image in images:
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            if digest in seen:
                raise ValueError(f"重复图片/数据泄漏：{image} 与 {seen[digest]}")
            seen[digest] = str(image)
            relative = image.relative_to(root / "images" / split)
            label = (root / "labels" / split / relative).with_suffix(".txt")
            if not label.exists():
                raise ValueError(f"缺少标注：{label}；背景图片也需空txt")
            for line in label.read_text().splitlines():
                if not line.strip():
                    continue
                fields = line.split()
                if len(fields) != 5:
                    raise ValueError(f"检测标注需要5列：{label}")
                cls = int(fields[0]); x, y, w, h = map(float, fields[1:])
                if not (0 <= cls < len(names) and all(math.isfinite(v) for v in [x,y,w,h])
                        and 0 < w <= 1 and 0 < h <= 1 and w/2-1e-5 <= x <= 1-w/2+1e-5
                        and h/2-1e-5 <= y <= 1-h/2+1e-5):
                    raise ValueError(f"标注越界：{label}: {line}")
                count[cls] += 1
        report[split] = {"images": len(images), "boxes_per_class": count}
    if any(n == 0 for n in report["train"]["boxes_per_class"]):
        raise ValueError("训练集存在零标注类别，请补充数据或修改names")
    return data, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(Path(__file__).with_name("dataset.yaml")))
    parser.add_argument("--model", default="yolov8n.pt", help="首次可自动下载通用预训练权重；这不是农业识别成品")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="cpu", help="cpu / 0 (CUDA) / mps")
    parser.add_argument("--out", default="runs/agriculture")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    data, report = validate_dataset(args.data)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.check_only:
        return
    from ultralytics import YOLO
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    resolved = out / "dataset.resolved.yaml"
    resolved.write_text(yaml.safe_dump(data, allow_unicode=True))
    (out / "dataset_report.json").write_text(json.dumps(report, indent=2))
    model = YOLO(args.model, task="detect")
    model.train(data=str(resolved), epochs=args.epochs, imgsz=640, batch=args.batch,
                device=args.device, project=str(out), name="train", seed=42, workers=0)
    best = out / "train" / "weights" / "best.pt"
    trained = YOLO(str(best))
    metrics = trained.val(data=str(resolved), split="test", device=args.device,
                          project=str(out), name="heldout_test")
    (out / "test_metrics.json").write_text(json.dumps(metrics.results_dict, indent=2))
    print(f"训练权重：{best}\n请复核独立测试集逐类指标和现场误检，再部署。")


if __name__ == "__main__":
    main()
