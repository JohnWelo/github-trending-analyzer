#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 热门项目分析 — 数据抓取与规则打分核心模块

数据源策略（自动降级，结果页会标注实际来源）：
  1. official —— 抓取 https://github.com/trending 页面，拿到官方「周期新增 Star」
  2. api      —— 走 api.github.com/search/repositories 推算 Star 增速（新项目高增长榜）

打分口径与 skill `references/scoring.md` 保持一致：
  受欢迎度 25 / 维护活跃度 25 / 实用可应用性 30 / 技术栈质量 20
规则打分是对 rubric 的可解释近似，不是 LLM 判断；每条得分都带依据说明。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
PERIOD_LABEL = {"daily": "今日", "weekly": "本周", "monthly": "本月"}

MAINSTREAM_LANGS = {
    "Python", "TypeScript", "JavaScript", "Go", "Rust", "Java", "C++", "C",
    "C#", "Rust", "Kotlin", "Swift", "Ruby", "PHP", "Shell", "Jupyter Notebook",
    "Vue", "HTML", "Rust", "Dart", "Scala", "Zig", "Lua", "Elixir",
}
PERMISSIVE_LICENSES = {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC", "MPL-2.0", "Unlicense"}

INSTALL_HINTS = [
    (r"pip3? install", "pip 安装"),
    (r"npm (i|install)", "npm 安装"),
    (r"pnpm (add|i|install)", "pnpm 安装"),
    (r"yarn (add|install)", "yarn 安装"),
    (r"go get|go install", "go install"),
    (r"cargo (add|install)", "cargo 安装"),
    (r"brew install", "brew 安装"),
    (r"docker (run|pull|compose)", "Docker 部署"),
    (r"uv (add|pip install)", "uv 安装"),
    (r"curl .*\| ?(ba)?sh", "一键脚本"),
]

# ---------------------------------------------------------------- 分类规则
# 关键词在 topics/仓库名 中命中权重 3、描述中命中权重 1；总分 ≥3 才归类，否则「其他」。
# 列表顺序即同分时的优先级。
CATEGORY_RULES = [
    ("视频与音频", ["video", "audio", "ffmpeg", "subtitle", "streaming", "media player", "podcast",
                "music", "transcod", "speech", "tts", "asr", "dubbing", "voice", "animation",
                "视频", "音频", "剪辑", "字幕", "配音", "动画", "短片"]),
    ("网络爬虫与采集", ["crawler", "scraper", "scraping", "spider", "downloader", "download manager",
                  "爬虫", "采集", "下载器", "抓取"]),
    ("AI 与大模型", ["llm", "gpt", "agent", "langchain", "rag", "diffusion", "transformer", "inference",
                 "embedding", "prompt", "fine-tun", "openai", "deepseek", "chatbot", "mcp", "copilot",
                 "大模型", "智能体", "人工智能", "提示词"]),
    ("开发工具", ["cli", "sdk", "ide", "vscode", "extension", "linter", "formatter", "debugger",
              "compiler", "devtool", "terminal", "shell", "git", "package manager", "开发工具", "编辑器", "命令行"]),
    ("数据与分析", ["analytics", "dashboard", "visualization", "chart", "sql", "etl", "dataframe",
               "notebook", "data pipeline", "database", "数据", "可视化", "报表", "统计"]),
    ("图像与设计", ["image", "photo", "design", "figma", "icon", "svg", "drawing", "graphics",
               "texture", "3d", "blender", "render", "pixel art", "art", "paint", "avatar",
               "fashion", "try-on", "outfit", "图像", "设计", "绘图", "艺术", "照片", "穿搭", "修图",
               "试穿", "女装", "图集", "素材"]),
    ("前端与 UI", ["react", "vue", "svelte", "next.js", "css", "tailwind", "component library",
               "design system", "frontend", "ui library", "ui", "界面", "前端", "组件库"]),
    ("后端与基础设施", ["server", "gateway", "kubernetes", "docker", "container", "proxy", "nginx",
                 "microservice", "backend", "distributed", "message queue", "api framework",
                 "后端", "网关", "微服务", "容器"]),
    ("安全与隐私", ["security", "pentest", "exploit", "vulnerability", "cve", "malware", "osint",
              "encryption", "privacy", "firewall", "安全", "渗透", "隐私", "漏洞", "权限"]),
    ("自动化与效率", ["automation", "workflow", "bot", "scheduler", "rpa", "productivity", "note",
               "todo", "markdown", "clipboard", "自动化", "效率", "笔记"]),
    ("系统与运维", ["kernel", "driver", "linux", "macos", "file manager", "monitor", "log analyzer",
              "embedded", "firmware", "systemd", "运维", "监控", "系统工具"]),
    ("游戏与图形", ["game", "unity", "godot", "engine", "shader", "opengl", "vulkan", "pixel art",
              "emulator", "simulator", "游戏", "引擎", "模拟器", "仿真器"]),
    ("移动与跨端", ["android", "ios", "flutter", "react native", "swiftui", "kotlin", "mobile app",
              "移动端", "跨平台", "安卓"]),
    ("区块链与 Web3", ["blockchain", "crypto", "wallet", "ethereum", "solana", "web3", "nft", "defi",
                  "区块链", "钱包"]),
    ("学习资源", ["awesome", "tutorial", "course", "roadmap", "interview", "cheatsheet", "learning",
              "handbook", "guide", "教程", "学习", "面试", "手册", "指南"]),
]


def _kw_hit(pattern: str, text: str) -> bool:
    if not text:
        return False
    if re.search(r"[a-z0-9]", pattern):        # 英文关键词按词边界匹配，避免 app/os/bi 误伤
        return re.search(rf"(?<![a-z0-9]){re.escape(pattern)}(?![a-z0-9])", text) is not None
    return pattern in text


def classify(repo: "Repo") -> str:
    """按关键词给项目贴一个中文分类。命中依据来自仓库名/英文描述/中文译名/topics。"""
    topics = " ".join(repo.topics).lower()
    name = repo.full_name.split("/")[-1].lower().replace("-", " ").replace("_", " ")
    desc = ((repo.description or "") + " " + (repo.description_zh or "")).lower()

    best, best_score = "其他", 0
    for label, kws in CATEGORY_RULES:
        score = 0
        for kw in kws:
            if _kw_hit(kw, topics):
                score += 3
            if _kw_hit(kw, name):
                score += 3
            elif _kw_hit(kw, desc):
                score += 1
        if score > best_score:
            best, best_score = label, score
    return best if best_score >= 2 else "其他"


def _session(token: str = "") -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/vnd.github+json"})
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def _parse_dt(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        try:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None


def _int(text: str) -> int:
    try:
        return int(re.sub(r"[^\d]", "", text))
    except Exception:
        return 0


# ---------------------------------------------------------------- 数据抓取

@dataclass
class Repo:
    full_name: str
    url: str
    description: str = ""
    language: str = ""
    stars: int = 0
    forks: int = 0
    stars_period: int = 0            # 周期新增 star（official 源才有）
    created_at: Optional[dt.datetime] = None
    pushed_at: Optional[dt.datetime] = None
    open_issues: int = 0
    license: str = ""
    topics: list = field(default_factory=list)
    homepage: str = ""
    archived: bool = False
    size_kb: int = 0
    default_branch: str = "main"
    velocity: float = 0.0            # star / 天
    readme: str = ""
    readme_ok: bool = False
    data_source: str = "api"
    category: str = "其他"           # 规则分类
    description_zh: str = ""          # 机器翻译后的中文简介

    # 评分结果
    scores: dict = field(default_factory=dict)
    reasons: dict = field(default_factory=dict)
    total: int = 0
    grade: str = "C"
    verdict: str = ""
    comment: str = ""

    @property
    def owner(self) -> str:
        return self.full_name.split("/")[0]


OFFICIAL_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "official")


def _official_cache_path(period: str, language: str) -> str:
    key = f"{period}_{(language or 'all').lower().replace(' ', '-')}"
    return os.path.join(OFFICIAL_CACHE_DIR, key + ".json")


def _save_official_cache(period: str, language: str, url: str, items: list) -> str:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        os.makedirs(OFFICIAL_CACHE_DIR, exist_ok=True)
        with open(_official_cache_path(period, language), "w", encoding="utf-8") as f:
            json.dump({"fetched_at": stamp, "url": url, "items": items}, f, ensure_ascii=False)
    except Exception:
        pass
    return stamp


def load_official_cache(period: str, language: str) -> dict:
    """读取该周期/语言的官方快照；没有则退回任意最近一份。"""
    import glob
    p = _official_cache_path(period, language)
    try:
        if os.path.exists(p):
            return json.load(open(p, "r", encoding="utf-8"))
    except Exception:
        pass
    files = glob.glob(os.path.join(OFFICIAL_CACHE_DIR, "*.json"))
    if files:
        try:
            latest = max(files, key=os.path.getmtime)
            return json.load(open(latest, "r", encoding="utf-8"))
        except Exception:
            pass
    return {}


_official_block_until = 0.0       # 官方页连续失败后，5 分钟内直接用缓存，不重复等代理超时


def _get_official_html(url: str, session, force: bool = False) -> str:
    """代理对 github.com 是间歇性放行：requests 一次 + curl 一次；失败后 5 分钟内不再试。
    force=True 用于「手动刷新」，跳过熔断窗口。"""
    global _official_block_until
    if not force and time.time() < _official_block_until:
        return ""
    try:
        r = session.get(url, timeout=5)
        if r.status_code == 200 and "Box-row" in r.text:
            _official_block_until = 0.0
            return r.text
    except Exception:
        pass
    try:
        import subprocess
        cp = subprocess.run(
            ["curl", "-sS", "-m", "8", "-A", UA, url],
            capture_output=True, text=True, timeout=10,
        )
        if cp.returncode == 0 and "Box-row" in cp.stdout:
            _official_block_until = 0.0
            return cp.stdout
    except Exception:
        pass
    _official_block_until = time.time() + 300
    return ""


def fetch_official_trending(session, period: str, language: str, limit: int,
                            force: bool = False) -> Optional[list]:
    """抓官方 trending 页；不可达返回 None（由调用方降级）。"""
    since = {"daily": "", "weekly": "?since=weekly", "monthly": "?since=monthly"}[period]
    path = f"/{language.lower()}" if language else ""
    url = f"https://github.com/trending{path}{since}"
    html = _get_official_html(url, session, force)
    if not html:
        return None

    rows = re.findall(r'<article class="Box-row".*?</article>', html, re.S)
    repos = []
    for row in rows[: limit * 2]:
        # GitHub 在数字前插了 <svg>，先整段剥掉再取文本，否则取到的是空串
        clean = re.sub(r"<svg.*?</svg>", "", row, flags=re.S)
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"]+)"', row, re.S)
        if not m:
            continue
        full = m.group(1).strip()
        desc_m = re.search(r'<p class="col-9[^"]*">\s*(.*?)\s*</p>', row, re.S)
        lang_m = re.search(r'itemprop="programmingLanguage">([^<]+)<', clean)
        star_m = re.search(r'href="/[^"]+/stargazers"[^>]*>\s*([\d,]+)', clean)
        fork_m = re.search(r'href="/[^"]+/forks"[^>]*>\s*([\d,]+)', clean)
        delta_m = re.search(r'([\d,]+)\s+stars?\s+(?:today|this week|this month)', clean)
        repos.append(Repo(
            full_name=full,
            url=f"https://github.com/{full}",
            description=re.sub(r"<[^>]+>", "", desc_m.group(1)).strip() if desc_m else "",
            language=lang_m.group(1).strip() if lang_m else "",
            stars=_int(star_m.group(1)) if star_m else 0,
            forks=_int(fork_m.group(1)) if fork_m else 0,
            stars_period=_int(delta_m.group(1)) if delta_m else 0,
            data_source="official",
        ))
    if repos:
        _save_official_cache(period, language, url, [{
            "full_name": r.full_name, "url": r.url, "description": r.description,
            "language": r.language, "stars": r.stars, "forks": r.forks,
            "stars_period": r.stars_period,
        } for r in repos])
    return repos or None


def refresh_official(period: str = "daily", language: str = "", limit: int = 25,
                     token: str = "", translate: bool = True) -> dict:
    """只刷新官方趋势页，不跑评分流程。供界面的「手动刷新」按钮调用。"""
    session = _session(token)
    url = (f"https://github.com/trending"
           + (f"/{language.lower()}" if language else "")
           + {"daily": "", "weekly": "?since=weekly", "monthly": "?since=monthly"}[period])

    repos = fetch_official_trending(session, period, language, limit, force=True)
    if repos:
        items = [{
            "full_name": r.full_name, "url": r.url, "description": r.description,
            "language": r.language, "stars": r.stars, "forks": r.forks,
            "stars_period": r.stars_period,
        } for r in repos]
        stamp = _save_official_cache(period, language, url, items)
        ok, stale = True, False
        message = f"已刷新，抓取到 {len(items)} 个项目"
    else:
        cached = load_official_cache(period, language)
        items = cached.get("items", [])
        stamp = cached.get("fetched_at", "")
        ok, stale = False, bool(items)
        message = (f"实时刷新失败，仍显示缓存于 {stamp} 的快照" if items
                   else "实时刷新失败，且本地没有可用快照")

    if items and translate:
        try:
            from translate import translate_many
            mapping, _ = translate_many(list(dict.fromkeys(i["description"] for i in items if i.get("description"))))
            for it in items:
                it["description_zh"] = mapping.get(it["description"], "")
        except Exception:
            pass

    return {"ok": ok, "message": message, "items": items, "fetched_at": stamp,
            "stale": stale, "url": url, "period": period,
            "language": language or "全语言"}


def search_api(session, period: str, language: str, limit: int, min_stars: int) -> list:
    """用 Search API 找「近期创建且 Star 增长快」的项目，作为趋势榜近似。"""
    days = PERIOD_DAYS[period]
    window = max(days * 4, 7)          # 候选池窗口放大，避免样本太少
    since = (dt.date.today() - dt.timedelta(days=window)).isoformat()
    q = f"created:>={since} stars:>={min_stars} fork:false"
    if language:
        q += f' language:"{language}"'
    params = {"q": q, "sort": "stars", "order": "desc", "per_page": min(100, max(limit * 3, 30))}
    r = session.get("https://api.github.com/search/repositories", params=params, timeout=20)
    r.raise_for_status()
    items = r.json().get("items", [])

    now = dt.datetime.now(dt.timezone.utc)
    repos = []
    for it in items:
        created = _parse_dt(it.get("created_at"))
        pushed = _parse_dt(it.get("pushed_at"))
        age_days = max((now - created).total_seconds() / 86400, 1) if created else 1
        repos.append(Repo(
            full_name=it["full_name"],
            url=it.get("html_url") or f"https://github.com/{it['full_name']}",
            description=(it.get("description") or "").strip(),
            language=it.get("language") or "",
            stars=it.get("stargazers_count") or 0,
            forks=it.get("forks_count") or 0,
            created_at=created,
            pushed_at=pushed,
            open_issues=it.get("open_issues_count") or 0,
            license=((it.get("license") or {}).get("spdx_id") or "").replace("NOASSERTION", ""),
            topics=it.get("topics") or [],
            homepage=it.get("homepage") or "",
            archived=bool(it.get("archived")),
            size_kb=it.get("size") or 0,
            default_branch=it.get("default_branch") or "main",
            velocity=round((it.get("stargazers_count") or 0) / age_days, 1),
            data_source="api",
        ))
    return repos


# ---------------------------------------------------------------- README 获取
README_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "readme")
README_TTL = 6 * 3600
_api_quota_hit = threading.Event()      # 本轮 API 配额耗尽后不再重试，避免逐个卡 8 秒
_raw_fails = [0]                        # raw CDN 连续失败计数
_raw_lock = threading.Lock()
_meta_stats = {"attempts": 0, "failures": 0}   # 官方条目元数据抓取成功率
_meta_lock = threading.Lock()


