import inspect
import subprocess

from reporter import approval, poller


def test_vague_words_alone_are_never_sufficient_privileged_approval():
    for word in ("lanjut", "ok", "OK.", "yes", "continue", "Continue!"):
        assert approval.is_vague_only(word) is True
        assert approval.contains_privileged_keyword(word) is False


def test_vague_word_embedded_in_a_longer_privileged_message_is_not_flagged_vague():
    text = "ok, go ahead and COMMIT the release branch"
    assert approval.is_vague_only(text) is False
    assert approval.contains_privileged_keyword(text) is True


def test_privileged_keywords_are_detected_case_insensitively():
    for word in ("commit", "PUSH", "Merge", "deploy", "destructive_db"):
        assert approval.contains_privileged_keyword(f"please {word} now") is True


def test_privileged_instruction_text_is_preserved_literally_in_the_inbox(store, settings, telegram):
    from reporter import notifications
    notifications.handle_event(
        store=store, settings=settings, telegram=telegram,
        agent_id="claude-1", agent_name="claude-1", task_id="task-a",
        status="OWNER_ACTION_REQUIRED", summary="need decision", details=None, client_timestamp=None,
    )
    literal_text = "APPROVE DEPLOY EMERGENT-MVP-001 — proceed exactly as staged, nothing else"
    update = {
        "update_id": 1,
        "message": {"message_id": 7001, "chat": {"id": int(settings.chat_id)}, "text": literal_text},
    }
    poller.process_update(store, settings, update)
    entry = store.inbox_for("claude-1")[0]
    assert entry["message_text"] == literal_text  # byte-for-byte, never paraphrased
    assert entry["contains_privileged_keyword"] == 1


def test_approval_module_never_exposes_an_approved_or_authorized_concept():
    # Structural guarantee: the reporter is messaging infrastructure, not an
    # authorization engine -- there must be no function anywhere in this
    # module whose name implies it decides whether an action may proceed.
    names = [name for name, _ in inspect.getmembers(approval, inspect.isfunction)]
    for name in names:
        assert "approve" not in name.lower()
        assert "authoriz" not in name.lower()


def test_poller_module_never_invokes_a_shell_or_git_or_deploy_command(monkeypatch, store, settings, telegram):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    from reporter import notifications
    notifications.handle_event(
        store=store, settings=settings, telegram=telegram,
        agent_id="claude-1", agent_name="claude-1", task_id="task-a",
        status="OWNER_ACTION_REQUIRED", summary="need decision", details=None, client_timestamp=None,
    )
    update = {
        "update_id": 1,
        "message": {"message_id": 7002, "chat": {"id": int(settings.chat_id)},
                    "text": "APPROVE DEPLOY EMERGENT-MVP-001"},
    }
    poller.process_update(store, settings, update)
    assert calls == []  # the reporter itself never shells out for a privileged instruction
