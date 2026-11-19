"""하네스 구조 점검: lint, describe, diagnose, 제안서 검사, adopt.

구조를 바꾸는 길은 하나다. 에이전트가 .gate/proposals/H###.md에 새 config와 근거를 쓰고, check-proposal이 lint,
지난 기록 재생, diff를 붙여 검토자에게 보이고, 사용자가 승인한 문장을 adopt --approved에 넣는다(guard가 그 문장이
이번 턴 사용자 입력에 있는지 본다). 진행 중인 계획은 시작할 때의 버전을 끝까지 쓴다.
"""
import difflib
import hashlib
import re
import shutil
import statistics
import string
import time
import tomllib
from datetime import datetime

from . import parts as PT
from . import records, score
from .checks import BUILTIN, CHECKS, STAGE_CHECKS, applies, call_reviewer, load_part_checks
from .core import (CFG, CLOSED, GATE_DIR, ROOT, body, bullets, cfg_for, changed_since, git, head, is_code, now,
                   parse_config, read_events, sections, tables, template_text)

PROPOSALS = GATE_DIR / "proposals"
PARTS = GATE_DIR / "parts"
COND_KEYS = {"option", "changed", "max_files", "not", "any"}
MUST_PROTECT = (".gate/config.toml", ".gate/history/", ".gate/gatekit/")
# 검사가 section을 주지 않을 때 쓰는 기본 절
DEFAULT_SECTION = {"results": "실행 계획", "sample": "표본 실행", "choice": "결정", "numbers": "값 근거"}
# 검사가 기본값 없이 읽는 키
REQUIRED = {"filled": ("section",), "table": ("section",), "paths_exist": ("section",),
            "refs": ("section", "file", "pattern"), "commit_since": ("path",), "unchanged": ("state",),
            "test_edits": ("section",), "covers": ("section", "from"), "field": ("field",),
            "cell_max": ("section", "max")}
# 계획 파일만 보고 판정하는 검사. 닫힌 계획에 새 구조의 검사를 다시 돌릴 수 있다
REPLAYABLE = ("filled", "table", "covers", "cell_max", "choice", "refs", "paths_exist")
# command의 use가 가리킬 수 있는 [project] 명령
COMMAND_USE = ("test", "affected")
# [review] effort에 쓸 수 있는 claude --effort 값
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# review 지시문에 쓸 수 있는 자리
REVIEW_FIELDS = {"plan", "title", "kind", "state", "diff", "context"}


# lint

def _conds(cond, where, kind_opts):
    out = []
    for k, v in cond.items():
        if k not in COND_KEYS:
            out.append(f"{where}: 알 수 없는 조건 키 '{k}'(쓸 수 있는 것: {', '.join(sorted(COND_KEYS))})")
        elif k == "option" and v not in kind_opts:
            out.append(f"{where}: 선택 '{v}'가 종류의 options에 없다")
        elif k == "not":
            out += _conds(v, where, kind_opts)
        elif k == "any":
            for c in v:
                out += _conds(c, where, kind_opts)
    return out


