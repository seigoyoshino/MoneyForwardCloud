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

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------- カラーパレット（Green基調） ----------
INK = "#000000"
SUBTLE = "#626264"
PAPER = "#F8F8FB"
LINE = "#E0E0E3"
GREEN_1200, GREEN_900, GREEN_600 = "#032213", "#115A36", "#259D63"
GREEN_400, GREEN_200, GREEN_50 = "#51B883", "#9BD4B5", "#E6F5EC"
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


# スマホでは月次の棒グラフを直近Nヶ月に絞る。400px幅では24ヶ月でバーが約7pxになり、
# 値ラベルも潰れて判読できないため。
CHART_MONTHS_MOBILE = 12


def is_fixed(df: pd.DataFrame, cats: list[str] | None = None,
             subs: list[str] | None = None) -> pd.Series:
    """固定費の判定。既定はバンドルの settings.json。ローカル版と同じ。"""
    if cats is None or subs is None:
        st_ = load_settings()
        cats = (st_.get("fixed_cats") or FIXED_CATS) if cats is None else cats
        subs = (st_.get("fixed_subs") or FIXED_SUBS) if subs is None else subs
    return (df["amount"] < 0) & (df["cat"].isin(cats) | df["sub"].isin(subs))


def valid_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["calc"] == 1) & (df["transfer"] != 1)]


# ============================================================
# ペース管理（「今月」タブ）— docs/pace_design.md
#
# ここの計算はサイドバーのトグルに影響されない（決定事項4）。
# クラウド版 cloud_app.py にも同じものを置くこと。差し替えるのは as-of 日の求め方だけ。
# ============================================================
JST = timezone(timedelta(hours=9))
# カード計上遅延。バンドルの履歴を復号して「後から何件足されたか」を実測した結果
# （2026-08-15）、最終取引日の2日前で98.6%が出そろい、3日目・4日目に増える精度は
# ゼロだった（残る取りこぼしは ¥2,980 で完全に横ばい）。4日だと鮮度を2日捨てるだけ。
# → 非公開リポジトリの tools/measure_lag.py で測り直せる。変えるときは必ず再測定
CARD_LAG_DAYS = 2
BASELINE_WINDOW = 6        # 基準線に使う過去月数
FREQ_HI, FREQ_LO = 0.9, 0.3  # 出現頻度の分岐（毎月 / 隔月等 / 不定期）
BONUS_RE = r"賞与|ボーナス"
BONUS_SUB = "賞与"
LEVELED_CONTENT = "年次費用の月割り"
SHARE_CONTENT = "相手が直接払っている分の自己負担"
SYNTHETIC_ID_PREFIXES = ("__leveled__", "__share__")   # 実在しない合成明細の目印


# 設定はバンドル（settings.json / settlement.json）から来る。
# バンドル読み込み後に _BUNDLE_SETTINGS / _BUNDLE_SETTLE へ入れる。
_BUNDLE_SETTINGS: dict = {}
_BUNDLE_SETTLE: dict = {}


def load_settings() -> dict:
    return _BUNDLE_SETTINGS


def settle_config() -> dict:
    return _BUNDLE_SETTLE


def leveled_defs() -> dict[str, dict]:
    """平準化する費目 → 契約額(amount)と支払間隔(months)。data/settings.json の leveled_subs。"""
    raw = load_settings().get("leveled_subs", {})
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(v, dict):
            v = {"amount": v, "months": 12}
        amt, mon = v.get("amount"), int(v.get("months") or 12)
        if amt is None:
            continue
        out[k] = {"amount": float(amt), "months": mon, "monthly": float(amt) / mon,
                  "note": str(v.get("note", ""))}
    return out


def savings_target() -> float:
    try:
        return float(load_settings().get("savings_target", 0.10))
    except Exception:
        return 0.10


def baseline_from() -> str | None:
    """基準線の起点月（"YYYY-MM"）。住み替えなどで生活水準が変わった月を指定する。

    これ以前のデータは基準線に使わない。前の家の家賃・管理費が混ざると
    「今後落ちる固定費」が過小になり、残り使える額が実態より多く出る。
    """
    v = load_settings().get("baseline_from")
    return str(v) if v else None


def settle_shares() -> dict:
    """清算の按分ルールをペース計算で使える形にまとめる。"""
    c = settle_config()
    cfg = c.get("config", c)
    rent = cfg.get("rent_subs") or RENT_SUBS_DEFAULT
    split = cfg.get("fixed_subs") or SETTLE_FIXED_DEFAULT
    rr = cfg.get("rent_ratio") or [2, 1]
    sr = cfg.get("split_ratio") or [1, 1]
    loans = {m: int(v.get("wife_loan", 0)) for m, v in (c.get("months") or {}).items()
             if v.get("wife_loan")}
    default_loan = int(cfg.get("wife_loan_default") or 0)
    if not default_loan and loans:            # 未保存なら過去の最頻値で代用する
        vals = list(loans.values())
        default_loan = max(set(vals), key=vals.count)
    return {"rent_subs": rent, "split_subs": split,
            "rent_share": rr[0] / sum(rr) if sum(rr) else 0.5,
            "split_share": sr[0] / sum(sr) if sum(sr) else 0.5,
            "wife_loans": loans, "wife_loan_default": default_loan}


CONTENT_SUBS_DEFAULT = [
    # MF上ひとつの中項目に混ざっているものを、内容で別の中項目として切り出す。
    # 切り出さないと基準線の中央値が引きずられ、見込みが実態から外れる。
    {"name": BONUS_SUB, "from": "給与", "pattern": BONUS_RE, "min_amount": 100000},
    {"name": "カード年会費", "from": "サブスク", "pattern": r"年会費|ネンカイヒ"},
]


def content_subs() -> list[dict]:
    v = load_settings().get("content_subs")
    return v if isinstance(v, list) and v else CONTENT_SUBS_DEFAULT


def fixed_amounts() -> dict[str, float]:
    """契約で決まっている月額（MFに出る実額）。基準線の推定より優先する。

    基準線が短い間（住み替え直後など）、毎月同額のものまで推定に頼るのは弱い。
    金額が分かっているものは、そのまま見込みに使う。
    """
    raw = load_settings().get("fixed_amounts", {})
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        try:
            out[k] = float(v if not isinstance(v, dict) else v.get("amount"))
        except (TypeError, ValueError):
            continue
    return out


def expected_monthly(sub: str, amount: float) -> float:
    """MFの実額を、ペース計算で使う「自分の負担」に換算する。

    apply_settlement_share と同じ按分を、1つの金額に対して行うもの。
    設定は MF に出る額のまま書けるようにして、按分は仕組み側で通す。
    """
    s = settle_shares()
    if sub in s["rent_subs"]:
        loan = s["wife_loan_default"] if sub == s["rent_subs"][0] else 0
        return (amount + loan) * s["rent_share"]
    if sub in s["split_subs"]:
        return amount * s["split_share"]
    return amount


def split_by_content(df: pd.DataFrame) -> pd.DataFrame:
    """内容欄を見て中項目を切り出す。

    賞与は中項目「給与」に、カード年会費は「サブスク」に混ざっている。
    どちらも年1回なので、切り出したうえで年次費用として平準化すると
    月々の見込みが素直になる。
    """
    d = df.copy()
    for rule in content_subs():
        name, src = rule.get("name"), rule.get("from")
        pat, lo = rule.get("pattern"), rule.get("min_amount")
        if not (name and src and pat):
            continue
        hit = (d["sub"] == src) & d["content"].str.contains(pat, na=False)
        if lo:
            hit &= d["amount"].abs() >= float(lo)
        d.loc[hit, "sub"] = name
    return d


def level_annual(df: pd.DataFrame) -> pd.DataFrame:
    """年次・複数年の費用を毎月の積立に置き換える（落ちた月だけ突出するのを防ぐ）。"""
    defs = leveled_defs()
    if not defs or df.empty:
        return df
    src = df[df["sub"].isin(defs)]
    if src.empty:
        return df
    months, rows = sorted(df["month"].unique()), []
    for sub, d in defs.items():
        g = src[src["sub"] == sub]
        if g.empty or not d["monthly"]:
            continue
        cat = str(g["cat"].iloc[-1])
        for m in months:
            rows.append({"id": f"__leveled__{sub}__{m}", "date": f"{m}-01", "month": m,
                         "content": LEVELED_CONTENT, "amount": -d["monthly"], "inst": "",
                         "cat": cat, "sub": sub, "memo": "", "calc": 1, "transfer": 0})
    return pd.concat([df[~df["sub"].isin(defs)], pd.DataFrame(rows)], ignore_index=True)


