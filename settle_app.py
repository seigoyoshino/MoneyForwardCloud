# -*- coding: utf-8 -*-
"""
清算ビュー専用のエントリーファイル（共有用）

cloud_app.py と中身は同じだが、常に清算ビューだけを表示する。
Streamlit Community Cloud は「リポジトリ + ブランチ + ファイル名」でアプリを識別するため、
同じ cloud_app.py では2つ目のアプリを作れない。そのため別名のこのファイルを用意している。

Secrets は cloud_app.py と同じものを設定すればよい（VIEW は指定しなくてよい）。
"""

import runpy
from pathlib import Path

import streamlit as st

# このアプリは常に清算ビューに固定する
st.session_state["_force_view"] = "settle"

runpy.run_path(str(Path(__file__).resolve().parent / "cloud_app.py"), run_name="__main__")