def meta_coverage(reset: bool = False) -> int:
    """官方条目仓库元数据的抓取成功率；配额耗尽时会明显下降。"""
    with _meta_lock:
        if reset:
            _meta_stats["attempts"] = 0
            _meta_stats["failures"] = 0
            return 100
        att, fail = _meta_stats["attempts"], _meta_stats["failures"]
    return round((att - fail) / att * 100) if att else 100


def meta_coverage_from_items(items: list) -> int:
    """按每条项目的 meta_ok 标记计算；复用旧分数时也能如实反映降级。"""
    known = [i for i in (items or []) if "meta_ok" in i]
    if not known:
        return meta_coverage()
    return round(sum(1 for i in known if i["meta_ok"]) / len(known) * 100)


def _readme_cache_path(repo: "Repo") -> str:
    return os.path.join(README_CACHE_DIR, repo.full_name.replace("/", "__") + ".txt")


def _readme_cache_get(repo: "Repo") -> str:
    p = _readme_cache_path(repo)
    try:
        if time.time() - os.path.getmtime(p) > README_TTL:
            return ""
        with open(p, "r", encoding="utf-8") as f:
            return f.read()[:20000]
    except Exception:
        return ""


def _readme_cache_put(repo: "Repo", text: str) -> None:
    try:
        os.makedirs(README_CACHE_DIR, exist_ok=True)
        with open(_readme_cache_path(repo), "w", encoding="utf-8") as f:
            f.write(text[:20000])
    except Exception:
        pass


