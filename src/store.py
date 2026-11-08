"""人脉库：JSON 存储 + 向量匹配 + 控制台字段（图片 / 名字 / 身份 / 信息 1-3）。

没有用数据库，因为三天黑客松里"能直接打开看、能手动改"比性能重要：
data/people.json 就是一个正常 JSON，出问题现场用记事本都能救。

一条记录长这样：
{
  "id": "p_ab12cd",
  "name": "王姐",
  "role": "产品经理 / 前同事",       <- 身份或关系，一行
  "info": ["答应下周给报价单", "", ""], <- 最多 3 条，控制台里叫"信息 1/2/3"
  "photo": "photos/p_ab12cd.jpg",      <- 控制台上传的那张脸（缩略图）
  "embeddings": [[...512 维...], ...], <- 一个人可以有多张底片
  "first_met": "2026-11-03", "last_met": "2026-11-04", "meet_count": 2
}
老数据（tags / notes / talking_points）会自动迁移，见 _migrate()。
"""
import json
import os
import threading
import time
import uuid

import numpy as np

DATA_DIR = os.environ.get("AR_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
PEOPLE_PATH = os.path.join(DATA_DIR, "people.json")
PHOTO_DIR = os.path.join(os.path.dirname(os.path.abspath(PEOPLE_PATH)), "photos")
# 相似度阈值：0.40 对 MobileFaceNet 偏松一点，现场好过；太高会"认不出熟人"
MATCH_THRESHOLD = float(os.environ.get("AR_MATCH_THRESHOLD", "0.40"))
MAX_INFO = 3
MAX_CONV = 20          # 每个人最多留多少条对话记录


class Store:
    def __init__(self, path=PEOPLE_PATH):
        self.path = os.path.abspath(path)
        self.photo_dir = os.path.join(os.path.dirname(self.path), "photos")
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        os.makedirs(self.photo_dir, exist_ok=True)
        self._data = self._load()
        self._migrate()
        if not any(p.get("owner") for p in self._data["people"]):
            for p in self._data["people"]:
                if (p.get("name") or "").strip().lower() in ("wjh", "我", "me"):
                    p["owner"] = True
                    self._save()

    # ---------- 读写 ----------

    def _load(self):
        if not os.path.exists(self.path):
            return {"people": [], "version": 2}
        try:
            # utf-8-sig: tolerate files saved by Notepad/PowerShell with a BOM
            with open(self.path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("people.json top-level is not an object")
        except Exception as e:
            # a corrupt people.json must not take the whole HTTP service down
            bad = self.path + ".bad-" + time.strftime("%Y%m%d-%H%M%S")
            try:
                os.replace(self.path, bad)
            except Exception:
                pass
            print(f"[store] people.json parse failed ({e}); moved to {bad}; starting empty",
                  flush=True)
            return {"people": [], "version": 2}
        data.setdefault("people", [])
        return data

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def _migrate(self):
        """把 v1 记录（tags/notes）补成 v2 字段（role/info/photo）。幂等。"""
        changed = False
        for p in self._data["people"]:
            if "role" not in p:
                p["role"] = " · ".join(p.get("tags") or [])
                changed = True
            if "info" not in p:
                notes = [n.get("text", "") for n in (p.get("notes") or [])]
                merged = [t for t in notes if t][-MAX_INFO:]
                if not merged:
                    merged = [t for t in (p.get("talking_points") or [])][:MAX_INFO]
                p["info"] = list(merged) + [""] * (MAX_INFO - len(merged))
                changed = True
            if "photo" not in p:
                p["photo"] = ""
                changed = True
            if "voice_embeddings" not in p:
                p["voice_embeddings"] = []
                changed = True
            if "simple" not in p:
                # 新字段：简单数据（会直接上镜片）。老记录的 role/info 先搬进来。
                simple = {}
                if (p.get("role") or "").strip():
                    simple["身份"] = p["role"].strip()
                for i, line in enumerate(p.get("info") or [], start=1):
                    if str(line).strip():
                        simple[f"信息{i}"] = str(line).strip()
                p["simple"] = simple
                changed = True
            if "complex" not in p:
                p["complex"] = {}
                changed = True
            if "conversations" not in p:
                # 对话分析模块留下的记录：[{at, transcript, summary, items}]
                p["conversations"] = []
                changed = True
            p["info"] = list(p.get("info") or [])[:MAX_INFO]

        # 顺手清理历史脏字符（早期一次编码事故留下的孤立代理项），否则 JSON 编码会炸
        for p in self._data["people"]:
            if _clean(p.get("name")) != p.get("name"):
                p["name"] = _clean(p.get("name"))
                changed = True
            if _clean(p.get("role")) != p.get("role"):
                p["role"] = _clean(p.get("role"))
                changed = True
            info = [_clean(x) for x in (p.get("info") or [])]
            if info != (p.get("info") or []):
                p["info"] = info
                changed = True
        if changed:
            self._save()

    # ---------- 查询 ----------

    def people(self):
        with self._lock:
            return [self._public(p) for p in self._data["people"]]

    def get(self, pid):
        with self._lock:
            p = self._raw(pid)
            return self._public(p) if p else None

    def match(self, embedding, threshold=MATCH_THRESHOLD):
        """返回 (person | None, similarity)。多人多张底片取最大值。"""
        emb = np.asarray(embedding, dtype=np.float32)
        best, best_s = None, -1.0
        with self._lock:
            for p in self._data["people"]:
                for e in p.get("embeddings", []):
                    s = float(np.dot(emb, np.asarray(e, dtype=np.float32)))
                    if s > best_s:
                        best, best_s = p, s
        if best is None or best_s < threshold:
            return None, best_s
        return best, best_s

    def voice_index(self):
        """给声纹匹配用的轻量视图（只带声纹向量，不带 512 维人脸底片）。"""
        with self._lock:
            return [
                {
                    "id": p["id"],
                    "name": p.get("name", ""),
                    "role": p.get("role", ""),
                    "voice_embeddings": list(p.get("voice_embeddings") or []),
                }
                for p in self._data["people"]
                if p.get("voice_embeddings")
            ]

    def add_voice(self, pid, embedding, replace=True):
        """给某人挂一条声纹。默认替换（声纹不像人脸，混多条容易互相拉扯）。"""
        with self._lock:
            p = self._raw(pid)
            if not p:
                return None
            vec = [float(x) for x in embedding]
            if replace:
                p["voice_embeddings"] = [vec]
            else:
                p.setdefault("voice_embeddings", []).append(vec)
            self._save()
            return self._public(p)

    def set_voice_clip(self, pid, at=None):
        """记一下"最近一次注册时的录音"的时间戳（录音文件由 server 落盘）。"""
        with self._lock:
            p = self._raw(pid)
            if not p:
                return None
            p["voice_clip_at"] = int(at or time.time())
            self._save()
            return self._public(p)

    # ---------- 写入（控制台用） ----------

    def save(self, pid=None, name="", role="", info=None, embedding=None,
             photo=None, replace_embeddings=False, simple=None, complex_=None):
        """新建或更新一个人。pid 为空 → 按名字找，找不到才新建。

        embedding 传了才会动底片：新建时追加；编辑且 replace_embeddings=True 时整体替换
        （换照片就该换特征，否则会把两个特征混在一条记录里）。
        simple / complex_ 是眼镜用的两档数据：simple 直接上镜片，complex 按需拉取。
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("名字不能为空")
        role = (role or "").strip()
        info = [str(x).strip() for x in (info or [])][:MAX_INFO]
        info += [""] * (MAX_INFO - len(info))

        with self._lock:
            today = time.strftime("%Y-%m-%d")
            person = self._raw(pid) if pid else self._by_name(name)
            created = person is None
            if created:
                person = {
                    "id": "p_" + uuid.uuid4().hex[:6],
                    "name": name,
                    "role": "",
                    "info": ["", "", ""],
                    "photo": "",
                    "first_met": today,
                    "last_met": today,
                    "meet_count": 1,
                    "embeddings": [],
                    "voice_embeddings": [],
                    "tags": [],
                    "notes": [],
                    "talking_points": [],
                }
                self._data["people"].append(person)

            person["name"] = name
            person["role"] = role
            person["info"] = info
            if isinstance(simple, dict):
                person["simple"] = simple
            if isinstance(complex_, dict):
                person["complex"] = complex_
            if photo is not None:
                person["photo"] = photo
            if embedding is not None:
                vec = [float(x) for x in embedding]
                if replace_embeddings or created:
                    person["embeddings"] = [vec]
                else:
                    person["embeddings"].append(vec)
            self._save()
            return self._public(person)

    def set_photo(self, pid, relpath):
        with self._lock:
            p = self._raw(pid)
            if not p:
                return None
            p["photo"] = relpath
            self._save()
            return self._public(p)

    def photo_path(self, pid):
        with self._lock:
            p = self._raw(pid)
            if not p or not p.get("photo"):
                return None
            rel = p["photo"]
        full = os.path.abspath(os.path.join(os.path.dirname(self.path), rel))
        return full if os.path.isfile(full) else None

    def owner(self):
        for p in self._data["people"]:
            if p.get("owner"):
                return p
        return None

    def set_owner(self, pid):
        with self._lock:
            for p in self._data["people"]:
                p["owner"] = p["id"] == pid
            self._save()

    def remove(self, pid):
        """删人，返回被删除的图片绝对路径（调用方负责删文件）。"""
        with self._lock:
            for p in list(self._data["people"]):
                if p["id"] == pid:
                    self._data["people"].remove(p)
                    self._save()
                    photo = p.get("photo") or ""
                    return os.path.join(os.path.dirname(self.path), photo) if photo else ""
            return None

    # ---------- 兼容老接口 ----------

    def enroll(self, name, embedding, note=""):
        """同名则追加底片（同一人不同角度），否则新建。"""
        with self._lock:
            person = self.save(name=name, embedding=embedding)
            if note:
                self.add_note(person["id"], note)
                person = self.get(person["id"])
            return person

    def add_note(self, pid, text):
        with self._lock:
            p = self._raw(pid)
            if not p:
                return None
            p.setdefault("notes", []).append({"ts": _now(), "text": text})
            p["last_met"] = time.strftime("%Y-%m-%d")
            p["meet_count"] = int(p.get("meet_count", 0)) + 1
            self._save()
            return self._public(p)

    def set_field(self, pid, field, value):
        with self._lock:
            p = self._raw(pid)
            if not p:
                return None
            p[field] = value
            self._save()
            return self._public(p)

    def touch(self, pid):
        with self._lock:
            p = self._raw(pid)
            if not p:
                return None
            p["last_met"] = time.strftime("%Y-%m-%d")
            p["meet_count"] = int(p.get("meet_count", 0)) + 1
            self._save()
            return self._public(p)

    # ---------- 对话分析 ----------

    def merge_facts(self, pid, items, transcript="", summary=""):
        """把「对话里提取出来的要点」并进档案。

        - 要点进 complex["对话要点"]（dict，同 key 覆盖，保留最新一条）
        - 原文和摘要进 conversations（最多留 MAX_CONV 条）
        - 不碰 embeddings / voice_embeddings，也不改 simple（simple 是给镜片直显的，得人工确认）
        """
        with self._lock:
            p = self._raw(pid)
            if not p:
                return None
            # 自动建号的陌生人：库里只留声纹（和名字/一句说明），
            # 不自动写「对话要点」也不存对话原文 —— 避免模型/转写幻觉变成"详细数据"。
            # 人工在控制台改过名、补过资料之后（名字不再以「陌生人」开头），才开始积累。
            if str(p.get("name") or "").startswith("陌生人"):
                p["last_met"] = time.strftime("%Y-%m-%d")
                self._save()
                return self._public(p)
            facts = p.setdefault("complex", {}).setdefault("对话要点", {})
            if not isinstance(facts, dict):
                facts = {}
                p["complex"]["对话要点"] = facts
            for it in items or []:
                key = str(it.get("key") or "其他").strip()[:8]
                val = str(it.get("value") or "").strip()[:40]
                if val:
                    facts[key] = val

            convs = p.setdefault("conversations", [])
            convs.append(
                {
                    "at": _now(),
                    "summary": _clean(summary or ""),
                    "transcript": _clean(transcript or ""),
                    "items": [
                        {"key": str(i.get("key") or ""), "value": str(i.get("value") or "")}
                        for i in (items or [])
                    ],
                }
            )
            p["conversations"] = convs[-MAX_CONV:]
            p["last_met"] = time.strftime("%Y-%m-%d")
            self._save()
            return self._public(p)

    # ---------- 内部 ----------

    def _raw(self, pid):
        for p in self._data["people"]:
            if p["id"] == pid:
                return p
        return None

    def _by_name(self, name):
        for p in self._data["people"]:
            if p["name"] == name:
                return p
        return None

    @staticmethod
    def _public(p):
        q = dict(p)
        q["embedding_count"] = len(p.get("embeddings", []))
        q["voice_count"] = len(p.get("voice_embeddings") or [])
        q.pop("embeddings", None)        # 别把 512 维向量发给前端
        q.pop("voice_embeddings", None)  # 192 维也一样
        return q


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


def _clean(s):
    """去掉孤立代理项等非法字符：能存进 JSON、也能正常 UTF-8 编码。"""
    if not isinstance(s, str):
        return s
    return s.encode("utf-8", "replace").decode("utf-8")
