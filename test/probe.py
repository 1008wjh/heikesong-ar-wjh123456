import cv2, onnxruntime as ort, numpy as np, sys

print("cv2", cv2.__version__)
print("has FaceDetectorYN:", hasattr(cv2, "FaceDetectorYN"))

m = r"E:\ar-memory\models\arcface_mbf.onnx"
s = ort.InferenceSession(m, providers=["CPUExecutionProvider"])
for i in s.get_inputs():
    print("IN ", i.name, i.shape, i.type)
for o in s.get_outputs():
    print("OUT", o.name, o.shape, o.type)

img = cv2.imread(r"E:\ar-memory\test\messi5.jpg")
h, w = img.shape[:2]
print("image", w, h)
det = cv2.FaceDetectorYN.create(r"E:\ar-memory\models\yunet.onnx", "", (w, h),
                                score_threshold=0.6, nms_threshold=0.3, top_k=5000)
ok, faces = det.detect(img)
print("ok", ok, "faces", None if faces is None else faces.shape)
if faces is not None:
    print(np.round(faces[0], 1))
