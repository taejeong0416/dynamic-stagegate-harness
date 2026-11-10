"""임시 git 저장소에 하네스를 설치해 명령과 hook을 실제로 돌려 본다.

저장소 루트에서 python -X utf8 -m unittest discover tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "dynamic-stagegate-harness"
PY = [sys.executable, "-X", "utf8"]
ENV = {k: v for k, v in os.environ.items() if k != "GATE_REVIEW"} | {"PYTHONDONTWRITEBYTECODE": "1"}


def run(cmd, cwd, stdin=None, env=ENV):
    return subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, encoding="utf-8", env=env)


class Repo(unittest.TestCase):
    """테스트마다 새 저장소. service preset을 설치하고 chore에 quick 선택과 실행 상태를 더한다"""

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
        return run([*PY, ".gate/gate.py", *args], self.root)

    def install(self, *args, target=None):
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

    def test_update는_config를_그대로_둔다(self):
        self.setup_harness()
        before = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        r = self.install("--update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.root / ".gate" / "config.toml").read_text(encoding="utf-8"), before)


class LintTest(Repo):
    def test_preset은_모두_lint를_통과한다(self):
        self.setup_harness()
        for preset in sorted(p.name for p in (PLUGIN / "presets").iterdir()):
            r = self.gate("lint", "--file", str(PLUGIN / "presets" / preset / "config.toml"))
            self.assertEqual(r.returncode, 0, preset + "\n" + r.stdout)

    def test_다른_종류에_오류가_있어도_검사의_빠진_키를_잡는다(self):
        self.setup_harness()
        text = (self.root / ".gate" / "config.toml").read_text(encoding="utf-8")
        text = text.replace("[kinds.chore]", '[kinds.broken]\nstates = ["NOPE"]\n\n[kinds.chore]')
        text = text.replace('{check = "filled", section = "범위 밖"},', '{check = "filled"},\n  {check = "commit_since"},')
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
        text = text.replace('back = {VERIFY = ["FIX", "REPRO"]}', 'back = {FIX = ["VERIFY"]}')
        text = text.replace('{check = "unchanged", state = "REPRO",', '{check = "unchanged", state = "VERIFY",')
        self.write("bad.toml", text)
        r = self.gate("lint", "--file", "bad.toml")
        self.assertIn("bugfix.back.FIX: VERIFY가 FIX보다 앞 상태가 아니다", r.stdout)
        self.assertIn("bugfix/FIX: unchanged가 가리키는 VERIFY가 이 종류에서 FIX보다 앞 상태가 아니다", r.stdout)


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

    def test_back은_max_visits에서_멈춘다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.write("src/a.py", "a = 2\n")
        self.fill_chore("P001")
        self.git("add", "src/a.py", "plans")
        self.git("commit", "-qm", "feat: a")
        for _ in range(2):
            self.assertEqual(self.gate("next", "P001").returncode, 0)
            self.assertEqual(self.gate("back", "P001", "IMPLEMENT", "--why", "w").returncode, 0)
        self.assertEqual(self.gate("next", "P001").returncode, 0)
        r = self.gate("back", "P001", "IMPLEMENT", "--why", "w")
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
        self.write("src/a.py", "a = 2\n")
        self.git("add", "src/a.py", "plans")
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
        self.edit(".gate/config.toml", ('back = {VERIFY = ["FIX", "REPRO"]}', 'back = {VERIFY = ["FIX"]}'))
        self.git("add", ".gate/config.toml")
        self.assertEqual(self.git("commit", "-qm", "harness: back 경로").returncode, 0)
        self.assertIn("VERIFY --> FIX : back", self.gate("describe", "--mermaid").stdout)
        self.bugfix_to_fix()
        self.assertEqual(self.gate("next", "P001").returncode, 0)
        r = self.gate("back", "P001", "REPRO", "--why", "w")
        self.assertIn("VERIFY에서는 FIX로만 돌아갈 수 있다", r.stderr)
        self.assertEqual(self.gate("back", "P001", "FIX", "--why", "덜 고침").returncode, 0)
        self.assertIn("| bugfix | VERIFY | FIX | 1 | 덜 고침 |", self.gate("diagnose").stdout)


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

    def test_코드_상태인_계획이_있으면_코드_편집이_열린다(self):
        self.gate("start", "--kind", "chore", "--title", "t")
        self.assertIsNone(self.guard("Write", file_path=self.path("src/a.py")))

    def test_구조_파일과_상태_파일과_git_hook은_직접_쓰지_못한다(self):
        for rel in (".gate/config.toml", ".gate/gatekit/core.py", ".gate/state.json", ".githooks/pre-commit",
                    ".git/config"):
            self.assertIsNotNone(self.guard("Edit", file_path=self.path(rel)), rel)
        self.assertIsNotNone(self.guard("Bash", command="echo x >> .gate/config.toml"))

    def test_상태_파일은_gate_명령과_읽기만_허용한다(self):
        self.assertIsNone(self.guard("Bash", command="python -X utf8 .gate/gate.py status"))
        self.assertIsNone(self.guard("Bash", command="cat .gate/state.json"))
        self.assertIsNone(self.guard("Bash", command="git add .gate/state.json"))
        self.assertIsNotNone(self.guard("Bash", command="python -X utf8 .gate/gate.py status && echo ok"))
        self.assertIsNotNone(self.guard("Bash", command="python -c \"open('.gate/state.json', 'w')\""))

    def test_hook을_끄는_명령은_막고_메시지_속_단어는_막지_않는다(self):
        for cmd in ("git commit --no-verify -m x", "git commit -n -m x", "git commit -m 'x' -n",
                    "git commit -nm 'x'", "git config core.hooksPath /dev/null"):
            self.assertIsNotNone(self.guard("Bash", command=cmd), cmd)
        for cmd in ("git commit -m \"fix: handle -n flag and -run option\"", "git commit -am 'docs: --no-verify 설명'",
                    "git commit --message=\"chore: -n\"", "git status"):
            self.assertIsNone(self.guard("Bash", command=cmd), cmd)

    def test_adopt는_승인_문장이_이번_턴_입력에_있어야_한다(self):
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


if __name__ == "__main__":
    unittest.main()
