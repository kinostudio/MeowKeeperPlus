"""輕量靜態貓咪姿態分類器訓練與校準工具 (Train & Calibrate Cat Posture Classifier)

資料來源：
1. cats-behaviors-test (Roboflow Universe 開源資料集支援)
2. 實際 C701 攝影機樣本 (SQLite identity_feedback_evidence，優先讀取已提取特徵)
3. 姿態幾何與生物力學分佈錨點 (Sitting / Standing / Lying / Unknown)

產出模型：
- models/behavior/posture_mlp_v1.pth (~200KB)
- 登錄至 SQLite model_registry 資料表
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure workspace src is on sys.path
WORKSPACE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(WORKSPACE_SRC) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_SRC))

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from meowkeeper.core.behavior.posture_classifier import (
    POSTURE_CLASSES,
    POSTURE_LABELS_ZH,
    PostureClassifier,
    PostureMLP,
)
from meowkeeper.core.identity.dinov2_embedder import DinoV2Embedder
from meowkeeper.runtime import (
    DEFAULT_POSTURE_MODEL_NAME,
    DEFAULT_POSTURE_MODEL_VERSION,
    posture_model_file,
    project_root,
)


def extract_c701_crops_and_features(
    db_path: Path,
    max_to_extract: int = 30,
    embedder: DinoV2Embedder | None = None,
) -> tuple[list[np.ndarray], list[str]]:
    """從 SQLite 讀取 C701 真實樣本特徵與幾何分群標籤。"""
    vectors: list[np.ndarray] = []
    labels: list[str] = []

    if not db_path.is_file():
        return vectors, labels

    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT event_id, crop_jpeg, feature_json FROM identity_feedback_evidence WHERE crop_jpeg IS NOT NULL"
    ).fetchall()
    con.close()

    precomputed_count = 0
    extracted_count = 0

    for eid, jpeg, fj in rows:
        vec: np.ndarray | None = None
        # 1. 優先使用 SQLite 既有特徵 (即時，零運算)
        if fj:
            try:
                data = json.loads(fj)
                raw_vec = data.get("vector")
                if raw_vec and len(raw_vec) == 384:
                    vec = np.asarray(raw_vec, dtype=np.float32)
                    precomputed_count += 1
            except Exception:
                pass

        # 2. 若無特徵且在配額內，則即時提取
        if vec is None and embedder is not None and extracted_count < max_to_extract:
            try:
                vec = embedder.embed(jpeg)
                extracted_count += 1
            except Exception:
                pass

        if vec is None:
            continue

        # 正規化
        norm = float(np.linalg.norm(vec))
        if norm <= 0:
            continue
        vec /= norm

        # 計算長寬比
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            continue
        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            continue
        aspect = float(w) / float(h)

        # 貓咪姿態分類規則：
        # - lying (趴躺): 身體水平拉長貼地，W/H >= 1.60
        # - sitting (坐著): 垂直立姿或方正正面，W/H <= 1.10
        # - standing (站立): 側身站立中型比例，1.10 < W/H < 1.60
        if aspect >= 1.60:
            posture = "lying"
        elif aspect <= 1.10:
            posture = "sitting"
        else:
            posture = "standing"

        vectors.append(vec)
        labels.append(posture)

    print(
        f"📊 C701 樣本加載完成：已提取特徵={precomputed_count} 筆, 即時計算={extracted_count} 筆, 總計={len(vectors)} 筆"
    )
    return vectors, labels


def load_dataset_folder(
    folder: Path,
    embedder: DinoV2Embedder | None = None,
    max_images_per_class: int = 50,
) -> tuple[list[np.ndarray], list[str]]:
    """從指定資料夾載入已分類圖片並提取特徵。"""
    vectors: list[np.ndarray] = []
    labels: list[str] = []

    if not folder.is_dir() or embedder is None:
        return vectors, labels

    for c in ("sitting", "standing", "lying"):
        c_dir = folder / c
        if not c_dir.is_dir():
            continue
        files = []
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            files.extend(list(c_dir.glob(ext)))

        for p in files[:max_images_per_class]:
            try:
                data = p.read_bytes()
                vec = embedder.embed(data)
                norm = float(np.linalg.norm(vec))
                if norm > 0:
                    vec /= norm
                    vectors.append(vec)
                    labels.append(c)
            except Exception:
                pass
    return vectors, labels


def augment_and_balance(
    vectors: list[np.ndarray],
    labels: list[str],
    target_per_class: int = 150,
) -> tuple[torch.Tensor, torch.Tensor]:
    """特徵空間數據增強 (Feature Noise & Interpolation) 與類別平衡。"""
    cls_map = {c: i for i, c in enumerate(POSTURE_CLASSES)}
    by_class: dict[str, list[np.ndarray]] = {c: [] for c in POSTURE_CLASSES}

    for v, l in zip(vectors, labels):
        if l in by_class:
            by_class[l].append(v)

    rng = np.random.default_rng(42)

    # 為 unknown 類別生成混合模糊特徵 (代表遮擋、非貓或邊緣)
    unknown_samples: list[np.ndarray] = []
    if vectors:
        for _ in range(target_per_class // 2):
            idx1, idx2 = rng.integers(0, len(vectors), size=2)
            alpha = rng.uniform(0.3, 0.7)
            blended = alpha * vectors[idx1] + (1 - alpha) * vectors[idx2]
            noise = rng.normal(0, 0.08, size=blended.shape).astype(np.float32)
            comb = blended + noise
            norm = np.linalg.norm(comb)
            if norm > 0:
                unknown_samples.append(comb / norm)
    by_class["unknown"].extend(unknown_samples)

    final_vecs: list[np.ndarray] = []
    final_labels: list[int] = []

    for c in POSTURE_CLASSES:
        cls_idx = cls_map[c]
        samples = by_class[c]
        if not samples:
            # 若無樣本則生成基準高斯錨點
            base = rng.normal(0, 1.0, size=(1, 384)).astype(np.float32)
            base /= np.linalg.norm(base)
            samples = [base[0]]

        # 原樣本加入
        for s in samples:
            final_vecs.append(s)
            final_labels.append(cls_idx)

        # 增強擴充至 target_per_class
        current_len = len(samples)
        if current_len < target_per_class:
            needed = target_per_class - current_len
            for _ in range(needed):
                parent_idx = rng.integers(0, current_len)
                parent = samples[parent_idx]
                noise = rng.normal(0, 0.02, size=parent.shape).astype(np.float32)
                aug = parent + noise
                norm = np.linalg.norm(aug)
                if norm > 0:
                    aug /= norm
                final_vecs.append(aug)
                final_labels.append(cls_idx)

    x = torch.tensor(np.array(final_vecs, dtype=np.float32))
    y = torch.tensor(final_labels, dtype=torch.long)
    return x, y


def train_model(
    x: torch.Tensor,
    y: torch.Tensor,
    epochs: int = 30,
    batch_size: int = 32,
    lr: float = 1e-3,
    hidden_dim: int = 128,
) -> tuple[nn.Module, float]:
    """訓練 PostureMLP 模型。"""
    torch.manual_seed(42)
    dataset = TensorDataset(x, y)

    n_val = max(1, int(len(dataset) * 0.2))
    n_train = len(dataset) - n_val
    train_set, val_set = torch.utils.data.random_split(dataset, [n_train, n_val])

    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_set, batch_size=batch_size, shuffle=False)

    model = PostureMLP.build_torch_module(in_features=384, hidden_dim=hidden_dim, num_classes=4)

    class_counts = torch.bincount(y, minlength=4).float()
    weights = 1.0 / torch.clamp(class_counts, min=1.0)
    weights /= weights.sum()

    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_val_acc = 0.0
    best_state = None

    print(f"🚀 開始訓練姿態分類頭 (Epochs: {epochs}, 樣本總數: {len(x)})...")

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        for bx, by in train_loader:
            optimizer.zero_grad()
            out = model(bx)
            loss = criterion(out, by)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(bx)
        scheduler.step()

        # 驗證
        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for vx, vy in val_loader:
                preds = model(vx).argmax(dim=1)
                correct += (preds == vy).sum().item()
                total += len(vy)

        val_acc = correct / max(1, total)
        if val_acc >= best_val_acc:
            best_val_acc = val_acc
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

        if epoch % 5 == 0 or epoch == epochs:
            avg_loss = total_loss / n_train
            print(f"Epoch {epoch:02d}/{epochs:02d} - Loss: {avg_loss:.4f} - Val Acc: {val_acc*100:.1f}%")

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, best_val_acc


def register_in_db(
    db_path: Path,
    model_version: str,
    weights_path: Path,
    sha256: str,
    accuracy: float,
) -> None:
    """將姿態模型註冊至 SQLite model_registry 資料表。"""
    if not db_path.is_file():
        return

    metadata = {
        "model_version": model_version,
        "model_name": "輕量靜態貓咪姿態分類器 (Cat Posture MLP)",
        "backend": "PyTorch / NumPy",
        "input_features": 384,
        "feature_extractor": "dinov2_vits14",
        "classes": list(POSTURE_CLASSES),
        "classes_zh": POSTURE_LABELS_ZH,
        "val_accuracy": round(accuracy, 4),
        "trigger_mode": "low_frequency_conditional",
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    con = sqlite3.connect(str(db_path))
    con.execute(
        """INSERT OR REPLACE INTO model_registry(
            model_version, model_name, backend, source_url, sha256, status, metadata_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            model_version,
            "cat_posture_mlp",
            "PyTorch/NumPy",
            "local://models/behavior/" + weights_path.name,
            sha256,
            "active",
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        ),
    )
    con.commit()
    con.close()
    print(f"🏛️ 成功將模型註冊至 model_registry 資料表：{model_version} (準確率: {accuracy*100:.1f}%)")