def _fetch_readme(session, repo: "Repo") -> str:
    """raw CDN 免费但常被代理掐断；连续失败 3 次后本轮直接走 GitHub API readme。"""
    if _raw_fails[0] < 3:
        try:
            u = f"https://raw.githubusercontent.com/{repo.full_name}/{repo.default_branch}/README.md"
            r = session.get(u, timeout=3)
            if r.status_code == 200 and r.text.strip():
                return r.text[:20000]
            with _raw_lock:
                _raw_fails[0] += 1
        except Exception:
            with _raw_lock:
                _raw_fails[0] += 1

    if _api_quota_hit.is_set():
        return ""
    try:
        r = session.get(f"https://api.github.com/repos/{repo.full_name}/readme",
                        headers={"Accept": "application/vnd.github.raw"}, timeout=8)
        if r.status_code == 200 and r.text.strip():
            return r.text[:20000]
        if r.status_code in (403, 429):
            _api_quota_hit.set()
    except Exception:
        pass
    return ""


def enrich(session, repo: Repo) -> None:
    """补 README（缓存 → raw CDN → API 三级），官方源还需补 pushed_at / license 等元数据。"""
    cached = _readme_cache_get(repo)
    if cached:
        repo.readme, repo.readme_ok = cached, True
    else:
        text = _fetch_readme(session, repo)
        if text:
            repo.readme, repo.readme_ok = text, True
            _readme_cache_put(repo, text)

    if repo.data_source == "official":
        ok = False
        if not _api_quota_hit.is_set():      # 已知配额耗尽就别再逐个发无效请求
            try:
                resp = session.get(f"https://api.github.com/repos/{repo.full_name}", timeout=15)
                if resp.status_code == 200:
                    d = resp.json()
                    repo.created_at = _parse_dt(d.get("created_at"))
                    repo.pushed_at = _parse_dt(d.get("pushed_at"))
                    repo.open_issues = d.get("open_issues_count") or 0
                    repo.license = ((d.get("license") or {}).get("spdx_id") or "").replace("NOASSERTION", "")
                    repo.topics = d.get("topics") or []
                    repo.homepage = d.get("homepage") or ""
                    repo.archived = bool(d.get("archived"))
                    repo.size_kb = d.get("size") or 0
                    repo.default_branch = d.get("default_branch") or repo.default_branch
                    if repo.created_at:
                        age = max((dt.datetime.now(dt.timezone.utc) - repo.created_at).total_seconds() / 86400, 1)
                        repo.velocity = round(repo.stars / age, 1)
                    ok = True
                elif resp.status_code in (403, 429):
                    _api_quota_hit.set()
            except Exception:
                pass
        with _meta_lock:
            _meta_stats["attempts"] += 1
            if not ok:
                _meta_stats["failures"] += 1


