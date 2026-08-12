# -*- coding: utf-8 -*-
"""
家計ダッシュボード クラウド版（閲覧専用）

- Streamlit Community Cloud にデプロイして使う（このファイルは公開リポジトリに置く）
- 家計データは含まない。非公開リポジトリの cloud_data/bundle.enc（暗号化済み）を
  GitHub API 経由で取得し、Secrets の ENCRYPTION_KEY で復号して表示する
- ログインメールで表示を出し分け:
    FULL_USERS   → 全タブ（概要/カテゴリ/固定費・サブスク/清算/明細）
    SETTLE_USERS → 清算ビューのみ
- 必要な Secrets: ENCRYPTION_KEY / GITHUB_TOKEN / DATA_REPO / FULL_USERS / SETTLE_USERS
- 編集機能なし。データの更新はローカルPCで「update_cloud_data.bat」を実行
"""

import json
import math
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------- カラーパレット（Green基調） ----------
INK = "#000000"
SUBTLE = "#626264"
PAPER = "#F8F8FB"
LINE = "#E0E0E3"
GREEN_1200, GREEN_900, GREEN_600 = "#032213", "#115A36", "#259D63"
GREEN_400, GREEN_200 = "#51B883", "#9BD4B5"
CYAN_800, CYAN_600, CYAN_400 = "#006F83", "#00A3BF", "#2BC8E4"
GRAY_800, GRAY_600, GRAY_400, GRAY_200 = "#333333", "#666666", "#999999", "#CCCCCC"
SUCCESS, ERROR = "#197A4B", "#CE0000"
INDIGO, SHU = GREEN_900, GRAY_600

CAT_COLORS = {
    "住宅": GREEN_1200, "食費": GREEN_900, "趣味・娯楽": GREEN_600,
    "教養・教育": GREEN_400, "日用品": GREEN_200,
    "通信費": CYAN_600, "水道・光熱費": CYAN_400, "交通費": CYAN_800,
    "税・社会保障": GRAY_800, "自動車": GRAY_600, "健康・医療": GRAY_400,
    "衣服・美容": "#B3B3B6", "特別な支出": GRAY_200,
    "保険": SUCCESS, "現金・カード": "#7A7A7E",
    "その他": "#C4C4C7", "未分類": "#E0E0E0",
}
FALLBACK_COLORS = [GREEN_600, CYAN_600, GREEN_400, CYAN_800, GREEN_200]

SPECIAL_EXP_CATS = ["特別な支出"]
SPECIAL_INC_SUBS = ["不動産所得"]
WARIKAN_SUB = "割り勘代"
FIXED_CATS = ["住宅", "水道・光熱費", "通信費", "保険", "税・社会保障"]
FIXED_SUBS = ["サブスク", "習いごと"]
RENT_SUBS_DEFAULT = ["ローン返済", "管理費・積立金"]
SETTLE_FIXED_DEFAULT = ["水道代", "電気代", "割り勘（食料品）", "割り勘（日用品）"]
SPECIAL_FULL_DEFAULT: list[str] = []  # 実データの設定(settlement.json)で上書きされる
SPECIAL_SPLIT_DEFAULT = ["その他（割り勘）", "旅行（割り勘）"]


def cat_color(cat: str) -> str:
    return CAT_COLORS.get(cat, FALLBACK_COLORS[hash(cat) % len(FALLBACK_COLORS)])


def fyen(n) -> str:
    n = 0 if pd.isna(n) else n
    return ("−" if n < 0 else "") + f"¥{abs(round(n)):,.0f}"


def fyen_x(v) -> str:
    """清算ビューの金額表示（円未満は切り捨て）。"""
    v = 0 if v is None or pd.isna(v) else v
    n = math.floor(abs(v))
    return ("−" if v < 0 else "") + f"¥{n:,.0f}"


def man_label(v) -> str:
    if v is None or pd.isna(v) or v == 0:
        return ""
    return f"{v / 10000:,.0f}万" if abs(v) >= 10000 else f"{v:,.0f}"


def month_label(m: str) -> str:
    return f"{m[:4]}年{int(m[5:7])}月" if isinstance(m, str) and len(m) >= 7 else str(m)


HOVER_YEN = "%{y:,.0f}円<extra>%{fullData.name}</extra>"

