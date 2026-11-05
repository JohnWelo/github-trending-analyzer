#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub 热门项目分析 — 本地 Web 服务（Flask）"""

import csv
import io
import os
import sys

from flask import Flask, jsonify, render_template, request, Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_AS_ASCII"] = False

LAST = {"result": None}


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
        "limit": request.args.get("limit", 20),
        "min_stars": request.args.get("min_stars", 100),
        "source": request.args.get("source", "auto"),
        "translate": request.args.get("translate", "1") != "0",
        "token": request.headers.get("X-GitHub-Token", ""),
    }
    return _do_analyze(p)


def _do_analyze(p: dict):
    period = p.get("period", "daily")
    if period not in core.PERIOD_DAYS:
        period = "daily"
    try:
        limit = max(3, min(50, int(p.get("limit", 20))))
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
        )
    except Exception as e:  # 网络/限流等异常统一回传，不静默失败
        return jsonify({"error": f"抓取失败：{type(e).__name__} — {e}"}), 502

    if "error" in result:
        return jsonify(result), 502

    LAST["result"] = result
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
