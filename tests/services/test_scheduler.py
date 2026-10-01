from datetime import date
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services import scheduler


def test_beat_has_weekly_friday_and_configured_daily_schedule():
    settings = Settings(publication_hour=11, publication_minute=25)
    app = scheduler._make_celery(settings)
    schedules = app.conf.beat_schedule
    assert app.conf.timezone == "America/Argentina/Buenos_Aires"
    weekly = schedules["generate-weekly-threads"]
    assert weekly["task"] == "app.services.scheduler.generar_hilos_semanales"
    assert weekly["schedule"].day_of_week == {5}
    assert weekly["schedule"].hour == {18} and weekly["schedule"].minute == {0}
    daily = schedules["publish-daily-stories"]
    assert daily["task"] == "app.services.scheduler.publicar_historias_pendientes"
    # Every 1 min (all 24 hours), not every 15 — each client can have its own
    # per-day publish time (content_jobs.publish_schedule) down to the exact
    # minute, so this has to check as close to continuously as practical
    # instead of waiting up to ~14 min for the next fixed 15-min tick.
    assert daily["schedule"].minute == set(range(60))
    assert daily["schedule"].hour == set(range(24))


@pytest.mark.parametrize("task_name,worker", [
    ("generar_hilos_semanales", "generate_weekly"),
    ("publicar_historias_pendientes", "publish_daily"),
])
def test_task_dispatches_to_portal_with_admin_database(monkeypatch, task_name, worker):
    from app.db import supabase
    from app.services import content_jobs
    db = object()
    monkeypatch.setattr(supabase, "get_admin_client", lambda: db)
    runner = Mock()
    monkeypatch.setattr(content_jobs, worker, runner)
    getattr(scheduler, task_name).run()
    runner.assert_called_once_with(db)


@pytest.mark.parametrize("setting,value", [("publication_hour", 24), ("publication_minute", -1)])
def test_invalid_schedule_is_rejected(setting, value):
    with pytest.raises(ValidationError):
        Settings(**{setting: value})
