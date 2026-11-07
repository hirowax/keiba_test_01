"""
テスト用の実ページ（tests/fixtures/*.html）の読み込み。

実ページは netkeiba・JRA のページを丸ごと保存したもの。リポジトリが公開なので git に入れず、手元だけに置く
（2026-11-07 ユーザー決定。過去の履歴からも消した。写しは ~/Desktop/netkeiba_private_backup/20261107/tests/fixtures/）。
手元にページが無いときは、そのテストを理由付きで飛ばす（unittest.SkipTest）。テストを実行したら skipped の数を見ること。
"""
import unittest
from pathlib import Path

FIX = Path(__file__).resolve().parent / "fixtures"


def fixture_path(name: str) -> Path:
    p = FIX / name
    if not p.exists():
        raise unittest.SkipTest(f"手元に実ページ tests/fixtures/{name} が無い（公開しないため git に入れていない）")
    return p


def read_fixture(name: str) -> str:
    return fixture_path(name).read_text(encoding="utf-8")