def html_table(df, height: int | None = None, **_ignored):
    """数値列を右揃えにした自前HTMLテーブルを描画する。
    Streamlit標準の表はCanvasに描画されるため文字揃えを変更できず、代わりにこれを使う。
    """
    import re as _re
    from html import escape
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    if df.empty:
        st.caption("表示する行がありません。")
        return
    num_re = _re.compile(r"^[+\-−]?[¥￥]?[\d,]+(\.\d+)?\s*(%|pt|円|件)?(（.*）)?$")
    date_re = _re.compile(r"^\d{2,4}[-/]\d{1,2}([-/]\d{1,2})?$")

    def _col_ratio(col, pattern) -> float:
        vals = [str(v).strip() for v in df[col]
                if str(v).strip() not in ("", "—", "-", "nan", "None")]
        if not vals:
            return 0.0
        return sum(1 for v in vals if pattern.match(v)) / len(vals)

    def _is_num_col(col) -> bool:
        if pd.api.types.is_numeric_dtype(df[col]):
            return True
        return _col_ratio(col, num_re) >= 0.6

    right = {c for c in df.columns if _is_num_col(c)}
    # 金額・日付は途中で折り返すと読み違える（「−」だけが行末に残る、"2026-" と "07-28" に割れる）
    # ため、狭い画面でも1トークンのまま保つ。
    nowrap = right | {c for c in df.columns if _col_ratio(c, date_re) >= 0.6}

    def _style(c, align_only=False) -> str:
        s = f"text-align:{'right' if c in right else 'left'}"
        return s if align_only else s + (";white-space:nowrap" if c in nowrap else "")

    head = "".join(f"<th style='{_style(c)}'>{escape(str(c))}</th>" for c in df.columns)
    body = []
    for _, r in df.iterrows():
        tds = []
        for c in df.columns:
            v = "" if pd.isna(r[c]) else str(r[c])
            tds.append(f"<td style='{_style(c)}'>{escape(v)}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    box = f"max-height:{height}px;overflow-y:auto;" if height else ""
    st.markdown(
        f"<div style='{box}overflow-x:auto;border:1px solid {LINE};border-radius:8px;"
        f"background:#FFFFFF;margin:2px 0 10px'>"
        f"<table class='ntab'><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div>",
        unsafe_allow_html=True)




# スマホ対策: ツールバーはグラフ上部（凡例の位置）にかぶるので出さない。
PLOTLY_CONFIG = {"displayModeBar": False, "scrollZoom": False}


def base_layout(fig: go.Figure, height=320) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=24, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=INK, size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        hovermode="closest",
        # 既定の dragmode="zoom" はタッチのドラッグを奪い、グラフ上で縦スクロールできなくなる
        dragmode=False,
    )
    fig.update_yaxes(gridcolor=LINE, tickformat=",", zeroline=True, zerolinecolor=SUBTLE)
    fig.update_xaxes(showgrid=False, linecolor=LINE)
    return fig


# ---------- 集計 ----------
def summarize(df: pd.DataFrame, net_warikan: bool) -> dict:
    pos, neg = df[df["amount"] > 0], df[df["amount"] < 0]
    wari = pos.loc[pos["sub"] == WARIKAN_SUB, "amount"].sum()
    income = pos.loc[pos["sub"] != WARIKAN_SUB, "amount"].sum()
    expense = -neg["amount"].sum()
    if net_warikan:
        expense -= wari
    else:
        income += wari
    balance = income - expense
    return {"income": income, "expense": expense, "balance": balance,
            "wari": wari, "rate": balance / income * 100 if income > 0 else None}


def apply_scope(df: pd.DataFrame, exclude_special: bool) -> pd.DataFrame:
    if not exclude_special:
        return df
    drop_exp = (df["amount"] < 0) & df["cat"].isin(SPECIAL_EXP_CATS)
    drop_inc = (df["amount"] > 0) & df["sub"].isin(SPECIAL_INC_SUBS)
    return df[~drop_exp & ~drop_inc]


def filled_months(months: list[str]) -> list[str]:
    if not months:
        return []
    out, cur, end = [], pd.Period(months[0], "M"), pd.Period(months[-1], "M")
    while cur <= end:
        out.append(str(cur))
        cur += 1
    return out


# ---------- データ読込・認証 ----------
@st.cache_data(ttl=300, show_spinner="データを読み込んでいます...")
def fetch_bundle(repo: str, path: str, branch: str, gh_token: str) -> bytes:
    """非公開リポジトリから暗号化データを取得する（GitHub API）。"""
    import base64
    import urllib.request
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "kakei-dashboard",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        meta = json.loads(r.read().decode("utf-8"))
    if meta.get("content"):
        return base64.b64decode(meta["content"])
    # サイズが大きい場合は content が空になるので blob API から取得
    req2 = urllib.request.Request(meta["git_url"], headers={
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "kakei-dashboard",
    })
    with urllib.request.urlopen(req2, timeout=60) as r:
        blob = json.loads(r.read().decode("utf-8"))
    return base64.b64decode(blob["content"])


@st.cache_data(show_spinner=False)
def decrypt_bundle(token: bytes, key: str) -> dict:
    from cryptography.fernet import Fernet
    raw = Fernet(key.encode()).decrypt(token)
    return json.loads(raw.decode("utf-8"))


def user_info() -> tuple[str | None, dict]:
    """ログイン中のメールアドレスと、診断用の生データを返す。"""
    debug: dict = {}
    for attr in ("user", "experimental_user"):
        try:
            u = getattr(st, attr, None)
            if u is None:
                debug[attr] = "なし"
                continue
            # dict 化を試みる（環境によって型が違うため）
            d = None
            for conv in (lambda x: dict(x), lambda x: x.to_dict(), lambda x: vars(x)):
                try:
                    d = conv(u)
                    break
                except Exception:
                    continue
            debug[attr] = d if d is not None else str(u)[:200]
            em = None
            if isinstance(d, dict):
                for k in ("email", "user_email", "mail", "preferred_username", "name"):
                    if d.get(k):
                        em = d[k]
                        break
            if not em:
                em = getattr(u, "email", None)
            if em and "@" in str(em):
                return str(em).strip().lower(), debug
        except Exception as e:
            debug[attr] = f"取得エラー: {type(e).__name__}"
    try:
        dev = str(st.secrets.get("DEV_USER", "")).strip().lower()
        if dev:
            return dev, debug
    except Exception:
        pass
    return None, debug


def current_email() -> str | None:
    return user_info()[0]


def display_names() -> tuple[str, str]:
    """表示名を Secrets から取得（公開リポジトリに実名を載せないため）。"""
    def g(k, d):
        try:
            v = str(st.secrets.get(k, "")).strip()
            return v or d
        except Exception:
            return d
    return g("NAME_ME", "僕"), g("NAME_PARTNER", "パートナー")