def lint(cfg):
    """구조 검사. 문제 문장 목록"""
    bad = []
    if not isinstance(cfg.get("version"), int):
        bad.append("version이 정수가 아니다")
    prot = cfg["project"]["protected"]
    for m in MUST_PROTECT:
        if m not in prot:
            bad.append(f"[project] protected에서 {m}를 뺄 수 없다")
    if not cfg["kinds"]:
        bad.append("종류가 없다")
    for kname, k in cfg["kinds"].items():
        states = k.get("states", [])
        undefined = [s for s in states if s not in cfg["states"]]
        if not states:
            bad.append(f"{kname}: 상태가 없다")
        bad += [f"{kname}: 상태 {s}가 [states]에 없다" for s in undefined]
        if not states or undefined:
            continue
        opts = k.get("options", [])
        for s, cond in k.get("when", {}).items():
            if s not in states:
                bad.append(f"{kname}.when: {s}가 이 종류의 상태가 아니다")
            bad += _conds(cond, f"{kname}.when.{s}", opts)
        bad += _conds(k.get("limit", {}), f"{kname}.limit", opts)
        for s, targets in k.get("back", {}).items():
            if s not in states:
                bad.append(f"{kname}.back: {s}가 이 종류의 상태가 아니다")
                continue
            bad += [f"{kname}.back.{s}: {t}가 {s}보다 앞 상태가 아니다" for t in targets
                    if t not in states[:states.index(s)]]
        tpl = template_text(cfg, kname)
        ts = sections(tpl)
        uses_files = False
        for s in states:
            st = cfg["states"][s]
            for c in st.get("checks", []):
                name = c.get("check")
                if name not in CHECKS:
                    bad.append(f"{s}: 검사 '{name}'가 없다(있는 것: {', '.join(CHECKS)})")
                    continue
                if (c.get("only") and kname not in c["only"]) or kname in c.get("skip", []):
                    continue
                missing = [key for key in REQUIRED.get(name, ()) if not c.get(key)]
                if missing:
                    bad.append(f"{kname}/{s}: {name}에 필요한 키가 없다: {', '.join(missing)}")
                    continue
                uses_files |= name in ("files_listed", "files_changed", "numbers", "sample", "test_edits")
                if name == "command" and c.get("use") and c["use"] not in COMMAND_USE:
                    bad.append(f"{kname}/{s}: command의 use는 {', '.join(COMMAND_USE)} 중 하나다(지금 {c['use']})")
                if name == "test_edits":
                    if not cfg["project"]["tests"]:
                        bad.append(f"{kname}/{s}: test_edits를 쓰는데 [project] tests가 비었다")
                    if c.get("state") and c["state"] not in states[:states.index(s)]:
                        bad.append(f"{kname}/{s}: test_edits가 가리키는 {c['state']}가 이 종류에서 {s}보다 앞 상태가 아니다")
                sec = c.get("section", DEFAULT_SECTION.get(name))
                if name == "command" and not sec:
                    continue
                for key in ("section", "options", "from"):
                    target = c.get(key, sec if key == "section" else None)
                    if target and target not in ts:
                        bad.append(f"{kname}/{s}: {name}이 가리키는 절 '{target}'이 계획 틀에 없다"
                                   f"(상태의 sections나 종류의 extra_sections에 넣는다)")
                items = list(c.get("items", [])) + ([c["item"]] if "item" in c else [])
                if name == "sample":
                    items += ["명령", "커밋", "결과 요약"]
                have = bullets(ts.get(sec, [])) if sec else {}
                for it in items:
                    if sec in ts and it not in have:
                        bad.append(f"{kname}/{s}: {name}이 가리키는 항목 '{sec}: {it}'이 절 정의에 없다")
                if name == "reviewed" and c.get("state") not in states:
                    bad.append(f"{kname}/{s}: reviewed가 가리키는 {c.get('state')}가 이 종류에 없다")
                if name == "unchanged":
                    if c["state"] not in states[:states.index(s)]:
                        bad.append(f"{kname}/{s}: unchanged가 가리키는 {c['state']}가 이 종류에서 {s}보다 앞 상태가 아니다")
                    bad += [f"{kname}/{s}: unchanged가 가리키는 절 '{x}'이 계획 틀에 없다"
                            for x in c.get("sections", []) if x not in ts]
            if st.get("edit") not in (None, "tests"):
                bad.append(f"{s}: edit는 \"tests\"만 쓸 수 있다(지금 {st['edit']})")
            elif st.get("edit") and not (st.get("code") and cfg["project"]["tests"]):
                bad.append(f"{s}: edit = \"tests\"는 code = true인 상태에 쓰고 [project] tests가 있어야 한다")
            if st.get("review") and not isinstance(st["review"], str):
                bad.append(f"{s}: review는 문자열이다")
            elif st.get("review"):
                try:
                    fields = {f for _, f, _, _ in string.Formatter().parse(st["review"]) if f is not None}
                except ValueError as e:
                    fields = {f"형식 오류 {e}"}
                bad += [f"{s}: review의 {{{x}}}는 쓸 수 없는 자리다(쓸 수 있는 것: {', '.join(sorted(REVIEW_FIELDS))})"
                        for x in sorted(fields - REVIEW_FIELDS)]
                if "context" in fields and not st.get("context"):
                    bad.append(f"{s}: review에 {{context}}를 쓰는데 상태의 context(발췌할 파일 목록)가 비었다")
            ex = st.get("review_exempt")  # 그 절이 없는 종류에서는 면제가 걸리지 않을 뿐이다
            if ex and ex.get("section") in ts and ex.get("item") not in bullets(ts[ex["section"]]):
                bad.append(f"{kname}/{s}: review_exempt가 가리키는 항목 '{ex.get('section')}: {ex.get('item')}'이 계획 틀에 없다")
        if uses_files and cfg["project"]["files_section"] not in ts:
            bad.append(f"{kname}: 파일 검사를 쓰는데 files_section '{cfg['project']['files_section']}' 절이 계획 틀에 없다")
    for s in cfg["project"]["stop_states"]:
        if s not in cfg["states"]:
            bad.append(f"stop_states의 {s}가 [states]에 없다")
    if cfg["review"].get("effort") not in (None, *EFFORTS):
        bad.append(f"[review] effort는 {', '.join(EFFORTS)} 중 하나다(지금 {cfg['review']['effort']})")
    for st in cfg["stages"]:
        for k in st.get("kinds", []):
            if k not in cfg["kinds"]:
                bad.append(f"{st.get('n')}단계의 종류 {k}가 [kinds]에 없다")
        for key in ("exit", "require"):
            for spec in st.get(key, []):
                c = spec.get("check")
                if c not in CHECKS:
                    bad.append(f"{st.get('n')}단계 {key}: 검사 '{c}'가 없다")
                elif c in BUILTIN and c not in STAGE_CHECKS:
                    bad.append(f"{st.get('n')}단계 {key}: '{c}'는 계획 파일을 읽는 검사라 단계 검사에 쓸 수 없다"
                               f"(쓸 수 있는 기본 검사: {', '.join(sorted(STAGE_CHECKS))}, 부품 checks.py의 검사)")
    for g in cfg.get("guard", []):
        try:
            re.compile(g.get("pattern", "("))
        except re.error as e:
            bad.append(f"[[guard]] pattern을 읽지 못했다: {g.get('pattern')} ({e})")
        if not g.get("reason"):
            bad.append(f"[[guard]] {g.get('pattern')}: 막은 이유(reason)가 없다")
    return bad


# 부품

def parts_problems():
    """부품을 쓰는 프로젝트에서 부품, parts.lock, config.toml이 서로 맞는지"""
    if not PARTS.exists():
        return []
    cfg, bad, used = PT.build(GATE_DIR, CFG["version"])
    if not bad and cfg != tomllib.loads((GATE_DIR / "config.toml").read_text(encoding="utf-8")):
        bad.append("config.toml이 부품과 local.toml을 조립한 결과와 다르다. 구조는 제안서와 adopt로만 바꾼다")
    locked = PT.read_lock(GATE_DIR)
    names = {p["name"] for p in used}
    bad += [f"부품 {p['name']}의 파일이 parts.lock과 다르다" for p in used
            if locked.get(p["name"], {}).get("hash") != p["_hash"]]
    bad += [f"parts.lock의 부품 {n}이 parts 폴더에 없다" for n in locked if n not in names]
    return bad


