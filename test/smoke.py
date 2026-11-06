"""冒烟测试：验证检测、对齐、特征、相似度是否都正常。

判断标准（人工核对）：
  - 同一张图的微扰（镜像/调亮/裁剪）相似度应 > 0.55
  - 不同人（Messi vs Lena）相似度应 < 0.15
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from face import FaceEngine, cosine  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
eng = FaceEngine()


def embed(path):
    img = cv2.imread(path)
    assert img is not None, f"读不到 {path}"
    faces = eng.scan(img)
    print(f"  {os.path.basename(path)}: 检出 {len(faces)} 张脸", 
          [round(f['score'], 2) for f in faces])
    assert faces, f"{path} 没检出人脸"
    return faces[0]["embedding"], img, faces[0]


messi, m_img, m_face = embed(os.path.join(HERE, "messi5.jpg"))
lena, _, _ = embed(os.path.join(HERE, "lena.jpg"))

# 微扰：镜像 + 调亮
pert = np.clip(cv2.flip(m_img, 1).astype(np.float32) * 1.25, 0, 255).astype(np.uint8)
faces = eng.scan(pert)
assert faces, "微扰图没检出脸"
pm = faces[0]["embedding"]

s_same = cosine(messi, pm)
s_diff = cosine(messi, lena)
print(f"\n同人微扰相似度: {s_same:.3f}   (期望 > 0.55)")
print(f"不同人相似度  : {s_diff:.3f}   (期望 < 0.15)")
print(f"判定阈值 0.40 下: 同人{'通过' if s_same > 0.40 else '不通过'}，"
      f"不同人{'通过' if s_diff < 0.40 else '不通过'}")
assert s_same > 0.55, "同人相似度偏低，检查对齐模板顺序"
assert s_diff < 0.15, "不同人相似度偏高，检查归一化"
print("\n人脸管线 OK")