def emails_from_secret(name: str) -> set[str]:
    try:
        v = st.secrets.get(name, "")
    except Exception:
        v = ""
    return {e.strip().lower() for e in str(v).split(",") if e.strip()}


def show_settle_table(rows, partner_name: str = "", **kw):
    """清算ビューの表を描画する（数値列の右揃えはグローバルCSS/JSで適用）。"""
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    html_table(df, **kw)


# ---------- 清算（閲覧専用） ----------
def render_settlement(master: pd.DataFrame, months: list[str], settle: dict):
    NM_ME, NM_PT = display_names()
    cfg = settle.get("config", {})
    mons = settle.get("months", {})
    rent_subs = cfg.get("rent_subs", RENT_SUBS_DEFAULT)
    fx_subs = cfg.get("fixed_subs", SETTLE_FIXED_DEFAULT)
    sp_full = cfg.get("special_full", SPECIAL_FULL_DEFAULT)
    sp_split = cfg.get("special_split", SPECIAL_SPLIT_DEFAULT)
    r_me, r_wf = cfg.get("rent_ratio", [2, 1])
    s_me, s_wf = cfg.get("split_ratio", [1, 1])
    r_share = r_me / (r_me + r_wf) if (r_me + r_wf) > 0 else 0.5
    s_share = s_me / (s_me + s_wf) if (s_me + s_wf) > 0 else 0.5

    s_month = st.selectbox("清算対象月", list(reversed(months)), format_func=month_label)
    rec = mons.get(s_month, {})
    # 元利均等返済で毎月同額のため、月ごとの入力がなければ既定額を使う
    if "wife_loan" in rec:
        wife_loan = int(rec["wife_loan"])
    else:
        wife_loan = int(cfg.get("wife_loan_default", 0))
    mrows = master[master["month"] == s_month]

    # 以下2つは settlement.json に保存された「キー」を読むためのもの。
    # 表示名（NM_ME / NM_PT）とは別物で、ローカル版は "僕" / "パートナー" で保存する。
    # 表示名を Secrets で変えても金額が 0 にならないよう、保存側のキーで引く。
    def _me_val(r: dict) -> float:
        """本人側の金額を取り出す（保存時の列名ゆれに対応）。"""
        for k in ("僕", NM_ME, "me"):
            if k in r and r.get(k) not in (None, ""):
                return float(r[k])
        return 0.0

    def _pt_val(r: dict) -> float:
        """パートナー側の金額を取り出す（保存時の列名ゆれに対応）。"""
        for k in (NM_PT, "妻", "partner"):
            if k in r and r.get(k) not in (None, ""):
                return float(r[k])
        return 0.0

    def manual_view(rows: list) -> tuple[float, float]:
        me_sum = sum(_me_val(r) for r in rows)
        wf_sum = sum(_pt_val(r) for r in rows)
        if rows:
            # メモは直下の「📝 メモ全文」に全文が出るので、狭い画面を圧迫する列は持たせない
            show_settle_table(pd.DataFrame([{
                "項目": r.get("項目", ""), NM_ME: fyen(_me_val(r)),
                NM_PT: fyen(_pt_val(r)),
            } for r in rows]), NM_PT)
            memos = [r for r in rows if str(r.get("メモ", "")).strip()]
            if memos:
                from html import escape
                items = "".join(
                    f"<div style='margin:3px 0'><b>{escape(str(r.get('項目') or '（項目名なし）'))}</b>："
                    f"{escape(str(r.get('メモ'))).replace(chr(10), '<br>')}</div>" for r in memos)
                st.markdown(
                    f"<div style='font-size:12px;color:{SUBTLE};background:#FFFFFF;"
                    f"border:1px solid {LINE};border-radius:6px;padding:8px 12px;"
                    f"margin:4px 0 8px'>📝 メモ全文<br>{items}</div>", unsafe_allow_html=True)
        return me_sum, wf_sum

    # ① 家賃
    st.subheader(f"① 家賃（{NM_ME}{r_me} : {NM_PT}{r_wf}）")
    rent_rows = mrows[(mrows["sub"].isin(rent_subs)) & (mrows["calc"] == 1)
                      & (mrows["transfer"] != 1) & (mrows["amount"] < 0)]
    rent_mf = -rent_rows["amount"].sum()
    rent_total = rent_mf + wife_loan
    rent_me_amt = rent_total * r_share
    rent_wf_amt = rent_total - rent_me_amt
    # 1行の長文はスマホで2行に折り返り、肝心の負担額が文中に埋もれるため表にする
    show_settle_table([
        {"項目": "MF側の家賃・管理費", "金額": fyen(rent_mf)},
        {"項目": f"＋ {NM_PT}のローン負担", "金額": fyen(wife_loan)},
        {"項目": "＝ 家賃合計", "金額": fyen_x(rent_total)},
        {"項目": f"→ {NM_ME}の負担", "金額": fyen_x(rent_me_amt)},
        {"項目": f"→ {NM_PT}の負担", "金額": fyen_x(rent_wf_amt)},
    ], NM_PT)
    with st.expander("▼ 家賃の対象明細を見る"):
        rows_view = [{"日付": r.date, "内容": r.content, "中項目": r.sub, "金額": fyen(-r.amount)}
                     for r in rent_rows.sort_values("date").itertuples()]
        rows_view.append({"日付": "—", "内容": f"{NM_PT}のローン負担（手入力）", "中項目": "—",
                          "金額": fyen(wife_loan)})
        rows_view.append({"日付": "", "内容": "合計", "中項目": "", "金額": fyen_x(rent_total)})
        show_settle_table(rows_view, NM_PT)

    # ② 固定費
    st.subheader(f"② 固定費（折半 {NM_ME}{s_me} : {NM_PT}{s_wf}）")
    fx_view, fx_auto_me, fx_auto_wf = [], 0.0, 0.0
    for s in fx_subs:
        amt = -mrows.loc[mrows["sub"] == s, "amount"].sum()
        me = amt * s_share
        fx_view.append({"項目": s, "金額": fyen_x(amt), NM_ME: fyen_x(me),
                        NM_PT: fyen_x(amt - me)})
        fx_auto_me += me
        fx_auto_wf += amt - me
    show_settle_table(fx_view, NM_PT)
    with st.expander("▼ 固定費の対象明細を見る"):
        fxd = mrows[mrows["sub"].isin(fx_subs)].sort_values(["sub", "date"])
        if fxd.empty:
            st.caption("対象明細はありません。")
        else:
            rows_view = []
            for s in fx_subs:
                g = fxd[fxd["sub"] == s]
                if g.empty:
                    continue
                for r in g.itertuples():
                    rows_view.append({"中項目": r.sub, "日付": r.date, "内容": r.content,
                                      "金額": fyen(-r.amount)})
                rows_view.append({"中項目": f"── {s} 小計", "日付": "", "内容": "",
                                  "金額": fyen_x(-g["amount"].sum())})
            show_settle_table(rows_view, NM_PT)
    st.markdown("**手入力の追加項目**")
    fx_man_me, fx_man_wf = manual_view(rec.get("fixed_manual", []))
    fx_me = fx_auto_me + fx_man_me
    fx_wf = fx_auto_wf + fx_man_wf
    st.caption(f"固定費 小計 → {NM_ME} {fyen_x(fx_me)} ／ {NM_PT} {fyen_x(fx_wf)}")

    # ③ 特殊費用
    st.subheader("③ 特殊費用（計算対象外の明細も含む）")
    # 「割合」を独立した列に持つと5列になり、スマホでは1セルが3〜4行に折り返って崩れる。
    # 全額負担の行だけ項目名に添え、②固定費と同じ4列に揃える（折半比率は下のcaptionに出す）。
    sp_view = []
    sp_full_wf = sp_split_me = sp_split_wf = 0.0
    for s in sp_full:
        amt = -mrows.loc[mrows["sub"] == s, "amount"].sum()
        sp_view.append({"項目": f"{s}（全額{NM_PT}）", "金額": fyen_x(amt),
                        NM_ME: fyen_x(0), NM_PT: fyen_x(amt)})
        sp_full_wf += amt
    for s in sp_split:
        amt = -mrows.loc[mrows["sub"] == s, "amount"].sum()
        me = amt * s_share
        sp_view.append({"項目": s, "金額": fyen_x(amt),
                        NM_ME: fyen_x(me), NM_PT: fyen_x(amt - me)})
        sp_split_me += me
        sp_split_wf += amt - me
    show_settle_table(sp_view, NM_PT)
    if sp_split:
        st.caption(f"「（全額{NM_PT}）」以外は折半 {NM_ME}{s_me} : {NM_PT}{s_wf}")
    with st.expander("▼ 特殊費用の対象明細を見る"):
        tgt = mrows[mrows["sub"].isin(list(sp_full) + list(sp_split))].sort_values(["sub", "date"])
        if tgt.empty:
            st.caption("対象明細はありません。")
        else:
            rows_view = []
            for s in list(sp_full) + list(sp_split):
                g = tgt[tgt["sub"] == s]
                if g.empty:
                    continue
                # 「計算対象」列を独立させると5列で崩れるので、対象外の行だけ内容に注記する
                for r in g.itertuples():
                    rows_view.append({"中項目": r.sub, "日付": r.date,
                                      "内容": r.content + ("" if r.calc == 1 else "（計算対象外）"),
                                      "金額": fyen(-r.amount)})
                rows_view.append({"中項目": f"── {s} 小計", "日付": "", "内容": "",
                                  "金額": fyen_x(-g["amount"].sum())})
            show_settle_table(rows_view, NM_PT)
    st.markdown("**手入力の追加項目**")
    sp_man_me, sp_man_wf = manual_view(rec.get("special_manual", []))
    sp_me_amt = sp_split_me + sp_man_me
    sp_wf_amt = sp_full_wf + sp_split_wf + sp_man_wf
    st.caption(f"特殊費用 小計 → {NM_ME} {fyen_x(sp_me_amt)} ／ {NM_PT} {fyen_x(sp_wf_amt)}")

    # ④ 清算結果
    st.subheader("④ 清算結果")
    total_me = rent_me_amt + fx_me + sp_me_amt
    total_wf = rent_wf_amt + fx_wf + sp_wf_amt
    billed = total_wf - wife_loan
    billed_floor = int(math.floor(billed))
    k1, k2 = st.columns(2)
    k1.metric(f"💰 請求額（{NM_PT}へ・円未満切捨て）", fyen(billed_floor))
    k2.metric(f"{NM_PT}の負担合計", fyen_x(total_wf))
    k3, k4 = st.columns(2)
    k3.metric(f"{NM_PT}ローン直接支払分（差引）", fyen(wife_loan))
    k4.metric(f"（参考）{NM_ME}の負担合計", fyen_x(total_me))
    if billed < 0:
        st.info(f"マイナスのため、{NM_ME}から {fyen(abs(billed_floor))} を支払う清算になります。")

    bd_rows = [(f"家賃（{NM_PT}の負担分）", rent_wf_amt),
               ("固定費（折半・自動集計）", fx_auto_wf), ("固定費（手入力）", fx_man_wf),
               ("特殊費用（全額負担分）", sp_full_wf), ("特殊費用（折半分）", sp_split_wf),
               ("特殊費用（手入力）", sp_man_wf)]
    td = "padding:1px 18px 1px 0;color:" + SUBTLE
    tdr = "text-align:right;font-variant-numeric:tabular-nums;padding:1px 0"
    lines = "".join(f"<tr><td style='{td}'>{n}</td><td style='{tdr}'>{fyen_x(v)}</td></tr>"
                    for n, v in bd_rows if v != 0)
    border = f"border-top:1px solid {LINE}"
    lines += (f"<tr><td style='{td};{border}'>{NM_PT}の負担合計</td>"
              f"<td style='{tdr};{border}'><b>{fyen_x(total_wf)}</b></td></tr>"
              f"<tr><td style='{td}'>− {NM_PT}ローン直接支払分</td><td style='{tdr}'>−{fyen(wife_loan)}</td></tr>"
              f"<tr><td style='{td};{border}'>＝ 請求額（切捨て）</td>"
              f"<td style='{tdr};{border}'><b>{fyen(billed_floor)}</b></td></tr>")
    st.markdown(f"<div style='font-size:12px;margin-top:4px'>請求額の内訳"
                f"<table style='font-size:12px;border-collapse:collapse;margin-top:2px'>{lines}</table></div>",
                unsafe_allow_html=True)

    ny, nm = int(s_month[:4]), int(s_month[5:7]) + 1
    nxt = f"{ny + (nm > 12)}-{str(nm if nm <= 12 else 1).zfill(2)}"
    wari_in = master.loc[(master["month"] == nxt) & (master["sub"] == WARIKAN_SUB)
                         & (master["amount"] > 0), "amount"].sum()
    if wari_in > 0:
        st.caption(f"参考: 翌月（{month_label(nxt)}）の「{WARIKAN_SUB}」入金は {fyen(wari_in)}"
                   f"（請求額との差 {fyen_x(wari_in - billed_floor)}）")


