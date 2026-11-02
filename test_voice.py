"""只测纯函数：情绪选择、参考目录回退。不需要 AstrBot。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tests", "stub"))
sys.path.insert(0, os.path.dirname(__file__))


class Dummy:
    def __init__(self, cfg):
        self.cfg = cfg

    def _c(self, k, d):
        v = self.cfg.get(k, d)
        return d if v is None else v

    # 借用真实实现
    _ref_path = None


def load_impl():
    # 直接从 main.py 里抠出两个纯函数，避免 import astrbot
    import re
    src = open(os.path.join(os.path.dirname(__file__), "main.py"), encoding="utf-8").read()
    ns = {"os": os}
    m = re.search(r"EMOTION_HINTS = \{[\s\S]*?\n\}", src)
    exec(m.group(0), ns)
    m2 = re.search(r"DEFAULT_EMOTION = .*", src)
    exec(m2.group(0), ns)
    m3 = re.search(r"    def _ref_path\(self, emotion: str\)[\s\S]*?\n        return \"\", \"\"", src)
    body = m3.group(0).replace("    def _ref_path(self, emotion: str)", "def _ref_path(self, emotion: str)")
    exec(body, ns)
    m4 = re.search(r"    @staticmethod\n    def _pick_emotion\(text: str\)[\s\S]*?\n        return best", src)
    b4 = m4.group(0).replace("    @staticmethod\n", "").replace("    def", "def")
    exec(b4, ns)
    return ns


def main():
    ns = load_impl()
    pick = ns["_pick_emotion"]
    cases = [
        ("哈哈哈太好了", "高兴"),
        ("想你了 抱抱", "害羞"),
        ("哼 不理你", "生气"),
        ("来不及了怎么办", "着急"),
        ("今天吃了面", "平静"),
    ]
    for text, want in cases:
        got = pick(text)
        assert got == want, f"{text!r} 期望 {want} 实际 {got}"
        print(f"ok  {text:14} -> {got}")

    # _ref_path 回退
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "平静"))
    open(os.path.join(root, "平静", "ref.wav"), "wb").write(b"RIFF")
    open(os.path.join(root, "平静", "asr.txt"), "w", encoding="utf-8").write("参考文本")
    d = Dummy({"ref_audio_root": root})

    class S:
        pass
    s = S()
    s.cfg = d.cfg
    s._c = d._c.__get__(s)
    ref, txt = ns["_ref_path"](s, "生气")       # 生气不存在 -> 回退平静
    assert ref.endswith("ref.wav") and txt == "参考文本", (ref, txt)
    print("ok  目录回退 -> 平静/ref.wav，文本=参考文本")
    print("\n全部通过（6 项）")


if __name__ == "__main__":
    main()
