# 病虫害识别：数据、训练与部署

本项目提供**目标检测**管线：相机拍照 → 上传JPEG → 本地YOLO检测 → 返回类别、置信度、检测框 → 网页展示。推理在服务器运行，ESP32-CAM不承载神经网络。没有模型时仍能采图，但明确返回 `model_unavailable`。

源码包不附带农业权重、标注图片或承诺识别准确率。识别可靠性依赖作物、类别、拍摄距离、季节和真实数据。默认提供番茄病害与虫害的示例类别，可按目标作物修改 `ai/dataset.yaml`；不同作物通常需要重新整理和训练。

## 数据结构

```text
ai/dataset/
  images/train/  images/val/  images/test/
  labels/train/  labels/val/  labels/test/
```

例如 `images/train/leaf001.jpg` 对应 `labels/train/leaf001.txt`。每行格式：

```text
class_id x_center y_center width height
```

后4项均相对于图片宽高归一化到0–1。例如 `3 0.50 0.40 0.10 0.08` 表示类别3的一个蚜虫框，**这只是格式示例，不是可训练样本**。

使用CVAT、Label Studio等工具标注并导出YOLO格式。统一标注粒度：病害类框住病叶，虫害类框住可见虫体/约定的虫群；不要在相同类别里混用病斑框和整叶框。健康/纯背景图保留空txt，不设“healthy”检测类，因此无框不等于健康诊断。

建议开始时每类收集数百张现场图片作为起点，包含正常叶片、干枯、肥害、遮挡和光照变化等负样本；数量不是准确率保证。按**植株、日期、地块和拍摄批次**划分train/val/test（如70/15/15），同一视频相邻帧不能跨集合。脚本能检查字节完全相同的重复图片，不能发现所有近似图，仍需人工审核。

摄像头和手机照片可能有明显域差异，最终独立测试集必须包含实际ESP32-CAM采集的图。小虫太小、失焦、叶背不可见时应改善拍摄，不要单纯降低置信度。

## 训练

使用Python3.11的独立环境，训练可在有GPU的电脑上进行：

```bash
python3.11 -m venv .venv-ai
source .venv-ai/bin/activate
pip install -r server/requirements.txt -r ai/requirements.txt
python ai/train.py --check-only
python ai/train.py --device 0 --epochs 100 --batch 8
# 无CUDA可用 --device cpu；Apple Silicon可尝试 --device mps
```

首次运行可自动下载 `yolov8n.pt` 作为迁移学习初始权重；通用COCO权重本身不能识别本项目的农业类别。离线训练可用 `--model /path/to/local.pt` 指向可信本地权重。不要加载来源不明的 `.pt` 文件。

输出默认在 `runs/agriculture/train/weights/best.pt`，独立test的指标在 `runs/agriculture/test_metrics.json`。输出目录已存在会拒绝覆盖，改 `--out runs/another-run` 开新实验。建议记录逐类precision/recall、mAP50、mAP50-95、混淆矩阵、低光/遮挡/远近分组和现场推理延迟。不要用训练集准确率当作现场效果。

`--check-only` 会检查目录、5列标注、类别范围、边框归一化/边界、完全重复图片和训练集零样本类别。数据目录默认未填充，因此未放入真实图片时命令应报错。

## 启用识别服务

方案A：在安装AI依赖的Python3.11环境中运行后端：

```bash
set -a
source .env
set +a
export MODEL_PATH="$PWD/runs/agriculture/train/weights/best.pt"
export MODEL_CONF=0.40
uvicorn server.app:app --host 0.0.0.0 --port 8000 --workers 1
```

方案B：Docker镜像（含AI依赖，体积大于基础镜像）：

```bash
mkdir -p models
cp runs/agriculture/train/weights/best.pt models/best.pt
docker compose -f compose.yaml -f compose.ai.yaml up --build -d
```

基础Docker镜像不含PyTorch/Ultralytics，仅在 `.env` 中设置MODEL_PATH无法让基础镜像识别。须选择AI镜像或在本地安装AI依赖。

服务启动时加载模型，更新权重后重启服务。CPU串行推理，同一时刻只处理一张图片，忙时返回429，相机会在下一周期重试。摄像头HTTP等待最长30秒，CPU过慢可减小相机分辨率、降低训练/推理imgsz或换更快服务器（推理imgsz当前在app.py中设为640）。

检测结果的 `xyxy` 坐标针对服务保存的、最长边≤1600的图片。结果 `detected / no_detection / model_unavailable / inference_error` 明确区分。零检测不能证明植株健康，病害相似症状需人工复核；识别结果不联动自动喷药。

## 许可

Ultralytics提供AGPL-3.0及商业许可路线。部署或商业交付前确认所用版本和场景的许可义务，训练集和预训练权重也各有许可。本包自写业务代码的许可见根目录LICENSE，不替代第三方许可。

官方参考：[数据格式](https://docs.ultralytics.com/datasets/detect/)、[训练接口](https://docs.ultralytics.com/modes/train/)、[许可说明](https://www.ultralytics.com/license)。本项目固定8.3.70依赖，最新文档中的新模型名称不一定适用于该固定版本。