def apply_settlement_share(df: pd.DataFrame) -> pd.DataFrame:
    """清算対象の費用を「按分後の自分の負担」に置き換える。

    相手と分け合う費用は、住宅費・光熱費・割り勘でルールがばらばらだった。
    清算タブが持っている按分ルール1つに統一する。
      住宅費（ローン返済・管理費等）… (MF計上額 + 相手のローン直接払い) × 2/3
      折半対象（水道代・電気代・割り勘の食料品/日用品）… 実額 × 1/2
    相手からの清算入金（割り勘代）は収入に入れない。按分後の自分の負担だけを
    費用として持つので、二重計上にはならない。
    """
    s = settle_shares()
    if df.empty:
        return df
    d = df.copy()
    d["amount"] = d["amount"].astype(float)   # 按分で小数になるため
    neg = d["amount"] < 0

    # 折半対象はその場で自分の取り分に縮める（計上日の分布を保つため）
    d.loc[neg & d["sub"].isin(s["split_subs"]), "amount"] *= s["split_share"]

    # 住宅費も同様に縮めたうえで、相手が直接払っている分の自分の取り分を足す
    rent_mask = neg & d["sub"].isin(s["rent_subs"])
    d.loc[rent_mask, "amount"] *= s["rent_share"]

    # 相手のローン分は必ずローン返済の費目に載せる。月によっては当月まだ
    # ローンが落ちておらず、その月の明細から拾うと別の費目に付いてしまう
    loan_sub = s["rent_subs"][0] if s["rent_subs"] else "ローン返済"
    src = d[neg & (d["sub"] == loan_sub)]
    loan_cat = str(src["cat"].iloc[-1]) if len(src) else "住宅"
    loan_day = int(pd.to_datetime(src["date"], errors="coerce").dt.day.median()) if len(src) else 27

    rows = []
    for m in sorted(d.loc[rent_mask, "month"].unique()):
        loan = s["wife_loans"].get(m, s["wife_loan_default"])
        if not loan:
            continue
        same = d[(d["month"] == m) & (d["sub"] == loan_sub) & neg]
        if len(same):
            day = int(pd.to_datetime(same["date"], errors="coerce").dt.day.max())
        else:
            day = min(loan_day, pd.Period(m, "M").days_in_month)
        rows.append({"id": f"__share__{m}", "date": f"{m}-{day:02d}", "month": m,
                     "content": SHARE_CONTENT, "amount": -loan * s["rent_share"],
                     "inst": "", "cat": loan_cat, "sub": loan_sub,
                     "memo": "", "calc": 1, "transfer": 0})
    if rows:
        d = pd.concat([d, pd.DataFrame(rows)], ignore_index=True)
    return d


def pace_base(master: pd.DataFrame) -> pd.DataFrame:
    """ペース計算の土台。サイドバーのトグルには影響されない。

    経常ベース → 賞与を切り出し → 清算対象を按分後の自分の負担に →
    相手からの清算入金を除外 → 年次費用を平準化。
    """
    d = apply_scope(valid_rows(master), True)
    d = split_by_content(d)
    d = apply_settlement_share(d)
    d = d[d["sub"] != WARIKAN_SUB]        # 清算入金は収入に含めない
    d = level_annual(d)
    d["d"] = pd.to_datetime(d["date"], errors="coerce")
    d["day"] = d["d"].dt.day
    return d.dropna(subset=["day"])


def days_in_month(m: str) -> int:
    return pd.Period(m, "M").days_in_month


def prev_months(m: str, n: int) -> list[str]:
    p = pd.Period(m, "M")
    return [str(p - i) for i in range(n, 0, -1)]


def last_real_date(df: pd.DataFrame) -> pd.Timestamp | None:
    """取り込み済み明細の最終日。合成明細は数えない。

    合成明細（年次費用の月割り・相手負担の按分）は実在しない行で、当月まだ
    落ちていないローンの日付＝未来を持つことがある。データの新しさの根拠から外す。
    """
    real = df[~df["id"].astype(str).str.startswith(SYNTHETIC_ID_PREFIXES)]
    last = pd.to_datetime(real["date"], errors="coerce").max()
    return None if pd.isna(last) else last


def pace_asof(df: pd.DataFrame, month: str, lag: int = CARD_LAG_DAYS,
              exported_at: str | None = None) -> tuple[int, pd.Timestamp]:
    """(as-of の日, as-of の日付)。カード計上遅延ぶん手前に置く。

    誤差はどの方式でも過小（＝「まだ使える」と嘘をつく）方向に出るため、
    データが揃っている手前の日で分子も分母もそろえる。

    ローカル版との違いはここだけ。クラウドは書き出し時刻（exported_at）が
    分かるので、明細の最終日ではなくそれを使う（更新が止まっていても正しく出る）。
    """
    dim = days_in_month(month)
    start = pd.Timestamp(f"{month}-01")
    end = start + pd.Timedelta(days=dim - 1)
    today = pd.Timestamp(datetime.now(JST).date())
    exp = pd.to_datetime(exported_at, errors="coerce")
    # 合成明細（年次費用の月割り・相手負担の按分）は実在しない行で、当月まだ
    # 落ちていないローンの日付＝未来を持つことがある。データの新しさの根拠から外す
    real = df[~df["id"].astype(str).str.startswith(SYNTHETIC_ID_PREFIXES)]
    last = pd.to_datetime(real["date"], errors="coerce").max()
    if pd.notna(exp):
        last = min(last, exp.normalize()) if pd.notna(last) else exp.normalize()
    # 「信じられるデータの最終日」。遅延を引くのはここで、月末からではない
    avail = min(x for x in (today, last) if pd.notna(x)) - pd.Timedelta(days=lag)
    if avail >= end:      # 終わった月は満額で見る
        return dim, end
    if avail < start:
        return 0, avail
    return int(avail.day), avail


def _classify(vals: list[float]) -> tuple[str, float]:
    """出現頻度で補完方法を決める（設計メモ §5）。"""
    hits = [v for v in vals if abs(v) > 1e-9]
    n = len(vals) or 1
    rate = len(hits) / n
    if rate >= FREQ_HI:
        return "monthly", float(pd.Series(hits).median())
    if rate >= FREQ_LO:
        return "periodic", sum(vals) / n
    return "irregular", 0.0


@st.cache_data(show_spinner=False)
def build_baseline(df: pd.DataFrame, month: str, window: int = BASELINE_WINDOW,
                   start: str | None = None) -> dict:
    """対象月より前の window ヶ月から中項目ごとの見込みを作る（未来を見ない）。

    start を指定すると、それ以前の月は使わない。住み替えで生活水準が変わった
    ときに、前の家のデータが基準線に混ざるのを防ぐ。
    """
    ms = prev_months(month, window)
    if start:
        ms = [m for m in ms if m >= start] or ms
    hist = df[df["month"].isin(ms)]
    out = {"months": ms, "exp": {}, "inc": {}}
    for key, sign in (("exp", -1), ("inc", 1)):
        d = hist[hist["amount"] < 0] if sign < 0 else hist[hist["amount"] > 0]
        if d.empty:
            continue
        piv = d.pivot_table(index="sub", columns="month", values="amount",
                            aggfunc="sum", fill_value=0.0) * sign
        for sub in piv.index:
            vals = [float(piv.loc[sub, m]) if m in piv.columns else 0.0 for m in ms]
            kind, value = _classify(vals)
            out[key][sub] = {"kind": kind, "value": value}
    # 契約で決まっているものは推定を上書きする（基準線が短いときに効く）
    for sub, amt in fixed_amounts().items():
        out["exp"][sub] = {"kind": "fixed", "value": expected_monthly(sub, amt)}
    return out


def forecast_landing(df: pd.DataFrame, month: str, asof_day: int, baseline: dict) -> float:
    """月末着地の支出見込み（方式D: 固定費は費目別に補完、変動費は日割り）。"""
    dim = days_in_month(month)
    asof_day = max(1, min(asof_day, dim))
    cur = df[df["month"] == month]
    seen = cur[cur["day"] <= asof_day]
    neg = seen[seen["amount"] < 0]
    f = is_fixed(neg)
    fixed_a = float(-neg.loc[f, "amount"].sum())
    var_a = float(-neg.loc[~f, "amount"].sum())

    got = (-neg.loc[f].groupby("sub")["amount"].sum()).to_dict()
    hist = df[df["month"].isin(baseline["months"])]
    hneg = hist[hist["amount"] < 0]
    fixed_subs = set(hneg.loc[is_fixed(hneg), "sub"]) | set(fixed_amounts())
    # 「1件でも計上されていれば残りは無い」とすると、サブスクのように月内へ
    # 何度も請求が来る費目で大きく取りこぼす。見込みに届いていない分を残りとみなす
    est = sum(max(0.0, baseline["exp"][s]["value"] - float(got.get(s, 0.0)))
              for s in fixed_subs if s in baseline["exp"])
    return fixed_a + est + var_a * dim / asof_day