# ---------------------------------------------------------------- 打分

def _piecewise(value: float, table: list) -> float:
    """table: [(阈值, 分数)]，降序匹配。"""
    for threshold, score in table:
        if value >= threshold:
            return score
    return table[-1][1]


def score_repo(repo: Repo, period: str) -> None:
    days = PERIOD_DAYS[period]
    reasons: dict = {}

    # ---- 受欢迎度 25
    vel = repo.stars_period / days if repo.stars_period else repo.velocity
    vel_pt = _piecewise(vel, [(1000, 20), (500, 18), (300, 16), (200, 14), (100, 11), (50, 8), (20, 5), (5, 3), (0, 1)])
    star_pt = _piecewise(repo.stars, [(100000, 5), (30000, 4.5), (10000, 4), (5000, 3), (1000, 2), (100, 1), (0, 0)])
    pop = min(25, round(vel_pt + star_pt))
    reasons["popularity"] = [
        f"Star 增速 ≈ {vel:,.0f}/天" + ("（官方趋势页新增）" if repo.stars_period else "（按创建至今均速推算）"),
        f"总 Star {repo.stars:,}",
    ]

    # ---- 维护活跃度 25
    if repo.pushed_at:
        gap = (dt.datetime.now(dt.timezone.utc) - repo.pushed_at).total_seconds() / 86400
        mt = _piecewise(-gap, [(-2, 23), (-7, 22), (-14, 20), (-30, 17), (-60, 13), (-90, 10), (-180, 6), (-9999, 2)])
        reasons["maintenance"] = [f"最近提交 {gap:.0f} 天前"]
    else:
        mt = 12
        reasons["maintenance"] = ["未获取到最近提交时间"]
    if repo.archived:
        mt = min(mt, 3)
        reasons["maintenance"].append("仓库已归档")
    if repo.open_issues > 500 and repo.stars < 3000:
        mt = max(0, mt - 3)
        reasons["maintenance"].append(f"Open Issues {repo.open_issues} 偏多")
    mt = min(25, round(mt))

    # ---- 实用可应用性 30
    pr = 5.0
    rd = repo.readme or ""
    low = rd.lower()
    if len(rd) >= 3000:
        pr += 4
        reasons["practicality"] = ["README 内容完整"]
    elif len(rd) >= 1000:
        pr += 2
        reasons["practicality"] = ["README 有基本说明"]
    else:
        reasons["practicality"] = ["README 内容较少" if repo.readme_ok else "未取到 README"]
    hits = [label for pat, label in INSTALL_HINTS if re.search(pat, low)]
    if hits:
        pr += 5
        reasons["practicality"].append("含安装方式：" + "、".join(dict.fromkeys(hits))[:40])
    if low.count("```") >= 4:
        pr += 3
        reasons["practicality"].append("含示例代码块")
    if re.search(r"!\[[^\]]*\]\([^)]+\.(png|jpg|jpeg|gif|webp)", low) or "img.shields.io" in low:
        pr += 2
        reasons["practicality"].append("README 含截图/徽章")
    if 20 <= len(repo.description) <= 200:
        pr += 2
    if repo.homepage:
        pr += 2
        reasons["practicality"].append("有项目主页")
    if len(repo.topics) >= 3:
        pr += 1
    if repo.stars >= 1000:
        pr += 2
        reasons["practicality"].append("已有较大采用度")
    pr = min(30, round(pr))

    # ---- 技术栈质量 20
    ql = 4.0
    if repo.license in PERMISSIVE_LICENSES:
        ql += 5
        reasons["quality"] = [f"许可证 {repo.license}（宽松）"]
    elif repo.license:
        ql += 3
        reasons["quality"] = [f"许可证 {repo.license}"]
    else:
        reasons["quality"] = ["未声明许可证，商用需谨慎"]
    if repo.language in MAINSTREAM_LANGS:
        ql += 3
    if not repo.archived:
        ql += 2
    if len(repo.topics) >= 5:
        ql += 1
    if 100 <= repo.size_kb <= 800000:
        ql += 1
    if re.search(r"actions/workflows|github\.com/.*/actions", low):
        ql += 2
        reasons["quality"].append("配置了 CI")
    if re.search(r"\btests?\b|\bpytest\b|\bjest\b|\bgo test\b", low):
        ql += 1
    ql = min(20, round(ql))

    total = pop + mt + pr + ql
    repo.scores = {"popularity": pop, "maintenance": mt, "practicality": pr, "quality": ql}
    repo.reasons = reasons
    repo.total = total
    repo.grade = "S" if total >= 90 else "A" if total >= 75 else "B" if total >= 60 else "C"
    repo.verdict = {"S": "强烈推荐", "A": "推荐", "B": "值得关注", "C": "一般"}[repo.grade]


