import app.email_worker as worker


def test_worker_cycle_reports_sync_and_delivery_results(monkeypatch):
    expected = {
        "sync": {"success": True, "inserted": 1},
        "processed": [{"status": "scored"}],
        "deliveries": [{"status": "sent"}, {"status": "pending"}],
    }
    monkeypatch.setattr(worker, "sync_and_process_emails", lambda limit: expected)
    result = worker.run_cycle(limit=7)
    assert result == expected


def test_worker_cycle_keeps_sync_failure_visible(monkeypatch):
    expected = {
        "sync": {"success": False, "error": "IMAP unavailable"},
        "processed": [],
        "deliveries": [],
    }
    monkeypatch.setattr(worker, "sync_and_process_emails", lambda limit: expected)
    result = worker.run_cycle()
    assert result["sync"]["success"] is False
    assert result["sync"]["error"] == "IMAP unavailable"
