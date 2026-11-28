"""Check the isolated native smoke database after the window closes."""
import json
import os
import sqlite3
from pathlib import Path

database = Path(os.environ["CAMPUS_DATA_DIR"]) / "career.sqlite"
assert "rich-editor-native-" in str(database), "Use the isolated smoke database"
with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
    content = connection.execute("SELECT content FROM knowledge_documents").fetchone()[0]
    assert json.loads(content)["format"] == "campus-knowledge-v1"
    assert "关闭窗口前最后输入的内容" in content
    assert "data:image/png;base64," in content
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
print("PASS: window close flushes rich text and images to SQLite")
