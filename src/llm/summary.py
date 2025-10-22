# src/llm/summary.py
from __future__ import annotations
import os, re, json, traceback
from datetime import datetime, date
from typing import Optional, Dict, Any, List
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

# ---------------------------------------------------------------------
# Environment & defaults
# ---------------------------------------------------------------------
load_dotenv()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY", "") or "").strip().strip('"').strip("'")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-4o-mini")

# ---------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------
def _fmt_date(d: Any) -> str:
    if isinstance(d, (datetime, date)):
        return d.isoformat()
    return str(d)

def _basic_stats(df: pd.DataFrame) -> Dict[str, Any]:
    out = {"n_tweets": int(len(df))}
    if "sentiment_label" in df.columns:
        counts = df["sentiment_label"].value_counts(dropna=False).to_dict()
        out["sentiment_breakdown"] = {str(k): int(v) for k, v in counts.items()}
    if "aspect" in df.columns:
        top_aspects = df["aspect"].value_counts().head(10).index.tolist()
        out["top_aspects"] = [str(a) for a in top_aspects]
    if "topic_keywords" in df.columns:
        top_kw = (
            df["topic_keywords"]
              .fillna("")
              .replace("", pd.NA)
              .dropna()
              .value_counts()
              .head(10)
              .index
              .tolist()
        )
        out["top_topic_keywords"] = top_kw
    return out

def build_prompt(stats: Dict[str, Any], examples: List[str], start: str, end: str, keyword: Optional[str]) -> str:
    # Keep the prompt compact but structured
    return f"""
You are an analytics copilot generating an **executive brief** of Twitter/X chatter about Walmart.

TIME WINDOW: {start} → {end}
KEYWORD FILTER: {keyword or "None"}

BASIC STATS (from analytics pipeline):
{stats}

SAMPLE TWEETS (cleaned, representative subset):
- """ + "\n- ".join(examples) + """

Write:
1) 5–8 bullet executive brief (impactful, business-ready).
2) A short paragraph on key drivers and pain points.
3) 4–6 concise theme labels (Title Case).
4) A strict JSON object with keys: executive_bullets (list[str]), themes (list[str]), risks (list[str]), opportunities (list[str]).

Rules:
- Be specific but avoid hallucinating numbers not present in data.
- Use neutral tone; no fluff.
- If the data is thin, say so explicitly.
"""

