"""기록의 수명: 닫힌 계획 보관, 계획 색인, 현재 상태 문서의 필드와 항목, 정리 점검 자료.

기록은 쌓이되 에이전트와 엔진이 한 번에 읽는 양은 늘지 않게 한다. state.json에는 진행 중인 계획과 최근에 닫힌
계획만 두고 나머지는 state-archive.jsonl로 옮긴다. 계획 파일은 자리를 바꾸지 않고 색인에 한 줄씩 남긴다.
"""
import json
import re

from .core import CFG, CLOSED, GATE_DIR, P, ROOT, bullets, sections

ARCHIVE = GATE_DIR / "state-archive.jsonl"
DEFAULTS = {
    "current_max": 10000,   # 현재 상태 문서(status_file) 글자 수 상한
    "archive_file": "",     # 현재 상태 문서에서 닫힌 항목을 옮기는 문서
    "index": "",            # 계획 색인 파일. 비우면 만들지 않는다
    "index_items": [],      # 색인에 옮길 계획 파일 항목. "절: 항목" 꼴
    "ledgers": [],          # 덧붙이기만 하는 기록(실험 기록, 결정 문서)
    "item_id": "",          # 현재 상태 문서 항목의 고정 ID 정규식(예: "\\[(S\\d{3})\\]")
    "keep_closed": 10,      # state.json에 남겨 둘 닫힌 계획 수. 근거 없이 정한 값
    "events_keep": 500,     # 막힌 기록에 남겨 둘 줄 수. 넘으면 성적으로 접는다. 근거 없이 정한 값
    "cleanup_every": 10,    # 마지막 정리 뒤 이만큼 계획이 닫히면 정리를 권한다. 근거 없이 정한 값
}
REC = DEFAULTS | CFG.get("records", {})


def archived():
    """보관한 닫힌 계획. 번호 → 계획 기록"""
    if not ARCHIVE.exists():
        return {}
    out = {}
    for line in ARCHIVE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out[d.pop("id")] = d
    return out


def all_plans(flow):
    return archived() | flow["plans"]


def closed_at(p):
    return p["entered"][p["state"]]["ts"]


def settle(flow):
    """닫힌 계획 가운데 오래된 것을 보관 파일로 옮기고 색인을 다시 쓴다. 옮긴 번호 목록"""
    closed = sorted((pid for pid, p in flow["plans"].items() if p["state"] in CLOSED),
                    key=lambda pid: closed_at(flow["plans"][pid]))
    moved = closed[:max(0, len(closed) - REC["keep_closed"])]
    if moved:
        with open(ARCHIVE, "a", encoding="utf-8", newline="\n") as f:
            for pid in moved:
                f.write(json.dumps({"id": pid} | flow["plans"].pop(pid), ensure_ascii=False) + "\n")
    write_index(flow)
    return moved


def write_index(flow):
    """닫힌 계획마다 한 줄. 계획 파일 경로는 바꾸지 않는다"""
    if not REC["index"]:
        return
    items = [tuple(x.strip() for x in s.split(":", 1)) for s in REC["index_items"]]
    rows = ["# 계획 색인", "", "| 계획 | 제목 | 종류 | 결과 | 닫은 때 | " + " | ".join(i for _, i in items) + (" |" if items else ""),
            "|---|---|---|---|---|" + "---|" * len(items)]
    for pid, p in sorted(all_plans(flow).items()):
        if p["state"] not in CLOSED:
            continue
        f = ROOT / p["plan"]
        s = sections(f.read_text(encoding="utf-8")) if f.exists() else {}
        cells = [bullets(s.get(sec, [])).get(item, "").replace("|", "/") for sec, item in items]
        rows.append(f"| {pid} | {p['title'].replace('|', '/')} | {p['kind']} | {'끝남' if p['state'] == 'DONE' else '그만둠'} | "
                    f"{p['entered'][p['state']]['at'][:10]} | " + " | ".join(cells) + (" |" if items else ""))
    out = ROOT / REC["index"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


# 현재 상태 문서

def fields(text):
    """문서 머리의 필드. 첫 줄이 ---면 다음 ---까지의 '이름: 값' 줄"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        k, sep, v = line.partition(":")
        if sep:
            out[k.strip()] = v.strip()
    return {}


def read(rel):
    f = ROOT / rel if rel else None
    return f.read_text(encoding="utf-8") if f and f.exists() else ""


def item_ids(text):
    return set(re.findall(REC["item_id"], text)) if REC["item_id"] else set()


def cited(text, cfg=None):
    """문서가 인용한 참조: 계획 번호와 [review] cite 패턴에 맞는 것"""
    pats = [re.escape(P["plan_prefix"]) + r"\d{3}"] + list((cfg or CFG)["review"].get("cite", []))
    return {m.group(0) for pat in pats for m in re.finditer(rf"(?<![A-Za-z0-9]){pat}(?![A-Za-z0-9])", text)}


def resolves(ref, flow, ledger_text):
    """참조가 풀리는가. 계획 번호는 계획 기록이나 계획 파일이 있으면, 그 밖의 참조는 ledger 문서에 있으면 풀린다"""
    if re.fullmatch(re.escape(P["plan_prefix"]) + r"\d{3}", ref):
        return ref in all_plans(flow) or (ROOT / P["plans_dir"] / f"{ref}.md").exists()
    return ref in ledger_text


def survey(flow):
    """정리 전후에 견줄 자료: 문서 크기, 항목 ID, 현재 상태 문서와 보관 문서가 인용한 참조 가운데 풀리는 것"""
    cur, arc = read(P["status_file"]), read(REC["archive_file"])
    ledger_text = "\n".join(read(x) for x in REC["ledgers"])
    refs = sorted(r for r in cited(cur + "\n" + arc) if resolves(r, flow, ledger_text))
    state = ROOT / P["state_file"]
    return {"current_chars": len(cur), "archive_chars": len(arc), "state_bytes": state.stat().st_size if state.exists() else 0,
            "open_plans": sum(p["state"] not in CLOSED for p in flow["plans"].values()),
            "kept_closed": sum(p["state"] in CLOSED for p in flow["plans"].values()), "archived": len(archived()),
            "items": sorted(item_ids(cur)), "refs": refs}


def report(flow):
    s = survey(flow)
    lines = [f"- 현재 상태 문서 {P['status_file'] or '(없음)'}: {s['current_chars']}자(상한 {REC['current_max']}자), "
             f"항목 ID {len(s['items'])}개",
             f"- 보관 문서 {REC['archive_file'] or '(없음)'}: {s['archive_chars']}자",
             f"- 상태 파일: {s['state_bytes']}바이트, 진행 중인 계획 {s['open_plans']}개, 남겨 둔 닫힌 계획 {s['kept_closed']}개, "
             f"보관한 계획 {s['archived']}개",
             f"- 풀리는 참조: {len(s['refs'])}개"]
    return lines + [f"- 정리를 권하는 이유: {'; '.join(due(flow)) or '없음'}"]


def due(flow):
    """기록 정리를 권할 이유"""
    why = []
    n = len(read(P["status_file"]))
    if n > REC["current_max"]:
        why.append(f"{P['status_file']}가 {n}자다(상한 {REC['current_max']}자)")
    last = max((closed_at(p) for p in all_plans(flow).values() if p["state"] == "DONE" and p.get("survey")), default=0)
    closed = sum(1 for p in all_plans(flow).values() if p["state"] in CLOSED and closed_at(p) > last)
    if P["status_file"] and closed >= REC["cleanup_every"]:
        why.append(f"마지막 정리 뒤 계획 {closed}개가 닫혔다")
    return why