# ============================================================
st.set_page_config(page_title="家計ダッシュボード", page_icon="🧾", layout="wide")
st.markdown(f"""<style>
  [data-testid="stMetricValue"] {{ font-variant-numeric: tabular-nums; }}
  [data-testid="stMetricValue"], [data-testid="stMetricDelta"] {{
    display: flex; justify-content: flex-end; text-align: right;
  }}
  table.ntab {{ width: 100%; border-collapse: collapse; font-size: 13px;
    font-variant-numeric: tabular-nums; }}
  table.ntab th {{ color: {SUBTLE}; font-weight: 500; font-size: 12px;
    padding: 8px 12px; border-bottom: 1px solid {LINE};
    background: #FFFFFF; position: sticky; top: 0; white-space: nowrap; }}
  table.ntab td {{ padding: 7px 12px; border-bottom: 1px solid #F0F0F2;
    vertical-align: top; }}
  table.ntab tr:last-child td {{ border-bottom: none; }}
  div[data-testid="stMetric"] {{ background:#FFF; border:1px solid {LINE};
      border-radius:8px; padding:12px 16px; }}
</style>""", unsafe_allow_html=True)


# ---- 認証 ----
def view_mode() -> str:
    """このアプリが何を表示するかを Secrets の VIEW で決める。
    'full'   → 全タブ（自分用）
    'settle' → 清算ビューのみ（共有用）
    アクセス制限は Streamlit の Viewers 設定で行うため、ここではメール判定をしない。
    """
    forced = st.session_state.get("_force_view")
    if forced:
        return "settle" if str(forced).startswith("s") else "full"
    try:
        v = str(st.secrets.get("VIEW", "full")).strip().lower()
    except Exception:
        v = "full"
    return "settle" if v.startswith("s") else "full"


