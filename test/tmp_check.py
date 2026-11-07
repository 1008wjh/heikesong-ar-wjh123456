import io

s = io.open(r"E:\ar-memory\src\agent.py", encoding="utf-8").read()
for k in ["window", "next_actions", "node_recommend", 'add_node("recommend")', "mode: str = "]:
    print(k, "->", s.count(k))