def parts_report():
    """쓰는 부품과 쓰지 않는 라이브러리 부품의 상태. 적용 조건은 지금 저장소와 답으로 다시 판정한다"""
    if not PARTS.exists():
        return ["부품을 쓰지 않는 구조다(config.toml이 원본이다)"]
    used, lib = PT.load_parts(PARTS), {p["name"]: p for p in PT.load_parts(GATE_DIR / "library")}
    locked, answers = PT.read_lock(GATE_DIR), PT.read_local(GATE_DIR)[1]
    out = ["쓰는 부품"]
    for p in used:
        notes = []
        src = lib.get(p["name"])
        if src and locked.get(p["name"], {}).get("from") != p["_hash"]:
            notes.append("프로젝트에서 고침")
        if src and src.get("version", 1) > p.get("version", 1):
            notes.append(f"라이브러리에 v{src['version']}이 있다")
        if p.get("applies_when") and not PT.holds(p["applies_when"], ROOT, answers):
            notes.append(f"조건이 거짓이 됐다({PT.fmt_cond(p['applies_when'])}). 제거 제안서를 검토한다")
        out.append(f"- {p['name']} v{p.get('version', 1)} ({p.get('origin', 'library')}): {p.get('description', '')}"
                   + (" [" + "; ".join(notes) + "]" if notes else ""))
    names = {p["name"] for p in used}
    out += ["", "쓰지 않는 라이브러리 부품"]
    for name, p in lib.items():
        if name in names:
            continue
        cond = p.get("applies_when")
        hit = " [조건이 맞는다. 추가 제안서를 검토한다]" if cond and PT.holds(cond, ROOT, answers) else ""
        out.append(f"- {name}: {p.get('description', '')} (조건: {PT.fmt_cond(cond or {}) or '없음'}){hit}")
    return out


# describe

def describe(cfg):
    lines = [f"구조 버전 {cfg['version']}"]
    for kname, k in cfg["kinds"].items():
        when = k.get("when", {})
        path = []
        for s in k["states"]:
            path.append(f"[{s} 조건: {fmt_cond(when[s])}]" if s in when else s)
        opts = f" (선택: {', '.join(k['options'])})" if k.get("options") else ""
        opts += f" (상한: {fmt_cond(k['limit'])})" if k.get("limit") else ""
        lines.append(f"- {kname}{opts}: " + " → ".join(path) + " → DONE")
        for s, targets in k.get("back", {}).items():
            lines.append(f"  - {s}에서 돌아갈 수 있는 상태: {', '.join(targets) or '없음'}")
    lines.append("")
    for s, st in cfg["states"].items():
        flags = [x for x in ("code", "run") if st.get(x)] + (["review"] if st.get("review") else [])
        flags += [f"edit={st['edit']}"] if st.get("edit") else []
        checks = ", ".join(c["check"] for c in st.get("checks", []))
        lines.append(f"- {s}{' (' + ', '.join(flags) + ')' if flags else ''}: {checks or '검사 없음'}")
    return "\n".join(lines)


def _back_groups(states, back):
    """back 대상이 같은 연속한 상태끼리 묶는다. [(상태 목록, 대상 목록)]"""
    groups = []
    for s in states:
        targets = back.get(s)
        if not targets:
            continue
        prev = groups[-1] if groups else None
        if prev and prev[1] == targets and states.index(prev[0][-1]) == states.index(s) - 1:
            prev[0].append(s)
        else:
            groups.append(([s], targets))
    return groups


def mermaid(cfg):
    """종류마다 상태 경로를 Mermaid flowchart로. 조건이 붙은 상태는 건너뛰는 화살표를 같이 그리고, back에 적은
    되돌아가는 경로는 점선으로 그린다. 돌아갈 대상이 같은 연속한 상태는 한 상자로 묶어 점선을 대상마다 하나만 그린다"""
    out = []
    for kname, k in cfg["kinds"].items():
        states, when = k["states"], k.get("when", {})
        groups = _back_groups(states, k.get("back", {}))
        boxed = {s: f"back{i}" for i, (members, _) in enumerate(groups) if len(members) > 1 for s in members}
        lines = ["flowchart LR", "    start(( ))", "    done((( )))"]

        def node(s):
            st = cfg["states"].get(s, {})
            code = "테스트 코드" if st.get("edit") == "tests" else "코드"
            tags = [t for t, on in ((code, st.get("code")), ("실행", st.get("run")), ("검토", st.get("review"))) if on]
            return f'{s}["{s}<br>{", ".join(tags)}"]' if tags else s

        drawn = set()
        for s in states:
            box = boxed.get(s)
            if not box:
                lines.append(f"    {node(s)}")
            elif box not in drawn:
                drawn.add(box)
                lines += [f'    subgraph {box} [" "]', "        direction LR",
                          *[f"        {node(x)}" for x in states if boxed.get(x) == box], "    end",
                          f"    style {box} fill:none,stroke:#999,stroke-dasharray:4 3"]
        for i in range(len(states) + 1):
            src = states[i - 1] if i else "start"
            skipped = []
            for dst in states[i:] + ["done"]:
                if skipped:
                    label = f"{', '.join(skipped)} 건너뜀"
                elif dst in when:
                    label = _cond_label(when[dst])
                else:
                    label = ""
                lines.append(f'    {src} -->|"{label}"| {dst}' if label else f"    {src} --> {dst}")
                if dst not in when:
                    break
                skipped.append(dst)
        for members, targets in groups:
            src = boxed.get(members[0], members[0])
            lines += [f"    {src} -.-> {t}" for t in targets]
        out += [f"### {kname}", "", "```mermaid", *lines, "```", ""]
    return "\n".join(out)


def _cond_label(c):
    """다이어그램 화살표에 붙일 조건 설명"""
    parts = []
    for k, v in c.items():
        if k == "option":
            parts.append(f"--with {v}")
        elif k == "changed":
            parts.append(f"{', '.join(v) if isinstance(v, list) else v} 변경")
        elif k == "max_files":
            parts.append(f"코드 파일 {v}개 이하")
        elif k == "not":
            parts.append(f"{_cond_label(v)} 아님")
        elif k == "any":
            parts.append(" 또는 ".join(_cond_label(x) for x in v))
    return ", ".join(parts).replace('"', "#quot;")