def remaining_budget(df: pd.DataFrame, month: str, asof_day: int,
                     target: float, baseline: dict) -> dict:
    """残り使える額（設計メモ §3）。上限は経常収入の見込みから作る。"""
    dim = days_in_month(month)
    d = max(1, min(asof_day, dim))
    cur = df[df["month"] == month]
    seen = cur[cur["day"] <= d]

    got_inc = seen.loc[seen["amount"] > 0].groupby("sub")["amount"].sum().to_dict()
    inc_est, irregular = 0.0, 0.0
    for sub, e in baseline["inc"].items():
        if e["kind"] == "irregular":
            continue
        inc_est += max(float(got_inc.get(sub, 0.0)), e["value"])
    for sub, v in got_inc.items():
        e = baseline["inc"].get(sub)
        if e is None or e["kind"] == "irregular":
            irregular += float(v)

    cap = inc_est * (1 - target)
    spent = float(-seen.loc[seen["amount"] < 0, "amount"].sum())

    seen_neg = seen[seen["amount"] < 0]
    booked = (-seen_neg.loc[is_fixed(seen_neg)].groupby("sub")["amount"].sum())
    booked_rows = [{"sub": s, "value": float(x)}
                   for s, x in booked.sort_values(ascending=False).items()]
    done = set(booked.index)

    upcoming_rows = []
    if d < dim:            # 終わった月にこれ以上落ちるものは無い
        hist = df[df["month"].isin(baseline["months"])]
        hneg = hist[hist["amount"] < 0]
        for s in set(hneg.loc[is_fixed(hneg), "sub"]) | set(fixed_amounts()):
            e = baseline["exp"].get(s)
            if e is None or e["value"] <= 0:
                continue
            # 計上済みでも、見込みに届いていなければ差分は今後落ちるとみなす
            # （サブスクのように月内へ何度も請求が来る費目の取りこぼしを防ぐ）
            got = float(booked.get(s, 0.0))
            rest = e["value"] - got
            if rest <= 0:
                continue
            upcoming_rows.append({"sub": s, "value": rest, "kind": e["kind"], "booked": got})
        upcoming_rows.sort(key=lambda x: -x["value"])
    upcoming = sum(x["value"] for x in upcoming_rows)

    remain = cap - spent - upcoming
    left = dim - d
    return {"income_est": inc_est, "cap": cap, "spent": spent, "upcoming": upcoming,
            "remain": remain, "left_days": left,
            "per_day": (remain / left if left > 0 else None),
            "irregular_income": irregular,
            "upcoming_rows": upcoming_rows, "booked_rows": booked_rows}



# 変動費の内訳の判定しきい値。色分けと4分類で同じものを使う
VP_MIN_DIFF = 1000     # これ未満の差は「いつも通り」。1件の買い物で動く額を拾わない
VP_OVER_RATE = 0.2     # いつもの何割増しから「明確に多い」とみなすか
VP_TINY_SHARE = 0.01   # 枠が総枠のこの割合未満なら「枠がない」と扱う（率も出さない）

# 4分類。判定の主軸は「いつも比」で、枠の日割りは「要注意」の第2条件にしか使わない。
# 月初は日割り枠が誤報を出すため（実データ: 外食はいつも比 −¥313 なのに日割り枠298%、
# 割り勘（食料品）はいつもより少ないのに170%）。いつも比のゲートを通らないものは赤くしない。
VP_OUT, VP_WARN, VP_OK_HIGH, VP_OK = "予算外", "要注意", "多いが余裕内", "想定内"
VP_FOLDED = (VP_OK_HIGH, VP_OK)        # 畳んで1行にまとめる分類


def vp_total(items: list[dict], label: str) -> dict:
    """費目の行をまとめて1行にする（合計行・畳んだ行に使う）。"""
    a = sum(x["actual"] for x in items)
    u = sum(x["usual"] for x in items)
    b = sum(x["budget"] for x in items)
    p = sum(x["paced"] for x in items)
    return {"sub": label, "actual": a, "usual": u, "vs_usual": a - u,
            "budget": b, "paced": p, "vs_budget": a - p,
            "ratio": (a / p if p > 0 else None),
            "count": sum(x["count"] for x in items)}


def vp_class(x: dict, total_budget: float) -> str:
    """変動費の1費目を4分類する。金額は変えず、ラベルを付けるだけ。"""
    d = x["vs_usual"]
    big = abs(d) >= VP_MIN_DIFF
    # 過去に実績がなく枠も付いていない＝一時的な支出。枠との比較に意味がない
    if x["usual"] <= 0 and x["budget"] < float(total_budget) * VP_TINY_SHARE:
        return VP_OUT if x["actual"] >= VP_MIN_DIFF else VP_OK
    over_usual = big and (x["usual"] <= 0 or d > x["usual"] * VP_OVER_RATE)
    if not over_usual:
        return VP_OK
    # ここまで来た費目は「いつもより明確に多い」。枠にも余裕がなければ要注意
    return VP_WARN if x["actual"] > x["paced"] else VP_OK_HIGH


def variable_progress(df: pd.DataFrame, month: str, asof_day: int,
                      total_budget: float, window: int = BASELINE_WINDOW) -> list[dict]:
    """変動費を費目ごとに「いつもより」「枠に対して」の2軸で見る。

    過去比: 直近 window ヶ月の「同じ日までの累積」の中央値との差。
      起点（baseline_from）は当てない。住み替えで変わったのは契約で決まる
      固定費であって、変動費に起点を当てる根拠がないため。実測でも
      6ヶ月窓の方が起点ありより誤差が小さい（4,699 対 8,789）。

    目標比: 月間の枠を経過割合で按分した額との差。着地の外挿はしない。
      件数の少ない費目（月2件のチケット代など）で破綻するため、
      過去比と同じ「as-of 時点同士の比較」にそろえる。

    枠は 総枠 × 過去シェア。枠のある費目に限れば、差の合計は全体の過不足と一致する。
    """
    dim = days_in_month(month)
    have = set(df["month"])
    ms = [m for m in prev_months(month, window) if m in have]
    neg = df[df["amount"] < 0]
    var = neg[~is_fixed(neg)]

    cur = var[(var["month"] == month) & (var["day"] <= asof_day)]
    actual = -cur.groupby("sub")["amount"].sum()
    if actual.empty:
        return []

    # 過去の「同じ日までの累積」。当月と条件をそろえる
    hist = var[(var["month"].isin(ms)) & (var["day"] <= asof_day)]
    hist_cum = -hist.groupby(["sub", "month"])["amount"].sum()

    # 枠の配分に使うシェアは月間の合計から出す（月末までの取り分なので）
    full = var[var["month"].isin(ms)]
    tot = -full.groupby("sub")["amount"].sum()
    share = tot / tot.sum() if tot.sum() > 0 else tot

    def make(sub: str, act: float, min_share: float) -> dict:
        vals = [float(hist_cum.get((sub, m), 0.0)) for m in ms]
        usual = float(pd.Series(vals).median()) if vals else 0.0
        budget = float(total_budget) * float(share.get(sub, 0.0))
        paced = budget * asof_day / dim
        return {
            "sub": sub, "actual": act, "usual": usual, "vs_usual": act - usual,
            "budget": budget, "paced": paced, "vs_budget": act - paced,
            # 枠が総枠の1%未満だと率が跳ねて意味を持たないので出さない
            "ratio": (act / paced if paced > 0 and budget >= min_share else None),
            "count": int((cur["sub"] == sub).sum()),
        }

    lo = float(total_budget) * 0.01
    rows = [make(s, float(a), lo) for s, a in actual.items()]
    rows.sort(key=lambda x: -x["vs_usual"])

    # 当月まだ使っていない費目も枠を持っている。一覧から抜くと分母が欠けて
    # 個別の率だけが跳ね上がるので、まとめて1行に出す
    spent = set(actual.index)
    unused = [make(s, 0.0, lo) for s in share.index if s not in spent]
    unused = [x for x in unused if x["budget"] > 0 or x["usual"] > 0]
    unused.sort(key=lambda x: -x["budget"])

    return {"rows": rows, "unused": unused,
            "unused_total": vp_total(unused, "まだ使っていない費目"),
            "total": vp_total(rows + unused, "合計"),
            "months": len(ms), "window": ms}



def variable_cumsum(df: pd.DataFrame, month: str, upto: int | None = None) -> list[float]:
    """変動費の日次累積（1日〜月末）。upto を超える日は None にする。"""
    dim = days_in_month(month)
    cur = df[(df["month"] == month) & (df["amount"] < 0)]
    cur = cur[~is_fixed(cur)]
    by_day = (-cur.groupby("day")["amount"].sum()).reindex(range(1, dim + 1), fill_value=0.0)
    cum = by_day.cumsum().tolist()
    if upto is not None:
        cum = [v if i + 1 <= upto else None for i, v in enumerate(cum)]
    return cum


def is_mobile() -> bool:
    """スマホからのアクセスかを User-Agent で判定する。
    Streamlit にビューポート幅を取る手段がないための代替で、幅そのものではない。
    そのため画面回転やPCの細いウィンドウには追随しない。
    """
    try:
        ua = str(st.context.headers.get("User-Agent", ""))
    except Exception:
        return False
    return any(k in ua for k in ("Mobile", "Android", "iPhone", "iPad"))


