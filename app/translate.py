#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简介中英翻译（MyMemory 公共接口）

为什么用它：Google 翻译在部分网络环境不可达、有道需要鉴权，MyMemory 公共接口可直连。
设计要点：
  - 本地 JSON 缓存，相同文本不重复请求（省额度）
  - 并发 6 路，20 条约 3-6 秒
  - 额度耗尽 / 网络失败不阻断分析，返回原文并标记状态
"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

API = "https://api.mymemory.translated.net/get"
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "translations.json")
CACHE_MAX = 5000

_lock = threading.Lock()
_cache: dict | None = None
_quota_exhausted = threading.Event()

CJK = re.compile(r"[\u4e00-\u9fff]")

# 机器翻译把专有名词音译了，做一层术语回正
FIXES = {
    "克劳德": "Claude",
    "开放人工智能": "OpenAI",
    "吉特哈布": "GitHub",
    "深度求索": "DeepSeek",
    "视觉工作室代码": "VS Code",
}


def _fix(zh: str) -> str:
    for bad, good in FIXES.items():
        zh = zh.replace(bad, good)
    return zh


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    return _cache


def _save() -> None:
    d = _load()
    if len(d) > CACHE_MAX:                     # 简单裁剪：保留最近写入的后半部分
        keys = list(d.keys())[-CACHE_MAX:]
        d = {k: d[k] for k in keys}
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=0)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


def _translate_one(text: str, sess: requests.Session) -> tuple[str, str]:
    """返回 (译文, 状态)。状态：ok / cached / cjk / quota / error"""
    if not text or not text.strip():
        return "", "ok"
    if CJK.search(text):
        return text, "cjk"
    if _quota_exhausted.is_set():
        return "", "quota"

    cache = _load()
    if text in cache:
        return _fix(cache[text]), "cached"

    try:
        r = sess.get(API, params={"q": text[:480], "langpair": "en|zh-CN"}, timeout=6)
        d = r.json()
        zh = (d.get("responseData") or {}).get("translatedText") or ""
        status = d.get("responseStatus")
        if "MYMEMORY WARNING" in zh.upper():
            _quota_exhausted.set()
            return "", "quota"
        if isinstance(status, int) and status >= 400:
            return "", "error"
        zh = zh.strip()
        if not zh or CJK.search(zh) is None:   # 没译出中文，视为失败
            return "", "error"
        zh = _fix(zh)
        with _lock:
            cache[text] = zh
        return zh, "ok"
    except Exception:
        return "", "error"


def translate_many(texts: list[str], max_workers: int = 6) -> tuple[dict, str]:
    """批量翻译。返回 ({原文: 中文}, 总体状态)，总体状态：ok / partial / quota / unavailable"""
    uniq = list(dict.fromkeys([t for t in texts if t]))
    if not uniq:
        return {}, "ok"

    sess = requests.Session()
    sess.headers.update({"User-Agent": "github-trending-gui/1.0"})

    results: dict[str, str] = {}
    statuses: list[str] = []

    from concurrent.futures import wait as _wait
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futs = {pool.submit(_translate_one, t, sess): t for t in uniq}
        done, pending = _wait(futs, timeout=15)      # 总时限：超时的不再等
        for f in done:
            text = futs[f]
            try:
                zh, st = f.result(timeout=0)
            except Exception:
                zh, st = "", "error"
            statuses.append(st)
            if zh:
                results[text] = zh
        if pending:
            statuses.extend(["timeout"] * len(pending))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    _save()

    if not statuses:
        overall = "ok"
    elif _quota_exhausted.is_set():
        overall = "quota"
    elif all(s in ("ok", "cached", "cjk") for s in statuses):
        overall = "ok"
    elif any(s in ("ok", "cached", "cjk") for s in statuses):
        overall = "partial"
    else:
        overall = "unavailable"
    return results, overall