def build_comment(repo: Repo) -> None:
    """按信号生成点评：价值点 + 风险点（规则生成，非人工判断）。"""
    pros, cons = [], []
    if repo.velocity >= 100:
        pros.append(f"增速强劲（≈{repo.velocity:,.0f} star/天）")
    if repo.license in PERMISSIVE_LICENSES:
        pros.append(f"{repo.license} 许可，可商用")
    if any("含安装方式" in r for r in repo.reasons.get("practicality", [])):
        pros.append("上手路径清晰")
    if repo.stars >= 5000:
        pros.append(f"{repo.stars:,} star 已过社区验证")

    if not repo.license:
        cons.append("无 LICENSE")
    if repo.pushed_at:
        gap = (dt.datetime.now(dt.timezone.utc) - repo.pushed_at).total_seconds() / 86400
        if gap > 90:
            cons.append(f"{gap:.0f} 天未更新")
    if repo.created_at:
        age = (dt.datetime.now(dt.timezone.utc) - repo.created_at).total_seconds() / 86400
        if age < 7:
            cons.append("项目过新，稳定性待观察")
    if not repo.readme_ok or len(repo.readme) < 1000:
        cons.append("文档偏薄")
    if repo.archived:
        cons.append("已归档")

    left = "、".join(pros[:2]) or "信号中性，价值需自行验证"
    right = "、".join(cons[:2]) or "暂无明显风险信号"
    repo.comment = f"价值：{left}。风险：{right}。"