def chart_window(month_list: list[str], mobile: bool) -> list[str]:
    """棒グラフに載せる月を絞る（スマホのみ）。集計そのものには影響しない。"""
    if mobile and len(month_list) > CHART_MONTHS_MOBILE:
        return month_list[-CHART_MONTHS_MOBILE:]
    return month_list


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
    from html import escape as esc
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
    # 表示名（NM_ME / NM_PT）とは別物で、ローカル版は固定のキー名で保存する。
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

    def manual_sums(rows: list) -> tuple[float, float]:
        """手入力行の合計。結論を先に描くため、描画とは切り離して先に計算する。"""
        return sum(_me_val(r) for r in rows), sum(_pt_val(r) for r in rows)

    def manual_table(rows: list) -> None:
        """手入力行の表とメモを描く（金額は manual_sums 側で計算済み）。"""
        if not rows:
            return
        st.markdown("**手で追加した項目**")
        # メモは直下の「📝 メモ全文」に全文が出るので、狭い画面を圧迫する列は持たせない
        show_settle_table(pd.DataFrame([{
            "項目": r.get("項目", ""), NM_ME: fyen(_me_val(r)),
            NM_PT: fyen(_pt_val(r)),
        } for r in rows]), NM_PT)
        memos = [r for r in rows if str(r.get("メモ", "")).strip()]
        if memos:
            items = "".join(
                f"<div style='margin:3px 0'><b>{esc(str(r.get('項目') or '（項目名なし）'))}</b>："
                f"{esc(str(r.get('メモ'))).replace(chr(10), '<br>')}</div>" for r in memos)
            st.markdown(
                f"<div style='font-size:12px;color:{SUBTLE};background:#FFFFFF;"
                f"border:1px solid {LINE};border-radius:6px;padding:8px 12px;"
                f"margin:4px 0 8px'>📝 メモ全文<br>{items}</div>", unsafe_allow_html=True)

    def landing(v: float) -> None:
        """この項目のうちいくらが請求額に流れたかを示す締めの1行。"""
        st.markdown(
            f"<div class='settle-land'>→ このうち <b>{fyen_x(v)}</b> が{esc(NM_PT)}の負担として、"
            "上の「この金額の内訳」に入っています</div>", unsafe_allow_html=True)

    def detail_rows(subs, note_excluded: bool) -> list:
        """費目ごとに明細＋小計を並べた行を作る。"""
        tgt = mrows[mrows["sub"].isin(list(subs))].sort_values(["sub", "date"])
        out = []
        for s in subs:
            g = tgt[tgt["sub"] == s]
            if g.empty:
                continue
            for r in g.itertuples():
                mark = "" if (r.calc == 1 or not note_excluded) else "（家計簿の集計対象外）"
                out.append({"費目": r.sub, "日付": r.date, "内容": r.content + mark,
                            "金額": fyen(-r.amount)})
            out.append({"費目": f"── {s} 小計", "日付": "", "内容": "",
                        "金額": fyen_x(-g["amount"].sum())})
        return out

    # ============================================================
    # 集計（描画より先にすべて計算する。結論を画面の先頭に出すため）
    # ============================================================
    # ① 住まいの費用
    rent_rows = mrows[(mrows["sub"].isin(rent_subs)) & (mrows["calc"] == 1)
                      & (mrows["transfer"] != 1) & (mrows["amount"] < 0)]
    rent_mf = -rent_rows["amount"].sum()
    rent_total = rent_mf + wife_loan
    rent_me_amt = rent_total * r_share
    rent_wf_amt = rent_total - rent_me_amt

    # ② 毎月の生活費
    fx_view, fx_auto_me, fx_auto_wf = [], 0.0, 0.0
    for s in fx_subs:
        amt = -mrows.loc[mrows["sub"] == s, "amount"].sum()
        me = amt * s_share
        fx_view.append({"項目": s, "金額": fyen_x(amt), NM_ME: fyen_x(me),
                        NM_PT: fyen_x(amt - me)})
        fx_auto_me += me
        fx_auto_wf += amt - me
    fx_manual = rec.get("fixed_manual", [])
    fx_man_me, fx_man_wf = manual_sums(fx_manual)
    fx_me = fx_auto_me + fx_man_me
    fx_wf = fx_auto_wf + fx_man_wf

    # ③ 今月だけの費用
    # 「割合」を独立した列に持つと5列になり、スマホでは1セルが3〜4行に折り返って崩れる。
    # 全額負担の行だけ項目名に添え、②と同じ4列に揃える。
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
    sp_manual = rec.get("special_manual", [])
    sp_man_me, sp_man_wf = manual_sums(sp_manual)
    sp_me_amt = sp_split_me + sp_man_me
    sp_wf_amt = sp_full_wf + sp_split_wf + sp_man_wf

    total_me = rent_me_amt + fx_me + sp_me_amt
    total_wf = rent_wf_amt + fx_wf + sp_wf_amt
    billed = total_wf - wife_loan
    billed_floor = int(math.floor(billed))

    # ============================================================
    # 描画
    # ============================================================
    # ---- 結論（この画面の主役）----
    if billed >= 0:
        cap = f"{month_label(s_month)} の {NM_PT} へのご請求"
        amt_txt = fyen(billed_floor)
        if wife_loan > 0:
            sub = (f"{NM_PT}の負担 {fyen_x(total_wf)} のうち、住宅ローンとして直接"
                   f"支払い済みの {fyen(wife_loan)} を差し引いた金額です。")
        else:
            sub = f"{NM_PT}の負担の合計です。"
    else:
        cap = f"{month_label(s_month)} は {NM_ME} から {NM_PT} へお支払い"
        amt_txt = fyen(abs(billed_floor))
        sub = (f"{NM_PT}の負担 {fyen_x(total_wf)} より、直接支払い済みの "
               f"{fyen(wife_loan)} の方が多いため、差額をお返しします。")
    st.markdown(f"<div class='settle-hero'><div class='cap'>{esc(cap)}</div>"
                f"<div class='amt'>{esc(amt_txt)}</div>"
                f"<div class='sub'>{esc(sub)}</div></div>", unsafe_allow_html=True)

    # ---- ①②③ が請求額に積み上がる流れ ----
    st.markdown("##### この金額の内訳")
    steps = [("①", "住まいの費用", rent_wf_amt),
             ("②", "毎月の生活費", fx_wf),
             ("③", "今月だけの費用", sp_wf_amt)]
    lines = "".join(
        f"<tr class='step'><td><span class='sflow-idx'>{i}</span> {esc(n)}</td>"
        f"<td class='n'>{fyen_x(v)}</td></tr>" for i, n, v in steps)
    lines += (f"<tr class='sum'><td>{esc(NM_PT)}の負担 合計</td>"
              f"<td class='n'>{fyen_x(total_wf)}</td></tr>")
    if wife_loan > 0:
        lines += (f"<tr class='minus'><td>− 直接支払い済み（住宅ローン）</td>"
                  f"<td class='n'>−{fyen(wife_loan)}</td></tr>")
    final_label = ("お支払いいただく金額" if billed >= 0
                   else f"{NM_ME} からお返しする金額")
    lines += (f"<tr class='final'><td>{esc(final_label)}</td>"
              f"<td class='n'>{fyen(abs(billed_floor))}</td></tr>")
    st.markdown(f"<div style='border:1px solid {LINE};border-radius:8px;overflow:hidden;"
                f"margin:2px 0 12px'><table class='sflow'>{lines}</table></div>",
                unsafe_allow_html=True)
    st.caption("下の項目をタップすると、それぞれの中身を確認できます。")

    # ---- ① 住まいの費用 ----
    with st.expander(f"① 住まいの費用　{NM_PT}の負担 {fyen_x(rent_wf_amt)}"):
        st.caption(f"住宅ローンと管理費を {NM_ME} {r_me} : {NM_PT} {r_wf} で分けています。")
        show_settle_table([
            {"項目": f"{NM_ME}が払った住宅ローン・管理費", "金額": fyen(rent_mf)},
            {"項目": f"＋ {NM_PT}が直接払った住宅ローン", "金額": fyen(wife_loan)},
            {"項目": "＝ 住まいの費用 合計", "金額": fyen_x(rent_total)},
            {"項目": f"→ {NM_ME}の負担（{r_me}）", "金額": fyen_x(rent_me_amt)},
            {"項目": f"→ {NM_PT}の負担（{r_wf}）", "金額": fyen_x(rent_wf_amt)},
        ], NM_PT)
        with st.expander("対象になった明細を見る"):
            rows_view = [{"日付": r.date, "内容": r.content, "費目": r.sub,
                          "金額": fyen(-r.amount)}
                         for r in rent_rows.sort_values("date").itertuples()]
            rows_view.append({"日付": "—", "内容": f"{NM_PT}が直接払った住宅ローン",
                              "費目": "—", "金額": fyen(wife_loan)})
            rows_view.append({"日付": "", "内容": "合計", "費目": "",
                              "金額": fyen_x(rent_total)})
            show_settle_table(rows_view, NM_PT)
        landing(rent_wf_amt)

    # ---- ② 毎月の生活費 ----
    with st.expander(f"② 毎月の生活費　{NM_PT}の負担 {fyen_x(fx_wf)}"):
        half = "半分ずつ" if s_me == s_wf else f"{NM_ME} {s_me} : {NM_PT} {s_wf}"
        st.caption(f"毎月かかる生活費を {half} で分けています。")
        show_settle_table(fx_view, NM_PT)
        with st.expander("対象になった明細を見る"):
            rows_view = detail_rows(fx_subs, note_excluded=False)
            if rows_view:
                show_settle_table(rows_view, NM_PT)
            else:
                st.caption("対象の明細はありません。")
        manual_table(fx_manual)
        landing(fx_wf)

    # ---- ③ 今月だけの費用 ----
    with st.expander(f"③ 今月だけの費用　{NM_PT}の負担 {fyen_x(sp_wf_amt)}"):
        note = f"「（全額{NM_PT}）」は全額{NM_PT}の負担です。"
        if sp_split:
            half = "半分ずつ" if s_me == s_wf else f"{NM_ME} {s_me} : {NM_PT} {s_wf}"
            note += f"それ以外は {half} で分けています。"
        st.caption(note)
        show_settle_table(sp_view, NM_PT)
        with st.expander("対象になった明細を見る"):
            rows_view = detail_rows(list(sp_full) + list(sp_split), note_excluded=True)
            if rows_view:
                show_settle_table(rows_view, NM_PT)
            else:
                st.caption("対象の明細はありません。")
        manual_table(sp_manual)
        landing(sp_wf_amt)

    # ---- 参考情報 ----
    st.caption(f"（参考）{NM_ME}の負担合計 {fyen_x(total_me)}")

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
  /* 「今月」タブの結論カード（清算ビューの .settle-hero と同じ考え方） */
  .pace-hero {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1px;
    background: {LINE}; border: 1px solid {LINE}; border-radius: 12px;
    overflow: hidden; margin: 4px 0 14px; }}
  .pace-hero .ph {{ background: #FFFFFF; padding: 16px 18px; }}
  .pace-hero .ph-label {{ color: {SUBTLE}; font-size: 12px; line-height: 1.4; }}
  .pace-hero .ph-big {{ font-size: 34px; font-weight: 600; line-height: 1.2;
    font-variant-numeric: tabular-nums; margin-top: 2px; }}
  .pace-hero .ph-mid {{ font-size: 26px; font-weight: 600; line-height: 1.25;
    font-variant-numeric: tabular-nums; margin-top: 2px; }}
  .pace-hero .ph-sub {{ color: {SUBTLE}; font-size: 12px; margin-top: 5px;
    line-height: 1.45; }}
  /* 速報（未確定）は確定より一段弱く見せる。主従が並んで見えると誤読を招く */
  .pace-hero .ph.prov {{ background: {PAPER}; }}
  .pace-hero .ph-prov {{ font-size: 22px; font-weight: 600; line-height: 1.25;
    font-variant-numeric: tabular-nums; margin-top: 2px; color: {SUBTLE}; }}
  .pace-hero .ph-tag {{ display: inline-block; font-size: 10px; font-weight: 600;
    padding: 0 5px; margin-left: 6px; border-radius: 3px; vertical-align: 2px;
    background: #FFF4E5; color: #A45B00; }}
  @media (max-width: 640px) {{
    .pace-hero {{ grid-template-columns: 1fr; }}
    .pace-hero .ph-big {{ font-size: 30px; }}
  }}
  table.ntab th {{ color: {SUBTLE}; font-weight: 500; font-size: 12px;
    padding: 8px 12px; border-bottom: 1px solid {LINE};
    background: #FFFFFF; position: sticky; top: 0; white-space: nowrap; }}
  table.ntab td {{ padding: 7px 12px; border-bottom: 1px solid #F0F0F2;
    vertical-align: top; }}
  table.ntab tr:last-child td {{ border-bottom: none; }}
  /* 変動費の費目別進捗（スマホ想定の2行構成） */
  .vprog {{ border: 1px solid {LINE}; border-radius: 8px; background: #FFFFFF;
    overflow: hidden; margin: 2px 0 10px; }}
  .vprog .vp {{ padding: 9px 12px; border-bottom: 1px solid #F0F0F2; }}
  .vprog .vp:last-child {{ border-bottom: none; }}
  .vprog .vp-head {{ display: flex; justify-content: space-between;
    align-items: baseline; gap: 10px; }}
  .vprog .vp-name {{ font-size: 13px; }}
  .vprog .vp-amt {{ font-size: 15px; font-weight: 600; white-space: nowrap;
    font-variant-numeric: tabular-nums; }}
  .vprog .vp-sub {{ color: {SUBTLE}; font-size: 12px; margin-top: 3px;
    line-height: 1.5; font-variant-numeric: tabular-nums; }}
  .vprog .vp-sub b {{ font-weight: 600; }}
  /* 4分類のラベル。要チェックだけ目に入るよう、想定内は無彩色にする */
  .vprog .vp-tag {{ display: inline-block; font-size: 11px; line-height: 1.6;
    padding: 0 6px; margin-right: 6px; border-radius: 3px; font-weight: 600;
    vertical-align: 1px; white-space: nowrap; }}
  .vprog .vp-tag.warn {{ background: #FDECEC; color: {ERROR}; }}
  .vprog .vp-tag.out  {{ background: #FFF4E5; color: #A45B00; }}
  .vprog .vp-tag.ok   {{ background: {PAPER}; color: {SUBTLE}; }}
  /* 残り＝主表示。実額といつも比は補足に落とす */
  .vprog .vp-left {{ font-size: 12px; color: {SUBTLE}; }}
  div[data-testid="stMetric"] {{ background:#FFF; border:1px solid {LINE};
      border-radius:8px; padding:12px 16px; }}
  /* 清算ビューの結論カード。st.metric では値の文字サイズを変えられないため自前で描く */
  .settle-hero {{ background:{GREEN_50}; border:1px solid {GREEN_900};
      border-radius:12px; padding:16px 18px; margin:6px 0 18px; }}
  .settle-hero .cap {{ font-size:12px; color:{GREEN_900}; font-weight:600;
      letter-spacing:.02em; }}
  .settle-hero .amt {{ font-size:40px; font-weight:700; color:{GREEN_1200};
      line-height:1.1; font-variant-numeric:tabular-nums; margin:4px 0 6px; }}
  .settle-hero .sub {{ font-size:12px; color:{SUBTLE}; line-height:1.6; }}
  /* ①②③ が請求額に積み上がる流れを示す表 */
  table.sflow {{ width:100%; border-collapse:collapse; font-size:13px;
      font-variant-numeric:tabular-nums; background:#FFFFFF; }}
  table.sflow td {{ padding:9px 12px; }}
  table.sflow td.n {{ text-align:right; white-space:nowrap; }}
  table.sflow tr.step td {{ border-bottom:1px solid #F0F0F2; }}
  table.sflow tr.sum td {{ border-top:2px solid {GREEN_900}; font-weight:600; }}
  table.sflow tr.minus td {{ color:{SUBTLE}; }}
  table.sflow tr.final td {{ border-top:2px solid {GREEN_900}; font-weight:700;
      background:{GREEN_50}; font-size:15px; }}
  .sflow-idx {{ color:{GREEN_900}; font-weight:600; }}
  .settle-land {{ background:#FFFFFF; border:1px dashed {GREEN_200};
      border-radius:8px; padding:8px 12px; font-size:13px; margin:8px 0 2px; }}
  .settle-land b {{ color:{GREEN_900}; }}
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
            IS_MOBILE = is_mobile()
            # 棒グラフの横軸に載せる月（スマホは直近12ヶ月）。KPIや表は全期間のまま。
            chart_months = chart_window(all_months, IS_MOBILE)
            settle = bundle.get("settlement", {})
            settings = bundle.get("settings", {})
            # ペース計算はローカル版と同じ関数を使う。設定の入口だけバンドルに差し替える
            _BUNDLE_SETTINGS = settings
            _BUNDLE_SETTLE = settle

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
                st.title("家計ダッシュボード")
                # スマホではサイドバーが既定で折りたたまれるため、切替頻度の高い期間の
                # 選択は本文に置く（清算ビューの「清算対象月」と同じ扱い）。
                pc1, pc2 = st.columns([1, 1])
                mode = pc1.radio("期間", ["月", "年", "全期間"], horizontal=True)
                sel_month = sel_year = None
                if mode == "月" and months:
                    sel_month = pc2.selectbox("対象月", list(reversed(months)),
                                              format_func=month_label)
                elif mode == "年" and years:
                    sel_year = pc2.selectbox("対象年", list(reversed(years)),
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

                tab_now, tab_ov, tab_cat, tab_fix, tab_settle, tab_tx = st.tabs(
                    ["今月", "概要", "カテゴリ", "固定費", "清算", "明細"])

                monthly = []
                grouped = dict(tuple(scoped_all.groupby("month")))
                for m in chart_months:
                    s = summarize(grouped.get(m, scoped_all.iloc[0:0]), net_warikan)
                    monthly.append({"label": m[2:].replace("-", "/"), "収入": s["income"],
                                    "支出": s["expense"], "収支": s["balance"]})
                monthly = pd.DataFrame(monthly)

                # ---------- 今月（ペース管理） ----------
                with tab_now:
                    pdf = pace_base(master)
                    pace_month = sel_month if mode == "月" else months[-1]
                    target = savings_target()

                    bl_start = baseline_from()
                    want = [m for m in prev_months(pace_month, BASELINE_WINDOW)
                            if not bl_start or m >= bl_start]
                    have = [m for m in want if m in set(pdf["month"])]

                    if pace_month not in set(pdf["month"]):
                        st.info(f"{month_label(pace_month)} のデータがまだありません。")
                    elif not have:
                        st.warning(
                            f"{month_label(pace_month)} の基準線に使える月がありません"
                            + (f"（起点 {month_label(bl_start)} 以降のデータが必要です）。" if bl_start
                               else "。"))
                    else:
                        asof_day, asof_ts = pace_asof(pdf, pace_month,
                                                          exported_at=bundle.get("exported_at"))
                        dim = days_in_month(pace_month)
                        bl = build_baseline(pdf, pace_month, BASELINE_WINDOW, bl_start)

                        if asof_day < 1:
                            st.info(
                                f"{month_label(pace_month)} はまだ集計できる日数がありません。"
                                f"カードの計上が{CARD_LAG_DAYS}日ほど遅れるため、"
                                f"{CARD_LAG_DAYS + 4}日目ごろから表示できます。"
                            )
                        else:
                            r = remaining_budget(pdf, pace_month, asof_day, target, bl)
                            over = r["remain"] < 0

                            # ---- 表示に使う数字をここでまとめて作る（計算は変えない） ----
                            # 残り日数は as-of の翌日から月末まで。カレンダー上の「今日」より
                            # 数日ぶん多いので、日数だけでなく期間も出して誤解を避ける
                            span_from = asof_ts + pd.Timedelta(days=1)
                            span = (f"{span_from.month}月{span_from.day}日〜"
                                    f"{int(pace_month[5:7])}月{dim}日の{r['left_days']}日分")

                            hist_months = bl["months"]
                            series = [variable_cumsum(pdf, m) for m in hist_months]
                            med = [float(pd.Series([s[i] for s in series if i < len(s)]).median())
                                   for i in range(31)]
                            cur_cum = variable_cumsum(pdf, pace_month, upto=asof_day)
                            v_now = next((v for v in reversed(cur_cum) if v is not None), 0.0)
                            v_med = med[min(asof_day, 31) - 1]

                            land = rate_est = None
                            if asof_day >= CARD_LAG_DAYS + 4:
                                land = forecast_landing(pdf, pace_month, asof_day, bl)
                                if r["income_est"] > 0:
                                    rate_est = (r["income_est"] - land) / r["income_est"] * 100

                            def cell(label, value, sub, color=INK, size="ph-big", cls="ph"):
                                return (f'<div class="{cls}"><div class="ph-label">{label}</div>'
                                        f'<div class="{size}" style="color:{color}">{value}</div>'
                                        f'<div class="ph-sub">{sub}</div></div>')

                            # ---- 速報（最終取引日まで・未確定） ----
                            # 確定 as-of は安全マージンを取って手前に置くが、それだけだと
                            # 「1週間前の数字しか見えない」。最終取引日までのぶんも並べて出す。
                            # ただし未確定なので、確定より一段弱く見せる（主従をはっきりさせる）
                            # 書き出し時刻も渡すので、速報も「バンドルより新しくは」ならない
                            prov_day, prov_ts = pace_asof(
                                pdf, pace_month, lag=0,
                                exported_at=bundle.get("exported_at"))
                            prov = None
                            if prov_day > asof_day:
                                pr = remaining_budget(pdf, pace_month, prov_day, target, bl)
                                pr_fixed = sum(x["value"] for x in pr["booked_rows"])
                                prov = {"day": prov_day, "ts": prov_ts, "r": pr,
                                        "fixed": pr_fixed, "var": pr["spent"] - pr_fixed}

                            # 使った額のうち、自分の裁量で動かせるのは変動費だけ。
                            # 固定費と混ぜて出すと「何にそんなに使ったのか」が分からなくなる
                            fixed_paid = sum(x["value"] for x in r["booked_rows"])
                            var_paid = r["spent"] - fixed_paid
                            paid_note = f"固定費 {fyen(fixed_paid)} ／ 変動費 {fyen(var_paid)}"

                            # 上段2つ: 日々の判断に使う数字。同じ大きさで並べる
                            # 過去の月も見られるので、ラベルに「今月」とは書かない
                            if over:
                                main = (
                                    cell("目標からの超過額", fyen(-r["remain"]),
                                         f"上限 {fyen(r['cap'])} に対して {fyen(r['spent'] + r['upcoming'])}",
                                         ERROR)
                                    + cell("使った額", fyen(r["spent"]), paid_note, INK))
                            elif r["per_day"]:
                                main = (
                                    cell("1日あたり使えるのは", fyen(r["per_day"]), span, GREEN_900)
                                    + cell("あと使えるのは", fyen(r["remain"]),
                                           f"上限 {fyen(r['cap'])} − 使った額 {fyen(r['spent'])}"
                                           f" − 今後の固定費 {fyen(r['upcoming'])}", GREEN_900))
                            else:
                                main = (
                                    cell("最終的な余り", fyen(r["remain"]), "今月は終了", GREEN_900)
                                    + cell("使った額", fyen(r["spent"]), paid_note, INK))

                            # 下段2つ: ペースの良し悪しを示す指標
                            if v_med > 0:
                                diff = v_now / v_med - 1
                                pace_val = f"{diff * 100:+.0f}%"
                                pace_sub = (f"{asof_day}日時点 {fyen(v_now)} ／ "
                                            f"過去{len(hist_months)}ヶ月の中央値 {fyen(v_med)}")
                                pace_col = ERROR if diff > 0.05 else (GREEN_900 if diff < -0.05 else INK)
                            else:
                                pace_val, pace_sub, pace_col = "—", "比較できる過去データがありません", SUBTLE

                            if rate_est is None:
                                rate_val, rate_sub, rate_col = "—", "月初は不安定なため非表示", SUBTLE
                            else:
                                gap = rate_est - target * 100
                                rate_val = f"{rate_est:.1f}%"
                                rate_sub = (f"目標 {target*100:.0f}% に対して {gap:+.1f}pt"
                                            f" ／ 着地見込み {fyen(land)}")
                                rate_col = GREEN_900 if gap >= 0 else ERROR

                            side = (
                                cell(f"変動費のペース（過去{len(hist_months)}ヶ月の同じ日と比べて）",
                                     pace_val, pace_sub, pace_col, "ph-mid")
                                + cell("このペースでの貯蓄率", rate_val, rate_sub, rate_col, "ph-mid"))

                            # 速報の1日あたり。暗算させないために画面で出す
                            prov_cells = ""
                            if prov and prov["r"]["per_day"]:
                                pv = prov["r"]["per_day"]
                                gap2 = pv - (r["per_day"] or 0)
                                # 月末を跨ぐので日付は Timestamp で足す（day+1 だと31日で壊れる）
                                p_from = prov["ts"] + pd.Timedelta(days=1)
                                prov_cells = (
                                    cell(f'1日あたり（速報 {prov["ts"].month}/{prov["ts"].day}時点）'
                                         '<span class="ph-tag">未確定</span>',
                                         fyen(pv),
                                         f'{p_from.month}/{p_from.day}〜'
                                         f'{int(pace_month[5:7])}/{dim} の{prov["r"]["left_days"]}日分'
                                         f'　／　確定ベースとの差 '
                                         f'{"+" if gap2 >= 0 else "−"}{fyen(abs(gap2))}',
                                         SUBTLE, "ph-prov", "ph prov")
                                    + cell('速報で増えたぶん<span class="ph-tag">未確定</span>',
                                           fyen(prov["r"]["spent"] - r["spent"]),
                                           f'変動費 {fyen(prov["var"] - var_paid)}'
                                           f' ／ 固定費 {fyen(prov["fixed"] - fixed_paid)}'
                                           f'　／　この{prov["day"] - asof_day}日分はまだ増えます',
                                           SUBTLE, "ph-prov", "ph prov"))

                            st.markdown(
                                f'<div class="pace-hero">{main}{side}{prov_cells}</div>',
                                unsafe_allow_html=True)
                            # 基準日は最新明細より手前に出るので、データが古いと誤解されやすい。
                            # 「どこまで取り込めているか」を並べて書いて、遅延ぶんだと分かるようにする
                            imported = last_real_date(pdf)
                            imported_note = (
                                f"明細は{imported.month}月{imported.day}日まで取込済み。"
                                f"カード計上の遅れ{CARD_LAG_DAYS}日分を引いた日を基準にしています"
                                if imported is not None else
                                f"カード計上の遅れ{CARD_LAG_DAYS}日分を手前に置いています")
                            st.caption(
                                f"{month_label(pace_month)}／{asof_ts.month}月{asof_ts.day}日時点のデータ"
                                f"（{asof_day} / {dim}日経過）　:grey[ⓘ {imported_note}]",
                                help=f"{imported_note}。データが古いのではなく、"
                                     "カード利用分がMoneyForwardに載るまでの遅れを見込んで"
                                     "基準日を手前に置いています。")

                            # 速報が確定より大きく出る月がある。理由を読めるようにしておく
                            if prov and prov["r"]["per_day"] and r["per_day"] \
                                    and prov["r"]["per_day"] > r["per_day"]:
                                with st.expander("▼ 速報のほうが1日あたりが大きいのはなぜか"):
                                    st.markdown(
                                        f"""
                                        **as-of を進めても、1日あたりが下がるとは限りません。**
                                        分子（残り使える額）と分母（残り日数）が同時に動くためです。

                                        | | 確定 {asof_ts.month}/{asof_ts.day} | 速報 {prov['ts'].month}/{prov['ts'].day} |
                                        |---|---:|---:|
                                        | 残り使える額 | {fyen(r['remain'])} | {fyen(prov['r']['remain'])} |
                                        | 残り日数 | {r['left_days']}日 | {prov['r']['left_days']}日 |
                                        | 1日あたり | {fyen(r['per_day'])} | {fyen(prov['r']['per_day'])} |

                                        この{prov['day'] - asof_day}日で**残り使える額は
                                        {fyen(abs(r['remain'] - prov['r']['remain']))} しか減っていない**のに、
                                        残り日数は{prov['day'] - asof_day}日減りました。だから割った結果が
                                        大きくなります。

                                        使った額は {fyen(prov['r']['spent'] - r['spent'])} 増えていますが、
                                        その大半は固定費（{fyen(prov['fixed'] - fixed_paid)}）で、
                                        **「今後落ちる固定費」から「使った額」へ移っただけ**なので
                                        残り使える額はほとんど動いていません。

                                        **速報はまだ増えます。**実測（2026-08-15）では、最終取引日の
                                        1日前で約8%、2日前で約1.5%の支出が後から足されました。
                                        いまの速報値は**低めに出ていると考えてください**。
                                        """
                                    )

                            # データの鮮度。クラウドは閲覧専用なので、ローカル版の
                            # 「取込時刻」ではなくバンドルの書き出し時刻を使う。
                            # 閲覧者にとってはこちらが正しい（取り込んでも書き出さなければ届かない）
                            exp_ts = pd.to_datetime(bundle.get("exported_at"), errors="coerce")
                            if pd.notna(exp_ts):
                                ago = (pd.Timestamp(datetime.now(JST).date())
                                       - exp_ts.normalize()).days
                                txt = (f"最後のデータ更新 {exp_ts.month}月{exp_ts.day}日 "
                                       f"{exp_ts.strftime('%H:%M')}"
                                       + (f"（{ago}日前）" if ago > 0 else "（今日）"))
                                if ago >= 1:
                                    st.info(
                                        txt + "。ローカルPCで `update_cloud_data.bat` を実行すると、"
                                        f"基準日が最大{ago}日ぶん新しくなります。", icon="📥")
                                else:
                                    st.caption(txt)
                            if r["irregular_income"] > 0:
                                st.caption(f"※ 今月は賞与など不定期の入金 {fyen(r['irregular_income'])} が"
                                           "あります（上限には含めていません）")
                            if bl_start and len(hist_months) < BASELINE_WINDOW:
                                st.warning(
                                    f"生活水準が変わった {month_label(bl_start)} を起点にしているため、"
                                    f"基準線は **{len(hist_months)}ヶ月ぶん**（{'・'.join(month_label(m) for m in hist_months)}）"
                                    f"のデータで作っています。{BASELINE_WINDOW}ヶ月そろうまでは見込みがぶれやすく、"
                                    "特に隔月・不定期の費目は精度が落ちます。", icon="⚠️")

                            # ---- 根拠 ----
                            with st.expander("▼ 残り使える額の計算過程"):
                                html_table(pd.DataFrame([
                                    {"項目": "今月の収入見込み", "金額": fyen(r["income_est"])},
                                    {"項目": f"× {(1-target)*100:.0f}%（目標貯蓄率 {target*100:.0f}%）",
                                     "金額": ""},
                                    {"項目": "= 使ってよい上限", "金額": fyen(r["cap"])},
                                    {"項目": "− すでに使った額", "金額": fyen(r["spent"])},
                                    {"項目": "　　うち 固定費（支払い済み）", "金額": fyen(fixed_paid)},
                                    {"項目": "　　うち 変動費", "金額": fyen(var_paid)},
                                    {"項目": "− 今後落ちる固定費", "金額": fyen(r["upcoming"])},
                                    {"項目": "= 残り使える額", "金額": fyen(r["remain"])},
                                ]))

                                KIND_LABEL = {"monthly": "毎月", "periodic": "隔月等",
                                              "irregular": "不定期", "fixed": "確定額"}
                                fc1, fc2 = st.columns(2)
                                with fc1:
                                    st.markdown(f"**今後落ちる固定費の内訳**（{fyen(r['upcoming'])}）")
                                    if r["upcoming_rows"]:
                                        html_table(pd.DataFrame([
                                            {"費目": x["sub"], "見込み": fyen(x["value"]),
                                             "頻度": KIND_LABEL.get(x["kind"], x["kind"])}
                                            for x in r["upcoming_rows"]]))
                                    else:
                                        st.caption("この月の固定費はすべて計上済みです。")
                                with fc2:
                                    st.markdown(f"**計上済みの固定費**（{fyen(sum(x['value'] for x in r['booked_rows']))}）")
                                    if r["booked_rows"]:
                                        html_table(pd.DataFrame([
                                            {"費目": x["sub"], "実績": fyen(x["value"])}
                                            for x in r["booked_rows"]]))
                                    else:
                                        st.caption("まだありません。")

                                lv = leveled_defs()
                                if lv:
                                    st.caption(
                                        "※ 年次費用（" + "・".join(lv) + "）は月割りの積立として"
                                        f"毎月 {fyen(sum(d['monthly'] for d in lv.values()))} 計上しています")
                                sh = settle_shares()
                                st.caption(
                                    f"※ {display_names()[1]}と分け合う費用は、清算タブと同じ按分で"
                                    f"自分の負担分だけを計上しています"
                                    f"（住宅費 {sh['rent_share']*100:.0f}%／"
                                    f"{'・'.join(sh['split_subs'])} {sh['split_share']*100:.0f}%）。"
                                    "相手からの清算入金は収入に含めていません")
                                if sh["wife_loan_default"]:
                                    st.caption(
                                        f"※ 相手が自分の口座から直接払っているローン "
                                        f"{fyen(sh['wife_loan_default'])} も住宅費の総額に含めています"
                                        "（MFには載らないため清算タブの入力値を使用）")

                            # ---- 変動費の累積グラフ ----
                            st.subheader("変動費の使い方")
                            st.caption("固定費は日々コントロールできないので、変動費だけで描いています。"
                                       f"薄い線は過去{len(hist_months)}ヶ月、破線はその中央値。"
                                       + ("点線は速報（未確定・まだ増えます）。" if prov else ""))
                            fig = go.Figure()
                            for m, cum in zip(hist_months, series):
                                fig.add_trace(go.Scatter(
                                    x=list(range(1, len(cum) + 1)), y=cum, mode="lines",
                                    name=month_label(m), line=dict(color=GRAY_200, width=1),
                                    hovertemplate="%{y:,.0f}円<extra>" + month_label(m) + "</extra>"))
                            fig.add_trace(go.Scatter(
                                x=list(range(1, 32)), y=med, mode="lines",
                                name=f"過去{len(hist_months)}ヶ月の中央値",
                                line=dict(color=GRAY_600, width=1.5, dash="dash"),
                                hovertemplate="%{y:,.0f}円<extra>中央値</extra>"))
                            fig.add_trace(go.Scatter(
                                x=list(range(1, dim + 1)), y=cur_cum, mode="lines",
                                name=month_label(pace_month),
                                line=dict(color=GREEN_600, width=3),
                                hovertemplate="%{y:,.0f}円<extra>" + month_label(pace_month) + "</extra>"))
                            if prov:
                                # 確定〜最終取引日は点線。まだ増えるぶんだと目で分かるようにする
                                p_cum = variable_cumsum(pdf, pace_month, upto=prov["day"])
                                seg = [v if asof_day <= i + 1 <= prov["day"] else None
                                       for i, v in enumerate(p_cum)]
                                fig.add_trace(go.Scatter(
                                    x=list(range(1, dim + 1)), y=seg, mode="lines",
                                    name="速報（未確定）",
                                    line=dict(color=GREEN_600, width=3, dash="dot"),
                                    hovertemplate="%{y:,.0f}円<extra>速報（未確定）</extra>"))
                            fig.add_vline(x=asof_day, line=dict(color=SUBTLE, width=1, dash="dot"))
                            fig.update_xaxes(title_text="日", dtick=5, range=[1, 31])
                            st.plotly_chart(base_layout(fig, height=300), width="stretch",
                                            key="pace_var_cum", config=PLOTLY_CONFIG)

                            # 何にいくら使っているか。ペース管理の主目的なので折りたたまない
                            total_budget = r["remain"] + var_paid      # 変動費の総枠（月間）
                            prog = variable_progress(pdf, pace_month, asof_day, total_budget)
                            if prog["rows"]:
                                st.markdown(f"**変動費の内訳**（{asof_day}日時点・{fyen(var_paid)}）")
                                st.caption(
                                    f"主な数字は**残り**（月間の枠 − 今月の実額）。判定は"
                                    f"「いつも」（過去{prog['months']}ヶ月の同じ日までの累積の中央値）を"
                                    "主軸にしています。枠の日割りは月初にぶれるので前面には出しません。")

                                # 4分類。要チェックだけ上に残し、想定内は1行に畳む
                                buckets: dict[str, list[dict]] = {VP_OUT: [], VP_WARN: [],
                                                                  VP_OK_HIGH: [], VP_OK: []}
                                for x in prog["rows"]:
                                    buckets[vp_class(x, total_budget)].append(x)
                                folded = buckets[VP_OK_HIGH] + buckets[VP_OK]

                                def left_txt(x):
                                    """主表示。満月ベースの残りなので月初でも安定する。"""
                                    if x["budget"] <= 0:
                                        return "枠なし", SUBTLE
                                    left = x["budget"] - x["actual"]
                                    return (f'残り {fyen(left)}' if left >= 0
                                            else f'枠を {fyen(-left)} 超過'), (INK if left >= 0 else ERROR)

                                def vp_card(x, tag=None, note=None, strong=False):
                                    val, col = left_txt(x)
                                    if note is None:
                                        d = x["vs_usual"]
                                        if x["usual"] <= 0:
                                            note = f'いつも ¥0 の費目に {fyen(x["actual"])}'
                                        else:
                                            pct = f"（{d / x['usual'] * 100:+.0f}%）"
                                            word = "多い" if d >= 0 else "少ない"
                                            note = (f'使った額 {fyen(x["actual"])} ／ '
                                                    f'いつもより {fyen(abs(d))} {word}{pct}')
                                    cls = {VP_WARN: "warn", VP_OUT: "out"}.get(tag, "ok")
                                    badge = f'<span class="vp-tag {cls}">{tag}</span>' if tag else ""
                                    # 件数は行から落として展開へ。何件かを見て打つ手は変わらない
                                    style = ' style="background:#FAFAFC"' if strong else ""
                                    name = f'<b>{x["sub"]}</b>' if strong else x["sub"]
                                    return (f'<div class="vp"{style}><div class="vp-head">'
                                            f'<span class="vp-name">{badge}{name}</span>'
                                            f'<span class="vp-amt" style="color:{col}">{val}</span></div>'
                                            f'<div class="vp-sub">{note}</div></div>')

                                cards = [vp_card(x, VP_OUT) for x in buckets[VP_OUT]]
                                cards += [vp_card(x, VP_WARN) for x in buckets[VP_WARN]]
                                if folded:
                                    # 畳んだ行も枠と実額を持つので、合計の整合はくずれない
                                    ft = vp_total(folded, f"{VP_OK}の{len(folded)}費目")
                                    cards.append(vp_card(ft, VP_OK))
                                ut = prog["unused_total"]
                                if ut["budget"] > 0 or ut["usual"] > 0:
                                    ut = dict(ut, sub=f"まだ使っていない費目（{len(prog['unused'])}件）",
                                              count=0)
                                    cards.append(vp_card(ut, note=f'いつも {fyen(ut["usual"])} 使う枠'))
                                cards.append(vp_card(prog["total"], strong=True,
                                                     note=f'使った額 {fyen(prog["total"]["actual"])} ／ '
                                                          f'いつも {fyen(prog["total"]["usual"])}'))
                                st.markdown(f'<div class="vprog">{"".join(cards)}</div>',
                                            unsafe_allow_html=True)
                                st.caption(
                                    "※ 枠は過去の使い方から割り振った目安で、月ごとの振れが大きいものです。"
                                    "「いつも」は費目ごとの中央値を足したものなので、合計行は全体の"
                                    "中央値とは少し違います。")

                                # 消した数字は捨てずにここへ。判定の根拠をたどれるようにする
                                with st.expander(f"▼ 全費目の詳しい数字（{len(prog['rows'])}件）"):
                                    order = {VP_OUT: 0, VP_WARN: 1, VP_OK_HIGH: 2, VP_OK: 3}
                                    detail = sorted(
                                        ((vp_class(x, total_budget), x) for x in prog["rows"]),
                                        key=lambda t: (order[t[0]], -t[1]["vs_usual"]))
                                    html_table(pd.DataFrame([
                                        {"判定": ("いつも並み〜下回る" if k == VP_OK else k),
                                         "費目": x["sub"], "件数": f"{x['count']}件",
                                         "実額": fyen(x["actual"]),
                                         "いつも": fyen(x["usual"]),
                                         "いつも比": ("+" if x["vs_usual"] >= 0 else "−")
                                                     + fyen(abs(x["vs_usual"])),
                                         "月間の枠": fyen(x["budget"]) if x["budget"] > 0 else "—",
                                         "残り": (fyen(x["budget"] - x["actual"])
                                                  if x["budget"] > 0 else "—"),
                                         f"{asof_day}日ぶんの枠": (fyen(x["paced"])
                                                                  if x["budget"] > 0 else "—"),
                                         "枠比": (f"{x['ratio']*100:.0f}%" if x["ratio"] is not None
                                                  else "—")}
                                        for k, x in detail]))

                                if prog["unused"]:
                                    # 「いつもならもう出ているのに今月まだ¥0」の費目だけを出す。
                                    # いつも¥0 の費目は出ていなくて当たり前なので並べる意味がない
                                    due = [x for x in prog["unused"] if x["usual"] > 0]
                                    if due:
                                        with st.expander(
                                                f"▼ いつもならもう出ている費目で、今月まだ使っていないもの"
                                                f"（{len(due)}件）"):
                                            st.caption(
                                                f"「いつも」は過去{prog['months']}ヶ月の"
                                                f"{asof_day}日までの累積の中央値。この額は"
                                                "これから出てくる可能性があります。")
                                            html_table(pd.DataFrame([
                                                {"費目": x["sub"], "いつも": fyen(x["usual"])}
                                                for x in due]))
                            else:
                                st.caption("この期間の変動費はまだありません。")

                            with st.expander("▼ この計算の前提"):
                                st.markdown(
                                    f"""
                                    - **as-of 日**: データの最終日から **{CARD_LAG_DAYS}日** 手前。
                                      カードの計上が遅れるぶん直近数日は歯抜けになるため。
                                      分子（実績）も分母（経過日数）もこの日でそろえています。
                                      値は実測で決めています（2日前で98.6%が出そろい、
                                      3日目・4日目に増える精度はゼロでした）
                                    - **速報**: 最終取引日までを未確定として併記しています。
                                      **as-of を進めても1日あたりが下がるとは限りません**
                                      （分子と分母が同時に動くため）。速報はまだ増えるので
                                      低めに出ていると考えてください
                                    - **上限**: 経常収入の見込み × {(1-target)*100:.0f}%。
                                      賞与など出現{FREQ_LO*100:.0f}%未満の不定期収入は含めません
                                    - **収入・固定費の見込み**: 基準線（{'・'.join(month_label(m) for m in hist_months)}）
                                      での中項目ごとの出現頻度で分岐
                                      （{FREQ_HI*100:.0f}%以上は出現月の中央値、{FREQ_LO*100:.0f}〜{FREQ_HI*100:.0f}%は0の月込みの平均、
                                      {FREQ_LO*100:.0f}%未満は補完しない）
                                    - **基準線の起点**: {month_label(bl_start) + ' 以降' if bl_start else '指定なし（直前' + str(BASELINE_WINDOW) + 'ヶ月）'}。
                                      住み替えなどで生活水準が変わると、前の家の家賃・管理費が混ざって
                                      固定費の見込みが過小になるため。ローカル版の `data/settings.json` で変更します
                                    - **相手と分け合う費用**: 清算タブと同じルールで按分し、
                                      自分の負担分だけを計上しています。相手からの清算入金は収入に含めません
                                    - **サイドバーのトグルは効きません**。ペース計算は
                                      経常ベース・割り勘相殺OFF で固定です
                                    - 目標貯蓄率は ローカル版の `data/settings.json` で変更します（クラウドは閲覧専用）
                                    """
                                )

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
                        # スマホは1本あたりが細く、値ラベルを載せても潰れるだけなので出さない
                        fig.add_bar(x=trend["label"], y=trend[name], name=name, marker_color=col,
                                    hovertemplate=HOVER_YEN,
                                    text=None if IS_MOBILE else [man_label(v) for v in trend[name]],
                                    textposition="outside", textangle=-90 if many else 0,
                                    textfont=dict(size=9, color=SUBTLE), cliponaxis=False)
                    fig.add_scatter(x=trend["label"], y=trend["収支"], name="収支",
                                    mode="lines+markers", line=dict(color=INK, width=2),
                                    hovertemplate=HOVER_YEN)
                    peak = max(trend["収入"].max(), trend["支出"].max()) if len(trend) else 0
                    if peak > 0:
                        fig.update_yaxes(range=[min(0, trend["収支"].min() * 1.1), peak * 1.22])
                    st.plotly_chart(base_layout(fig), width="stretch", config=PLOTLY_CONFIG)
                    if mode != "年" and len(chart_months) < len(all_months):
                        st.caption(f"グラフは直近{len(chart_months)}ヶ月を表示しています"
                                   "（スマホ表示・上のKPIと下の表は選んだ期間のままです）。")

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
                                  .groupby("month")["amount"].sum()).reindex(chart_months).fillna(0)
                        many = len(series) > 8
                        fig = go.Figure(go.Bar(
                            x=[m[2:].replace("-", "/") for m in series.index], y=series.values,
                            marker_color=cat_color(sel_cat), hovertemplate=HOVER_YEN, name=sel_cat,
                            text=None if IS_MOBILE else [man_label(v) for v in series.values],
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
                    fixed_mask = is_fixed(exp, f_cats, f_subs)
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
                    pv.loc[is_fixed(pv, f_cats, f_subs), "kind"] = "固定費"
                    pivot = (-pv.pivot_table(index="month", columns="kind", values="amount",
                                             aggfunc="sum")).reindex(chart_months).fillna(0)
                    fig = go.Figure()
                    many = len(pivot) > 8
                    for k_name, color, tcolor in [("固定費", GREEN_900, "#FFFFFF"),
                                                  ("変動費", GRAY_200, INK)]:
                        if k_name in pivot.columns:
                            fig.add_bar(x=[m[2:].replace("-", "/") for m in pivot.index],
                                        y=pivot[k_name], name=k_name, marker_color=color,
                                        hovertemplate=HOVER_YEN,
                                        text=None if IS_MOBILE else [man_label(v) for v in pivot[k_name]],
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
