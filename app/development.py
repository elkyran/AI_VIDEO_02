"""Small atomic development journal; never a production job queue."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .registry import canonical_json, workspace_path


DEPENDENCIES = [[], ["T00"], ["T01"], ["T02"], ["T03"], ["T03", "T04"],
                ["T05"], ["T02", "T06"], ["T07"], ["T00", "T07"],
                ["T03", "T07", "T08"], ["T10"], [f"T{i:02d}" for i in range(12)]]
STATUSES = {"pending", "in_progress", "blocked", "needs_review", "done"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def locked(path: Path):
    """OS lock is released on process exit, including an interrupted write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Journal is being updated by another process") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Journal:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.path = self.root / "development/tasks.json"
        self.lock = self.path.with_suffix(".lock")

    def initialize(self, plan_ref: str, run_ref: str):
        plan_hash = digest(workspace_path(self.root, plan_ref).read_bytes())
        with locked(self.lock):
            if self.path.exists():
                state, _ = self.read()
                if state["plan_sha256"] != plan_hash:
                    raise ValueError("Existing journal belongs to a different plan; it was preserved")
                return state
            state = {"schema_version": 1, "generation": 0, "plan_ref": plan_ref,
                     "plan_sha256": plan_hash, "profile_ref": "config/production_profile.json",
                     "run_ref": run_ref, "authorized_phase": "A", "operations": {},
                     "tasks": [{"id": f"T{i:02d}", "phase": "A" if i < 4 else "B" if i < 7 else "C" if i < 10 else "D",
                                "status": "pending", "depends_on": deps, "result_refs": [], "test_refs": [],
                                "revision": None, "blocker": None, "updated_at": now()}
                               for i, deps in enumerate(DEPENDENCIES)]}
            self._write(state)
            return state

    def read(self):
        raw = self.path.read_bytes()
        state = json.loads(raw)
        if state.get("schema_version") != 1 or [t["id"] for t in state["tasks"]] != [f"T{i:02d}" for i in range(13)]:
            raise ValueError("Unknown journal format; existing state was preserved")
        for i, task in enumerate(state["tasks"]):
            expected = ["T02"] if task["id"] == "T07" and state.get("map_authorization") else DEPENDENCIES[i]
            if task["status"] not in STATUSES or task["depends_on"] != expected:
                raise ValueError("Invalid task status/dependencies")
        return state, digest(raw)

    def authorize_map_batch(self, *, expected_hash: str, operation_key: str, decision_ref: str, run_ref: str):
        """Record the owner's explicit T07–T10 priority amendment, preserving B and all checks."""
        request = {"tasks": ["T07", "T08", "T09", "T10"], "decision_ref": decision_ref,
                   "run_ref": run_ref, "T07_depends_on": ["T02"]}
        request_hash = digest(canonical_json(request).encode())
        if not operation_key or len(operation_key) > 128:
            raise ValueError("A stable operation_key of 1–128 characters is required")
        with locked(self.lock):
            state, actual = self.read()
            if operation_key in state["operations"]:
                if state["operations"][operation_key] != request_hash:
                    raise ValueError("Idempotency conflict")
                self._views(state)
                return state
            if actual != expected_hash:
                raise ValueError("Revision conflict")
            if not workspace_path(self.root, decision_ref).is_file():
                raise ValueError("Missing accepted owner decision")
            if any(t["status"] != "done" for t in state["tasks"][:4]):
                raise ValueError("Package A is unfinished")
            if state.get("map_authorization"):
                raise ValueError("Map authorization already recorded; preserve its history")
            state["map_authorization"] = {**request, "recorded_at": now(),
                                           "previous_T07_depends_on": ["T02", "T06"],
                                           "basis": "Owner request: T06 was a sequencing constraint; T02 is the technical prerequisite"}
            state["authorized_task_ids"] = request["tasks"]
            state["authorized_phase"] = "C+T10"
            state["tasks"][7]["depends_on"] = ["T02"]
            state["run_ref"] = run_ref
            state["operations"][operation_key] = request_hash
            state.setdefault("history", []).append({"at": now(), "operation_key": operation_key,
                                                     "kind": "owner_authorization", **request})
            state["generation"] += 1
            if digest(self.path.read_bytes()) != actual:
                raise ValueError("Journal changed during authorization")
            self._write(state)
            return state

    def _write(self, state):
        atomic_write(self.path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        self._views(state)

    def update(self, task_id: str, status: str, *, expected_hash: str, operation_key: str,
               result_refs=None, test_refs=None, revision=None, blocker=None):
        request = {"task_id": task_id, "status": status, "result_refs": sorted(set(result_refs or [])),
                   "test_refs": sorted(set(test_refs or [])), "revision": revision, "blocker": blocker}
        request_hash = digest(canonical_json(request).encode())
        if not operation_key or len(operation_key) > 128:
            raise ValueError("A stable operation_key of 1–128 characters is required")
        with locked(self.lock):
            state, actual_hash = self.read()
            if operation_key in state["operations"]:
                if state["operations"][operation_key] != request_hash:
                    raise ValueError("Idempotency conflict; existing result was preserved")
                self._views(state)
                return state
            if expected_hash != actual_hash:
                raise ValueError("Revision conflict; reload tasks.json before writing")
            if status not in STATUSES:
                raise ValueError("Unknown status")
            task = next((t for t in state["tasks"] if t["id"] == task_id), None)
            if task is None:
                raise ValueError("Unknown task ID")
            allowed = task_id in state["authorized_task_ids"] if "authorized_task_ids" in state else task["phase"] == state["authorized_phase"]
            if not allowed:
                raise ValueError("Task is outside the recorded authorized phase")
            if status in {"in_progress", "done"} and any(
                    next(t for t in state["tasks"] if t["id"] == dep)["status"] != "done" for dep in task["depends_on"]):
                raise ValueError("Dependencies are unfinished")
            if status == "blocked" and not blocker:
                raise ValueError("Blocked tasks require a concrete reason")
            for ref in [*request["result_refs"], *request["test_refs"]]:
                if not workspace_path(self.root, ref).is_file():
                    raise ValueError(f"Missing evidence: {ref}")
            if status == "done":
                if not revision or not request["result_refs"] or not request["test_refs"] or blocker:
                    raise ValueError("Done requires a revision, results and passed checks, without a blocker")
                for ref in request["test_refs"]:
                    check = json.loads(workspace_path(self.root, ref).read_text(encoding="utf-8"))
                    if check.get("status") != "passed" or check.get("task_id") != task_id or check.get("revision") != revision:
                        raise ValueError("Done requires passed evidence for this task and revision")
            task.update({k: deepcopy(v) for k, v in request.items() if k != "task_id"})
            task["updated_at"] = now()
            state.setdefault("history", []).append({"at": task["updated_at"], "operation_key": operation_key,
                                                     "kind": "task_update", **deepcopy(request)})
            state["operations"][operation_key] = request_hash
            state["generation"] += 1
            # Detect even a non-cooperating editor between read and replacement.
            if digest(self.path.read_bytes()) != actual_hash:
                raise ValueError("Journal changed during update; no write performed")
            self._write(state)
            return state

    def sync(self):
        with locked(self.lock):
            state, _ = self.read()
            self._views(state)
            return state

    def _views(self, state):
        tasks = state["tasks"]
        done = [t["id"] for t in tasks if t["status"] == "done"]
        blocked = [f"{t['id']}: {t['blocker']}" for t in tasks if t["status"] == "blocked"]
        available = [t["id"] for t in tasks if t["status"] != "done" and set(t["depends_on"]) <= set(done)]
        allowed_ids = state.get("authorized_task_ids", [t["id"] for t in tasks if t["phase"] == state["authorized_phase"]])
        authorized_available = [i for i in allowed_ids if i in available]
        next_id = (authorized_available or available or ["нет"])[0]
        next_task = next((t for t in tasks if t["id"] == next_id), None)
        permitted = next_task is not None and next_id in allowed_ids
        action = f"Продолжить {next_id} в разрешённом пакете {state['authorized_phase']}." if permitted else f"Остановиться. Ждать отдельного поручения владельца на пакет {next_task['phase'] if next_task else '—'}; затем начать {next_id}."
        if state.get("map_authorization") and not authorized_available:
            action = "Остановиться после T07–T10. T04–T06 и T11–T12 требуют отдельного поручения; идеи не реализовывать."
        marker = f"Сформировано из development/tasks.json; generation={state['generation']}. Не редактировать отдельно."
        status = ["# Прогресс развития", "", marker, "", "| ID | Пакет | Статус |", "|---|---|---|"]
        status.extend(f"| {t['id']} | {t['phase']} | {t['status']} |" for t in tasks)
        status += ["", f"Следующий доступный ID: {next_id}.", action,
                   "", "Блокировки: " + ("; ".join(blocked) or "нет"), "",
                   f"Отчёт: [{state['run_ref']}/REPORT.md](../{state['run_ref']}/REPORT.md)."]
        handoff = ["# HANDOFF", "", marker, "", "Корень: AI_VIDEO_02 (каталог над development/).",
                   f"Разрешённый пакет: {state['authorized_phase']}. Следующий доступный ID: {next_id}.",
                   "Закрыты: " + (", ".join(done) or "нет") + ".",
                   f"Профиль: [{state['profile_ref']}](../{state['profile_ref']}).",
                   "Интерфейсы: [INTERFACES.md](INTERFACES.md). План: [01_DEVELOPMENT_PLAN.md](01_DEVELOPMENT_PLAN.md).",
                   "", "Изменения и результаты — ссылки из единственного журнала:"]
        for task in tasks:
            if task["result_refs"]:
                handoff.append(f"- {task['id']} ({task['status']}): " + "; ".join(f"[{p}](../{p})" for p in task["result_refs"]))
        handoff += ["", "Ограничения: production не запускался; нового видео нет. Проверки моделей/рендера имитированы либо исключены.",
                    "Токены/настройки модели, недоступные из структурированного источника, — null.",
                    "Финальная замена Ruslan второго выпуска пока вне штатной цепочки QA/package (T05–T06).",
                    "Исторические статусы: читать существующий registry.sqlite; исходный срез — production_baseline.json в отчёте.",
                    "Graphify/Obsidian и T04–T12 не выполнялись в пакете A.",
                    "", "Точное следующее действие: " + action,
                    "Не повторять аудит и выполненный пакет. Сначала читать этот HANDOFF и tasks.json.",
                    f"Отчёт/откат: [{state['run_ref']}/REPORT.md](../{state['run_ref']}/REPORT.md)."]
        if state.get("map_authorization"):
            handoff += ["", "Текущее разрешение: T07 → T08 → T09 → T10; T04–T06 не закрывать.",
                        f"Решение: [{state['map_authorization']['decision_ref']}](../{state['map_authorization']['decision_ref']}).",
                        f"Карта, проверки, ограничения и откат: [{state['run_ref']}/MAP_REPORT.md](../{state['run_ref']}/MAP_REPORT.md)."]
        atomic_write(self.path.parent / "STATUS.md", "\n".join(status) + "\n")
        atomic_write(self.path.parent / "HANDOFF.md", "\n".join(handoff) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["status", "sync"])
    p.add_argument("--root", type=Path, default=Path.cwd())
    args = p.parse_args(argv)
    try:
        journal = Journal(args.root)
        if args.command == "sync":
            journal.sync()
        state, version = journal.read()
        print(json.dumps({"generation": state["generation"], "sha256": version,
                          "authorized_phase": state["authorized_phase"],
                          "tasks": {t["id"]: t["status"] for t in state["tasks"]}}))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