# ---------------------------------------------------------------- 主流程

def attach_scores(items: list, scored: list, period: str = "daily", token: str = "") -> list:
    """给官方榜单条目补四维评分。已评分的按仓库名复用，未评分的才补抓元数据与 README。
    与评分看板共用 score_repo / build_comment，口径完全一致。"""
    by_name = {s.get("full_name"): s for s in (scored or [])}
    missing = []
    for it in items:
        s = by_name.get(it["full_name"])
        if s and "scores" in s:
            for k in ("total", "grade", "scores", "reasons", "comment", "category",
                      "velocity", "meta_ok"):
                if k in s:
                    it[k] = s[k]
        else:
            missing.append(it)

    if not missing:
        return items

    session = _session(token)
    pending = [Repo(
        full_name=it["full_name"], url=it["url"], description=it.get("description", ""),
        language=it.get("language", ""), stars=it.get("stars", 0), forks=it.get("forks", 0),
        stars_period=it.get("stars_period", 0), data_source="official",
        description_zh=it.get("description_zh", ""),
    ) for it in missing]

    from concurrent.futures import ThreadPoolExecutor, wait as _wait
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        _wait([pool.submit(enrich, session, r) for r in pending], timeout=12)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    for r in pending:
        score_repo(r, period)
        r.category = classify(r)          # 与评分看板用同一套分类规则（中英双语）
        build_comment(r)
    lookup = {r.full_name: r for r in pending}
    for it in missing:
        r = lookup[it["full_name"]]
        it.update({"total": r.total, "grade": r.grade, "scores": r.scores,
                   "reasons": r.reasons, "comment": r.comment,
                   "category": r.category, "velocity": r.velocity,
                   "meta_ok": r.pushed_at is not None})
    return items


