---
name: flow-setup
description: 프로젝트에 flow 상태 기계 하네스를 설치하거나 엔진을 갱신한다. 사용자가 "flow 하네스 설치", "flow-setup", "상태 기계 깔아줘", "하네스 갱신"이라고 하거나 preset(ml, agent, service)을 골라 하네스를 넣어 달라고 할 때 쓴다.
argument-hint: "<ml|agent|service|update>"
---

# flow 하네스 설치

인자: `$ARGUMENTS` (ml, agent, service 중 하나이거나 update)

## preset

| preset | 쓰는 곳 | 종류 |
|---|---|---|
| ml | 대회, 연구 실험. 실험 기록 log.csv, 아이디어 목록, 서버 실행 | new, model, tune, freeze, repro, eda, infra |
| agent | LLM 에이전트, 프롬프트, 도구 개발. eval을 먼저 고정하고 잰다 | behavior, prompt, bugfix, chore |
| service | 웹 서비스, 앱 개발. 테스트 명령으로 넘어간다 | feature, bugfix, refactor, chore |

인자가 없으면 저장소를 보고 맞는 preset을 권한 뒤 사용자에게 하나를 고르게 한다.

## 설치 순서

1. 프로젝트 루트가 git 저장소이고 커밋이 하나 이상 있는지 본다. 없으면 사용자에게 `git init`과 첫 커밋을 먼저 하자고 한다.
2. 프로젝트에 이미 다른 하네스(`.claude/settings.json`의 상태 기계 hook, `.githooks/`)가 있으면 멈추고 사용자에게 알린다. 두 하네스를 함께 돌리지 않는다.
3. 설치 스크립트를 돌린다.
   ```
   python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" <preset> --target "<프로젝트 루트>"
   ```
4. `.flow/config.toml`의 `[project]`에서 `code`(고칠 때 코드 상태 계획이 있어야 하는 파일 패턴)와 `tracked`(stop hook이 미커밋 변경을 보는 폴더)를 이 저장소의 실제 폴더에 맞춘다. 빌드 산출물과 캐시는 `.gitignore`에 들어 있는지 본다. 들어 있지 않으면 커밋하지 않은 코드 변경으로 잡힌다.
5. 하네스 파일만 먼저 커밋한다.
   ```
   git add .flow .githooks
   git reset .flow/state.json
   git commit -m "harness: flow 하네스 설치"
   ```
6. 상태 파일을 따로 커밋한다(저장소의 커밋 메시지 관례를 따른다).
7. `python -X utf8 .flow/flow.py status`를 돌려 출력을 보여 준다.
8. 사용자에게 다음을 알린다. 이 프로젝트에서 플러그인이 켜져 있어야 hook이 돈다. 설정이 없는 프로젝트에서는 플러그인 hook이 아무것도 하지 않는다.

## 갱신(update)

플러그인을 새 버전으로 받은 뒤 프로젝트의 엔진 복사본을 바꾼다. config.toml, 틀, FLOW.md는 건드리지 않는다.

```
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" --update --target "<프로젝트 루트>"
git add .flow/flowkit .flow/flow.py .githooks
git commit -m "harness: flowkit 갱신"
```

갱신 뒤 `python -X utf8 .flow/flow.py status`가 오류 없이 도는지 본다.