def fmt_cond(c):
    parts = []
    for k, v in c.items():
        if k == "not":
            parts.append(f"아님({fmt_cond(v)})")
        elif k == "any":
            parts.append("하나라도(" + " / ".join(fmt_cond(x) for x in v) + ")")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


# diagnose

def _entered(p):
    """history에서 건너뛴 기록을 뺀, 실제로 들어간 상태 기록"""
    return [x for x in p.get("history", []) if not (len(x) > 3 and str(x[3]).startswith("skip"))]


def _durations(p):
    """상태별 머문 분. history의 연속 기록 사이 시간"""
    out = {}
    h = _entered(p)
    for (s, at, *_), (_, at2, *_) in zip(h, h[1:]):
        try:
            m = (datetime.fromisoformat(at2) - datetime.fromisoformat(at)).total_seconds() / 60
            out.setdefault(s, []).append(m)
        except ValueError:
            pass
    return out


def diagnose(flow):
    hz = flow.get("harness", {})
    since_ts, since_commit = hz.get("ts", 0), hz.get("commit") or ""
    lines = [f"# 하네스 진단 ({now()})", "",
             f"- 지금 구조 버전: {CFG['version']}, 마지막 adopt: {hz.get('adopted', '설치')} {hz.get('at', '')}",
             f"- PROFILE.md: {'있음' if (GATE_DIR / 'PROFILE.md').exists() else '없음'}", ""]
    stat, edges, spend = {}, {}, {}

    def slot(kind, state):
        return stat.setdefault((kind, state), {"visits": 0, "back": 0, "skip": 0, "abandon": 0, "fail": [],
                                               "mins": [], "chars": []})

    plans = records.all_plans(flow)
    for p in plans.values():
        cfg = cfg_for(p)
        text = (ROOT / p["plan"]).read_text(encoding="utf-8") if (ROOT / p["plan"]).exists() else ""
        ss = sections(text)
        durs = _durations(p)
        for x in _entered(p):
            d = slot(p["kind"], x[0])
            d["visits"] += 1
            d["back"] += len(x) > 3 and str(x[3]).startswith("back")
        h = _entered(p)
        for (src, *_), x in zip(h, h[1:]):
            if len(x) > 3 and str(x[3]).startswith("back: "):
                edges.setdefault((p["kind"], src, x[0]), []).append(str(x[3])[6:])
        for s in p.get("skipped", []):
            slot(p["kind"], s)["skip"] += 1
        if p["state"] == "ABANDONED" and len(p.get("history", [])) >= 2:
            slot(p["kind"], p["history"][-2][0])["abandon"] += 1
        for s, rev in p.get("review", {}).items():
            if not rev.get("pass") and (p["kind"], s) in stat:
                stat[(p["kind"], s)]["fail"] += rev.get("missing", [])[:2]
            sp = spend.setdefault((p["kind"], s), [0, 0.0, set()])
            sp[0] += rev.get("calls", 1)
            sp[1] += rev.get("spent", rev.get("cost") or 0)
            sp[2].add(rev.get("model") or "기록 없음")
        for s, ms in durs.items():
            if (p["kind"], s) in stat:
                stat[(p["kind"], s)]["mins"] += ms
        if p["state"] == "DONE":
            for s in cfg["kinds"].get(p["kind"], {}).get("states", []):
                secs = cfg["states"].get(s, {}).get("sections", [])
                n = sum(len("".join(body(ss.get(x, [])))) for x in secs)
                if secs and (p["kind"], s) in stat:
                    stat[(p["kind"], s)]["chars"].append(n)
    lines += ["## 상태별 기록", "",
              "| 종류 | 상태 | 들어온 횟수 | back으로 들어온 횟수 | 건너뜀 | 여기서 그만둠 | 머문 시간 중앙값(분) | 끝난 계획의 절 글자 수 중앙값 | 검토 FAIL 이유 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for (k, s), d in sorted(stat.items()):
        if s in CLOSED:
            continue
        med = f"{statistics.median(d['mins']):.0f}" if d["mins"] else ""
        ch = f"{statistics.median(d['chars']):.0f}" if d["chars"] else ""
        lines.append(f"| {k} | {s} | {d['visits']} | {d['back']} | {d['skip']} | {d['abandon']} | {med} | {ch} | "
                     + "; ".join(x[:60] for x in d["fail"][:3]) + " |")
    lines += ["", "## 검토 호출", ""]
    if spend:
        lines += ["| 종류 | 상태 | 호출 수 | 비용 합계(USD) | 모델/추론 단계 |", "|---|---|---|---|---|"]
        lines += [f"| {k} | {s} | {n} | {usd:.2f} | {', '.join(sorted(ms))} |"
                  for (k, s), (n, usd, ms) in sorted(spend.items())]
    else:
        lines.append("없음")
    lines += ["", "## 되돌아간 경로", ""]
    if edges:
        lines += ["| 종류 | 떠난 상태 | 돌아간 상태 | 횟수 | 이유 |", "|---|---|---|---|---|"]
        lines += [f"| {k} | {src} | {dst} | {len(why)} | " + "; ".join(w[:60] for w in why[-3:]) + " |"
                  for (k, src, dst), why in sorted(edges.items())]
    else:
        lines.append("없음")
    done = [p for p in plans.values() if p["state"] == "DONE" and p["entered"]["DONE"]["ts"] >= since_ts]
    lines += ["", f"- 마지막 adopt 뒤 끝난 계획: {len(done)}개"]
    ev = read_events(since_ts)
    lines += ["", "## 막힌 기록(마지막 adopt 뒤)", ""]
    counts, misses = {}, {}
    for e in ev:
        if e["kind"] in ("pass", "guard_pass"):
            continue
        if e["kind"] == "next":
            m = misses.setdefault((e.get("plan_kind", ""), e.get("state", ""), e["reason"][:70]), [0, set()])
            m[0] += 1
            m[1].add(e.get("plan", ""))
        else:
            counts.setdefault(e["kind"], []).append(e["reason"])
    if not counts:
        lines.append("없음")
    for k, rs in sorted(counts.items(), key=lambda x: -len(x[1])):
        common = {}
        for r in rs:
            common[r[:70]] = common.get(r[:70], 0) + 1
        top = sorted(common.items(), key=lambda x: -x[1])[:3]
        lines.append(f"- {k} {len(rs)}회: " + "; ".join(f"{r} ({n}회)" for r, n in top))
    lines += ["", "## next에서 막힌 이유(마지막 adopt 뒤)", ""]
    if misses:
        lines += ["| 종류 | 상태 | 이유 | 횟수 | 계획 |", "|---|---|---|---|---|"]
        lines += [f"| {k} | {s} | {r.replace('|', '/')} | {n} | {', '.join(sorted(ps))} |"
                  for (k, s, r), (n, ps) in sorted(misses.items(), key=lambda x: -x[1][0])[:15]]
    else:
        lines.append("없음")
    lines += ["", "## 저장소 변화(마지막 adopt 뒤)", ""]
    if since_commit:
        added = [l.split("\t")[-1] for l in git("diff", "--name-status", f"{since_commit}..HEAD").splitlines()
                 if l.startswith("A")]
        groups = {}
        for f in added:
            g = "/".join(f.split("/")[:2]) if "/" in f else f
            groups[g] = groups.get(g, 0) + 1
        lines.append("- 새로 생긴 파일(폴더별): " + (", ".join(f"{g} {n}개" for g, n in sorted(groups.items())) or "없음"))
        changed = changed_since(since_commit)
        outside = sorted(f for f in changed if not is_code(f) and not f.endswith((".md", ".json", ".lock", ".txt"))
                         and not f.startswith((".gate/", ".githooks/")))
        lines.append("- code 패턴 밖에서 바뀐 파일: " + (", ".join(outside[:20]) or "없음"))
    else:
        lines.append("- 기준 커밋이 없다")
    if PARTS.exists():
        lines += ["", "## 부품", "", *parts_report()]
    lines += ["", *score.report(flow), "", "## 기록 크기", "", *records.report(flow)]
    return "\n".join(lines)


# 제안서

def proposal_path(hid):
    return PROPOSALS / f"{hid}.md"


def proposal_config(text):
    m = re.search(r"^## 새 구조\s*\n+```toml\n(.*?)\n```", text, re.S | re.M)
    return m.group(1) if m else None


def propose(hid):
    """지금 부품과 local.toml의 사본을 제안서 폴더에 만든다. 에이전트는 이 사본을 고친다"""
    if not PARTS.exists():
        raise SystemExit("부품을 쓰지 않는 구조다. 제안서의 '새 구조' 절에 새 config.toml 전체를 쓴다")
    d = PROPOSALS / hid
    if d.exists():
        raise SystemExit(f"{d.relative_to(ROOT).as_posix()}가 이미 있다")
    shutil.copytree(PARTS, d / "parts")
    if (GATE_DIR / "local.toml").exists():
        shutil.copy2(GATE_DIR / "local.toml", d / "local.toml")
    return d


def proposed(hid, text):
    """제안서가 정하는 새 구조. (구조, config.toml 글, 문제 목록, 부품 변경 목록)"""
    if not PARTS.exists():
        raw = proposal_config(text)
        if raw is None:
            return None, None, ["'새 구조' 절에 ```toml 블록이 없다"], []
        try:
            new = parse_config(raw)
        except Exception as e:
            return None, None, [f"새 구조를 읽지 못했다: {e}"], []
        bad = [] if new["version"] == CFG["version"] + 1 else [
            f"새 구조의 version은 {CFG['version'] + 1}이어야 한다(지금 {CFG['version']})"]
        return new, raw.rstrip("\n") + "\n", bad, []
    d = PROPOSALS / hid
    if not (d / "parts").exists():
        return None, None, [f"{d.relative_to(ROOT).as_posix()}/parts가 없다. propose {hid}로 사본을 만들어 고친다"], []
    try:
        load_part_checks(d / "parts")  # 제안서가 더한 검사 모듈
        cfg, bad, used = PT.build(d, CFG["version"] + 1)
    except Exception as e:
        return None, None, [f"제안서의 부품을 읽지 못했다: {e}"], []
    old = {p["name"]: p["_hash"] for p in PT.load_parts(PARTS)}
    new_ = {p["name"]: p["_hash"] for p in used}
    changes = ([f"더함: {n}" for n in new_ if n not in old] + [f"뺌: {n}" for n in old if n not in new_]
               + [f"고침: {n}" for n in new_ if n in old and old[n] != new_[n]])
    a, b = (PT.read_local(x)[0] for x in (GATE_DIR, d))
    changes += ["고침: local.toml"] if a != b else []
    raw = PT.dumps(cfg, PT.HEAD)
    return parse_config(raw), raw, [f"조립: {x}" for x in bad], changes


def proposal_hash(hid, text):
    """제안서 글과 제안서 폴더의 부품, local.toml을 함께 본 해시. 검토 뒤 바뀌면 다시 검토한다"""
    h = hashlib.sha256(text.encode("utf-8"))
    d = PROPOSALS / hid
    for f in sorted(d.rglob("*")) if d.exists() else []:
        if f.is_file():
            h.update(f.relative_to(d).as_posix().encode() + b"\0" + f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def replay(flow, new):
    """지난 계획을 새 구조에 대 본다(계획을 옮기지는 않는다). 끝난 계획은 새 구조였다면 달라졌을 상태와 절,
    진행 중인 계획은 시작할 때의 버전을 끝까지 쓴다는 것과 새 구조와의 차이"""
    out = ["진행 중인 계획은 adopt 뒤에도 시작할 때의 구조 버전을 끝까지 쓰고, 새 구조는 다음 계획부터 적용된다. "
           "아래는 각 계획의 종류를 새 구조에 대 본 차이다.", ""]
    n0 = len(out)
    for pid, p in records.all_plans(flow).items():
        active = p["state"] not in CLOSED
        tag = f"진행 중, 구조 v{p.get('version', CFG['version'])} 유지" if active else "끝남, 새 구조였다면"
        if p["kind"] not in new["kinds"]:
            out.append(f"- {pid} ({p['kind']}, {tag}): 새 구조에 이 종류가 없다")
            continue
        old = cfg_for(p)
        o, n = old["kinds"].get(p["kind"], {}).get("states", []), new["kinds"][p["kind"]]["states"]
        gone, added = [s for s in o if s not in n], [s for s in n if s not in o]
        old_secs = set(sections(template_text(old, p["kind"]))) if p["kind"] in old["kinds"] else set()
        new_secs = set(sections(template_text(new, p["kind"])))
        if gone or added or old_secs != new_secs:
            out.append(f"- {pid} ({p['kind']}, {p['state']}, {tag}): 빠지는 상태 {gone or '없음'}, 새 상태 {added or '없음'}, "
                       f"없어지는 절 {sorted(old_secs - new_secs) or '없음'}, 새 절 {sorted(new_secs - old_secs) or '없음'}")
    return out if len(out) > n0 else out + ["- 차이가 나는 계획 없음"]


def _bare(c):
    """부품 표시를 뺀 검사. 검사가 다른 부품으로 옮겨 가기만 한 것은 바뀐 것으로 치지 않는다"""
    return {k: v for k, v in c.items() if k != "part"}


def _spec_text(c):
    return c["check"] + "(" + ", ".join(f"{k}={v}" for k, v in c.items() if k not in ("check", "part")) + ")"


def replay_checks(flow, new):
    """끝난 계획의 계획 파일에 새 구조의 검사를 돌려, 지금 구조와 결과가 달라지는 계획을 보인다"""
    out, skipped = [], 0
    incidents = flow.get("incidents", [])
    for pid, p in sorted(records.all_plans(flow).items()):
        if p["state"] != "DONE" or p["kind"] not in new["kinds"]:
            continue
        f = ROOT / p["plan"]
        text = f.read_text(encoding="utf-8") if f.exists() else git("show", f"{p['entered']['DONE']['commit']}:{p['plan']}")
        s = sections(text)

        def misses(cfg):
            nonlocal skipped
            found = []
            for st in cfg["kinds"].get(p["kind"], {}).get("states", []):
                for spec in cfg["states"].get(st, {}).get("checks", []):
                    if spec["check"] not in REPLAYABLE or not applies(spec, p["kind"]):
                        continue
                    if spec.get("section") and spec["section"] not in s:
                        skipped += cfg is new  # 그때 계획 틀에 없던 절은 견줄 수 없다
                        continue
                    ctx = {"pid": pid, "p": p, "text": text, "s": s, "flow": flow, "state": st, "cfg": cfg}
                    try:
                        found += [f"{st}: {m}" for m in CHECKS[spec["check"]](ctx, spec)]
                    except Exception as e:
                        found.append(f"{st}: {spec['check']}를 재생하지 못했다({e!r})")
            return found

        before, after = misses(cfg_for(p)), misses(new)
        newly, freed = [m for m in after if m not in before], [m for m in before if m not in after]
        mine = [i["id"] for i in incidents if i["plan"] == pid]
        if newly or freed:
            tag = f", 사고 {', '.join(mine)}" if mine else ""
            out.append(f"- {pid} ({p['kind']}{tag}): 새로 막힘 {newly or '없음'}, 더는 막히지 않음 {freed or '없음'}")
    out = out or ["- 끝난 계획 가운데 결과가 달라지는 것 없음"]
    return out + ([f"- 그때 계획 틀에 없던 절을 보는 검사 {skipped}개는 견주지 않았다"] if skipped else [])


def weakened(old, new):
    """새 구조에서 빠지거나 풀리는 것. 종류, 상태, 검사, 검토, 검토 면제, 테스트만 쓰는 제한, 건너뛰는 조건, 상한"""
    out = []
    for kname, k in old["kinds"].items():
        nk = new["kinds"].get(kname)
        if nk is None:
            out.append(f"{kname}: 종류가 없어진다")
            continue
        for s in k["states"]:
            if s not in nk["states"]:
                out.append(f"{kname}: 상태 {s}가 빠진다")
                continue
            o, n, where = old["states"].get(s, {}), new["states"].get(s, {}), f"{kname}/{s}"
            kept = [_bare(c) for c in n.get("checks", []) if applies(c, kname)]
            out += [f"{where}: 검사 {_spec_text(c)}가 빠지거나 바뀐다" for c in o.get("checks", [])
                    if applies(c, kname) and _bare(c) not in kept]
            if o.get("review") and not n.get("review"):
                out.append(f"{where}: 검토가 빠진다")
            elif o.get("review") != n.get("review") and o.get("review"):
                out.append(f"{where}: 검토 지시문이 바뀐다")
            if n.get("code") and not o.get("code"):
                out.append(f"{where}: 코드 편집이 열린다")
            if o.get("edit") and n.get("edit") != o.get("edit"):
                out.append(f"{where}: 테스트 파일만 고치는 제한이 풀린다")
            if s in nk.get("when", {}) and nk["when"][s] != k.get("when", {}).get(s):
                out.append(f"{where}: 건너뛰는 조건이 붙거나 바뀐다({fmt_cond(nk['when'][s])})")
            if o.get("review") and n.get("review") and _model_rank(n.get("review_model")) < _model_rank(o.get("review_model")):
                out.append(f"{where}: 검토 모델이 낮아진다({o.get('review_model') or '기본'}에서 {n.get('review_model')})")
            if n.get("review") and n.get("review_exempt") and n["review_exempt"] != o.get("review_exempt"):
                ex = n["review_exempt"]
                out.append(f"{where}: 검토 면제 항목이 생기거나 바뀐다('{ex.get('section')}: {ex.get('item')}'이 예면 검토하지 않음)")
        if k.get("limit") and nk.get("limit") != k["limit"]:
            out.append(f"{kname}: 상한이 빠지거나 바뀐다({fmt_cond(k['limit'])})")
    return out + weakened_settings(old, new)


# 설정 키마다 푸는 방향. less: 원소가 빠지면 푼다, more: 원소가 늘면 푼다, up: 수가 커지면 푼다
LOOSEN = {
    "project": {"less": ["code", "tracked", "stop_states", "status_sections", "harness", "protected", "tests",
                         "pre_commit"],
                "more": ["unreviewed"], "up": ["max_visits", "review_every_plans", "review_every_events"],
                "cleared": ["test", "affected", "decisions_file", "status_file"]},
    "review": {"more": ["tools"]},
    "remote": {"more": ["exec_states"], "cleared": ["host"]},
}
EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def _model_rank(m):
    """검토 모델의 순서. 지정하지 않은 기본 모델과 opus를 가장 높게 본다"""
    m = (m or "").lower()
    return 0 if "haiku" in m else 1 if "sonnet" in m else 2


def _items(v):
    if isinstance(v, str):
        return {x.strip() for x in v.split(",") if x.strip()}
    return set(v or [])


def weakened_settings(old, new):
    """[project], [review], [remote], [[stages]]에서 검사를 푸는 변경. 방향을 정할 수 없는 키 변경은 '바뀐 설정'으로 둔다"""
    out = []
    for part, rules in LOOSEN.items():
        o, n = old.get(part, {}), new.get(part, {})
        judged = set()
        for key in rules.get("less", []):
            judged.add(key)
            gone = sorted(_items(o.get(key)) - _items(n.get(key)))
            if gone:
                out.append(f"[{part}] {key}: {', '.join(gone)}가 빠진다")
        for key in rules.get("more", []):
            judged.add(key)
            added = sorted(_items(n.get(key)) - _items(o.get(key)))
            if added:
                out.append(f"[{part}] {key}: {', '.join(added)}가 늘어난다")
        for key in rules.get("up", []):
            judged.add(key)
            if isinstance(o.get(key), (int, float)) and isinstance(n.get(key), (int, float)) and n[key] > o[key]:
                out.append(f"[{part}] {key}: {o[key]}에서 {n[key]}로 커진다")
        for key in rules.get("cleared", []):
            judged.add(key)
            if o.get(key) and not n.get(key):
                out.append(f"[{part}] {key}: 값이 빠진다({o[key]})")
        if part == "review":
            judged |= {"model", "effort"}
            if _model_rank(n.get("model")) < _model_rank(o.get("model")):
                out.append(f"[review] model: {o.get('model') or '기본'}에서 {n.get('model')}로 낮아진다")
            oe, ne = o.get("effort"), n.get("effort")
            if oe in EFFORTS and (ne not in EFFORTS or EFFORTS.index(ne) < EFFORTS.index(oe)):
                out.append(f"[review] effort: {oe}에서 {ne or '지정 없음'}로 낮아진다")
        for key in sorted(set(o) | set(n)):
            if key not in judged and o.get(key) != n.get(key):
                out.append(f"[{part}] {key}: 바뀐 설정({o.get(key)!r}에서 {n.get(key)!r}). 검사를 푸는 변경인지 본다")
    ostages = {st["n"]: set(st.get("kinds", [])) for st in old.get("stages", [])}
    for st in new.get("stages", []):
        added = sorted(set(st.get("kinds", [])) - ostages.get(st["n"], set(st.get("kinds", []))))
        if added:
            out.append(f"[[stages]] {st['n']}단계: 허용 종류 {', '.join(added)}가 늘어난다")
        gone = [x for x in next((o.get("require", []) for o in old.get("stages", []) if o["n"] == st["n"]), [])
                if x not in st.get("require", [])]
        if gone:
            out.append(f"[[stages]] {st['n']}단계: 시작 조건(require) {', '.join(x.get('check', '?') for x in gone)}가 빠지거나 바뀐다")
    return out


REVIEW = ("하네스 구조 변경 제안서 {path}를 검토한다. 지금 구조 .gate/config.toml, 목적 .gate/PROFILE.md(없으면 건너뛴다), "
          "진단과 검사 결과 {diag}를 읽는다. 다음을 찾는다. (1) 진단 파일의 '빼거나 푸는 변경' 항목 가운데 '바꾸는 것' 표에 "
          "근거가 없거나 근거가 진단 자료의 수치나 PROFILE과 맞지 않는 것 (2) PROFILE의 목적과 어긋나는 변경 (3) 근거 없이 "
          "넣은 새 상태나 경로 (4) '진행 중인 계획' 절이 재생 결과와 다른 것 (5) 같은 목록의 '바뀐 설정' 항목 가운데 "
          "실제로는 검사를 푸는데(검사 범위가 줄거나, 검토가 약해지거나, 막던 행동이 열림) '바꾸는 것' 표에 근거가 없는 것.")


def check_proposal(flow, hid, allow_review):
    """문제 목록. 검토 결과는 flow['proposals']에 남긴다"""
    path = proposal_path(hid)
    if not path.exists():
        return [f"{path.relative_to(ROOT).as_posix()}가 없다"]
    text = path.read_text(encoding="utf-8")
    s = sections(text)
    miss = []
    rows = [r for t in tables(s.get("바꾸는 것", [])) for r in t if r and all(r)]
    if not rows:
        miss.append("'바꾸는 것' 표에 칸이 모두 찬 행(바꾸는 것 | 근거)이 없다")
    if not body(s.get("진행 중인 계획", [])):
        miss.append("'진행 중인 계획' 절이 비었다")
    new, raw, bad, changes = proposed(hid, text)
    if new is None:
        return miss + bad
    miss += bad
    problems = lint(new)
    miss += [f"lint: {x}" for x in problems]
    cur = (GATE_DIR / "config.toml").read_text(encoding="utf-8")
    diff = "".join(difflib.unified_diff(cur.splitlines(True), raw.splitlines(True), "지금 config.toml", "새 구조"))
    diag = PROPOSALS / f"{hid}.diag.md"
    loose = [f"- {x}" for x in weakened(CFG, new)] or ["없음"]
    part_lines = ["## 부품 변경", "", *[f"- {c}" for c in changes or ["없음"]], ""] if PARTS.exists() else []
    diag.write_text("\n".join([diagnose(flow), "", *part_lines,
                               "## lint", "", *(problems or ["문제 없음"]), "", "## 빼거나 푸는 변경", "",
                               *loose, "", "## 지난 계획 재생", "",
                               *replay(flow, new), "", "## 지난 계획 검사 재생", "",
                               "끝난 계획의 계획 파일에 새 구조의 검사를 다시 돌린 결과다. 계획 파일만 보고 판정하는 검사("
                               + ", ".join(REPLAYABLE) + ")만 돌린다. 사고가 난 계획이 새로 막히면 그 검사가 사고를 잡는다는 "
                               "근거이고, 사고가 없던 계획이 새로 막히면 오탐 위험이다.", "",
                               *replay_checks(flow, new), "", "## 새 구조 경로", "", describe(new), "", mermaid(new),
                               "## diff", "",
                               "```diff", diff, "```", ""]), encoding="utf-8")
    if miss:
        return miss
    h = proposal_hash(hid, text)
    rec = flow.setdefault("proposals", {}).get(hid)
    if rec and rec["hash"] == h and rec["pass"]:
        return []
    if not allow_review:
        return [f"검토자 호출 1회(약 1분)가 필요하다. check-proposal {hid} --review로 다시 실행한다"]
    result, meta = call_reviewer(REVIEW.format(path=path.relative_to(ROOT).as_posix(),
                                               diag=diag.relative_to(ROOT).as_posix()), CFG["review"].get("model"))
    flow["proposals"][hid] = {"hash": h, "pass": bool(result["pass"]), "missing": result["missing"], "at": now(), **meta}
    return [] if result["pass"] else [f"검토: {m}" for m in result["missing"]] or ["검토: FAIL"]


def adopt(flow, hid, approved):
    path = proposal_path(hid)
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT).as_posix()}가 없다")
    text = path.read_text(encoding="utf-8")
    rec = flow.get("proposals", {}).get(hid)
    h = proposal_hash(hid, text)
    if not rec or not rec["pass"] or rec["hash"] != h:
        raise SystemExit(f"{hid}가 check-proposal 검토를 통과하지 않았거나 검토 뒤 바뀌었다. check-proposal {hid} --review를 먼저 한다")
    if rec.get("adopted"):
        raise SystemExit(f"{hid}는 이미 적용했다")
    user = CFG["project"].get("approval") == "user"
    if user and not (approved or "").strip():
        raise SystemExit("승인 방식이 user라 --approved에 사용자가 이번 턴에 승인한 문장을 그대로 넣는다")
    new, raw, bad, _ = proposed(hid, text)
    if new is None or bad or lint(new):
        raise SystemExit("새 구조의 version이나 lint가 맞지 않다. check-proposal을 다시 한다")
    hist = GATE_DIR / "history"
    hist.mkdir(exist_ok=True)
    shutil.copy2(GATE_DIR / "config.toml", hist / f"v{CFG['version']}.toml")
    (GATE_DIR / "config.toml").write_text(raw, encoding="utf-8")
    flow["harness"] = {"version": new["version"], "adopted": hid, "at": now(), "ts": time.time(), "commit": head(),
                       "stage": flow.get("stage")}
    if PARTS.exists():
        d, old = PROPOSALS / hid, PT.read_lock(GATE_DIR)
        shutil.rmtree(PARTS)
        shutil.copytree(d / "parts", PARTS)
        if (d / "local.toml").exists():
            shutil.copy2(d / "local.toml", GATE_DIR / "local.toml")
        used = PT.load_parts(PARTS)
        PT.write_lock(GATE_DIR, used, old)
        flow["harness"]["lock"] = PT.lock_hash(used)
    rec.update({"adopted": True, "approved": " ".join((approved or "").split()) if user else "자동(check-proposal 통과)",
                "adopted_at": now()})
    return new["version"]


def review_due(flow):
    """하네스 점검을 권할 이유 목록"""
    hz = flow.get("harness", {})
    since = hz.get("ts", 0)
    why = []
    done = sum(1 for p in records.all_plans(flow).values()
               if p["state"] == "DONE" and p["entered"]["DONE"]["ts"] >= since)
    if done >= CFG["project"]["review_every_plans"]:
        why.append(f"마지막 구조 변경 뒤 계획 {done}개가 끝났다")
    ev = sum(1 for e in read_events(since) if e["kind"] in ("guard", "stop"))
    if ev >= CFG["project"]["review_every_events"]:
        why.append(f"마지막 구조 변경 뒤 hook이 {ev}번 막았다")
    if CFG["stages"] and hz.get("stage") != flow.get("stage"):
        why.append(f"프로젝트 단계가 {hz.get('stage')}에서 {flow.get('stage')}로 바뀌었다")
    if not (GATE_DIR / "PROFILE.md").exists():
        why.append("PROFILE.md(프로젝트 목적)가 없다")
    return why