# ---------------------------------------------------------------------
# Structured “executive brief” (Responses API) — your original
# ---------------------------------------------------------------------
def summarize_tweets(
    df: pd.DataFrame,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    sample_size: int = 50,
) -> Dict[str, Any]:
    """Return a dict with 'executive_text' and 'structured' (parsed JSON-like) fields.
    Expects df with a text column (clean_tweet/text_used/text/fulltext) and a date column
    named 'created_at' or 'date' (api/main.py already renames date_only -> date).
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set. Put it in your .env or environment.")

    # Pick date col
    date_col = None
    for c in ["created_at", "date", "timestamp"]:
        if c in df.columns:
            date_col = c
            break
    if date_col is None:
        raise KeyError("No date-like column. Expected one of: created_at, date, timestamp")

    # Pick a robust text column
    text_col = (
        "clean_tweet" if "clean_tweet" in df.columns else
        ("text_used" if "text_used" in df.columns else
         ("text" if "text" in df.columns else
          ("fulltext" if "fulltext" in df.columns else None)))
    )
    if text_col is None:
        # fallback: first object-like column
        text_col = df.select_dtypes(include=["object"]).columns.tolist()[0]

    dfx = df.copy()
    dfx[date_col] = pd.to_datetime(dfx[date_col], errors="coerce")
    if start_date:
        dfx = dfx[dfx[date_col] >= pd.to_datetime(start_date)]
    if end_date:
        dfx = dfx[dfx[date_col] <= pd.to_datetime(end_date)]

    # Safe keyword filter on the chosen text column
    if keyword:
        mask = dfx[text_col].astype(str).str.contains(keyword, case=False, na=False)
        dfx = dfx[mask]

    if len(dfx) == 0:
        return {
            "executive_text": f"No tweets found for {start_date} to {end_date} keyword={keyword}",
            "structured": {"executive_bullets": [], "themes": [], "risks": [], "opportunities": []},
            "stats": {"n_tweets": 0},
        }

    # lightweight stats & examples
    stats = _basic_stats(dfx)
    samples = (
        dfx[text_col]
        .dropna()
        .astype(str)
        .sample(min(sample_size, len(dfx)), random_state=42)
        .tolist()
    )

    prompt = build_prompt(
        stats=stats,
        examples=samples,
        start=_fmt_date(start_date),
        end=_fmt_date(end_date),
        keyword=keyword,
    )

    client = OpenAI(api_key=OPENAI_API_KEY)

    # Responses API
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": "You are a precise analytics summarizer that returns clean, business-ready insights."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_output_tokens=900,
    )

    # Unified text accessor
    full_text = getattr(response, "output_text", None) or str(response)

    # Best-effort JSON extraction
    json_obj = {"executive_bullets": [], "themes": [], "risks": [], "opportunities": []}
    try:
        code_blocks = re.findall(r"```json\s*(\{[\s\S]*?\})\s*```", full_text)
        if code_blocks:
            json_obj = json.loads(code_blocks[-1])
        else:
            first_brace = full_text.find("{")
            last_brace = full_text.rfind("}")
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                json_obj = json.loads(full_text[first_brace:last_brace+1])
    except Exception:
        pass

    return {"executive_text": full_text, "structured": json_obj, "stats": stats}


# ---------------------------------------------------------------------
# NEW: 1–2 paragraph Executive Summary over a date window (LLM + fallback)
#      Works directly with the sentiment dataframe already loaded in api/main.py
# ---------------------------------------------------------------------

# Reuse a sturdy stopword set for keywording
_STOP = {
    "walmart","rt","amp","https","http","co","www","com","org","net",
    "user","users","you","your","yours","u","ur","me","we","us","they","them",
    "im","ive","dont","didnt","cant","couldnt","wont","wouldnt","shouldnt",
    "like","just","get","got","one","two","three","also","still","even",
    "going","go","gotta","gonna","really","please","thanks","thank","help",
    "hey","hi","hello","ok","okay","any","every","everyone","someone","anyone",
    "today","yesterday","tomorrow","now","time","back","make","made","see",
    "store","stores","shop","shopping","customer","customers","people",
    "good","bad","great","best","worst","better","worse",
    "buy","bought","purchase","purchased","sale","sales",
    "app","apps","site","website","httpst","httpsco","tco"
}
_URL_MENTION_HASHTAG = re.compile(r"https?://\S+|[@#]\w+")
_NON_ALNUM = re.compile(r"[^a-z0-9\s']")
_MULTI_SP = re.compile(r"\s+")

_TEXT_CANDIDATES = ["text_used", "clean_tweet", "text", "fulltext"]

def _pick_text_col(df: pd.DataFrame) -> str:
    for c in _TEXT_CANDIDATES:
        if c in df.columns:
            return c
    raise KeyError(f"No text column among {_TEXT_CANDIDATES}")

def _normalize(text: str) -> str:
    t = text.lower()
    t = _URL_MENTION_HASHTAG.sub(" ", t)
    t = _NON_ALNUM.sub(" ", t)
    t = _MULTI_SP.sub(" ", t).strip()
    return t

def _top_keywords(texts: List[str], top_k: int = 12) -> List[str]:
    from collections import Counter
    cnt = Counter()
    for t in texts:
        for tok in _normalize(str(t)).split():
            if len(tok) < 3 or tok in _STOP or tok.isdigit():
                continue
            cnt[tok] += 1
    return [w for w,_ in cnt.most_common(top_k)]

def _sentiment_summary_window(sub: pd.DataFrame) -> dict:
    counts = sub["sentiment_label"].value_counts().to_dict() if "sentiment_label" in sub.columns else {}
    total = int(sum(counts.values()) or 1)
    pct = {k: round(v / total * 100, 2) for k, v in counts.items()}
    for k in ["positive","neutral","negative"]:
        counts.setdefault(k, 0); pct.setdefault(k, 0.0)
    return {"total": total, "counts": counts, "percent": pct}

def _aspect_top(adf: Optional[pd.DataFrame], start, end, k: int = 6) -> List[Dict]:
    if adf is None or adf.empty:
        return []
    mask = (adf["date_only"] >= start) & (adf["date_only"] <= end)
    sub = adf.loc[mask]
    if sub.empty or "aspect_dominant" not in sub.columns:
        return []
    vc = sub["aspect_dominant"].value_counts().head(k)
    out = []
    for name, cnt in vc.items():
        row = {"aspect": name, "count": int(cnt)}
        if "sentiment_label" in sub.columns:
            ss = sub[sub["aspect_dominant"]==name]["sentiment_label"].value_counts().to_dict()
            row["pos"] = int(ss.get("positive", 0))
            row["neg"] = int(ss.get("negative", 0))
            row["neu"] = int(ss.get("neutral", 0))
        out.append(row)
    return out

def build_executive_summary(
    df_senti: pd.DataFrame,
    df_aspects: Optional[pd.DataFrame],
    start: str,
    end: str,
    openai_api_key: str = "",
    sample_per_sentiment: int = 250,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Returns:
      {
        "start": "...", "end": "...",
        "used_llm": bool,
        "summary": "<1–2 paragraph exec summary>",
        "stats": {"sentiment": {...}, "top_aspects": [...], "keywords": [...]}
      }
    Assumes df_senti already has a 'date_only' column (as created in api/main.py).
    """
    # 1) Filter window
    mask = (df_senti["date_only"] >= pd.to_datetime(start).date()) & \
           (df_senti["date_only"] <= pd.to_datetime(end).date())
    sub = df_senti.loc[mask].copy()
    if sub.empty:
        return {
            "start": start, "end": end, "used_llm": False,
            "summary": "No tweets in the selected period.",
            "stats": {"sentiment": {"total":0,"counts":{"positive":0,"neutral":0,"negative":0},"percent":{"positive":0,"neutral":0,"negative":0}},
                      "top_aspects": [], "keywords": []}
        }

    text_col = _pick_text_col(sub)
    senti_stats = _sentiment_summary_window(sub)
    aspects_top = _aspect_top(df_aspects, pd.to_datetime(start).date(), pd.to_datetime(end).date(), k=6)

    # 2) Balanced sampling for prompt size
    samples: List[str] = []
    if "sentiment_label" in sub.columns:
        for label in ["negative","positive","neutral"]:
            part = sub[sub["sentiment_label"]==label][text_col].astype(str).head(sample_per_sentiment).tolist()
            samples.extend(part)
    else:
        samples = sub[text_col].astype(str).head(3*sample_per_sentiment).tolist()

    # 3) Keywords from samples
    kw = _top_keywords(samples, top_k=12)

    # 4) LLM summary (Responses API), else fallback
    used_llm = False
    summary_text = ""

    effective_key = (openai_api_key or OPENAI_API_KEY).strip()
    if effective_key:
        try:
            client = OpenAI(api_key=effective_key)
            # trim examples to fit token limits
            examples = samples[:120]
            stats_block = {"sentiment": senti_stats, "top_aspects": aspects_top, "keywords": kw[:10]}

            system_msg = "You are a retail insights analyst. Produce a concise, business-ready executive summary."
            user_msg = (
                f"Date window: {start} to {end}\n"
                f"Grounding (JSON): {json.dumps(stats_block, ensure_ascii=False)}\n"
                f"Examples ({len(examples)} tweets):\n" + "\n".join(f"- {t}" for t in examples) + "\n\n"
                "Write 1–2 short paragraphs summarizing the period, citing the main issues/opportunities, "
                "and finish with 2–3 actionable focus areas. Do not invent numeric metrics."
            )

            resp = client.responses.create(
                model=model,
                input=[
                    {"role":"system","content":system_msg},
                    {"role":"user","content":user_msg}
                ],
                temperature=0.3,
                max_output_tokens=400,
            )
            summary_text = (getattr(resp, "output_text", None) or str(resp)).strip()
            used_llm = bool(summary_text)
        except Exception:
            print("[Executive summary OpenAI error]\n", traceback.format_exc())

    if not summary_text:
        pos = senti_stats["counts"]["positive"]; neg = senti_stats["counts"]["negative"]; neu = senti_stats["counts"]["neutral"]
        top_aspect_names = ", ".join(a["aspect"] for a in aspects_top[:4]) if aspects_top else "various areas"
        key_phrases = ", ".join(kw[:6]) if kw else "common customer concerns"
        summary_text = (
            f"Between {start} and {end}, customers discussed {top_aspect_names}. "
            f"Sentiment shows {neg} negative, {neu} neutral, and {pos} positive posts. "
            f"Prominent phrases included {key_phrases}. "
            f"Focus next on resolving key negative drivers and reinforcing positive experiences."
        )

    return {
        "start": start, "end": end, "used_llm": used_llm,
        "summary": summary_text,
        "stats": {"sentiment": senti_stats, "top_aspects": aspects_top, "keywords": kw[:12]}
    }