def auth_configured() -> bool:
    """Secrets に [auth]（Google OIDC）が設定されているか。
    設定あり → Render等でのGoogleログイン+許可リスト照合。
    設定なし → 従来どおり Secrets の VIEW で表示を決める（Streamlit Cloud互換）。
    """
    try:
        a = st.secrets.get("auth", None)
        return bool(a) and "client_id" in a
    except Exception:
        return False


role = None
if auth_configured():
    if not st.user.is_logged_in:
        st.title("🧾 家計ダッシュボード")
        st.write("このアプリは非公開です。登録済みのGoogleアカウントでログインしてください。")
        st.button("Googleでログイン", on_click=st.login, type="primary")
        st.stop()
    else:
        _email = str(getattr(st.user, "email", "") or "").strip().lower()
        if _email in emails_from_secret("FULL_USERS"):
            role = "full"
        elif _email in emails_from_secret("SETTLE_USERS"):
            role = "settle"
        else:
            st.error(f"このアプリの閲覧権限がありません（{_email}）。")
            st.button("ログアウト", on_click=st.logout)
            st.stop()
        if role:
            st.session_state["_auth_email"] = _email
else:
    role = view_mode()

if role:
    # ---- データ読込（非公開リポジトリから取得して復号）----
    def sec(name: str, default: str = "") -> str:
        try:
            return str(st.secrets.get(name, default)).strip()
        except Exception:
            return default

    key = sec("ENCRYPTION_KEY")
    gh_token = sec("GITHUB_TOKEN")
    data_repo = sec("DATA_REPO")
    data_path = sec("DATA_PATH", "cloud_data/bundle.enc") or "cloud_data/bundle.enc"
    data_branch = sec("DATA_BRANCH", "main") or "main"

    bundle = None
    if not key or not gh_token or not data_repo:
        missing = [n for n, v in [("ENCRYPTION_KEY", key), ("GITHUB_TOKEN", gh_token),
                                  ("DATA_REPO", data_repo)] if not v]
        st.error(f"Secrets が不足しています: {', '.join(missing)}（手順書参照）")
    else:
        try:
            enc = fetch_bundle(data_repo, data_path, data_branch, gh_token)
        except Exception as e:
            st.error("データの取得に失敗しました。GITHUB_TOKEN の権限・有効期限、DATA_REPO の指定を確認してください。")
            st.caption(f"詳細: {type(e).__name__}")
            enc = None
        if enc:
            try:
                bundle = decrypt_bundle(enc, key)
            except Exception:
                st.error("データの復号に失敗しました。ENCRYPTION_KEY が data/cloud_key.txt の内容と一致しているか確認してください。")

    if True:

        if bundle:
            master = pd.DataFrame(bundle["transactions"])
            master["amount"] = pd.to_numeric(master["amount"], errors="coerce")
            master["calc"] = pd.to_numeric(master["calc"], errors="coerce").fillna(1).astype(int)
            master["transfer"] = pd.to_numeric(master["transfer"], errors="coerce").fillna(0).astype(int)
            for c in ("content", "memo", "cat", "sub", "inst", "date", "month"):
                master[c] = master[c].fillna("").astype(str)
            valid = master[(master["calc"] == 1) & (master["transfer"] != 1)]
            months = sorted(valid["month"].unique().tolist())
            all_months = filled_months(months)
            years = sorted({m[:4] for m in months})
            settle = bundle.get("settlement", {})
            settings = bundle.get("settings", {})

            st.sidebar.title("🧾 家計ダッシュボード")
            st.sidebar.caption(f"クラウド版（閲覧専用）\nデータ更新: {bundle.get('exported_at', '不明')}")
            if st.sidebar.button("🔄 最新データを取得", width="stretch"):
                fetch_bundle.clear()
                decrypt_bundle.clear()
                st.rerun()
            st.sidebar.caption("清算ビュー" if role == "settle" else "全体ビュー")
            if st.session_state.get("_auth_email"):
                st.sidebar.caption(f"ログイン: {st.session_state['_auth_email']}")
                st.sidebar.button("ログアウト", on_click=st.logout, width="stretch")

            # ================= 清算のみ =================
            if role == "settle":
                st.title("生活費の清算")
                if months:
                    render_settlement(master, months, settle)
                else:
                    st.info("データがありません。")

            # ================= フル表示 =================
            else:
                st.sidebar.divider()
                mode = st.sidebar.radio("期間", ["月", "年", "全期間"], horizontal=True)
                sel_month = sel_year = None
                if mode == "月" and months:
                    sel_month = st.sidebar.selectbox("対象月", list(reversed(months)),
                                                     format_func=month_label)
                elif mode == "年" and years:
                    sel_year = st.sidebar.selectbox("対象年", list(reversed(years)),
                                                    format_func=lambda y: f"{y}年")
                st.sidebar.divider()
                exclude_special = st.sidebar.checkbox("経常ベース（特別・臨時を除く）", value=True)
                net_warikan = st.sidebar.checkbox("割り勘を支出と相殺", value=False)

                scoped_all = apply_scope(valid, exclude_special)
                if mode == "月":
                    period = scoped_all[scoped_all["month"] == sel_month]
                    period_label = month_label(sel_month)
                elif mode == "年":
                    period = scoped_all[scoped_all["month"].str.startswith(sel_year)]
                    period_label = f"{sel_year}年"
                else:
                    period = scoped_all
                    period_label = "全期間"

                kpi = summarize(period, net_warikan)
                prev = None
                if mode == "月" and sel_month in all_months:
                    i = all_months.index(sel_month)
                    if i > 0 and all_months[i - 1] in months:
                        prev = summarize(scoped_all[scoped_all["month"] == all_months[i - 1]],
                                         net_warikan)

                st.title("家計ダッシュボード")
                st.caption(f"表示期間: **{period_label}** ／ {'経常ベース' if exclude_special else '全体'}"
                           + ("・割り勘相殺" if net_warikan else ""))
                c1, c2 = st.columns(2)
                d = ((kpi["income"] - prev["income"]) / abs(prev["income"]) * 100
                     if prev and prev["income"] else None)
                c1.metric("収入", fyen(kpi["income"]), f"{d:+.1f}% 前月比" if d is not None else None)
                d = ((kpi["expense"] - prev["expense"]) / abs(prev["expense"]) * 100
                     if prev and prev["expense"] else None)
                c2.metric("支出", fyen(kpi["expense"]), f"{d:+.1f}% 前月比" if d is not None else None,
                          delta_color="inverse")
                c3, c4 = st.columns(2)
                c3.metric(f"収支（{'黒字' if kpi['balance'] >= 0 else '赤字'}）", fyen(kpi["balance"]),
                          f"前月 {fyen(prev['balance'])}" if prev else None, delta_color="off")
                c4.metric("貯蓄率", f"{kpi['rate']:.1f}%" if kpi["rate"] is not None else "—")

                tab_ov, tab_cat, tab_fix, tab_settle, tab_tx = st.tabs(
                    ["概要", "カテゴリ", "固定費・サブスク", "清算", "明細"])

                monthly = []
                grouped = dict(tuple(scoped_all.groupby("month")))
                for m in all_months:
                    s = summarize(grouped.get(m, scoped_all.iloc[0:0]), net_warikan)
                    monthly.append({"label": m[2:].replace("-", "/"), "収入": s["income"],
                                    "支出": s["expense"], "収支": s["balance"]})
                monthly = pd.DataFrame(monthly)

                # ---- 概要 ----
                with tab_ov:
                    if mode == "年":
                        st.subheader("年次推移")
                        rows = []
                        for y in years:
                            s = summarize(scoped_all[scoped_all["month"].str.startswith(y)],
                                          net_warikan)
                            rows.append({"label": f"{y}年", "収入": s["income"],
                                         "支出": s["expense"], "収支": s["balance"]})
                        trend = pd.DataFrame(rows)
                    else:
                        st.subheader("月次推移")
                        trend = monthly
                    many = len(trend) > 8
                    fig = go.Figure()
                    for name, col in [("収入", INDIGO), ("支出", SHU)]:
                        fig.add_bar(x=trend["label"], y=trend[name], name=name, marker_color=col,
                                    hovertemplate=HOVER_YEN,
                                    text=[man_label(v) for v in trend[name]],
                                    textposition="outside", textangle=-90 if many else 0,
                                    textfont=dict(size=9, color=SUBTLE), cliponaxis=False)
                    fig.add_scatter(x=trend["label"], y=trend["収支"], name="収支",
                                    mode="lines+markers", line=dict(color=INK, width=2),
                                    hovertemplate=HOVER_YEN)
                    peak = max(trend["収入"].max(), trend["支出"].max()) if len(trend) else 0
                    if peak > 0:
                        fig.update_yaxes(range=[min(0, trend["収支"].min() * 1.1), peak * 1.22])
                    st.plotly_chart(base_layout(fig), width="stretch", config=PLOTLY_CONFIG)

                    exp = period[period["amount"] < 0]
                    cat_sum = (-exp.groupby("cat")["amount"].sum()).sort_values(ascending=False)
                    st.subheader(f"支出の内訳（{period_label}）")
                    if cat_sum.empty:
                        st.info("この期間の支出はありません。")
                    else:
                        vals = cat_sum[::-1]
                        total = cat_sum.sum()
                        fig = go.Figure(go.Bar(
                            x=vals.values, y=vals.index, orientation="h",
                            marker_color=[cat_color(c) for c in vals.index],
                            text=[f"{fyen(v)}（{v / total * 100:.1f}%）" for v in vals.values],
                            textposition="outside", cliponaxis=False,
                            textfont=dict(size=10, color=INK),
                            hovertemplate="%{y}: %{x:,.0f}円<extra></extra>"))
                        fig.update_xaxes(range=[0, float(vals.max()) * 1.5])
                        st.plotly_chart(base_layout(fig, height=max(300, 26 * len(vals) + 80)),
                                        width="stretch", config=PLOTLY_CONFIG)
                        st.caption(f"合計 {fyen(total)}")

                    st.subheader(f"支出トップ10（{period_label}）")
                    top10 = exp.nsmallest(10, "amount")
                    if not top10.empty:
                        html_table(pd.DataFrame({
                            "日付": top10["date"], "内容": top10["content"],
                            "カテゴリ": top10["cat"] + " › " + top10["sub"],
                            "金額": top10["amount"].map(lambda a: fyen(-a)),
                        }))

                # ---- カテゴリ ----
                with tab_cat:
                    exp = period[period["amount"] < 0]
                    cat_sum = (-exp.groupby("cat")["amount"].sum()).sort_values(ascending=False)
                    if cat_sum.empty:
                        st.info("この期間の支出はありません。")
                    else:
                        sel_cat = st.selectbox("大項目", cat_sum.index.tolist(),
                                               format_func=lambda c: f"{c}　{fyen(cat_sum[c])}")
                        series = (-scoped_all[(scoped_all["amount"] < 0)
                                              & (scoped_all["cat"] == sel_cat)]
                                  .groupby("month")["amount"].sum()).reindex(all_months).fillna(0)
                        many = len(series) > 8
                        fig = go.Figure(go.Bar(
                            x=[m[2:].replace("-", "/") for m in series.index], y=series.values,
                            marker_color=cat_color(sel_cat), hovertemplate=HOVER_YEN, name=sel_cat,
                            text=[man_label(v) for v in series.values],
                            textposition="outside", textangle=-90 if many else 0,
                            textfont=dict(size=9, color=SUBTLE), cliponaxis=False))
                        fig.add_hline(y=series.mean(), line_dash="dash", line_color=SUBTLE,
                                      annotation_text=f"月平均 {fyen(series.mean())}",
                                      annotation_font_size=11)
                        if series.max() > 0:
                            fig.update_yaxes(range=[0, float(series.max()) * 1.25])
                        st.plotly_chart(base_layout(fig, height=260), width="stretch",
                                        config=PLOTLY_CONFIG)

                        sub_sum = (-exp[exp["cat"] == sel_cat].groupby("sub")["amount"].sum()
                                   ).sort_values()
                        sub_total = sub_sum.sum()
                        fig = go.Figure(go.Bar(
                            x=sub_sum.values, y=sub_sum.index, orientation="h",
                            marker_color=cat_color(sel_cat),
                            text=[f"{fyen(v)}（{v / sub_total * 100:.1f}%）" for v in sub_sum.values],
                            textposition="outside", cliponaxis=False,
                            textfont=dict(size=10, color=INK),
                            hovertemplate="%{y}: %{x:,.0f}円<extra></extra>"))
                        if len(sub_sum) and sub_sum.max() > 0:
                            fig.update_xaxes(range=[0, float(sub_sum.max()) * 1.5])
                        st.plotly_chart(
                            base_layout(fig, height=max(200, 34 * len(sub_sum) + 60)),
                            width="stretch", config=PLOTLY_CONFIG)

                        pick = st.selectbox("明細を中項目で絞り込み",
                                            ["すべて"] + sub_sum.sort_values(ascending=False)
                                            .index.tolist())
                        pool = exp[exp["cat"] == sel_cat]
                        if pick != "すべて":
                            pool = pool[pool["sub"] == pick]
                        big = pool.nsmallest(30, "amount")
                        st.caption("金額の大きい明細（上位30件）")
                        html_table(pd.DataFrame({
                            "日付": big["date"].str[5:], "内容": big["content"],
                            "中項目": big["sub"],
                            "金額": big["amount"].map(lambda a: fyen(-a)),
                        }))

                # ---- 固定費・サブスク ----
                with tab_fix:
                    f_cats = settings.get("fixed_cats", FIXED_CATS)
                    f_subs = settings.get("fixed_subs", FIXED_SUBS)
                    exp = period[period["amount"] < 0]
                    fixed_mask = exp["cat"].isin(f_cats) | exp["sub"].isin(f_subs)
                    fixed_amt = -exp.loc[fixed_mask, "amount"].sum()
                    var_amt = -exp.loc[~fixed_mask, "amount"].sum()
                    ratio = (fixed_amt / (fixed_amt + var_amt) * 100
                             if fixed_amt + var_amt > 0 else None)
                    c1, c2, c3 = st.columns(3)
                    c1.metric(f"固定費（{period_label}）", fyen(fixed_amt))
                    c2.metric("変動費", fyen(var_amt))
                    c3.metric("固定費率", f"{ratio:.1f}%" if ratio is not None else "—")
                    st.caption(f"固定費の定義 — 大項目: {'・'.join(f_cats)} ／ 中項目: {'・'.join(f_subs)}")

                    pv = scoped_all[scoped_all["amount"] < 0].copy()
                    pv["kind"] = "変動費"
                    pv.loc[pv["cat"].isin(f_cats) | pv["sub"].isin(f_subs), "kind"] = "固定費"
                    pivot = (-pv.pivot_table(index="month", columns="kind", values="amount",
                                             aggfunc="sum")).reindex(all_months).fillna(0)
                    fig = go.Figure()
                    many = len(pivot) > 8
                    for k_name, color, tcolor in [("固定費", GREEN_900, "#FFFFFF"),
                                                  ("変動費", GRAY_200, INK)]:
                        if k_name in pivot.columns:
                            fig.add_bar(x=[m[2:].replace("-", "/") for m in pivot.index],
                                        y=pivot[k_name], name=k_name, marker_color=color,
                                        hovertemplate=HOVER_YEN,
                                        text=[man_label(v) for v in pivot[k_name]],
                                        textposition="inside", insidetextanchor="middle",
                                        textangle=-90 if many else 0,
                                        textfont=dict(size=9, color=tcolor))
                    fig.update_layout(barmode="stack")
                    st.plotly_chart(base_layout(fig, height=300), width="stretch",
                                    config=PLOTLY_CONFIG)

                    subs_all = valid[(valid["sub"] == "サブスク") & (valid["amount"] < 0)]
                    if not subs_all.empty:
                        st.subheader("サブスクリプション一覧")
                        g = subs_all.groupby("content").agg(
                            累計=("amount", lambda s: -s.sum()), 出現月数=("month", "nunique"),
                            直近月=("month", "max"))
                        latest_amt = (subs_all.sort_values("month").groupby("content").tail(1)
                                      .set_index("content")["amount"] * -1)
                        g["直近の請求"] = latest_amt
                        g["月平均"] = g["累計"] / g["出現月数"]
                        g = g.sort_values("月平均", ascending=False).reset_index()
                        html_table(pd.DataFrame({
                            "サービス": g["content"],
                            "直近の請求": g["直近の請求"].map(fyen),
                            "月平均": g["月平均"].map(fyen),
                            "出現月数": g["出現月数"], "累計": g["累計"].map(fyen),
                        }))

                # ---- 清算 ----
                with tab_settle:
                    render_settlement(master, months, settle)

                # ---- 明細 ----
                with tab_tx:
                    q = st.text_input("検索（内容・メモ・カテゴリ・金融機関）", "")
                    pick_cat = st.selectbox("大項目で絞り込み",
                                            ["すべて"] + sorted(valid["cat"].unique().tolist()))
                    base = valid
                    if mode == "月":
                        base = base[base["month"] == sel_month]
                    elif mode == "年":
                        base = base[base["month"].str.startswith(sel_year)]
                    if pick_cat != "すべて":
                        base = base[base["cat"] == pick_cat]
                    if q.strip():
                        hay = (base["content"] + " " + base["memo"] + " " + base["cat"] + " "
                               + base["sub"] + " " + base["inst"]).str.lower()
                        base = base[hay.str.contains(q.strip().lower(), regex=False)]
                    base = base.sort_values("date", ascending=False)
                    st.caption(f"{len(base):,}件")
                    html_table(pd.DataFrame({
                        "日付": base["date"], "内容": base["content"],
                        "カテゴリ": base["cat"] + " › " + base["sub"],
                        "金額": base["amount"].map(lambda a: ("+" if a > 0 else "−") + f"¥{abs(a):,.0f}"),
                    }).head(300), height=520)
                    if len(base) > 300:
                        st.caption("先頭300件を表示しています。検索で絞り込んでください。")
