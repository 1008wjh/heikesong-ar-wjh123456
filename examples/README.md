# 示例图片

仓库里**只放这几张公开示例**，用来验证识别链路；真实人脉数据不进仓库。

| 文件 | 内容 | 用来试什么 |
|---|---|---|
| `sample-1.jpg` | 单人、运动场景、侧脸但正脸特征清楚 | `python tools\try_face.py examples\sample-1.jpg` |
| `sample-2.jpg` | 单人、经典测试图，分辨率较高 | 同上；也能看出大图会先缩到 960 长边 |
| `sample-3.jpg` | 单人、室内光线 | 同上；测"库里没有这个人"时的陌生人分支 |

这几张图都来自公开数据集（OpenCV 官方样图、`ageitgey/face_recognition` 的示例目录），可以自由再分发。

用法：

```powershell
python tools\try_face.py examples\sample-1.jpg     # known / unknown + sim + 耗时
```

想往控制台里加人，是把照片**上传**到 `data/photos/`，不要往这个目录塞——

```
data/people.json          你本机的人脉库（.gitignore 之外，别提交真实数据）
data/photos/<id>.jpg      对应的照片
```

`data/` 里现有的几条是演示数据，如果你要换成自己的，直接删掉 `data/people.json` 和 `data/photos/` 重开即可。
