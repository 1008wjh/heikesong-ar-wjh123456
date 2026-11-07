"""把历史上误存进人脸底片里的声纹向量挪回 voice_embeddings。

起因：眼镜端"陌生人自动入库"早期调用了 enroll()，把 192 维的声纹写进了 512 维的人脸底片，
导致之后任何人脸匹配都抛 "shapes (512,) and (192,) not aligned"。
这个脚本可以重复跑：512 维留在 embeddings，192 维挪到 voice_embeddings，其它长度的直接丢。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import store as store_mod  # noqa: E402


def main():
    st = store_mod.Store()
    moved = dropped = 0
    with st._lock:
        for p in st._data["people"]:
            face, voice = [], list(p.get("voice_embeddings") or [])
            for v in p.get("embeddings") or []:
                n = len(v)
                if n == 512:
                    face.append(v)
                elif n == 192:
                    voice.append(v)
                    moved += 1
                else:
                    dropped += 1
            p["embeddings"] = face
            p["voice_embeddings"] = voice
        st._save()
    print("moved %d voice vectors -> voice_embeddings, dropped %d unknown" % (moved, dropped))
    for p in st.people():
        print("  %-8s face=%d voice=%d" % (p.get("name"), p.get("embedding_count", 0), p.get("voice_count", 0)))


if __name__ == "__main__":
    main()
