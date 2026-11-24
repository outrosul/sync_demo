"""守卫：.bat 纯 ASCII + CRLF + 无 BOM；其余文本 LF。"""

import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_bat_files_are_ascii_crlf_no_bom():
    for path in ROOT.rglob("*.bat"):
        data = path.read_bytes()
        # 无 BOM
        assert not data.startswith(b"\xef\xbb\xbf"), "{} 不能有 BOM".format(path)
        # 纯 ASCII
        try:
            data.decode("ascii")
        except UnicodeDecodeError:
            raise AssertionError("{} 必须纯 ASCII".format(path))
        # CRLF：每个 \n 前面都要有 \r
        text = data.decode("ascii")
        for i, ch in enumerate(text):
            if ch == "\n":
                assert i > 0 and text[i - 1] == "\r", "{} 必须 CRLF".format(path)


def test_other_text_files_are_lf():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts or "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        if path.suffix in (".bat", ".xlsx", ".png", ".jpg"):
            continue
        if path.suffix not in (".py", ".md", ".txt", ".gitignore", ".gitattributes"):
            continue
        data = path.read_bytes()
        crlf = b"\r\n" in data
        assert not crlf, "{} 必须 LF（不该有 CRLF）".format(path)