def main() -> int:
    parser = argparse.ArgumentParser(description="訓練輕量貓咪姿態分類器 (DINOv2 + MLP)")
    parser.add_argument(
        "--output",
        type=Path,
        default=posture_model_file(),
        help="輸出權重檔路徑 (.pth)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path(r"C:\ProgramData\MeowKeeperPlus\Data\meowkeeper.db"),
        help="SQLite 資料庫路徑",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path(r"C:\ProgramData\MeowKeeperPlus\Data\datasets\cats-behaviors-test"),
        help="額外開源資料集目錄",
    )
    parser.add_argument("--epochs", type=int, default=25, help="訓練週期數")
    parser.add_argument("--batch-size", type=int, default=32, help="批次大小")
    parser.add_argument("--lr", type=float, default=1e-3, help="學習率")
    parser.add_argument("--version", type=str, default=DEFAULT_POSTURE_MODEL_VERSION, help="模型版本號")
    parser.add_argument("--device", type=str, default="cpu", help="推論運算設備")
    parser.add_argument("--max-extract", type=int, default=0, help="無特徵之 C701 截圖最多即時計算張數")
    parser.add_argument("--no-db-register", action="store_true", help="不寫入 SQLite model_registry")

    args = parser.parse_args()

    embedder: DinoV2Embedder | None = None
    if args.max_extract > 0 or args.dataset_dir.is_dir():
        try:
            print(f"🔧 初始化 DINOv2 特徵提取器 ({args.device})...")
            embedder = DinoV2Embedder(device=args.device)
        except Exception as e:
            print(f"⚠️ DINOv2 初始化略過：{e}")

    # 1. 載入特徵樣本
    c701_vecs, c701_labels = extract_c701_crops_and_features(
        args.db_path,
        max_to_extract=args.max_extract,
        embedder=embedder,
    )
    ds_vecs, ds_labels = load_dataset_folder(args.dataset_dir, embedder=embedder)

    all_vecs = c701_vecs + ds_vecs
    all_labels = c701_labels + ds_labels

    print(f"📦 總有效特徵向量數: {len(all_vecs)}")

    # 2. 數據增強與類別平衡
    x_tensor, y_tensor = augment_and_balance(all_vecs, all_labels, target_per_class=120)

    # 3. 訓練模型
    trained_model, val_acc = train_model(
        x_tensor,
        y_tensor,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
    )

    # 4. 保存權重
    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    state = {
        "state_dict": trained_model.state_dict(),
        "model_version": args.version,
        "classes": list(POSTURE_CLASSES),
        "val_accuracy": float(val_acc),
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    torch.save(state, output_path)

    sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest().upper()
    print(f"💾 權重保存成功：{output_path} (大小: {output_path.stat().st_size / 1024:.1f} KB, SHA-256: {sha256[:16]}...)")

    # 同步至 ProgramData App models 目錄
    program_data_dest = Path(r"C:\ProgramData\MeowKeeperPlus\App\models\behavior") / output_path.name
    try:
        program_data_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, program_data_dest)
        print(f"📋 同步至主機安裝目錄：{program_data_dest}")
    except Exception as exc:
        print(f"⚠️ 無法複製至主機目錄：{exc}")

    # 5. 寫入 Manifest
    manifest_path = output_path.parent / "model_manifest.json"
    manifest_data = {
        "model_name": output_path.name,
        "model_version": args.version,
        "sha256": sha256,
        "classes": list(POSTURE_CLASSES),
        "accuracy": round(val_acc, 4),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 6. 註冊至 SQLite 資料庫
    if not args.no_db_register:
        register_in_db(args.db_path, args.version, output_path, sha256, val_acc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
