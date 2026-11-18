"""임시 git 저장소에 하네스를 설치해 명령과 hook을 실제로 돌려 본다.

저장소 루트에서 python -X utf8 -m unittest discover tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "dynamic-stagegate-harness"
COMBOS = Path(__file__).resolve().parent / "combos"
sys.path.insert(0, str(PLUGIN))
from gatekit import parts as PT  # noqa: E402
PY = [sys.executable, "-X", "utf8"]
ENV = {k: v for k, v in os.environ.items() if k != "GATE_REVIEW"} | {"PYTHONDONTWRITEBYTECODE": "1"}
# 실제로 돌아 본 조합. 이 답이면 tests/combos/<이름>.toml 구조가 조립되어야 한다
ANSWERS = {
    "ml": {"work": "experiment", "run": "remote", "ships": "yes", "failure_cost": "time"},
    "service": {"work": "service", "tests": "yes"},
    "agent": {"work": "agent"},
}
# 검토자 대신 쓰는 claude. 받은 지시문과 옵션을 남기고 PASS를 돌려준다
FAKE_CLAUDE = '''import json, os, sys
prompt = sys.stdin.buffer.read().decode("utf-8")
with open(os.environ["GATE_FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"prompt": prompt, "args": sys.argv[1:]}) + "\\n")
print(json.dumps({"structured_output": {"pass": True, "missing": []}, "session_id": "s", "total_cost_usd": 0.5}))
'''


def run(cmd, cwd, stdin=None, env=ENV):
    return subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, encoding="utf-8", env=env)


class Repo(unittest.TestCase):
    """테스트마다 새 저장소. service 조합을 설치하고 chore에 quick 선택과 실행 상태를 더한다"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "src").mkdir()
        self.git("init", "-q", ".")
        for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.autocrlf", "false")):
            self.git("config", k, v)
        self.write("README.md", "x\n")
        self.git("add", ".")
        self.git("commit", "-qm", "init")

    def git(self, *args):
        return run(["git", *args], self.root)

    def gate(self, *args):
        return run([*PY, ".gate/gate.py", *args], self.root, env=getattr(self, "env", ENV))

    def fake_reviewer(self):
        """PATH 앞에 가짜 claude를 둔다. 받은 지시문은 self.prompts(), 옵션은 self.calls()로 읽는다"""
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "fake.py").write_text(FAKE_CLAUDE, encoding="utf-8")
        if os.name == "nt":
            (d / "claude.bat").write_text(f'@"{sys.executable}" "{d / "fake.py"}" %*\n', encoding="utf-8")
        else:
            (d / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{d / "fake.py"}" "$@"\n', encoding="utf-8")
            (d / "claude").chmod(0o755)
        self.log = d / "prompts.jsonl"
        self.env = ENV | {"PATH": str(d) + os.pathsep + ENV.get("PATH", ""), "GATE_FAKE_LOG": str(self.log)}

    def calls(self):
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def prompts(self):
        return [x["prompt"] for x in self.calls()]

    def install(self, *args, target=None):
        if args and args[0] in ANSWERS:
            args = [x for k, v in ANSWERS[args[0]].items() for x in ("--answer", f"{k}={v}")] + list(args[1:])
        return run([*PY, str(PLUGIN / "scripts" / "install.py"), *args, "--target", str(target or self.root)], self.root)

    def write(self, rel, text):
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")

    def edit(self, rel, *pairs):
        text = (self.root / rel).read_text(encoding="utf-8")
        for old, new in pairs:
            self.assertIn(old, text)
            text = text.replace(old, new)
        self.write(rel, text)

    def setup_harness(self):
        r = self.install("service")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.edit(".gate/config.toml",
                  ('[kinds.chore]\nstates = ["IMPLEMENT", "RECORD"]',
                   '[kinds.chore]\nstates = ["IMPLEMENT", "RECORD"]\noptions = ["quick"]\n'
                   'when = {IMPLEMENT = {not = {option = "quick"}}}'),
                  ("[states.IMPLEMENT]\ncode = true", "[states.IMPLEMENT]\ncode = true\nrun = true"))
        self.git("add", ".gate", ".githooks")
        self.git("reset", "-q", ".gate/state.json")
        r = self.git("commit", "-qm", "harness: install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.git("add", ".gate/state.json")
        self.git("commit", "-qm", "chore: state")

    def guard(self, tool, **tool_input):
        """플러그인의 hook 진입점을 부른다. 막으면 이유, 통과하면 None"""
        r = run([*PY, str(PLUGIN / "hooks" / "run.py"), "guard"], self.root,
                json.dumps({"tool_name": tool, "tool_input": tool_input}),
                ENV | {"CLAUDE_PROJECT_DIR": str(self.root)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"] if r.stdout.strip() else None


class InstallTest(Repo):
    def test_하위_폴더에는_설치하지_않는다(self):
        (self.root / "sub").mkdir()
        r = self.install("service", target=self.root / "sub")
        self.assertIn("저장소의 루트가 아니다", r.stderr)
        self.assertEqual(list((self.root / "sub").iterdir()), [])

    def test_다른_git_hook이_있으면_복사하기_전에_멈춘다(self):
        self.write(".githooks/pre-commit", "#!/bin/sh\n")
        r = self.install("service")
        self.assertIn("stagegate 하네스 hook이 아니다", r.stderr)
        self.assertFalse((self.root / ".gate").exists())

    def test_설치하지_않은_프로젝트에서는_hook이_아무것도_하지_않는다(self):
        self.assertIsNone(self.guard("Write", file_path=str(self.root / "src" / "a.py")))

    def test_답으로_부품을_골라_조립하고_lock을_남긴다(self):
        r = self.install("service")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("  test-first: ", r.stdout.split("뺀 부품")[0])
        self.assertIn("  run-record: ", r.stdout.split("뺀 부품")[1])
        gate = self.root / ".gate"
        self.assertEqual(sorted(p.name for p in (gate / "parts").iterdir()),
                         ["bugfix", "chore", "diff-review", "feature", "test-first"])
        self.assertEqual((gate / "config.toml").read_text(encoding="utf-8"),
                         (COMBOS / "service.toml").read_text(encoding="utf-8"))
        self.assertIn('work = "service"', (gate / "local.toml").read_text(encoding="utf-8"))
        lock = json.loads((gate / "parts.lock").read_text(encoding="utf-8"))
        self.assertEqual(lock["feature"]["from"], lock["feature"]["hash"])
        self.assertEqual(self.gate("lint").returncode, 0, self.gate("lint").stdout)
        self.assertIn("- test-first v1 (library)", self.gate("parts").stdout)

    def test_dry_run은_쓰지_않고_함께_조립되지_않는_부품이면_멈춘다(self):
        r = self.install("--answer", "work=service", "--dry-run")
        self.assertIn("  feature: ", r.stdout)
        self.assertFalse((self.root / ".gate").exists())
        r = self.install("--answer", "work=experiment,service")
        self.assertIn("[states.PLAN]: experiment와 feature가 다르게 정의한다", r.stderr)
        self.assertFalse((self.root / ".gate").exists())

    def test_local_파일의_덮어쓰기가_조립_결과에_들어간다(self):
        self.write("in.toml", '[answers]\nwork = "agent"\n\n[project]\ncode = ["bot/*"]\n\n[states.RECORD]\ntodo = ["x"]\n')
        self.assertEqual(self.install("--local", "in.toml").returncode, 0)
        cfg = PT.tomllib.loads((self.root / ".gate" / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["project"]["code"], ["bot/*"])
        self.assertEqual(cfg["states"]["RECORD"]["todo"], ["x"])
        self.assertIn("checks", cfg["states"]["RECORD"])
        self.assertEqual(self.gate("lint").returncode, 0, self.gate("lint").stdout)

    def test_config를_직접_고치면_lint가_조립_결과와_다르다고_알린다(self):
        self.install("service")
        self.edit(".gate/config.toml", ("max_visits = 3", "max_visits = 9"))
        self.assertIn("config.toml이 부품과 local.toml을 조립한 결과와 다르다", self.gate("lint").stdout)

    def test_parts는_조건이_바뀐_부품을_알린다(self):
        self.install("--answer", "work=service")
        out = self.gate("parts").stdout
        self.assertIn("- test-first: ", out.split("쓰지 않는 라이브러리 부품")[1])
        self.assertNotIn("조건이 맞는다", out)
        self.edit(".gate/local.toml", ('work = "service"', 'work = "agent"\ntests = "yes"'))
        out = self.gate("parts").stdout
        self.assertIn("- feature v1 (library): 기능 경로(feature, refactor). 명세, 계획, 구현, 테스트, 기록으로 간다 "
                      "[조건이 거짓이 됐다(답 work = service). 제거 제안서를 검토한다]", out)
        self.assertIn("(조건: 답 work = agent) [조건이 맞는다. 추가 제안서를 검토한다]", out)

    def test_update는_config를_그대로_둔다(self):
        self.setup_harness()
        before = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        r = self.install("--update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.root / ".gate" / "config.toml").read_text(encoding="utf-8"), before)


class LintTest(Repo):
    def test_조합의_답으로_조립하면_기대_구조가_나오고_lint를_통과한다(self):
        self.setup_harness()
        for name, answers in ANSWERS.items():
            on, _ = PT.select(PT.load_parts(PLUGIN / "library"), self.root, answers)
            cfg, bad = PT.assemble(on)
            self.assertEqual(bad, [], name)
            text = PT.dumps(cfg, PT.HEAD)
            self.assertEqual(PT.tomllib.loads(text), cfg, name)
            self.assertEqual(text, (COMBOS / f"{name}.toml").read_text(encoding="utf-8"), name)
            r = self.gate("lint", "--file", str(COMBOS / f"{name}.toml"))
            self.assertEqual(r.returncode, 0, name + "\n" + r.stdout)

    def test_조립은_빠진_부품과_붙을_자리가_없는_부품을_문제로_낸다(self):
        lib = {p["name"]: p for p in PT.load_parts(PLUGIN / "library")}
        _, bad = PT.assemble([lib["test-first"], lib["bugfix"]])
        self.assertIn("test-first: 필요한 부품 feature이 없다", bad)
        self.assertIn("test-first: TESTFIRST를 넣을 자리(역할 implement)가 어느 종류에도 없다", bad)
        self.assertIn("test-first: attach 1번이 붙을 상태가 없다(역할 [], 상태 ['TEST'])", bad)
        cfg, bad = PT.assemble([lib["eval"], lib["bugfix"], lib["chore"], lib["diff-review"], lib["run-record"]])
        self.assertEqual(bad, [])
        self.assertEqual(cfg["kinds"]["behavior"]["states"][-4:], ["EVAL", "REVIEW", "RECORD", "REPLAN"])
        self.assertEqual(cfg["kinds"]["behavior"]["back"]["REVIEW"], ["IMPLEMENT", "EVALSET", "SPEC"])
        self.assertEqual(cfg["states"]["EVAL"]["checks"][0], {"check": "run_record", "part": "run-record"})

    def test_검토자_추론_단계는_claude가_받는_값만_쓴다(self):
        self.setup_harness()
        text = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        self.write("bad.toml", text.replace('effort = "high"', 'effort = "highest"', 1))
        r = self.gate("lint", "--file", "bad.toml")
        self.assertEqual(r.returncode, 1)
        self.assertIn("[review] effort는 low, medium, high, xhigh, max 중 하나다(지금 highest)", r.stdout)

    def test_다른_종류에_오류가_있어도_검사의_빠진_키를_잡는다(self):
        self.setup_harness()
        text = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        text = text.replace("[kinds.chore]", '[kinds.broken]\nstates = ["NOPE"]\n\n[kinds.chore]')
        text = text.replace('{check = "filled", section = "범위 밖", part = "feature"},',
                            '{check = "filled"},\n  {check = "commit_since"},')
        self.write("bad.toml", text)
        r = self.gate("lint", "--file", "bad.toml")
        self.assertEqual(r.returncode, 1)
        self.assertIn("broken: 상태 NOPE가 [states]에 없다", r.stdout)
        self.assertIn("feature/SPEC: filled에 필요한 키가 없다: section", r.stdout)
        self.assertIn("feature/SPEC: commit_since에 필요한 키가 없다: path", r.stdout)

    def test_보호_파일_목록은_줄일_수_없다(self):
        self.setup_harness()
        text = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        self.write("bad.toml", text.replace("[project]", '[project]\nprotected = [".gate/config.toml"]', 1))
        r = self.gate("lint", "--file", "bad.toml")
        self.assertIn("protected에서 .gate/gatekit/를 뺄 수 없다", r.stdout)

    def test_back과_unchanged는_앞_상태만_가리킨다(self):
        self.setup_harness()
        text = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        text = text.replace('back = {VERIFY = ["FIX", "REPRO"], REVIEW = ["FIX", "REPRO"]}', 'back = {FIX = ["VERIFY"]}')
        text = text.replace('{check = "unchanged", state = "REPRO",', '{check = "unchanged", state = "VERIFY",')
        self.write("bad.toml", text)
        r = self.gate("lint", "--file", "bad.toml")
        self.assertIn("bugfix.back.FIX: VERIFY가 FIX보다 앞 상태가 아니다", r.stdout)
        self.assertIn("bugfix/FIX: unchanged가 가리키는 VERIFY가 이 종류에서 FIX보다 앞 상태가 아니다", r.stdout)

    def test_review의_자리와_command의_use와_테스트_패턴을_본다(self):
        self.setup_harness()
        text = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        text = text.replace("{plan}과 계획을", "{plan}과 {nope} 계획을").replace('use = "affected"', 'use = "bogus"')
        self.write("bad.toml", text.replace("\ntests = [", "\ntests_off = [", 1))
        r = self.gate("lint", "--file", "bad.toml")
        self.assertIn("REVIEW: review의 {nope}는 쓸 수 없는 자리다", r.stdout)
        self.assertIn("chore/IMPLEMENT: command의 use는 test, affected 중 하나다(지금 bogus)", r.stdout)
        self.assertIn("feature/TEST: test_edits를 쓰는데 [project] tests가 비었다", r.stdout)

    def test_edit는_코드_상태에만_쓰고_covers는_절을_가리킨다(self):
        self.setup_harness()
        text = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        text = text.replace('[states.TEST]\n', '[states.TEST]\nedit = "tests"\n').replace('from = "수용 기준"', 'from = "없는 절"')
        self.write("bad.toml", text)
        r = self.gate("lint", "--file", "bad.toml")
        self.assertIn('TEST: edit = "tests"는 code = true인 상태에 쓰고', r.stdout)
        self.assertIn("feature/TEST: covers이 가리키는 절 '없는 절'이 계획 틀에 없다", r.stdout)


class FlowTest(Repo):
    def setUp(self):
        super().setUp()
        self.setup_harness()

    def fill_chore(self, pid):
        self.edit(f"plans/{pid}.md",
                  ("| 파일 | 바꿀 내용 |\n|---|---|", "| 파일 | 바꿀 내용 |\n|---|---|\n| src/a.py | 새 파일 |"),
                  ("- 바뀐 동작:", "- 바뀐 동작: a"), ("- 문서 영향:", "- 문서 영향: 없음"),
                  ("- 남은 일:", "- 남은 일: 없음"))

    def test_제목에_역슬래시가_있어도_시작한다(self):
        r = self.gate("start", "--kind", "chore", "--title", r"fix C:\path \1")
        self.assertEqual(r.returncode, 0, r.stderr)
        first = (self.root / "plans" / "P001.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first, r"# P001 fix C:\path \1")

    def test_run은_없는_시각을_받지_않는다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.assertNotEqual(self.gate("run", "P001", "--eta", "99:99", "--basis", "b").returncode, 0)
        self.assertEqual(self.gate("run", "P001", "--eta", "23:59", "--basis", "b").returncode, 0)

    def test_run은_실행_기록을_상태_파일만_바로_커밋한다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.write("docs/other.md", "x\n")
        r = self.gate("run", "P001", "--eta", "23:59", "--basis", "b")
        self.assertIn(".gate/state.json 커밋", r.stdout, r.stdout + r.stderr)
        self.assertEqual(self.git("status", "--porcelain", ".gate/state.json").stdout, "")
        self.assertIn("docs: P001 실행 기록", self.git("log", "-1", "--format=%s").stdout)
        self.assertEqual(self.git("show", "--name-only", "--format=", "HEAD").stdout.split(), [".gate/state.json"])

    def test_조건을_채우지_못하면_머문다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        r = self.gate("next", "P001")
        self.assertEqual(r.returncode, 1)
        self.assertIn("P001 IMPLEMENT에서 넘어가지 못했다", r.stdout)

    def test_chore는_코드를_커밋하고_기록을_채우면_끝난다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.write("src/a.py", "a = 2\n")
        self.fill_chore("P001")
        self.git("add", "src/a.py", "plans")
        r = self.git("commit", "-qm", "feat: a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("P001 t: RECORD", self.gate("next", "P001").stdout)
        self.assertIn("모든 상태를 지났다", self.gate("next", "P001").stdout)

    def test_pre_commit은_계획에_없는_코드_파일을_막는다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.fill_chore("P001")
        self.write("src/other.py", "b = 2\n")
        self.git("add", "src/other.py")
        r = self.git("commit", "-qm", "feat: other")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("src/other.py", r.stdout + r.stderr)

    def test_변경_파일_표의_설명_칸에_적은_경로는_고칠_파일로_치지_않는다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.fill_chore("P001")
        self.edit("plans/P001.md", ("| src/a.py | 새 파일 |", "| src/a.py | src/other.py와 같은 형식 |"))
        self.write("src/other.py", "b = 2\n")
        self.git("add", "src/other.py")
        r = self.git("commit", "-qm", "feat: other")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("src/other.py", r.stdout + r.stderr)

    def test_commit_msg는_하네스_커밋을_따로_두게_한다(self):
        self.write(".gate/GATE.md", "x\n")
        self.git("add", ".gate/GATE.md")
        self.assertNotEqual(self.git("commit", "-qm", "docs: gate").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "harness: gate 문서").returncode, 0)

    def test_건너뛴_상태는_들어온_횟수에_넣지_않는다(self):
        r = self.gate("start", "--kind", "chore", "--title", "t", "--with", "quick")
        self.assertIn("P001 t: RECORD", r.stdout)
        rows = [line for line in self.gate("diagnose").stdout.splitlines() if line.startswith("| chore | IMPLEMENT")]
        self.assertEqual(rows, ["| chore | IMPLEMENT | 0 | 0 | 1 | 0 |  |  |  |"])

    def test_status는_경로에_지금_위치와_회차를_표시한다(self):
        self.gate("start", "--kind", "chore", "--title", "q", "--with", "quick")
        self.assertIn("  IMPLEMENT 건너뜀 → [RECORD] → DONE", self.gate("status").stdout)
        self.gate("start", "--kind", "chore", "--title", "t")
        self.assertIn("  [IMPLEMENT] → RECORD → DONE", self.gate("status").stdout)
        self.assertEqual(self.gate("status", "--line").stdout.strip(), "gate P001 RECORD 2/2, P002 IMPLEMENT 1/2")
        self.write("src/a.py", "a = 2\n")
        self.fill_chore("P002")
        self.git("add", "src/a.py", "plans")
        self.git("commit", "-qm", "feat: a")
        self.gate("next", "P002")
        self.assertIn("  IMPLEMENT ✓ → [RECORD] → DONE", self.gate("status").stdout)
        self.gate("back", "P002", "IMPLEMENT", "--why", "w", "--defect", "plan")
        self.assertIn("  [IMPLEMENT 2회차] → RECORD → DONE", self.gate("status").stdout)

    def test_back은_max_visits에서_멈춘다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.write("src/a.py", "a = 2\n")
        self.fill_chore("P001")
        self.git("add", "src/a.py", "plans")
        self.git("commit", "-qm", "feat: a")
        for _ in range(2):
            self.assertEqual(self.gate("next", "P001").returncode, 0)
            self.assertEqual(self.gate("back", "P001", "IMPLEMENT", "--why", "w", "--defect", "plan").returncode, 0)
        self.assertEqual(self.gate("next", "P001").returncode, 0)
        r = self.gate("back", "P001", "IMPLEMENT", "--why", "w", "--defect", "plan")
        self.assertIn("3번 들어왔다", r.stderr)

    def test_종류의_상한을_벗어나면_커밋과_next를_막는다(self):
        self.edit(".gate/config.toml", ("limit = {max_files = 5}", "limit = {max_files = 1}"))
        self.git("add", ".gate/config.toml")
        self.assertEqual(self.git("commit", "-qm", "harness: 상한").returncode, 0)
        self.assertIn("상한: max_files=1", self.gate("describe").stdout)
        self.gate("start", "--kind", "chore", "--title", "t")
        self.fill_chore("P001")
        self.edit("plans/P001.md", ("| src/a.py | 새 파일 |", "| src/a.py | 새 파일 |\n| src/b.py | 새 파일 |"))
        self.write("src/a.py", "a = 2\n")
        self.git("add", "src/a.py")
        r = self.git("commit", "-qm", "feat: a")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("chore 종류의 상한(max_files=1)을 벗어났다", r.stdout + r.stderr)
        r = self.gate("next", "P001")
        self.assertEqual(r.returncode, 1)
        self.assertIn("chore 종류의 상한(max_files=1)을 벗어났다", r.stdout)

    def bugfix_to_fix(self):
        """bugfix 계획을 REPRO에서 넘겨 FIX에 두고, 고친 코드까지 커밋한다"""
        self.gate("start", "--kind", "bugfix", "--title", "b")
        self.edit("plans/P001.md", ("- 증상:", "- 증상: 표시가 없다"),
                  ("- 재현 명령:", "- 재현 명령: git grep -q FIXED -- src/a.py"),
                  ("| 파일 | 바꿀 내용 |\n|---|---|", "| 파일 | 바꿀 내용 |\n|---|---|\n| src/a.py | 표시를 더함 |"))
        self.git("add", "plans")
        self.assertEqual(self.git("commit", "-qm", "test: 재현").returncode, 0)
        self.assertIn("P001 b: FIX", self.gate("next", "P001").stdout)
        self.write("src/a.py", "a = 2  # FIXED\n")
        self.edit("plans/P001.md", ("## 원인\n", "## 원인\n\n표시가 빠져 있었다.\n"))
        self.git("add", "src/a.py", "plans")
        self.assertEqual(self.git("commit", "-qm", "fix: 표시").returncode, 0)

    def test_넘긴_뒤_재현_명령을_바꾸면_막는다(self):
        self.bugfix_to_fix()
        self.edit("plans/P001.md", ("git grep -q FIXED -- src/a.py", "git status"))
        r = self.gate("next", "P001")
        self.assertEqual(r.returncode, 1)
        self.assertIn("REPRO를 넘긴 뒤 '재현: 재현 명령'이 바뀌었다", r.stdout)
        self.edit("plans/P001.md", ("- 재현 명령: git status", "- 재현 명령: git grep -q FIXED -- src/a.py"))
        self.assertIn("P001 b: VERIFY", self.gate("next", "P001").stdout)

    def test_back은_적어_둔_경로로만_가고_진단에_경로별로_남는다(self):
        self.edit(".gate/config.toml", ('back = {VERIFY = ["FIX", "REPRO"], REVIEW', 'back = {VERIFY = ["FIX"], REVIEW'))
        self.git("add", ".gate/config.toml")
        self.assertEqual(self.git("commit", "-qm", "harness: back 경로").returncode, 0)
        self.assertIn("VERIFY -.-> FIX", self.gate("describe", "--mermaid").stdout)
        self.bugfix_to_fix()
        self.assertEqual(self.gate("next", "P001").returncode, 0)
        r = self.gate("back", "P001", "REPRO", "--why", "w", "--defect", "plan")
        self.assertIn("VERIFY에서는 FIX로만 돌아갈 수 있다", r.stderr)
        self.assertEqual(self.gate("back", "P001", "FIX", "--why", "덜 고침", "--defect", "plan").returncode, 0)
        self.assertIn("| bugfix | VERIFY | FIX | 1 | 덜 고침 |", self.gate("diagnose").stdout)

    def test_실패해야_하는_명령이_테스트를_돌리지_못하면_막는다(self):
        self.gate("start", "--kind", "bugfix", "--title", "b")
        self.edit("plans/P001.md", ("- 증상:", "- 증상: x"), ("- 재현 명령:", "- 재현 명령: gate_no_such_cmd_xyz --run"))
        self.assertIn("gate_no_such_cmd_xyz를 찾지 못했다", self.gate("next", "P001").stdout)
        self.edit("plans/P001.md", ("gate_no_such_cmd_xyz --run", f'"{sys.executable}" -c "raise SystemExit(5)"'))
        self.assertIn("테스트를 돌리지 못한 것으로 보인다(종료 코드 5)", self.gate("next", "P001").stdout)

    def runx(self):
        """chore를 IMPLEMENT, RUNX(실행 상태), RECORD로 바꾸고 RUNX에서 코드와 IMPLEMENT의 변경 파일 절이 그대로인지 본다"""
        self.edit(".gate/config.toml",
                  ('[kinds.chore]\nstates = ["IMPLEMENT", "RECORD"]',
                   '[kinds.chore]\nstates = ["IMPLEMENT", "RUNX", "RECORD"]\nback = {RUNX = ["IMPLEMENT"]}'),
                  ("[states.RECORD]", '[states.RUNX]\nrun = true\nchecks = [\n  {check = "code_unchanged"},\n'
                   '  {check = "unchanged", state = "IMPLEMENT", sections = ["변경 파일"]},\n]\n\n[states.RECORD]'))
        self.git("add", ".gate/config.toml")
        self.assertEqual(self.git("commit", "-qm", "harness: runx").returncode, 0)
        self.gate("start", "--kind", "chore", "--title", "t")
        self.write("src/a.py", "a = 2\n")
        self.fill_chore("P001")
        self.git("add", "src/a.py", "plans")
        self.assertEqual(self.git("commit", "-qm", "feat: a").returncode, 0)
        self.assertIn("P001 t: RUNX", self.gate("next", "P001").stdout)

    def test_실행_상태에_들어온_뒤_계획의_코드가_바뀌면_막는다(self):
        self.runx()
        self.write("src/a.py", "a = 3\n")
        r = self.gate("next", "P001")
        self.assertIn("RUNX에 들어온 뒤 계획의 코드 파일 src/a.py가 바뀌었다", r.stdout)
        self.write("src/a.py", "a = 2\n")
        self.assertIn("P001 t: RECORD", self.gate("next", "P001").stdout)

    def test_gate_run_뒤에는_back으로_다시_넘겨도_unchanged_기준이_그대로다(self):
        self.runx()
        self.assertEqual(self.gate("back", "P001", "IMPLEMENT", "--why", "실행 전", "--defect", "plan").returncode, 0)
        self.edit("plans/P001.md", ("| src/a.py | 새 파일 |", "| src/a.py | 새 파일, 설명 고침 |"))
        self.assertIn("P001 t: RUNX", self.gate("next", "P001").stdout)
        self.assertEqual(self.gate("run", "P001", "--eta", "23:59", "--basis", "b").returncode, 0)
        self.assertEqual(self.gate("back", "P001", "IMPLEMENT", "--why", "결과를 봄", "--defect", "plan").returncode, 0)
        self.edit("plans/P001.md", ("| src/a.py | 새 파일, 설명 고침 |", "| src/a.py | 결과 뒤 고침 |"))
        self.assertIn("P001 t: RUNX", self.gate("next", "P001").stdout)
        r = self.gate("next", "P001")
        self.assertIn("gate run 뒤라 IMPLEMENT를 다시 넘겨도 기준이 그대로다", r.stdout)

    def test_SessionStart는_status_절의_항목_머리만_상한_안으로_넣는다(self):
        text = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        text = text.replace("[project]\n", '[project]\nstatus_file = "docs/status.md"\nstatus_sections = ["다음에 볼 것"]\n', 1)
        self.write(".gate/config.toml", text)
        self.git("add", ".gate/config.toml")
        self.assertEqual(self.git("commit", "-qm", "harness: status").returncode, 0)
        items = "\n".join(f"{i}. 항목 {i} " + "긴 설명 " * 200 for i in range(1, 60))
        self.write("docs/status.md", "# 상태\n\n## 다음에 볼 것\n\n" + items + "\n")
        r = run([*PY, str(PLUGIN / "hooks" / "run.py"), "session"], self.root, "{}",
                ENV | {"CLAUDE_PROJECT_DIR": str(self.root)})
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), 6200)
        self.assertIn("## 다음에 볼 것(머리)\n1. 항목 1 긴 설명", ctx)
        self.assertIn("…", ctx)

    def bump_structure(self, *pairs):
        """adopt를 거치지 않고 구조를 다음 버전으로 바꾼다(앞 버전은 history에 남긴다)"""
        cfg = self.root / ".gate" / "config.toml"
        (self.root / ".gate" / "history").mkdir(exist_ok=True)
        shutil.copy(cfg, self.root / ".gate" / "history" / "v1.toml")
        self.edit(".gate/config.toml", ("version = 1", "version = 2"), *pairs)

    def test_구조가_바뀌어도_진행_중인_계획은_시작할_때의_project_값을_쓴다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.fill_chore("P001")
        self.bump_structure(('code = ["src/*", "app/*",', 'code = ["app/*",'), ("max_visits = 3", "max_visits = 1"))
        # 새 구조에서는 src/가 코드가 아니지만, v1로 시작한 P001에서는 코드라 표에 없는 파일은 막는다
        self.assertIn("'변경 파일' 절에 없어", self.guard("Write", file_path=str(self.root / "src" / "other.py")))
        self.assertIsNone(self.guard("Write", file_path=str(self.root / "src" / "a.py")))
        self.write("src/other.py", "b = 2\n")
        self.git("add", "src/other.py")
        self.assertIn("src/other.py: 코드를 고치는 상태인 계획", self.gate("git-pre-commit").stdout)
        self.git("reset", "-q", "src/other.py")
        (self.root / "src" / "other.py").unlink()
        self.write("src/a.py", "a = 2\n")
        self.git("add", "src/a.py", "plans")
        r = self.git("commit", "-qm", "feat: a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("P001 t: RECORD", self.gate("next", "P001").stdout)  # v1의 files_section과 code로 파일을 찾는다
        self.gate("back", "P001", "IMPLEMENT", "--why", "w", "--defect", "plan")  # v1의 max_visits 3이라 한 번 더 들어올 수 있다
        self.assertIn("P001 t: IMPLEMENT", self.gate("status").stdout)

    def test_엔진이_바뀐_진행_중_계획을_알리고_update가_경고한다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.assertNotIn("엔진 바뀜", self.gate("status").stdout)
        with open(self.root / ".gate" / "gatekit" / "checks.py", "a", encoding="utf-8") as f:
            f.write("\n# 엔진 수정\n")
        self.assertIn("엔진 바뀜", self.gate("status").stdout)
        r = run([*PY, str(PLUGIN / "hooks" / "run.py"), "session"], self.root, "{}",
                ENV | {"CLAUDE_PROJECT_DIR": str(self.root)})
        self.assertIn("[엔진 변경] P001", r.stdout)
        r = self.install("--update")
        self.assertIn("경고: 진행 중인 계획 P001", r.stdout)
        self.assertNotIn("엔진 바뀜", self.gate("status").stdout)  # 시작할 때와 같은 엔진으로 돌아왔다

    def test_chore는_affected_명령에_계획의_파일을_넣어_돌린다(self):
        self.edit(".gate/config.toml", ('affected = ""', 'affected = "git grep -q zzz -- {files}"'))
        self.git("add", ".gate/config.toml")
        self.assertEqual(self.git("commit", "-qm", "harness: affected").returncode, 0)
        self.gate("start", "--kind", "chore", "--title", "t")
        self.write("src/a.py", "a = 2\n")
        self.fill_chore("P001")
        self.git("add", "src/a.py", "plans")
        self.git("commit", "-qm", "feat: a")
        r = self.gate("next", "P001")
        self.assertEqual(r.returncode, 1)
        self.assertIn("`git grep -q zzz -- src/a.py`가 통과해야 하는데", r.stdout)


class RecordTest(Repo):
    """닫힌 계획 보관과 색인, 사고 기록과 성적, 부품이 더하는 검사와 막을 명령"""

    def setUp(self):
        super().setUp()
        self.setup_harness()

    def commit(self, msg, *paths):
        self.git("add", *paths)
        r = self.git("commit", "-qm", msg)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def chore(self, n, note="x"):
        """chore 계획 하나를 quick으로 시작해 끝낸다"""
        pid = f"P{n:03d}"
        self.assertIn(f"{pid} c{n}: RECORD", self.gate("start", "--kind", "chore", "--title", f"c{n}", "--with", "quick").stdout)
        self.edit(f"plans/{pid}.md", ("- 바뀐 동작:", f"- 바뀐 동작: {note}"), ("- 문서 영향:", "- 문서 영향: 없음"),
                  ("- 남은 일:", "- 남은 일: 없음"))
        r = self.gate("next", pid)
        self.assertIn("모든 상태를 지났다", r.stdout, r.stdout + r.stderr)
        return r

    def events(self):
        f = self.root / ".git" / "gate-events.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def test_닫힌_계획은_색인에_남고_오래된_것은_보관_파일로_옮긴다(self):
        self.edit(".gate/config.toml", ('index = "plans/INDEX.md"', 'index = "plans/INDEX.md"\nkeep_closed = 2'))
        self.commit("harness: keep", ".gate/config.toml")
        self.chore(1, "첫째")
        self.chore(2)
        self.assertIn("닫힌 계획 P001를 .gate/state-archive.jsonl로 옮겼다", self.chore(3).stdout)
        state = json.loads((self.root / ".gate" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(state["plans"]), ["P002", "P003"])
        index = (self.root / "plans" / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("| P001 | c1 | chore | 끝남 |", index)
        self.assertIn("| 첫째 |", index)
        self.assertIn("P004 n: RECORD", self.gate("start", "--kind", "chore", "--title", "n", "--with", "quick",
                                                  "--from", "P001").stdout)
        diag = self.gate("diagnose").stdout
        self.assertIn("보관한 계획 1개", diag)
        self.assertIn("| chore | RECORD | 4 |", diag)

    def test_back은_결함_분류를_받아_사고로_남기고_잡는_부품이_없으면_생성을_제안한다(self):
        for n in (1, 2):
            self.gate("start", "--kind", "bugfix", "--title", f"b{n}")
            self.assertIn("--defect", self.gate("back", f"P00{n}", "REPRO", "--why", "w").stderr)
        state = json.loads((self.root / ".gate" / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("incidents", state)
        r = self.gate("incident", "--defect", "env", "--plan", "P001", "--state", "VERIFY", "--note", "서버 파이썬 버전이 달랐다")
        self.assertIn("사고 I001(env)", r.stdout)
        self.gate("incident", "--defect", "env", "--plan", "P002", "--note", "CI 환경 변수가 빠졌다")
        self.gate("incident", "--defect", "logic", "--plan", "P002", "--note", "경계값을 놓쳤다")
        diag = self.gate("diagnose").stdout
        self.assertIn("| I001 | env | P001 | VERIFY | result | 서버 파이썬 버전이 달랐다 |", diag)
        self.assertIn("- 생성: 결함 분류 env(실행 환경)가 계획 P001, P002에서 나왔는데 이 분류를 잡는 부품이 없다(사고 I001, I002)", diag)
        self.assertIn("- 강화: diff-review(잡으려는 분류 logic)를 쓰는데 그 분류의 결함이 1번 나왔다(사고 I003)", diag)
        self.assertIn("| diff-review | 0 | 0 | 0 | 0 | I003 |", diag)

    def test_incident의_quote는_이번_턴_사용자_입력에_있어야_한다(self):
        log = self.root / "t.jsonl"
        log.write_text(json.dumps({"type": "user", "message": {"content": "그 실험은 누수가 있었어"}}) + "\n", encoding="utf-8")
        cmd = 'python -X utf8 .gate/gate.py incident --defect data --note "누수가 있었다" --quote "{}"'
        ask = lambda q: run([*PY, str(PLUGIN / "hooks" / "run.py"), "guard"], self.root,
                            json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd.format(q)},
                                        "transcript_path": str(log)}), ENV | {"CLAUDE_PROJECT_DIR": str(self.root)}).stdout
        self.assertEqual(ask("누수가 있었어").strip(), "")
        self.assertIn("이번 턴 사용자 입력에 없다", ask("전부 틀렸어"))

    def test_막힌_뒤_구조가_그대로인_채_통과하면_진짜_막힘이고_부품별로_모인다(self):
        self.gate("start", "--kind", "chore", "--title", "c", "--with", "quick")
        self.assertEqual(self.gate("next", "P001").returncode, 1)
        ev = self.events()
        self.assertEqual([(e["kind"], e["part"], e["v"]) for e in ev], [("next", "feature", 1)] * 3)
        self.edit("plans/P001.md", ("- 바뀐 동작:", "- 바뀐 동작: x"), ("- 문서 영향:", "- 문서 영향: 없음"),
                  ("- 남은 일:", "- 남은 일: 없음"))
        self.gate("next", "P001")
        self.assertIsNotNone(self.guard("Write", file_path=str(self.root / "src" / "a.py")))
        self.assertIsNone(self.guard("Write", file_path=str(self.root / "docs" / "a.md")))
        diag = self.gate("diagnose").stdout
        self.assertIn("| feature | 3 | 3 | 0 | 0 | 0 |", diag)
        self.assertIn("| 엔진 | 1 | 0 | 0 | 1 | 0 |", diag)
        # 막혔던 편집이 구조가 바뀐 뒤에 통과하면 오탐이다
        self.gate("start", "--kind", "chore", "--title", "d")
        self.edit("plans/P002.md", ("| 파일 | 바꿀 내용 |\n|---|---|", "| 파일 | 바꿀 내용 |\n|---|---|\n| src/a.py | x |"))
        self.edit(".gate/config.toml", ("version = 1", "version = 2"))
        shutil.copy2(self.root / ".gate" / "config.toml", self.root / "v1.toml")
        (self.root / ".gate" / "history").mkdir()
        self.edit("v1.toml", ("version = 2", "version = 1"))
        shutil.move(self.root / "v1.toml", self.root / ".gate" / "history" / "v1.toml")
        self.assertIsNone(self.guard("Write", file_path=str(self.root / "src" / "a.py")))
        self.assertIn("| 엔진 | 1 | 0 | 1 | 0 | 0 |", self.gate("diagnose").stdout)

    def test_막힌_기록이_상한을_넘으면_성적으로_접는다(self):
        self.edit(".gate/config.toml", ('index = "plans/INDEX.md"', 'index = "plans/INDEX.md"\nevents_keep = 2'))
        self.gate("start", "--kind", "chore", "--title", "c", "--with", "quick")
        self.gate("next", "P001")
        self.gate("next", "P001")
        self.assertEqual(len(self.events()), 6)
        out = self.gate("diagnose").stdout
        self.assertIn("막힌 기록 4줄을 .gate/scorecard.json으로 접었다", out)
        self.assertEqual(len(self.events()), 2)
        self.assertIn("| feature | 6 | 0 | 0 | 6 | 0 |", out)

    def test_부품의_checks_모듈과_막을_명령이_제안서로_들어간다(self):
        self.fake_reviewer()
        self.git("checkout", "-q", ".gate/config.toml")
        self.gate("propose", "H001")
        d = ".gate/proposals/H001/parts/no-todo/"
        self.write(d + "part.toml", 'name = "no-todo"\ndescription = "기록에 TODO를 남기지 않는다"\nversion = 1\n'
                   'origin = "project"\ncovers = ["plan"]\nprovenance = {incident = "I001"}\n\n[[attach]]\nroles = ["record"]\n'
                   'checks = [\n  {check = "no_todo", section = "기록"},\n]\n\n[[guard]]\npattern = "\\\\bpgrep\\\\b"\n'
                   'reason = "pgrep으로 기다리지 않는다"\n')
        self.write(d + "checks.py", "from gatekit.checks import check, sec\n\n\n@check\ndef no_todo(ctx, spec):\n"
                   "    return [\"기록에 TODO가 남았다\"] if any(\"TODO\" in x for x in sec(ctx, spec)) else []\n")
        self.write(".gate/proposals/H001.md", "# H001 x\n\n## 바꾸는 것\n\n| 바꾸는 것 | 근거 |\n|---|---|\n| no-todo 부품 | I001 |\n\n"
                   "## 진행 중인 계획\n\n없음\n")
        r = self.gate("check-proposal", "H001", "--review")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("- 더함: no-todo", (self.root / ".gate" / "proposals" / "H001.diag.md").read_text(encoding="utf-8"))
        self.assertEqual(self.gate("adopt", "H001", "--approved", "적용해").returncode, 0)
        self.assertEqual(self.gate("lint").returncode, 0, self.gate("lint").stdout)
        self.assertEqual(self.guard("Bash", command="pgrep -f train"), "pgrep으로 기다리지 않는다")
        self.assertEqual(self.events()[-1]["part"], "no-todo")
        self.gate("start", "--kind", "chore", "--title", "c")
        self.edit("plans/P001.md", ("| 파일 | 바꿀 내용 |\n|---|---|", "| 파일 | 바꿀 내용 |\n|---|---|\n| src/a.py | x |"),
                  ("- 바뀐 동작:", "- 바뀐 동작: TODO"), ("- 문서 영향:", "- 문서 영향: 없음"), ("- 남은 일:", "- 남은 일: 없음"))
        self.write("src/a.py", "x = 2\n")
        self.commit("feat: a", "src", "plans")
        self.assertIn("P001 c: RECORD", self.gate("next", "P001").stdout)
        self.assertIn("기록에 TODO가 남았다", self.gate("next", "P001").stdout)
        self.assertIn("| no-todo | 2 | 0 | 0 | 2 | 0 |", self.gate("diagnose").stdout)

    def test_제안서_진단이_끝난_계획에_새_검사를_돌려_새로_막히는_계획을_보인다(self):
        self.git("checkout", "-q", ".gate/config.toml")
        self.gate("start", "--kind", "chore", "--title", "c")
        self.edit("plans/P001.md", ("| 파일 | 바꿀 내용 |\n|---|---|", "| 파일 | 바꿀 내용 |\n|---|---|\n| src/a.py | x |"),
                  ("- 바뀐 동작:", "- 바뀐 동작: x"), ("- 문서 영향:", "- 문서 영향: 없음"), ("- 남은 일:", "- 남은 일: 없음"))
        self.write("src/a.py", "x = 2\n")
        self.commit("feat: a", "src", "plans")
        self.gate("next", "P001")
        self.gate("next", "P001")
        self.gate("incident", "--defect", "plan", "--plan", "P001", "--note", "변경 파일 표가 한 줄뿐이었다")
        self.gate("propose", "H001")
        self.edit(".gate/proposals/H001/parts/feature/part.toml",
                  ('  {check = "filled", section = "기록", items = ["바뀐 동작", "문서 영향", "남은 일"]},\n',
                   '  {check = "filled", section = "기록", items = ["바뀐 동작", "문서 영향", "남은 일"]},\n'
                   '  {check = "table", section = "변경 파일", min_rows = 2, only = ["chore"]},\n'))
        self.write(".gate/proposals/H001.md", "# H001 x\n\n## 바꾸는 것\n\n| 바꾸는 것 | 근거 |\n|---|---|\n| 표 두 줄 | I001 |\n\n"
                   "## 진행 중인 계획\n\n없음\n")
        self.gate("check-proposal", "H001")
        diag = (self.root / ".gate" / "proposals" / "H001.diag.md").read_text(encoding="utf-8")
        self.assertIn("- P001 (chore, 사고 I001): 새로 막힘 [\"RECORD: '변경 파일' 절 표에 칸이 모두 찬 행이 2개 이상 있어야 한다"
                      "(지금 1개)\"], 더는 막히지 않음 없음", diag)


class TidyTest(Repo):
    """ml 조합의 정리 경로. 정리 전에 풀리던 참조와 항목 ID가 정리 뒤에도 남아야 한다"""

    def test_정리는_풀리던_참조와_항목을_잃으면_막고_상한을_넘으면_권한다(self):
        self.write("in.toml", '[answers]\nwork = "experiment"\nrun = "remote"\nships = "yes"\nfailure_cost = "time"\n\n'
                   '[records]\ncurrent_max = 120\nitem_id = "\\\\[(S\\\\d{3})\\\\]"\n')
        self.assertEqual(self.install("--local", "in.toml").returncode, 0)
        self.git("add", ".gate", ".githooks")
        self.git("reset", "-q", ".gate/state.json")
        self.git("commit", "-qm", "harness: install")
        self.write("experiments/log.csv", "id,score\nE001,0.5\nE002,0.6\n")
        self.write("progress/status.md", "---\n다음 실험: E003\n---\n\n## 단계\n\n0단계\n\n## 열린 질문\n\n"
                   "- [S001] E001이 왜 낮은가(끝남, E002에서 확인)\n- [S002] 다음 feature\n\n## 다음에 볼 것\n\n- E002 재현\n")
        self.write("progress/history.md", "# 경과\n")
        self.git("add", "experiments", "progress", ".gate/state.json")
        self.git("commit", "-qm", "docs: 기록")
        out = self.gate("records").stdout
        self.assertIn("항목 ID 2개", out)
        self.assertIn("풀리는 참조: 2개", out)
        self.assertIn("progress/status.md가", out)
        r = run([*PY, str(PLUGIN / "hooks" / "run.py"), "session"], self.root, "{}", ENV | {"CLAUDE_PROJECT_DIR": str(self.root)})
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("progress/status.md 머리: 다음 실험 E003", ctx)
        self.assertIn("[기록 정리 권장] progress/status.md가", ctx)
        self.assertIn("P001 t: SURVEY", self.gate("start", "--kind", "tidy", "--title", "t").stdout)  # 단계와 관계없이 시작한다
        self.edit("experiments/plans/P001.md", ("- 정리 전 크기:", "- 정리 전 크기: 150자"), ("- 옮길 항목:", "- 옮길 항목: S001"))
        self.assertIn("P001 t: TIDY", self.gate("next", "P001").stdout)
        # 항목을 지우기만 하고 실험 기록의 행도 지웠다
        self.write("progress/status.md", "---\n다음 실험: E003\n---\n\n## 단계\n\n0단계\n\n## 열린 질문\n\n- [S002] 다음 feature\n\n"
                   "## 다음에 볼 것\n\n- E002 재현\n")
        self.write("experiments/log.csv", "id,score\nE002,0.6\n")
        self.edit("experiments/plans/P001.md", ("- 옮긴 항목:", "- 옮긴 항목: S001"))
        self.assertIn("P001 t: CONFIRM", self.gate("next", "P001").stdout)
        out = self.gate("next", "P001").stdout
        self.assertIn("정리 전에 풀리던 참조 E001가 풀리지 않는다", out)
        self.assertIn("정리 전 progress/status.md에 있던 항목 S001가 progress/status.md에도 progress/history.md에도 없다", out)
        self.write("experiments/log.csv", "id,score\nE001,0.5\nE002,0.6\n")
        self.write("progress/history.md", "# 경과\n\n- [S001] E001이 왜 낮은가(끝남, E002에서 확인)\n")
        self.assertIn("모든 상태를 지났다", self.gate("next", "P001").stdout)
        self.assertIn("| P001 | t | tidy | 끝남 |", (self.root / "experiments" / "plans" / "INDEX.md").read_text(encoding="utf-8"))

    def test_field_검사는_머리_필드가_기록의_마지막_번호_다음인지_본다(self):
        self.install("service")
        self.edit(".gate/config.toml",
                  ('  {check = "filled", section = "기록", items = ["바뀐 동작", "문서 영향", "남은 일"], part = "feature"},\n',
                   '  {check = "field", path = "docs/status.md", field = "다음 실험", '
                   'after_last = {file = "log.csv", pattern = "E(\\\\d{3})"}},\n'),
                  ('[kinds.chore]\nstates = ["IMPLEMENT", "RECORD"]', '[kinds.chore]\nstates = ["RECORD"]'))
        self.git("add", ".gate", ".githooks")
        self.git("reset", "-q", ".gate/state.json")
        self.git("commit", "-qm", "harness: install")
        self.write("log.csv", "E001\nE007\n")
        self.gate("start", "--kind", "chore", "--title", "c")
        self.assertIn("docs/status.md 머리의 '다음 실험' 필드가 비었다", self.gate("next", "P001").stdout)
        self.write("docs/status.md", "---\n다음 실험: E007\n---\n")
        self.assertIn("'다음 실험'가 E007인데, log.csv의 마지막 번호 다음은 8다", self.gate("next", "P001").stdout)
        self.write("docs/status.md", "---\n다음 실험: E008\n---\n")
        self.assertIn("모든 상태를 지났다", self.gate("next", "P001").stdout)


class MlTest(Repo):
    """ml 조합의 tune 경로. 결과 파일 칸, 가벼운 실행, 값 근거 숫자, 검토 해시 범위, 후보 이유 칸 상한"""
    PLAN = "experiments/plans/P001.md"

    def setUp(self):
        super().setUp()
        self.assertEqual(self.install("ml").returncode, 0)
        self.git("add", ".gate", ".githooks")
        self.git("reset", "-q", ".gate/state.json")
        self.git("commit", "-qm", "harness: install")
        self.write("experiments/log.csv", "id,score\nE001,0.5\n")
        self.write("docs/a.md", "x\n")
        self.write("progress/decisions.md", "# 결정\n\n## 결합 단계로 간다\n")
        self.assertEqual(self.gate("stage", "2", "--decision", "결합 단계로 간다").returncode, 0)  # new와 tune을 받는 단계
        self.git("add", "experiments", "docs", "progress", ".gate/state.json")
        self.git("commit", "-qm", "docs: 기록")
        self.fake_reviewer()

    def commit(self, msg, *paths):
        self.git("add", *paths)
        r = self.git("commit", "-qm", msg)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def to_implement(self, light=None):
        """tune 계획의 PLAN을 채워 검토를 받고 IMPLEMENT에 둔다. light를 주면 '가벼운 실행' 칸에 쓴다"""
        self.assertIn("P001 t: PLAN", self.gate("start", "--kind", "tune", "--title", "t").stdout)
        self.edit(self.PLAN,
                  ("| 아이디어 행의 조건 | 구현 파일 | 함수나 위치 |\n|---|---|---|",
                   "| 아이디어 행의 조건 | 구현 파일 | 함수나 위치 |\n|---|---|---|\n| src/m.py | src/m.py | main |"),
                  ("- 기준 실험:", "- 기준 실험: E001"), ("- 판정 규칙:", "- 판정 규칙: seed 3개 평균"),
                  ("| 결과 경계 | 다음 행동 | 가리키는 항목 |\n|---|---|---|",
                   "| 결과 경계 | 다음 행동 | 가리키는 항목 |\n|---|---|---|\n| +0.001 이상 | 채택 | E001 |\n| 그 밖 | 멈춤 | E001 |"),
                  ("- 실행 위치:", "- 실행 위치: 로컬"),
                  ("- 결과 파일:", "- 결과 파일: results/a.csv, results/b_<변형>.csv, results/fold{k}.csv (fold별)"),
                  ("- 조기 종료 결과 파일:", "- 조기 종료 결과 파일: results/early.csv"),
                  ("- 예상 소요 시간과 근거:", "- 예상 소요 시간과 근거: 약 20분, 묶음 2,681개"))
        if light:
            self.edit(self.PLAN, ("- 가벼운 실행:", f"- 가벼운 실행: {light}"))
        r = self.gate("next", "P001", "--review")
        self.assertIn("P001 t: IMPLEMENT", r.stdout, r.stdout + r.stderr)

    def implement(self, code, values=("7",)):
        """코드를 커밋하고 표본 실행과 값 근거를 채운다"""
        self.write("src/m.py", code)
        self.commit("feat: m", "src")
        h = self.git("log", "-1", "--format=%h").stdout.strip()
        rows = "".join(f"\n| {v} | src/m.py | 근거 없이 정한 값 |" for v in values)
        self.edit(self.PLAN, ("- 명령:", "- 명령: python src/m.py"), ("- 커밋:", f"- 커밋: {h}"),
                  ("- 결과 요약:", "- 결과 요약: 끝까지 돌았다"),
                  ("| 값 | 쓰는 곳 | 근거 |\n|---|---|---|", "| 값 | 쓰는 곳 | 근거 |\n|---|---|---|" + rows))
        self.commit("docs: 계획", "experiments")

    def test_값_근거는_여러_줄_문자열과_첨자_속_숫자를_세지_않는다(self):
        self.to_implement()
        self.implement('"""사용법\n\nFOLDS=12346 python src/m.py\n"""\nimport sys\n\nk = sys.argv[4]\nx = 7\n', values=())
        out = self.gate("next", "P001").stdout
        self.assertIn("'값 근거' 표의 값 칸에 없는 숫자 7", out)
        self.assertNotIn("12346", out)
        self.assertNotIn("숫자 4 ", out)
        self.edit(self.PLAN, ("| 값 | 쓰는 곳 | 근거 |\n|---|---|---|", "| 값 | 쓰는 곳 | 근거 |\n|---|---|---|\n| 7 | src/m.py | x |"))
        self.assertIn("검토자 호출 1회", self.gate("next", "P001").stdout)  # 숫자 검사를 지나 IMPLEMENT 검토만 남았다

    def test_가벼운_실행은_구현_검토를_건너뛰고_면제를_검토_기록에_남긴다(self):
        self.to_implement("예(로컬 1분, 서버 없음)")
        self.implement("x = 7\n")
        r = self.gate("next", "P001")
        self.assertIn("'가벼운 실행: 예(로컬 1분, 서버 없음)'이라 IMPLEMENT 검토를 건너뛰었다", r.stdout)
        self.assertIn("P001 t: RUN", r.stdout)
        self.assertEqual(len(self.calls()), 1)  # PLAN 검토만 불렀다
        rev = json.loads((self.root / ".gate" / "state.json").read_text(encoding="utf-8"))["plans"]["P001"]["review"]
        self.assertEqual((rev["IMPLEMENT"]["exempt"], rev["IMPLEMENT"]["pass"]), ("예(로컬 1분, 서버 없음)", True))
        self.assertIn("'가벼운 실행'이 '예'인데", self.prompts()[0])  # 그 주장이 맞는지는 PLAN 검토자가 본다

    def test_결과_파일은_틀_경로를_빼고_조기_종료_파일이_있으면_나머지_없이_넘어간다(self):
        self.to_implement()
        self.implement("x = 7\n")
        self.assertIn("P001 t: RUN", self.gate("next", "P001", "--review").stdout)
        self.assertEqual(self.gate("run", "P001", "--eta", "23:59", "--basis", "b").returncode, 0)
        out = self.gate("next", "P001").stdout
        self.assertIn("결과 파일이 없다: results/a.csv", out)
        for skipped in ("<변형>", "fold{k}", "fold별"):
            self.assertNotIn(skipped, out)
        self.write("results/early.csv", "x\n")
        out = self.gate("next", "P001").stdout
        self.assertIn("'조기 종료 결과 파일' 1개가 있어 나머지 결과 파일 1개 없이 넘어간다", out)
        self.assertIn("P001 t: RECORD", out)

    def test_조기_종료_파일이_있어도_서버_확인이_실패하면_막고_틀_경로는_서버에_넘기지_않는다(self):
        self.write("results/early.csv", "x\n")
        self.write("probe.py", "import json, sys\nsys.path.insert(0, '.gate')\nfrom gatekit import checks, core\n"
                   "core.CFG['remote']['host'] = 'srv'\nseen = []\n"
                   "class R:\n    returncode, stdout, stderr = 255, '', 'Connection refused'\n"
                   "def fake(args, **kw):\n    seen.append(args[-1])\n    return R()\n"
                   "core.subprocess.run = fake\n"
                   "s = core.sections('## 실행 계획\\n\\n- 결과 파일: results/a.csv, results/b_<x>.csv\\n"
                   "- 조기 종료 결과 파일: results/early.csv\\n')\n"
                   "ctx = {'s': s, 'p': {'entered': {'RUN': {'ts': 0}}}, 'state': 'RUN'}\n"
                   "print(json.dumps({'miss': checks.results(ctx, {}), 'scripts': seen}, ensure_ascii=False))\n")
        r = run([*PY, "probe.py"], self.root)
        got = json.loads(r.stdout)
        self.assertTrue(got["miss"] and got["miss"][0].startswith("(서버 확인 실패"), got)
        self.assertIn("results/a.csv", got["scripts"][0])
        self.assertNotIn("<", got["scripts"][0])

    def test_판정_경계_절은_그대로_묶고_나머지_절의_숫자는_검토_해시에서_뺀다(self):
        self.to_implement()
        self.implement("x = 7\n")
        self.edit(self.PLAN, ("seed 3개 평균", "seed 5개 평균"))
        self.assertIn("PLAN에서 검토한 계획 내용이 바뀌었다", self.gate("next", "P001").stdout)
        self.edit(self.PLAN, ("seed 5개 평균", "seed 3개 평균"), ("묶음 2,681개", "묶음 2,669개"))
        out = self.gate("next", "P001").stdout
        self.assertNotIn("PLAN에서 검토한 계획 내용이 바뀌었다", out)
        self.assertIn("검토자 호출 1회", out)  # reviewed를 지나 IMPLEMENT 검토만 남았다

    def test_검토_면제_항목은_계획_틀에_있어야_하고_바꾸면_푸는_변경으로_나온다(self):
        self.assertEqual(self.gate("lint").returncode, 0, self.gate("lint").stdout)
        text = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        self.write("bad.toml", text.replace('item = "가벼운 실행"', 'item = "싼 실행"'))
        self.assertIn("tune/IMPLEMENT: review_exempt가 가리키는 항목 '실행 계획: 싼 실행'이 계획 틀에 없다",
                      self.gate("lint", "--file", "bad.toml").stdout)
        self.gate("propose", "H001")
        self.edit(".gate/proposals/H001/parts/experiment-diff-review/part.toml", ('item = "가벼운 실행"', 'item = "예상 소요 시간과 근거"'))
        self.write(".gate/proposals/H001.md", "# H001 x\n\n## 바꾸는 것\n\n| 바꾸는 것 | 근거 |\n|---|---|\n| 면제 | 없음 |\n\n"
                   "## 진행 중인 계획\n\n없음\n")
        self.gate("check-proposal", "H001")
        diag = (self.root / ".gate" / "proposals" / "H001.diag.md").read_text(encoding="utf-8")
        self.assertIn("tune/IMPLEMENT: 검토 면제 항목이 생기거나 바뀐다('실행 계획: 예상 소요 시간과 근거'이 예면 검토하지 않음)", diag)

    def test_후보_이유_칸이_상한을_넘으면_IDEATE를_넘기지_않는다(self):
        self.assertIn("P001 t: RESEARCH", self.gate("start", "--kind", "new", "--title", "t").stdout)
        self.edit(self.PLAN, ("| 읽은 문서(경로나 URL) | 읽고 얻은 것 |\n|---|---|",
                              "| 읽은 문서(경로나 URL) | 읽고 얻은 것 |\n|---|---|\n| docs/a.md | x |"),
                  ("| 지난 실험 번호 | 이번 계획과의 관계 |\n|---|---|", "| 지난 실험 번호 | 이번 계획과의 관계 |\n|---|---|\n| E001 | 기준 |"))
        self.assertIn("P001 t: IDEATE", self.gate("next", "P001").stdout)
        head = "|---|---|---|---|---|---|---|---|"
        rows = "".join(f"\n| C{i} | 표현 | 후보 {i} | 작음 | 작음 | E001 | 선택 | 짧은 이유, E001 |" for i in (1, 2))
        self.edit(self.PLAN, (head, head + rows + "\n| C3 | 범위 | 후보 3 | 작음 | 작음 | E001 | 제외 | " + "긴 이유 " * 60 + "|"))
        out = self.gate("next", "P001", "--review").stdout
        self.assertIn("'후보' 표 'C3' 행의 칸이 299자다(상한 250자)", out)
        self.edit(self.PLAN, ("긴 이유 " * 60, "E001과 같은 방식이라 뺀다. "))
        self.assertIn("P001 t: SELECT", self.gate("next", "P001", "--review").stdout)


class FeatureTest(Repo):
    """feature 경로 전체. 새 테스트는 tests/expect.txt의 패턴이 src/a.py에 있는지 본다"""
    NEW = "git grep -q -f tests/expect.txt -- src/a.py"

    def setUp(self):
        super().setUp()
        self.setup_harness()
        self.fake_reviewer()

    def commit(self, msg, *paths):
        self.git("add", *paths)
        r = self.git("commit", "-qm", msg)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def feature_to_test(self):
        """SPEC부터 IMPLEMENT까지 지나 TEST에 두고 테스트 결과 표를 채운다"""
        self.gate("start", "--kind", "feature", "--title", "f")
        self.edit("plans/P001.md", ("## 요구사항\n", "## 요구사항\n\nok를 둔다.\n"),
                  ("| 기준 | 확인 방법 |\n|---|---|", "| 기준 | 확인 방법 |\n|---|---|\n| ok가 있다 | 새 테스트 |"),
                  ("## 범위 밖\n", "## 범위 밖\n\n없음\n"),
                  ("| 파일 | 바꿀 내용 |\n|---|---|",
                   "| 파일 | 바꿀 내용 |\n|---|---|\n| src/a.py | ok를 더함 |\n| tests/expect.txt | 기대값 |"),
                  ("- 새 테스트 명령:", f"- 새 테스트 명령: {self.NEW}"), ("- 테스트 명령:", f"- 테스트 명령: {self.NEW}"),
                  ("- 확인할 동작:", "- 확인할 동작: ok"), ("## 위험\n", "## 위험\n\n없음\n"))
        self.assertIn("P001 f: PLAN", self.gate("next", "P001", "--review").stdout)
        self.assertIn("P001 f: TESTFIRST", self.gate("next", "P001", "--review").stdout)
        self.write("tests/expect.txt", "ok\n")
        self.commit("test: 기대값", "tests", "plans")
        self.assertIn("P001 f: IMPLEMENT", self.gate("next", "P001").stdout)
        self.write("src/a.py", "ok = 1\n")
        self.commit("feat: ok", "src")
        self.assertIn("P001 f: TEST", self.gate("next", "P001").stdout)
        self.edit("plans/P001.md", ("| 기준이나 동작 | 결과 |\n|---|---|", "| 기준이나 동작 | 결과 |\n|---|---|\n| ok가 있다 | 통과 |"))

    def test_REVIEW는_계획을_시작한_뒤의_diff를_주고_코드가_바뀌면_다시_검토한다(self):
        self.feature_to_test()
        self.assertIn("P001 f: REVIEW", self.gate("next", "P001").stdout)
        self.assertIn("검토자 호출 1회", self.gate("next", "P001").stdout)
        self.assertIn("P001 f: RECORD", self.gate("next", "P001", "--review").stdout)
        self.assertIn(".git/gate-review/P001.diff", self.prompts()[-1])
        self.assertNotIn("--model", self.calls()[-1]["args"])  # 코드 diff 검토는 기본 모델
        diff = (self.root / ".git" / "gate-review" / "P001.diff").read_text(encoding="utf-8")
        self.assertIn("+ok = 1", diff)
        self.assertIn("# 표에 있는데 바뀌지 않은 파일: 없음", diff)
        self.gate("back", "P001", "REVIEW", "--why", "w", "--defect", "plan")
        self.assertIn("P001 f: RECORD", self.gate("next", "P001").stdout)  # 코드가 그대로면 앞 검토를 쓴다
        self.gate("back", "P001", "IMPLEMENT", "--why", "w", "--defect", "plan")
        self.write("src/a.py", "ok = 2\n")
        self.commit("feat: ok 2", "src")
        self.assertIn("P001 f: TEST", self.gate("next", "P001").stdout)
        self.assertIn("P001 f: REVIEW", self.gate("next", "P001").stdout)
        self.assertIn("검토자 호출 1회", self.gate("next", "P001").stdout)
        self.assertIn("P001 f: RECORD", self.gate("next", "P001", "--review").stdout)
        diff = (self.root / ".git" / "gate-review" / "P001.diff").read_text(encoding="utf-8")
        self.assertIn("앞 검토를 통과한 뒤의 변경만", diff)  # 다시 검토할 때는 통과한 뒤 바뀐 코드만 준다
        self.assertIn("+ok = 2", diff)
        self.assertNotIn("+ok = 1", diff)

    def test_검토자는_context_파일의_발췌를_받는다(self):
        self.edit(".gate/config.toml",
                  ("[states.SPEC]\n", '[states.SPEC]\ncontext = ["docs/notes.md", "docs/small.md"]\n'),
                  ("{plan}의 '요구사항', '수용 기준', '범위 밖' 절을 검토한다.",
                   "{plan}의 '요구사항', '수용 기준', '범위 밖' 절을 검토한다. 먼저 발췌 {context}를 읽는다."))
        self.git("add", ".gate/config.toml")
        self.assertEqual(self.git("commit", "-qm", "harness: context").returncode, 0)
        filler = "\n".join(f"채움 줄 {i} " + "x" * 80 for i in range(120))
        self.write("docs/notes.md", f"# 노트\n\n## 결정 A\n\n결정 A의 첫 줄이다.\n{filler}\n\n## 결정 B\n\n- P001이 쓰는 줄\n{filler}\n")
        self.write("docs/small.md", "작은 파일 전문\n")
        self.feature_to_test()
        ctx = (self.root / ".git" / "gate-review" / "P001-SPEC-context.md").read_text(encoding="utf-8")
        self.assertIn(".git/gate-review/P001-SPEC-context.md", next(p for p in self.prompts() if "발췌" in p))
        for want in ("## 결정 A", "결정 A의 첫 줄이다.", "- P001이 쓰는 줄", "## docs/small.md (전문)", "작은 파일 전문"):
            self.assertIn(want, ctx)
        self.assertNotIn("채움 줄 5 ", ctx)

    def test_발췌는_대체된_원장_항목을_제목만_남기고_오래된_제목을_뺀다(self):
        self.edit(".gate/config.toml",
                  ("[states.SPEC]\n", '[states.SPEC]\ncontext = ["docs/decisions.md"]\n'),
                  ("{plan}의 '요구사항', '수용 기준', '범위 밖' 절을 검토한다.",
                   "{plan}의 '요구사항', '수용 기준', '범위 밖' 절을 검토한다. 먼저 발췌 {context}를 읽는다."))
        cfg = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        rec = ('ledgers = ["docs/decisions.md"]\nsupersedes = "^- 대체하는 결정:"\nsuperseded = "^- 대체됨:"\n'
               'context_recent = 3\n')
        cfg = cfg.replace("[records]\n", "[records]\n" + rec, 1) if "[records]\n" in cfg else cfg + "\n[records]\n" + rec
        self.write(".gate/config.toml", cfg)
        self.git("add", ".gate/config.toml")
        self.assertEqual(self.git("commit", "-qm", "harness: context").returncode, 0)
        filler = "x" * 7000
        entries = [f"## 2026-01-0{i} 결정 {i}\n\n- 결정: 결정 {i}의 결정 줄이다.\n" for i in range(1, 7)]
        entries[4] += '- 대체하는 결정: 2026-01-01 "결정 1"의 순서.\n'
        entries[2] += "- 대체됨: 2026-01-06 결정 6\n"
        self.write("docs/decisions.md", "# 결정\n\n" + "\n".join(entries) + f"\n<!-- {filler} -->\n")
        self.feature_to_test()
        ctx = (self.root / ".git" / "gate-review" / "P001-SPEC-context.md").read_text(encoding="utf-8")
        self.assertIn("## 2026-01-01 결정 1 (대체됨: 2026-01-05 결정 5", ctx)
        self.assertIn("## 2026-01-03 결정 3 (대체됨: - 대체됨: 2026-01-06 결정 6", ctx)
        self.assertNotIn("결정 1의 결정 줄", ctx)
        self.assertNotIn("2026-01-02 결정 2", ctx)  # 인용되지 않았고 최근 제목 3개 밖이다
        self.assertIn("결정 6의 결정 줄", ctx)
        self.assertIn("인용되지 않은 앞 제목", ctx)

    def test_검토자는_상태별_모델과_추론_단계로_부르고_다시_검토한_비용을_쌓는다(self):
        self.feature_to_test()
        spec = next(c["args"] for c in self.calls() if "'요구사항', '수용 기준', '범위 밖' 절을 검토한다" in c["prompt"])
        for flag in ("--model sonnet", "--effort high", "--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, " ".join(spec))
        self.gate("back", "P001", "SPEC", "--why", "w", "--defect", "plan")
        self.edit("plans/P001.md", ("ok를 둔다.", "ok를 1로 둔다."))
        self.assertIn("P001 f: PLAN", self.gate("next", "P001", "--review").stdout)
        rev = json.loads((self.root / ".gate" / "state.json").read_text(encoding="utf-8"))["plans"]["P001"]["review"]
        self.assertEqual((rev["SPEC"]["calls"], rev["SPEC"]["spent"], rev["SPEC"]["model"]), (2, 1.0, "sonnet/high"))
        self.assertIn("| feature | SPEC | 2 | 1.00 | sonnet/high |", self.gate("diagnose").stdout)

    def test_수용_기준마다_테스트_결과가_있어야_하고_막힌_이유가_진단에_남는다(self):
        self.feature_to_test()
        self.edit("plans/P001.md", ("| ok가 있다 | 통과 |", "| ok | 통과 |"))
        r = self.gate("next", "P001")
        self.assertEqual(r.returncode, 1)
        self.assertIn("'수용 기준' 표의 'ok가 있다'가 '테스트 결과' 표 첫 칸에 없다", r.stdout)
        self.assertIn("| feature | TEST | '수용 기준' 표의 'ok가 있다'가", self.gate("diagnose").stdout)
        self.edit("plans/P001.md", ("| ok | 통과 |", "| ok가  있다 | 통과 |"))
        self.assertIn("P001 f: REVIEW", self.gate("next", "P001").stdout)

    def test_제안서_진단에_빼거나_푸는_변경이_나온다(self):
        self.assertEqual(self.gate("propose", "H001").returncode, 0)
        self.edit(".gate/proposals/H001/parts/test-first/part.toml", ('code = true\nedit = "tests"', "code = true"))
        self.edit(".gate/proposals/H001/parts/feature/part.toml",
                  ('  {check = "covers", section = "테스트 결과", from = "수용 기준", only = ["feature"]},\n', ""))
        self.write(".gate/proposals/H001.md", "# H001 x\n\n## 바꾸는 것\n\n| 바꾸는 것 | 근거 |\n|---|---|\n| covers 빼기 | 없음 |\n\n"
                   "## 진행 중인 계획\n\n없음\n")
        self.gate("check-proposal", "H001")
        diag = (self.root / ".gate" / "proposals" / "H001.diag.md").read_text(encoding="utf-8")
        self.assertIn("## 부품 변경\n\n- 고침: feature\n- 고침: test-first\n", diag)
        self.assertIn("- feature/TEST: 검사 covers(section=테스트 결과, from=수용 기준, only=['feature'])가 빠지거나 바뀐다",
                      diag)
        self.assertIn("- feature/TESTFIRST: 테스트 파일만 고치는 제한이 풀린다", diag)
        self.assertNotIn("bugfix/REPRO", diag)

    def test_제안서_진단에_project_review_remote_설정을_푸는_변경이_나온다(self):
        self.gate("propose", "H001")
        self.edit(".gate/proposals/H001/parts/feature/part.toml",
                  ('code = ["src/*", "app/*",', 'code = ["app/*",'),
                  ('unreviewed = ["테스트 결과",', 'unreviewed = ["요구사항", "테스트 결과",'),
                  ('tools = "Read,Grep,Glob"\n', 'tools = "Read,Grep,Glob,Bash"\nmodel = "haiku"\ncontext_full = 9000\n'),
                  ('effort = "high"', 'effort = "low"'), ('stop_states = ["RECORD"]', "stop_states = []"))
        self.write(".gate/proposals/H001/local.toml", '[answers]\nwork = "service"\n\n[project]\nmax_visits = 9\n')
        self.write(".gate/proposals/H001.md", "# H001 x\n\n## 바꾸는 것\n\n| 바꾸는 것 | 근거 |\n|---|---|\n| 설정 | 없음 |\n\n"
                   "## 진행 중인 계획\n\n없음\n")
        self.gate("check-proposal", "H001")
        diag = (self.root / ".gate" / "proposals" / "H001.diag.md").read_text(encoding="utf-8")
        for line in ("[project] code: src/*가 빠진다", "[project] unreviewed: 요구사항가 늘어난다",
                     "[project] max_visits: 3에서 9로 커진다", "[project] stop_states: RECORD가 빠진다",
                     "[review] tools: Bash가 늘어난다", "[review] model: 기본에서 haiku로 낮아진다",
                     "[review] effort: high에서 low로 낮아진다", "[review] context_full: 바뀐 설정(6000에서 9000)"):
            self.assertIn(line, diag)

    def test_제안서로_부품을_빼면_adopt가_부품과_lock과_구조를_함께_바꾼다(self):
        self.git("checkout", "-q", ".gate/config.toml")
        self.gate("propose", "H001")
        shutil.rmtree(self.root / ".gate" / "proposals" / "H001" / "parts" / "diff-review")
        self.write(".gate/proposals/H001.md", "# H001 x\n\n## 바꾸는 것\n\n| 바꾸는 것 | 근거 |\n|---|---|\n| REVIEW 빼기 | 없음 |\n\n"
                   "## 진행 중인 계획\n\n없음\n")
        r = self.gate("check-proposal", "H001", "--review")
        self.assertEqual(r.returncode, 0, r.stdout)
        diag = (self.root / ".gate" / "proposals" / "H001.diag.md").read_text(encoding="utf-8")
        self.assertIn("- 뺌: diff-review", diag)
        self.assertIn("- feature: 상태 REVIEW가 빠진다", diag)
        self.edit(".gate/proposals/H001/parts/chore/part.toml", ("max_files = 5", "max_files = 6"))
        self.assertIn("검토 뒤 바뀌었다", self.gate("adopt", "H001", "--approved", "적용해").stderr)
        self.edit(".gate/proposals/H001/parts/chore/part.toml", ("max_files = 6", "max_files = 5"))
        r = self.gate("adopt", "H001", "--approved", "적용해")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        gate = self.root / ".gate"
        cfg = PT.tomllib.loads((gate / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["version"], 2)
        self.assertNotIn("REVIEW", cfg["states"])
        self.assertFalse((gate / "parts" / "diff-review").exists())
        self.assertNotIn("diff-review", json.loads((gate / "parts.lock").read_text(encoding="utf-8")))
        self.assertTrue((gate / "history" / "v1.toml").exists())
        self.assertEqual(self.gate("lint").returncode, 0, self.gate("lint").stdout)
        self.assertIn("- diff-review: ", self.gate("parts").stdout.split("쓰지 않는 라이브러리 부품")[1])

    def test_먼저_쓴_테스트를_고치면_표에_적어야_하고_구현한_뒤에도_TESTFIRST를_다시_지난다(self):
        self.feature_to_test()
        self.gate("back", "P001", "PLAN", "--why", "w", "--defect", "plan")
        self.assertIn("P001 f: TESTFIRST", self.gate("next", "P001", "--review").stdout)
        self.assertIn("P001 f: IMPLEMENT", self.gate("next", "P001").stdout)  # 같은 테스트로 실패를 확인했다
        self.write("tests/expect.txt", "o\n")
        self.commit("test: 기대값을 바꿈", "tests")
        self.assertIn("P001 f: TEST", self.gate("next", "P001").stdout)
        r = self.gate("next", "P001")
        self.assertIn("tests/expect.txt는 TESTFIRST를 넘긴 뒤 고치거나 지운 테스트 파일이다", r.stdout)
        self.edit("plans/P001.md", ("| 파일 | 고친 이유 |\n|---|---|", "| 파일 | 고친 이유 |\n|---|---|\n| tests/expect.txt | 패턴을 줄임 |"))
        self.assertIn("P001 f: REVIEW", self.gate("next", "P001").stdout)
        self.gate("back", "P001", "PLAN", "--why", "w", "--defect", "plan")
        self.gate("next", "P001", "--review")
        r = self.gate("next", "P001")  # 테스트가 바뀌어 실패를 다시 확인해야 한다
        self.assertIn("실패해야 하는데 종료 코드가 0", r.stdout)


class GuardTest(Repo):
    def setUp(self):
        super().setUp()
        self.setup_harness()

    def path(self, rel):
        return str(self.root / rel)

    def test_코드_상태인_계획이_없으면_코드_편집을_막는다(self):
        self.assertIn("코드를 고치는 상태인 계획이 없어", self.guard("Write", file_path=self.path("src/a.py")))
        self.assertIn("셸로 src/b.py를 쓰는 것을 막았다", self.guard("Bash", command="echo 1 > src/b.py"))
        self.assertIsNone(self.guard("Write", file_path=self.path("docs/a.md")))

    def test_코드_상태인_계획의_변경_파일_표에_적은_파일만_열린다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.assertIn("'변경 파일' 절에 없어", self.guard("Write", file_path=self.path("src/a.py")))
        self.edit("plans/P001.md", ("| 파일 | 바꿀 내용 |\n|---|---|", "| 파일 | 바꿀 내용 |\n|---|---|\n| src/a.py | 새 파일 |"))
        self.assertIsNone(self.guard("Write", file_path=self.path("src/a.py")))
        self.assertIn("지금 열린 파일: src/a.py", self.guard("Write", file_path=self.path("src/other.py")))
        self.assertIn("'변경 파일' 절에 없어", self.guard("Bash", command="echo 1 > src/other.py"))

    def test_테스트만_쓰는_상태에서는_테스트_파일만_고치고_커밋한다(self):
        self.gate("start", "--kind", "bugfix", "--title", "b")
        self.edit("plans/P001.md", ("| 파일 | 바꿀 내용 |\n|---|---|",
                                    "| 파일 | 바꿀 내용 |\n|---|---|\n| src/a.py | 고침 |\n| tests/test_a.py | 재현 |"))
        self.assertIn("테스트만 쓰는 상태(P001 REPRO)", self.guard("Write", file_path=self.path("src/a.py")))
        self.assertIn("테스트만 쓰는 상태(P001 REPRO)", self.guard("Bash", command="echo 1 > src/b.py"))
        self.assertIsNone(self.guard("Write", file_path=self.path("tests/test_a.py")))
        self.write("src/a.py", "a = 2\n")
        self.write("tests/test_a.py", "x\n")
        self.git("add", "src", "tests", "plans")
        r = self.git("commit", "-qm", "test: 재현")
        self.assertIn("src/a.py: P001 REPRO에서는 테스트 파일만 커밋한다", r.stdout + r.stderr)
        self.git("reset", "-q", "src/a.py")
        self.assertEqual(self.git("commit", "-qm", "test: 재현").returncode, 0)

    def test_구조_파일과_상태_파일과_git_hook은_직접_쓰지_못한다(self):
        for rel in (".gate/config.toml", ".gate/gatekit/core.py", ".gate/state.json", ".githooks/pre-commit",
                    ".git/config"):
            self.assertIsNotNone(self.guard("Edit", file_path=self.path(rel)), rel)
        self.assertIsNotNone(self.guard("Bash", command="echo x >> .gate/config.toml"))

    def test_상태_파일은_gate_명령과_읽기만_허용한다(self):
        self.assertIsNone(self.guard("Bash", command="python -X utf8 .gate/gate.py status"))
        self.assertIsNone(self.guard("Bash", command="cat .gate/state.json"))
        self.assertIsNone(self.guard("Bash", command="git add .gate/state.json"))
        self.assertIsNotNone(self.guard("Bash", command="python -c \"open('.gate/state.json', 'w')\""))

    def test_읽기_명령과_gate_명령은_이을_수_있고_쓰기는_막는다(self):
        for cmd in ("python -X utf8 .gate/gate.py status && echo ok",
                    "cd sub && wc -l .gate/state.json 2>/dev/null; git log -1 -- .gate/state.json",
                    "grep -n '\"P0[12]\"' .gate/state.json | tail -3",
                    "git commit -m \"docs: .gate/state.json 설명; a | b\" && python -X utf8 .gate/gate.py status 2>&1 | head"):
            self.assertIsNone(self.guard("Bash", command=cmd), cmd)
        for cmd in ("cat .gate/state.json > x", "echo '{}' > .gate/state.json",
                    "grep x .gate/state.json | awk '{print > \"y\"}'", "cat .gate/state.json & rm x",
                    "echo \"$(python -c 1)\" .gate/state.json",
                    "python -X utf8 .gate/gate.py status && python -c \"import json\""):
            self.assertIsNotNone(self.guard("Bash", command=cmd), cmd)

    def user_approval(self):
        """승인 방식을 user로 바꾼다(기본은 auto)"""
        self.edit(".gate/config.toml", ("[project]\n", '[project]\napproval = "user"\n'))

    def test_다른_명령에_이은_adopt도_승인_문장을_본다(self):
        self.user_approval()
        cmd = 'echo x && python -X utf8 .gate/gate.py adopt H001 --approved "그대로 적용해"'
        self.assertIn("이번 턴 사용자 입력에 없다", self.guard("Bash", command=cmd))

    def remote(self):
        """[remote]를 켜고 chore 계획의 코드를 커밋해 둔다(테스트 설정의 chore IMPLEMENT는 실행 상태다)"""
        self.edit(".gate/config.toml", ("[review]", '[remote]\nhost = "srv"\nexec_states = ["RECORD"]\n\n[review]'))
        self.git("add", ".gate/config.toml")
        self.assertEqual(self.git("commit", "-qm", "harness: remote").returncode, 0)
        self.gate("start", "--kind", "chore", "--title", "t")
        self.edit("plans/P001.md", ("| 파일 | 바꿀 내용 |\n|---|---|", "| 파일 | 바꿀 내용 |\n|---|---|\n| src/a.py | 새 파일 |"))
        self.write("src/a.py", "a = 1\n")
        self.git("add", "src", "plans")
        self.assertEqual(self.git("commit", "-qm", "feat: a").returncode, 0)

    def test_ssh로_저장소_코드를_그_자리에서_돌리면_실행_기록을_본다(self):
        self.remote()
        for cmd in ("ssh srv 'cd ~/p && python -X utf8 src/a.py --fold 0'", "ssh srv \"python -m src.a\"",
                    "ssh srv 'bash src/chain.sh'"):
            self.assertIn("ssh로 저장소 코드", self.guard("Bash", command=cmd), cmd)
        for cmd in ("ssh srv 'nvidia-smi; tail -5 ~/p/log'", "ssh srv 'cd ~/p && source src/lib.sh && watch_x log'",
                    "python -X utf8 src/a.py"):
            self.assertIsNone(self.guard("Bash", command=cmd), cmd)
        self.assertEqual(self.gate("run", "P001", "--eta", "23:59", "--basis", "b").returncode, 0)
        self.assertIsNone(self.guard("Bash", command="ssh srv 'cd ~/p && python -X utf8 src/a.py'"))
        self.write("src/a.py", "a = 2\n")
        self.assertIn("커밋하지 않은 코드", self.guard("Bash", command="ssh srv 'python src/a.py'"))

    def test_sample_states_상태에서는_표본_경로가_든_ssh_실행만_열린다(self):
        self.remote()
        self.edit(".gate/config.toml", ('exec_states = ["RECORD"]',
                                        'exec_states = ["RECORD"]\nsample_states = ["IMPLEMENT"]\n'
                                        'sample_pattern = "(?i)\\\\bresults/p\\\\d+_sample\\\\b"'))
        self.git("add", ".gate/config.toml")
        self.assertEqual(self.git("commit", "-qm", "harness: sample").returncode, 0)
        self.assertIsNone(self.guard("Bash", command="ssh srv 'cd ~/p && python src/a.py --out results/P001_sample'"))
        self.assertIsNone(self.guard("Bash", command="ssh srv 'bash results/p001_sample/run.sh && python -m src.a'"))
        r = self.guard("Bash", command="ssh srv 'python src/a.py --out results/main'")
        self.assertIn("ssh로 저장소 코드", r)
        self.assertIn("sample_pattern", r)
        self.write("src/a.py", "a = 3\n")
        self.assertIn("커밋하지 않은 코드", self.guard("Bash", command="ssh srv 'python src/a.py --out results/p001_sample'"))

    def test_stop은_PreToolUse_hook이_막은_명령을_띄운_것으로_세지_않는다(self):
        self.remote()
        cmd = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ssh srv 'nohup python src/a.py &'"}}
        answer = {"type": "assistant", "message": {"content": [{"type": "text", "text": "띄웠다"}]}}

        def stop(result):
            rows = [{"type": "user", "message": {"content": "실험을 띄워"}},
                    {"type": "assistant", "message": {"content": [cmd]}},
                    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", **result}]}}, answer]
            log = self.root / "t.jsonl"
            log.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
            r = run([*PY, str(PLUGIN / "hooks" / "run.py"), "stop"], self.root,
                    json.dumps({"transcript_path": str(log), "session_id": f"s{len(result)}"}),
                    ENV | {"CLAUDE_PROJECT_DIR": str(self.root)})
            return r.stdout
        self.assertIn("서버 작업을 띄웠는데", stop({"content": "started"}))
        blocked = stop({"content": "PreToolUse:Bash hook error: 실행 상태이고 gate run 기록이 없어", "is_error": True})
        self.assertNotIn("서버 작업을 띄웠는데", blocked)

    def test_hook을_끄는_명령은_막고_메시지_속_단어는_막지_않는다(self):
        for cmd in ("git commit --no-verify -m x", "git commit -n -m x", "git commit -m 'x' -n",
                    "git commit -nm 'x'", "git config core.hooksPath /dev/null"):
            self.assertIsNotNone(self.guard("Bash", command=cmd), cmd)
        for cmd in ("git commit -m \"fix: handle -n flag and -run option\"", "git commit -am 'docs: --no-verify 설명'",
                    "git commit --message=\"chore: -n\"", "git status"):
            self.assertIsNone(self.guard("Bash", command=cmd), cmd)

    def test_adopt는_승인_문장이_이번_턴_입력에_있어야_한다(self):
        self.user_approval()
        log = self.root / "t.jsonl"
        log.write_text(json.dumps({"type": "user", "message": {"content": "H001 그대로 적용해"}}) + "\n", encoding="utf-8")
        cmd = 'python -X utf8 .gate/gate.py adopt H001 --approved "{}"'
        self.assertIsNotNone(self.guard("Bash", command=cmd.format("그대로 적용해")))  # 대화 기록이 없을 때
        r = run([*PY, str(PLUGIN / "hooks" / "run.py"), "guard"], self.root,
                json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd.format("그대로 적용해")},
                            "transcript_path": str(log)}), ENV | {"CLAUDE_PROJECT_DIR": str(self.root)})
        self.assertEqual(r.stdout.strip(), "")
        r = run([*PY, str(PLUGIN / "hooks" / "run.py"), "guard"], self.root,
                json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd.format("전부 승인")},
                            "transcript_path": str(log)}), ENV | {"CLAUDE_PROJECT_DIR": str(self.root)})
        self.assertIn("이번 턴 사용자 입력에 없다", r.stdout)

    def test_승인_방식이_auto면_adopt에_승인_문장이_없어도_된다(self):
        for cmd in ("python -X utf8 .gate/gate.py adopt H001", 'echo x && python -X utf8 .gate/gate.py adopt H001'):
            self.assertIsNone(self.guard("Bash", command=cmd), cmd)

    def test_gate가_나오지_않는_heredoc은_gate_명령과_이을_수_있다(self):
        ok = "python -X utf8 - <<'EOF'\np = 'plans/P001.md'\nprint(p)\nEOF\npython -X utf8 .gate/gate.py status"
        self.assertIsNone(self.guard("Bash", command=ok))
        for cmd in ("python -X utf8 - <<'EOF'\nopen('.gate/state.json', 'w')\nEOF\npython -X utf8 .gate/gate.py status",
                    "python -X utf8 - <<'EOF'\nimport gatekit\nEOF\npython -X utf8 .gate/gate.py status",
                    "python -X utf8 - <<'EOF'\nx = 1\nEOF\necho '{}' > .gate/state.json"):
            self.assertIsNotNone(self.guard("Bash", command=cmd), cmd)

    def test_검토_해시는_unreviewed의_bullet_항목과_표_칸을_뺀다(self):
        code = ("import sys; sys.path.insert(0, '.gate'); from gatekit.core import plan_hash, CFG\n"
                "cfg = {**CFG, 'project': CFG['project'] | {'unreviewed': ['실행 계획: 예상 소요 시간과 근거', '대조표[3]']}}\n"
                "a = '# P\\n\\n## 실행 계획\\n\\n- 결과 파일: x\\n- 예상 소요 시간과 근거: 약 1시간\\n\\n## 대조표\\n\\n"
                "| 조건 | 파일 | 함수 |\\n|---|---|---|\\n| c | a.py | f |\\n'\n"
                "b = a.replace('약 1시간', '표본 측정으로 세 시간').replace('| f |', '| g |')\n"
                "c = a.replace('결과 파일: x', '결과 파일: y').replace('a.py', 'b.py')\n"
                "print(plan_hash(a, cfg) == plan_hash(b, cfg), plan_hash(a, cfg) == plan_hash(c, cfg))")
        r = run([*PY, "-c", code], self.root)
        self.assertEqual(r.stdout.split(), ["True", "False"], r.stderr)

if __name__ == "__main__":
    unittest.main()
