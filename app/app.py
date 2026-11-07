#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub 热门项目分析 — 本地 Web 服务（Flask）"""

import csv
import io
import json
import os
import sys

from flask import Flask, jsonify, render_template, request, Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_AS_ASCII"] = False

LAST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "last-result.json")


def _load_last():
    """启动时恢复上一次分析结果；文件损坏或版本不匹配时安全忽略。"""
    try:
        with open(LAST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except Exception:
        pass
    return None


def _save_last(result):
    try:
        os.makedirs(os.path.dirname(LAST_FILE), exist_ok=True)
        tmp = LAST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        os.replace(tmp, LAST_FILE)
    except Exception:
        pass


LAST = {"result": _load_last()}


@app.get("/api/last")
def api_last():
    """前端启动时拉取上次结果，重开应用即可看到上一次分析。"""
    r = LAST["result"]
    return jsonify(r) if r else jsonify({"empty": True})


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/analyze")
def api_analyze():
    p = request.get_json(force=True, silent=True) or {}
    return _do_analyze(p)


@app.get("/api/analyze")
def api_analyze_get():
    """GET 备用通道：部分环境会拦截本地 POST，前端失败后自动回落。"""
    p = {
        "period": request.args.get("period", "daily"),
        "language": request.args.get("language", ""),
        "limit": request.args.get("limit", 10),
        "min_stars": request.args.get("min_stars", 100),
        "source": request.args.get("source", "auto"),
        "translate": request.args.get("translate", "1") != "0",
        "include_official": request.args.get("include_official", "1") != "0",
        "token": request.headers.get("X-GitHub-Token", ""),
    }
    return _do_analyze(p)


def _do_analyze(p: dict):
    period = p.get("period", "daily")
    if period not in core.PERIOD_DAYS:
        period = "daily"
    try:
        limit = max(3, min(50, int(p.get("limit", 10))))
        min_stars = max(0, min(100000, int(p.get("min_stars", 100))))
    except (TypeError, ValueError):
        return jsonify({"error": "数量或最低 Star 参数不是有效数字"}), 400

    try:
        result = core.analyze(
            period=period,
            language=(p.get("language") or "").strip(),
            limit=limit,
            min_stars=min_stars,
            token=(p.get("token") or "").strip(),
            source=p.get("source", "auto"),
            translate=bool(p.get("translate", True)),
            include_official=bool(p.get("include_official", True)),
        )
    except Exception as e:  # 网络/限流等异常统一回传，不静默失败
        return jsonify({"error": f"抓取失败：{type(e).__name__} — {e}"}), 502

    if "error" in result:
        return jsonify(result), 502

    LAST["result"] = result
    _save_last(result)
    return jsonify(result)


@app.get("/api/export/html")
def export_html():
    r = LAST["result"]
    if not r:
        return "暂无数据，请先运行一次分析", 400
    html = render_template("report.html", r=r)
    name = f"github-trending-{r['generated_at'][:10]}-{r['period']}.html"
    return Response(html, mimetype="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/export/csv")
def export_csv():
    r = LAST["result"]
    if not r:
        return "暂无数据，请先运行一次分析", 400
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["排名", "仓库", "分类", "语言", "简介（中文）", "简介（英文）", "总Star", "增速(star/天)",
                "总分", "评级", "受欢迎度25", "维护活跃度25", "实用可应用性30", "技术栈质量20",
                "最近提交", "点评", "地址"])
    for i, it in enumerate(r["items"], 1):
        s = it["scores"]
        w.writerow([i, it["full_name"], it["category"], it["language"],
                    it.get("description_zh") or "", it["description"],
                    it["stars"], it["velocity"], it["total"], it["grade"],
                    s["popularity"], s["maintenance"], s["practicality"], s["quality"],
                    it["pushed_at"], it["comment"], it["url"]])
    name = f"github-trending-{r['generated_at'][:10]}-{r['period']}.csv"
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/official/refresh")
def official_refresh():
    """手动刷新官方趋势页：绕过熔断，不重跑评分，失败时回落到缓存快照。"""
    period = request.args.get("period", "daily")
    if period not in core.PERIOD_DAYS:
        period = "daily"
    try:
        res = core.refresh_official(
            period=period,
            language=request.args.get("language", "").strip(),
            limit=max(3, min(50, int(request.args.get("limit", 10)))),
            token=request.headers.get("X-GitHub-Token", ""),
        )
    except Exception as e:
        return jsonify({"ok": False, "message": f"刷新失败：{type(e).__name__} — {e}",
                        "items": [], "stale": False}), 502
    prior = (LAST["result"] or {}).get("items", [])
    token = request.headers.get("X-GitHub-Token", "")
    core.meta_coverage(reset=True)
    res["items"] = core.attach_scores(res["items"], prior, period, token)
    res["meta_coverage"] = core.meta_coverage_from_items(res["items"])
    if LAST["result"] is None:      # 首次就单独刷新：也留一份可恢复的结果
        LAST["result"] = {
            "generated_at": res["fetched_at"], "period": period,
            "period_label": core.PERIOD_LABEL[period],
            "language": res["language"], "source": "api",
            "source_label": "官方趋势页（独立刷新）", "items": [],
            "readme_coverage": 100, "board_meta_coverage": 100,
            "translate_status": "ok",
        }
    LAST["result"]["official_items"] = res["items"]
    LAST["result"]["official_fetched_at"] = res["fetched_at"]
    LAST["result"]["official_stale"] = res["stale"]
    LAST["result"]["official_url"] = res["url"]
    LAST["result"]["official_period"] = res["period"]
    LAST["result"]["official_language"] = res["language"]
    LAST["result"]["meta_coverage"] = res["meta_coverage"]
    _save_last(LAST["result"])
    return jsonify(res)


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


def run(port: int = 0, open_window: bool = False):
    from werkzeug.serving import make_server
    import threading

    srv = make_server("127.0.0.1", port, app, threaded=True)
    actual = srv.server_port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    if open_window:
        import webview
        webview.create_window("GitHub 热门项目分析", f"http://127.0.0.1:{actual}",
                              width=1360, height=920, min_size=(1000, 680))
        webview.start()
        return

    print(f"服务已启动：http://127.0.0.1:{actual}")
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--window", action="store_true", help="用原生窗口打开（pywebview）")
    a = ap.parse_args()
    port = a.port if a.port is not None else (0 if a.window else int(os.environ.get("PORT", 8760)))
    run(port, a.window)