def analyze(period: str = "daily", language: str = "", limit: int = 20,
            min_stars: int = 100, token: str = "", source: str = "auto",
            translate: bool = True) -> dict:
    session = _session(token)
    _raw_fails[0] = 0
    _api_quota_hit.clear()
    meta_coverage(reset=True)
    used = "api"
    repos: list = []
    official_raw: list = []
    official_fetched_at = ""
    official_stale = False
    official_url = (f"https://github.com/trending"
                    + (f"/{language.lower()}" if language else "")
                    + {"daily": "", "weekly": "?since=weekly", "monthly": "?since=monthly"}[period])

    if source in ("auto", "official"):
        official = fetch_official_trending(session, period, language, limit)
        if official:
            repos, used = official, "official"
            official_raw = [{
                "full_name": r.full_name, "url": r.url, "description": r.description,
                "language": r.language, "stars": r.stars, "forks": r.forks,
                "stars_period": r.stars_period,
            } for r in official]
            official_fetched_at = load_official_cache(period, language).get("fetched_at", "")
        elif source == "official" and not load_official_cache(period, language).get("items"):
            return {"error": "官方趋势页不可达（当前网络无法访问 github.com），且本地无缓存；请改用「API 推算」"}

    if not repos:
        repos = search_api(session, period, language, limit, min_stars)
        used = "api"

    # 实时抓取失败时，官方视图退回最近一次成功抓取的快照（明确标注为缓存）
    if not official_raw:
        cached = load_official_cache(period, language)
        if cached.get("items"):
            official_raw = cached["items"]
            official_fetched_at = cached.get("fetched_at", "")
            official_stale = True

    if not repos:
        return {"error": "未获取到项目，请放宽最低 Star 条件或更换周期"}

    repos.sort(key=lambda r: r.velocity if used == "api" else r.stars_period, reverse=True)
    repos = repos[: limit * 2]

    # 第一轮：只用 API 元数据粗排，避免为全部候选抓 README
    for r in repos:
        score_repo(r, period)
    repos.sort(key=lambda r: r.total, reverse=True)
    repos = repos[:limit]

    # 第二轮：只给入选项目补 README，并行 + 总时限 12 秒（不等待慢请求收尾）
    from concurrent.futures import ThreadPoolExecutor, wait as _wait
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        futures = [pool.submit(enrich, session, r) for r in repos]
        _wait(futures, timeout=12)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    for r in repos:
        score_repo(r, period)
        build_comment(r)
    repos.sort(key=lambda r: r.total, reverse=True)

    trans_map, trans_status = {}, "off"
    if translate:
        try:
            from translate import translate_many
            # 评分卡片与官方表格共用一次翻译，重复描述由缓存去重
            texts = [r.description for r in repos] + [it["description"] for it in official_raw]
            trans_map, trans_status = translate_many(list(dict.fromkeys(t for t in texts if t)))
        except Exception:
            trans_status = "unavailable"
    for r in repos:
        r.description_zh = trans_map.get(r.description, "")
        r.category = classify(r)          # 用中英双语判断，中文关键词也能命中
    for it in official_raw:
        it["description_zh"] = trans_map.get(it["description"], "")

    # 官方榜单条目也带上同一套四维评分；评分看板已算过的直接复用，不重复抓
    scored_dicts = []
    for r in repos:
        item = asdict_repo(r)
        item["meta_ok"] = (r.pushed_at is not None) or (r.data_source != "official")
        scored_dicts.append(item)
    if official_raw:
        attach_scores(official_raw, scored_dicts, period, token)

    coverage = round(sum(1 for r in repos if r.readme_ok) / len(repos) * 100) if repos else 0

    return {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period": period,
        "period_label": PERIOD_LABEL[period],
        "language": language or "全语言",
        "source": used,
        "source_label": "官方趋势页" if used == "official" else "GitHub API 推算（新项目高增长榜）",
        "translate_status": trans_status,
        "readme_coverage": coverage,
        "meta_coverage": meta_coverage_from_items(official_raw) if official_raw else 100,
        "board_meta_coverage": meta_coverage_from_items(scored_dicts),
        "official_items": official_raw,
        "official_fetched_at": official_fetched_at,
        "official_stale": official_stale,
        "official_period": period,
        "official_language": language or "全语言",
        "official_url": official_url,
        "items": scored_dicts,
    }


def asdict_repo(r: Repo) -> dict:
    d = asdict(r)
    d["created_at"] = r.created_at.strftime("%Y-%m-%d") if r.created_at else ""
    d["pushed_at"] = r.pushed_at.strftime("%Y-%m-%d") if r.pushed_at else ""
    d.pop("readme", None)
    return d


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="daily", choices=list(PERIOD_DAYS))
    ap.add_argument("--language", default="")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--min-stars", type=int, default=100)
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    a = ap.parse_args()
    t0 = time.time()
    res = analyze(a.period, a.language, a.limit, a.min_stars, a.token)
    if "error" in res:
        print("ERROR:", res["error"])
    else:
        print(f"source={res['source']} 用时={time.time()-t0:.1f}s  共{len(res['items'])}条")
        for it in res["items"]:
            print(f"{it['total']:>3} {it['grade']} {it['full_name']:<38} ★{it['stars']:>7,} "
                  f"v={it['velocity']:>7,.1f} {it['language'] or '-':<12} {it['scores']}")
