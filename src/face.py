"""人脸检测 + 对齐 + 特征提取。

依赖：opencv-python-headless、onnxruntime、numpy
模型：yunet.onnx（检测，232KB）、arcface_mbf.onnx（512 维特征，13MB）
两者都能在 CPU 上跑，单帧几十毫秒，所以"云端"可以就是背包里这台笔记本。
"""
import os
import threading

import cv2
import numpy as np
import onnxruntime as ort

# ArcFace 标准 5 点模板（112x112），顺序：左眼、右眼、鼻尖、左嘴角、右嘴角
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)

MODEL_DIR = os.environ.get("AR_MODEL_DIR", os.path.join(os.path.dirname(__file__), "..", "models"))


class FaceEngine:
    """线程安全的检测+特征提取器（HTTP 服务是多线程的，所以内部加锁）。"""

    def __init__(self, model_dir=MODEL_DIR):
        self._det = cv2.FaceDetectorYN.create(
            os.path.join(model_dir, "yunet.onnx"),
            "",
            (640, 480),
            score_threshold=0.6,
            nms_threshold=0.3,
            top_k=5000,
        )
        self._sess = ort.InferenceSession(
            os.path.join(model_dir, "arcface_mbf.onnx"),
            providers=["CPUExecutionProvider"],
        )
        self._input = self._sess.get_inputs()[0].name
        self._lock = threading.Lock()

    # ---------- 对外 ----------

    def scan(self, img_bgr):
        """检测图中所有人脸，返回 [{'box','score','landmarks','embedding'}]。"""
        if img_bgr is None or img_bgr.size == 0:
            return []
        with self._lock:
            faces = self._detect_locked(img_bgr)
            if not faces:
                return []
            crops = [self._align(img_bgr, f["landmarks"]) for f in faces]
            embs = self._embed_batch(crops)
        for f, e in zip(faces, embs):
            f["embedding"] = e
        return faces

    def embedding_of_aligned(self, aligned_bgr):
        with self._lock:
            return self._embed_batch([aligned_bgr])[0]

    # ---------- 内部 ----------

    def _detect_locked(self, img_bgr):
        h, w = img_bgr.shape[:2]
        self._det.setInputSize((w, h))
        ok, raw = self._det.detect(img_bgr)
        if not ok or raw is None:
            return []
        out = []
        for f in raw:
            out.append(
                {
                    "box": [float(v) for v in f[:4]],  # x, y, w, h
                    "score": float(f[14]),
                    "landmarks": f[4:14].reshape(5, 2).astype(np.float32),
                }
            )
        return out

    @staticmethod
    def _align(img_bgr, landmarks):
        mat, _ = cv2.estimateAffinePartial2D(landmarks, ARCFACE_TEMPLATE, method=cv2.LMEDS)
        if mat is None:  # 理论上不会发生，兜底给个中心裁剪
            h, w = img_bgr.shape[:2]
            s = min(h, w)
            crop = img_bgr[(h - s) // 2 : (h + s) // 2, (w - s) // 2 : (w + s) // 2]
            return cv2.resize(crop, (112, 112))
        return cv2.warpAffine(img_bgr, mat, (112, 112), borderValue=0)

    def _embed_batch(self, crops):
        if not crops:
            return []
        batch = np.stack([self._to_blob(c) for c in crops]).astype(np.float32)
        feats = self._sess.run(None, {self._input: batch})[0]
        feats = feats.reshape(len(crops), -1)
        norms = np.linalg.norm(feats, axis=1, keepdims=True) + 1e-9
        return (feats / norms).astype(np.float32)

    @staticmethod
    def _to_blob(aligned_bgr):
        rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        rgb = (rgb - 127.5) / 127.5
        return np.transpose(rgb, (2, 0, 1))  # CHW


def cosine(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))
