from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.engine.exceptions import ClaudeGenerationError, MetaPublishError
from app.engine.schemas import HiloGenerado, ResultadoPublicacion
from app.services import content_jobs as jobs


class Query:
    def __init__(self, db, name):
        self.db, self.name, self.filters = db, name, []
        self.in_filters = []
        self.op = None
        self.values = None
        self.sort = []
        self.bounds = None
        self.or_expr = None

    def select(self, *_): return self
    def eq(self, key, value):
        self.filters.append((key, value))
        return self
    def in_(self, key, values):
        self.in_filters.append((key, values))
        return self
    def or_(self, expr):
        self.or_expr = expr
        return self
    def order(self, key):
        self.sort.append(key)
        return self
    def update(self, values):
        self.op = "update"
        self.values = values
        return self
    def delete(self):
        self.op = "delete"
        return self
    def range(self, start, end):
        self.bounds = (start, end)
        return self
    def _matches_or(self, row):
        if not self.or_expr:
            return True
        for clause in self.or_expr.split(','):
            field, op, value = clause.split('.', 2)
            cell = row.get(field)
            if op == 'is' and value == 'null' and cell is None:
                return True
            if op == 'lte' and cell is not None and str(cell) <= value:
                return True
        return False
    def execute(self):
        rows = [r for r in self.db.rows[self.name]
                if all(r.get(k) == v for k, v in self.filters)
                and all(r.get(k) in values for k, values in self.in_filters)
                and self._matches_or(r)]
        for key in reversed(self.sort):
            rows.sort(key=lambda r: r[key])
        if self.bounds:
            rows = rows[self.bounds[0]:self.bounds[1] + 1]
        if self.op == "delete":
            deleted_ids = {id(row) for row in rows}
            self.db.rows[self.name] = [
                row for row in self.db.rows[self.name] if id(row) not in deleted_ids
            ]
            if self.name == "story_groups":
                deleted_group_ids = {row.get("id") for row in rows}
                self.db.rows["stories"] = [
                    story for story in self.db.rows["stories"]
                    if story.get("story_group_id") not in deleted_group_ids
                ]
        elif self.op == "update":
            for row in rows: row.update(self.values)
        return SimpleNamespace(data=deepcopy(rows))


class Database:
    def __init__(self, clients=(), stories=()):
        self.rows = {"clients": list(clients), "stories": list(stories), "story_groups": [],
                     "client_images": []}
        self.saved = []

    def table(self, name): return Query(self, name)
    def rpc(self, name, payload):
        assert name == "persist_generated_thread"
        self.saved.append(payload)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data="group-id"))


def client(id="good", **overrides):
    base = dict(id=id, agency_id="agency", name="Studio", active=True,
                business_description="Helpful studio", tone_examples=[["a", "b", "c", "d"]],
                topics=["design"], weekly_focus="Launch", weekly_focus_expires_at="2026-09-20",
                drive_folder_id=id, logo_url="logo", calendly_link="booking", prob_link=1)
    base.update(overrides)
    return base


def images(n=5): return [(f"file-{i}", f"image-{i}.jpg", b"image") for i in range(n)]


def image_metadata(n=5): return [(f"file-{i}", f"image-{i}.jpg") for i in range(n)]


def generated(cta_agregado=True):
    return HiloGenerado(historias=[f"Story {i}" for i in range(4)],
        imagenes_originales_url=[f"raw-{i}" for i in range(4)],
        imagenes_editadas_url=[f"edited-{i}" for i in range(4)],
        drive_file_ids_usados=[f"file-{i}" for i in range(4)],
        cta_agregado=cta_agregado)


@pytest.fixture
def generation(monkeypatch):
    drive = Mock(return_value=image_metadata())
    download = Mock(side_effect=lambda rows, limit=None, **k: [(*row, b"image") for row in rows[:limit]])
    engine = Mock(return_value=generated())
    monkeypatch.setattr(jobs.drive, "list_image_metadata", drive)
    monkeypatch.setattr(jobs.drive, "download_images", download)
    monkeypatch.setattr(jobs.content, "generar_hilo", engine)
    return drive, engine


def test_build_content_config_defaults_null_business_description_and_tone_examples():
    row = client(business_description=None, tone_examples=None, prob_link=None)
    config = jobs.build_content_config(row)
    assert config.business_description == "" and config.tone_examples == [] and config.prob_link == 0.0


def test_setup_gaps_reports_blocking_and_recommended():
    assert jobs.setup_gaps({}) == {
        "blocking": ["descripcion", "drive"], "recommended": ["ejemplos", "temas"],
    }
    assert jobs.setup_gaps(client(business_description="   "))["blocking"] == ["descripcion"]
    assert jobs.setup_gaps(client(drive_folder_id="   "))["blocking"] == ["drive"]
    assert jobs.setup_gaps(client()) == {"blocking": [], "recommended": []}
    assert jobs.setup_gaps(client(tone_examples=None)) == {
        "blocking": [], "recommended": ["ejemplos"],
    }
    assert jobs.setup_gaps(client(logo_url=None, calendly_link=None)) == {
        "blocking": [], "recommended": [],
    }


def test_generate_for_client_returns_existing_group_without_external_work(generation):
    drive, engine = generation
    db = Database([client()])
    db.rows["story_groups"].append({
        "id": "existing-group", "client_id": "good", "generation_week": "2026-09-14",
    })

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result == {"created": False, "group_id": "existing-group", "recycled": False}
    drive.assert_not_called()
    engine.assert_not_called()
    assert db.saved == []


def test_generate_for_client_blocks_when_existing_batch_still_has_active_stories(generation):
    drive, engine = generation
    db = Database([client()])
    db.rows["story_groups"].append({
        "id": "existing-group", "client_id": "good", "generation_week": "2026-09-14",
    })
    db.rows["stories"].extend([
        {"id": "s1", "story_group_id": "existing-group", "estado": "cancelada"},
        {"id": "s2", "story_group_id": "existing-group", "estado": "pendiente"},
    ])

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result == {"created": False, "group_id": "existing-group", "recycled": False}
    drive.assert_not_called()
    engine.assert_not_called()
    assert any(group["id"] == "existing-group" for group in db.rows["story_groups"])


def test_generate_for_client_regenerates_when_existing_batch_is_fully_cancelled(generation):
    db = Database([client()])
    db.rows["story_groups"].append({
        "id": "existing-group", "client_id": "good", "generation_week": "2026-09-14",
    })
    db.rows["stories"].extend([
        {"id": "s1", "story_group_id": "existing-group", "estado": "cancelada"},
        {"id": "s2", "story_group_id": "existing-group", "estado": "cancelada"},
    ])

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result == {"created": True, "group_id": "group-id", "recycled": False}
    assert not any(group["id"] == "existing-group" for group in db.rows["story_groups"])
    assert not any(story["story_group_id"] == "existing-group" for story in db.rows["stories"])
    assert len(db.saved) == 1


def test_generate_for_client_persists_and_reports_success(generation):
    db = Database([client()])

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result == {"created": True, "group_id": "group-id", "recycled": False}
    assert len(db.saved) == 1
    assert db.saved[0]["p_client_id"] == "good"


def test_generate_for_client_clears_pool_bajo_after_a_generation_without_recycling(generation):
    db = Database([client(pool_bajo_at="2026-09-10T00:00:00+00:00")])

    jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert db.rows["clients"][0]["pool_bajo_at"] is None


def test_generate_for_client_marks_pool_bajo_after_recycling(generation):
    _, engine = generation
    db = Database([client()])
    now = datetime.now(timezone.utc)
    db.rows["client_images"] = [
        {"client_id": "good", "drive_file_id": f"file-{index}",
         "last_used_at": (now - timedelta(days=1)).isoformat()}
        for index in range(3)
    ]
    engine.return_value = HiloGenerado(
        historias=[f"Story {index}" for index in range(4)],
        imagenes_originales_url=[f"raw-{index}" for index in range(4)],
        imagenes_editadas_url=[f"edited-{index}" for index in range(4)],
        drive_file_ids_usados=["file-3", "file-4", "file-0", "file-1"],
    )

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result["recycled"] is True
    assert db.rows["clients"][0]["pool_bajo_at"] is not None


def test_generate_for_client_keeps_success_when_pool_bookkeeping_fails(generation):
    class PoolBookkeepingFailureDatabase(Database):
        def table(self, name):
            if name != "clients":
                return super().table(name)

            class FailingQuery(Query):
                def execute(self):
                    if self.op == "update" and self.values and "pool_bajo_at" in self.values:
                        raise RuntimeError("pool flag unavailable")
                    return super().execute()

            return FailingQuery(self, name)

    db = PoolBookkeepingFailureDatabase([client()])

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result == {"created": True, "group_id": "group-id", "recycled": False}
    assert len(db.saved) == 1


class RecordingDatabase(Database):
    """Records every `clients` update with its filters, so tests can assert on
    the conditional weekly_format clear (filtered by id AND by the format used)."""
    def __init__(self, *args, fail_format_clear=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_updates = []
        self.fail_format_clear = fail_format_clear

    def table(self, name):
        if name != "clients":
            return super().table(name)
        db = self

        class RecordingQuery(Query):
            def execute(self):
                if self.op == "update":
                    db.client_updates.append((self.values, list(self.filters)))
                    if db.fail_format_clear and "weekly_format" in self.values:
                        raise RuntimeError("format clear unavailable")
                return super().execute()

        return RecordingQuery(self, name)


def _format_clears(db):
    return [(values, filters) for values, filters in db.client_updates if "weekly_format" in values]


def test_build_content_config_copies_weekly_format():
    assert jobs.build_content_config(client(weekly_format="promo")).weekly_format == "promo"
    assert jobs.build_content_config(client()).weekly_format is None


def test_generacion_consume_el_formato_de_la_semana(generation):
    _, engine = generation
    db = RecordingDatabase([client(weekly_format="promo")])

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result["created"] is True
    assert engine.call_args.args[0].weekly_format == "promo"
    assert _format_clears(db) == [
        ({"weekly_format": None}, [("id", "good"), ("weekly_format", "promo")])
    ]
    assert db.rows["clients"][0]["weekly_format"] is None


def test_generacion_sin_formato_no_toca_la_columna(generation):
    db = RecordingDatabase([client()])

    jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert _format_clears(db) == []


def test_falla_al_limpiar_el_formato_no_rompe_el_hilo(generation):
    db = RecordingDatabase([client(weekly_format="promo")], fail_format_clear=True)

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result == {"created": True, "group_id": "group-id", "recycled": False}
    assert len(db.saved) == 1
    assert "generation_error" not in db.rows["clients"][0]


def test_hilo_ya_existente_no_consume_el_formato(generation):
    db = RecordingDatabase([client(weekly_format="promo")])
    db.rows["story_groups"].append({
        "id": "existing-group", "client_id": "good", "generation_week": "2026-09-14",
    })

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result["created"] is False
    assert _format_clears(db) == []
    assert db.rows["clients"][0]["weekly_format"] == "promo"


def test_generate_for_client_propagates_operational_errors(generation):
    drive, _ = generation
    drive.side_effect = RuntimeError("Drive unavailable")
    db = Database([client()])

    with pytest.raises(RuntimeError, match="Drive unavailable"):
        jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert db.saved == []
    assert "generation_error" not in db.rows["clients"][0]


def test_generate_for_client_by_id_fetches_fresh_row_and_generates(generation):
    db = Database([client()])

    jobs.generate_for_client_by_id(db, "good", date(2026, 9, 18))

    assert len(db.saved) == 1


def test_generate_for_client_by_id_skips_inactive_or_missing_client(generation):
    db = Database([client(active=False)])

    jobs.generate_for_client_by_id(db, "good", date(2026, 9, 18))

    assert db.saved == []
    jobs.generate_for_client_by_id(Database([]), "nope", date(2026, 9, 18))


def test_weekly_skips_client_with_no_business_description_without_error(generation):
    drive, engine = generation
    db = Database([client("onboarding", business_description=None, tone_examples=None), client()])
    jobs.generate_weekly(db, date(2026, 9, 18))
    assert "generation_error" not in db.rows["clients"][0]
    assert {p["p_client_id"] for p in db.saved} == {"good"}
    drive.assert_called_once_with("good", None)
    engine.assert_called_once()


def test_weekly_skips_client_without_drive_folder_without_writing_error(generation):
    drive, engine = generation
    db = Database([client("onboarding", drive_folder_id=""), client()])

    jobs.generate_weekly(db, date(2026, 9, 18))

    drive.assert_called_once_with("good", None)
    engine.assert_called_once()
    assert "generation_error" not in db.rows["clients"][0]
    assert {p["p_client_id"] for p in db.saved} == {"good"}


def test_generate_for_client_by_id_skips_incomplete_client(generation):
    drive, engine = generation
    db = Database([client(business_description="")])

    jobs.generate_for_client_by_id(db, "good", date(2026, 9, 18))

    assert db.saved == []
    assert "generation_error" not in db.rows["clients"][0]
    drive.assert_not_called()
    engine.assert_not_called()


def test_generate_for_client_still_allows_manual_generation_of_incomplete_client(generation):
    db = Database([client(tone_examples=None, topics=None)])

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result["created"] is True
    assert {p["p_client_id"] for p in db.saved} == {"good"}


def test_weekly_continues_after_insufficient_images_and_saves_complete_thread(generation):
    drive, engine = generation
    drive.side_effect = lambda folder, *a, **k: image_metadata(2 if folder == "a-short" else 5)
    db = Database([client("a-short"), client()])
    jobs.generate_weekly(db, date(2026, 9, 18))
    assert "sin imágenes suficientes" in db.rows["clients"][0]["generation_error"]
    assert db.rows["clients"][0]["weekly_focus"] == "Launch"
    assert len(db.saved) == 1
    config, selected = engine.call_args.args
    assert config.client_id == "good" and config.nombre_negocio == "Studio"
    assert config.topics == ["design"] and config.tone_examples == [["a", "b", "c", "d"]]
    assert [image.drive_file_id for image in selected] == [f"file-{i}" for i in range(4)]
    saved = db.saved[0]
    assert saved["p_used_focus"] == "Launch"
    assert saved["p_used_focus_expires_at"] == "2026-09-20"
    assert saved["p_generation_week"] == "2026-09-14"
    assert saved["p_scheduled_date"] == "2026-09-21"
    assert len(saved["p_stories"]) == 4 and len(saved["p_images"]) == 4
    assert saved["p_stories"][0]["image_original_url"] == "raw-0"
    assert saved["p_stories"][0]["image_url"] == "edited-0"
    assert saved["p_stories"][3]["agregar_cta"] is True
    assert saved["p_images"][0] == {"drive_file_id": "file-0", "drive_file_name": "image-0.jpg"}
    # Spread across the week (Mon/Wed/Fri/Sun) instead of all four sharing p_scheduled_date.
    assert [s["fecha_publicacion"] for s in saved["p_stories"]] == [
        "2026-09-21", "2026-09-23", "2026-09-25", "2026-09-27"]


def test_weekly_uses_clients_custom_publish_schedule(generation):
    _, engine = generation
    custom = [{"day": 1, "time": "08:00"}, {"day": 3, "time": "12:30"},
              {"day": 5, "time": "18:00"}, {"day": 6, "time": "20:15"}]  # Tue/Thu/Sat/Sun
    db = Database([client(publish_days=custom)])
    jobs.generate_weekly(db, date(2026, 9, 18))
    stories = db.saved[0]["p_stories"]
    assert [s["fecha_publicacion"] for s in stories] == [
        "2026-09-22", "2026-09-24", "2026-09-26", "2026-09-27"]
    assert [s["hora_publicacion"] for s in stories] == [
        "08:00:00", "12:30:00", "18:00:00", "20:15:00"]


def test_weekly_publish_together_uses_first_ordered_slot_for_all_four_stories(generation):
    custom = [{"day": 5, "time": "18:00"}, {"day": 1, "time": "08:30"}]
    db = Database([client(publish_days=custom, publish_together=True)])

    jobs.generate_weekly(db, date(2026, 9, 18))

    stories = db.saved[0]["p_stories"]
    assert [story["fecha_publicacion"] for story in stories] == ["2026-09-22"] * 4
    assert [story["hora_publicacion"] for story in stories] == ["08:30:00"] * 4


def test_weekly_uses_explicit_uneven_story_counts(generation):
    custom = [
        {"day": 0, "time": "08:00", "count": 3},
        {"day": 4, "time": "18:00", "count": 1},
    ]
    # An old true flag may remain after the new UI saves explicit counts;
    # explicit counts must take precedence over the legacy fallback.
    db = Database([client(publish_days=custom, publish_together=True)])

    jobs.generate_weekly(db, date(2026, 9, 18))

    stories = db.saved[0]["p_stories"]
    assert [story["fecha_publicacion"] for story in stories] == [
        "2026-09-21", "2026-09-21", "2026-09-21", "2026-09-25"]
    assert [story["hora_publicacion"] for story in stories] == [
        "08:00:00", "08:00:00", "08:00:00", "18:00:00"]


def test_weekly_single_explicit_day_replaces_publish_together(generation):
    db = Database([client(publish_days=[{"day": 2, "time": "10:15", "count": 4}])])

    jobs.generate_weekly(db, date(2026, 9, 18))

    stories = db.saved[0]["p_stories"]
    assert [story["fecha_publicacion"] for story in stories] == ["2026-09-23"] * 4
    assert [story["hora_publicacion"] for story in stories] == ["10:15:00"] * 4


@pytest.mark.parametrize("publish_together", [False, None])
def test_weekly_publish_together_disabled_or_missing_preserves_distribution(
        generation, publish_together):
    custom = [{"day": 1, "time": "08:00"}, {"day": 5, "time": "18:00"}]
    overrides = {"publish_days": custom}
    if publish_together is not None:
        overrides["publish_together"] = publish_together
    db = Database([client(**overrides)])

    jobs.generate_weekly(db, date(2026, 9, 18))

    stories = db.saved[0]["p_stories"]
    assert [story["fecha_publicacion"] for story in stories] == [
        "2026-09-22", "2026-09-22", "2026-09-26", "2026-09-26"]
    assert [story["hora_publicacion"] for story in stories] == [
        "08:00:00", "08:00:00", "18:00:00", "18:00:00"]


@pytest.mark.parametrize("custom,expected_dates,expected_times", [
    ([{"day": 2, "time": "10:00"}],
     ["2026-09-23"] * 4, ["10:00:00"] * 4),
    ([{"day": 0, "time": "08:00"}, {"day": 4, "time": "18:00"}],
     ["2026-09-21", "2026-09-21", "2026-09-25", "2026-09-25"],
     ["08:00:00", "08:00:00", "18:00:00", "18:00:00"]),
    ([{"day": 1, "time": "08:00"}, {"day": 3, "time": "12:00"},
      {"day": 6, "time": "20:00"}],
     ["2026-09-22", "2026-09-22", "2026-09-24", "2026-09-27"],
     ["08:00:00", "08:00:00", "12:00:00", "20:00:00"]),
])
def test_weekly_balances_four_stories_across_one_to_three_days(
        generation, custom, expected_dates, expected_times):
    db = Database([client(publish_days=custom)])
    jobs.generate_weekly(db, date(2026, 9, 18))
    stories = db.saved[0]["p_stories"]
    assert [story["fecha_publicacion"] for story in stories] == expected_dates
    assert [story["hora_publicacion"] for story in stories] == expected_times


_GLOBAL_SCHEDULE = tuple((offset, "09:00", 1) for offset in jobs.PUBLISH_DAY_OFFSETS)


@pytest.mark.parametrize("days,expected", [
    (None, _GLOBAL_SCHEDULE),
    ([{"day":0,"time":"08:00"},{"day":2,"time":"08:00"},{"day":4,"time":"08:00"},{"day":6,"time":"08:00"}],
     ((0,"08:00",1),(2,"08:00",1),(4,"08:00",1),(6,"08:00",1))),
    ([{"day":6,"time":"08:00"},{"day":0,"time":"08:00"},{"day":2,"time":"08:00"},{"day":4,"time":"08:00"}],
     ((0,"08:00",1),(2,"08:00",1),(4,"08:00",1),(6,"08:00",1))),  # sorted by day
    ([{"day":5,"time":"18:00"},{"day":1,"time":"08:00"}],
     ((1,"08:00",2),(5,"18:00",2))),
    ([{"day":3,"time":"12:30"}], ((3,"12:30",4),)),
    ([{"day":0,"time":"08:00","count":3},{"day":4,"time":"18:00","count":1}],
     ((0,"08:00",3),(4,"18:00",1))),
    ([{"day":0,"time":"08:00","count":1},{"day":4,"time":"18:00","count":1}],
     ((0,"08:00",2),(4,"18:00",2))),  # invalid total -> legacy split
    ([{"day":0,"time":"08:00","count":3},{"day":4,"time":"18:00"}],
     ((0,"08:00",2),(4,"18:00",2))),  # partial counts -> legacy split
    ([], _GLOBAL_SCHEDULE),
    ([{"day":0,"time":"08:00"},{"day":0,"time":"09:00"},{"day":2,"time":"08:00"},{"day":4,"time":"08:00"}],
     _GLOBAL_SCHEDULE),  # not 4 distinct days -> fall back
    ([{"day":0,"time":"08:00"},{"day":2,"time":"08:00"},{"day":4,"time":"08:00"},{"day":7,"time":"08:00"}],
     _GLOBAL_SCHEDULE),  # out of range -> fall back
    ([{"day":0,"time":"25:00"},{"day":2,"time":"08:00"},{"day":4,"time":"08:00"},{"day":6,"time":"08:00"}],
     _GLOBAL_SCHEDULE),  # invalid time -> fall back
    ("garbage", _GLOBAL_SCHEDULE),
])
def test_publish_schedule_validates_and_falls_back(days, expected):
    assert jobs.publish_schedule({"publish_days": days}) == tuple(expected)


def test_engine_error_does_not_stop_next_client_or_consume_focus(generation):
    _, engine = generation
    engine.side_effect = [ClaudeGenerationError("Claude unavailable"), generated()]
    db = Database([client("broken"), client()])
    jobs.generate_weekly(db, date(2026, 9, 18))
    assert db.rows["clients"][0]["generation_error"] == "Claude unavailable"
    assert db.rows["clients"][0]["weekly_focus"] == "Launch"
    assert [p["p_client_id"] for p in db.saved] == ["good"]


def test_history_read_failure_is_logged_marks_error_and_skips_only_that_client(
        generation, caplog):
    drive, engine = generation

    class HistoryFailureQuery(Query):
        def execute(self):
            if ("client_id", "broken") in self.filters:
                raise RuntimeError("Supabase history unavailable")
            return super().execute()

    class HistoryFailureDatabase(Database):
        def table(self, name):
            if name == "client_images":
                return HistoryFailureQuery(self, name)
            return super().table(name)

    db = HistoryFailureDatabase([client("broken"), client()])
    with caplog.at_level("ERROR"):
        jobs.generate_weekly(db, date(2026, 9, 18))

    assert "No se pudo leer el historial" in db.rows["clients"][0]["generation_error"]
    assert "Could not read image history for client broken" in caplog.text
    assert [p["p_client_id"] for p in db.saved] == ["good"]
    assert engine.call_count == 1
    assert drive.call_count == 2


def test_inactive_and_already_generated_clients_skip_engine(generation):
    drive, engine = generation
    disabled = client("disabled")
    disabled["active"] = False
    db = Database([disabled, client()])
    db.rows["story_groups"].append({"client_id": "good", "generation_week": "2026-09-14"})
    jobs.generate_weekly(db, date(2026, 9, 18))
    drive.assert_not_called()
    engine.assert_not_called()


def test_invalid_engine_result_is_not_partially_saved(generation):
    _, engine = generation
    result = generated()
    result.historias.pop()
    engine.return_value = result
    db = Database([client()])
    jobs.generate_weekly(db, date(2026, 9, 18))
    assert db.saved == []
    assert "four" in db.rows["clients"][0]["generation_error"]


def test_select_images_prioritizes_never_used_over_expired():
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    available = images(6)
    history = [{"drive_file_id": "file-0", "last_used_at": (now - timedelta(weeks=4)).isoformat()},
               {"drive_file_id": "file-1", "last_used_at": (now - timedelta(weeks=1)).isoformat()}]
    selected, recycled = jobs.seleccionar_imagenes(available, history, now)
    assert [item[0] for item in selected] == ["file-2", "file-3", "file-4", "file-5"]
    assert recycled is False


def test_select_images_uses_expired_before_recent_and_marks_recycling():
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    available = images(4)
    history = [{"drive_file_id": f"file-{i}",
                "last_used_at": (now - timedelta(weeks=1 + i)).isoformat()}
               for i in range(4)]
    selected, recycled = jobs.seleccionar_imagenes(available, history, now)
    assert [item[0] for item in selected] == ["file-3", "file-2", "file-1", "file-0"]
    assert recycled is True


def test_weekly_recycling_logs_a_warning_without_touching_generation_error(generation, caplog):
    now = datetime.now(timezone.utc)
    _, engine = generation
    db = Database([client()])
    db.rows["client_images"] = [{"client_id": "good", "drive_file_id": f"file-{i}",
        "last_used_at": (now - timedelta(days=1)).isoformat()} for i in range(3)]
    engine.return_value = HiloGenerado(
        historias=[f"Story {i}" for i in range(4)],
        imagenes_originales_url=[f"raw-{i}" for i in range(4)],
        imagenes_editadas_url=[f"edited-{i}" for i in range(4)],
        drive_file_ids_usados=["file-3", "file-4", "file-0", "file-1"],
    )
    with caplog.at_level("WARNING"):
        jobs.generate_weekly(db, date(2026, 9, 18))
    # Recycling is a benign, informational event: it must not be written into
    # generation_error, which is documented (AGENTS.md) as "the last generation
    # error" and gets cleared on every successful, persisted generation.
    assert len(db.saved) == 1
    assert "generation_error" not in db.rows["clients"][0]
    assert any("pool_bajo" in record.message for record in caplog.records)


def story(id="story", **updates):
    row = dict(id=id, story_group_id="group", client_id="good", order=1,
               image_url="edited", fecha_publicacion="2026-09-21", estado="pendiente",
               agregar_cta=True, aprobado=True, clients={"instagram_account_id": "ig",
               "meta_access_token_encrypted": "encrypted", "calendly_link": "booking"})
    row.update(updates)
    return row


@pytest.fixture
def publication(monkeypatch):
    engine = Mock(return_value=ResultadoPublicacion(ok=True, ig_media_id="media"))
    decrypt = Mock(return_value="plain-token")
    monkeypatch.setattr(jobs.content, "publicar_historia", engine)
    monkeypatch.setattr(jobs, "decrypt", decrypt)
    return engine, decrypt


def test_publication_success_uses_decrypted_token_and_only_pending_today(publication):
    engine, decrypt = publication
    db = Database(stories=[story(), story("future", fecha_publicacion="2026-09-22"),
                          story("old", estado="error")])
    jobs.publish_daily(db, date(2026, 9, 21))
    engine.assert_called_once_with(image_url="edited", instagram_account_id="ig",
        meta_access_token="plain-token", agregar_cta=True, calendly_link="booking")
    decrypt.assert_called_once_with("encrypted")
    assert db.rows["stories"][0]["estado"] == "publicado"
    assert db.rows["stories"][0]["ig_media_id"] == "media"
    assert db.rows["stories"][1]["estado"] == "pendiente"
    assert db.rows["stories"][2]["estado"] == "error"


@pytest.mark.parametrize("failure", [MetaPublishError("Meta failed"),
    ResultadoPublicacion(ok=False, error="Meta failed")])
def test_publication_error_is_saved_and_next_story_runs(publication, failure):
    engine, _ = publication
    engine.side_effect = [failure, ResultadoPublicacion(ok=True, ig_media_id="second")]
    db = Database(stories=[story(), story("next", order=2)])
    jobs.publish_daily(db, date(2026, 9, 21))
    assert db.rows["stories"][0]["estado"] == "error"
    assert db.rows["stories"][0]["error"] == "Meta failed"
    assert db.rows["stories"][1]["estado"] == "publicado"
    jobs.publish_daily(db, date(2026, 9, 21))
    assert engine.call_count == 2  # No automatic retries of errors or published rows.


def test_encryption_failure_never_calls_meta(publication):
    engine, decrypt = publication
    decrypt.side_effect = ValueError("Token de cifrado inválido o alterado")
    db = Database(stories=[story()])
    jobs.publish_daily(db, date(2026, 9, 21))
    engine.assert_not_called()
    assert db.rows["stories"][0]["estado"] == "error"
    assert "cifrado" in db.rows["stories"][0]["error"]


def test_unapproved_manual_story_never_publishes(publication):
    engine, _ = publication
    db = Database(stories=[story(aprobado=False)])
    jobs.publish_daily(db, date(2026, 9, 21))
    engine.assert_not_called()
    assert db.rows["stories"][0]["estado"] == "pendiente"


def test_publish_now_ignores_date_and_hour_gates(publication):
    engine, _ = publication
    db = Database(stories=[story(fecha_publicacion="2099-01-01", hora_publicacion="23:59:00")])

    result = jobs.publish_now(db, "good", "2099-01-01")

    engine.assert_called_once()
    assert db.rows["stories"][0]["estado"] == "publicado"
    assert result == {"publicadas": 1, "fallidas": 0}


def test_publish_now_retries_a_previously_failed_story(publication):
    engine, _ = publication
    db = Database(stories=[story(estado="error", error="Meta failed")])

    result = jobs.publish_now(db, "good", "2026-09-21")

    engine.assert_called_once()
    assert db.rows["stories"][0]["estado"] == "publicado"
    assert result == {"publicadas": 1, "fallidas": 0}


def test_publish_now_does_not_retry_published_cancelled_or_in_flight_stories(publication):
    engine, _ = publication
    db = Database(stories=[
        story("done", estado="publicado"),
        story("cancelled", estado="cancelada"),
        story("mid", estado="publicando"),
    ])

    result = jobs.publish_now(db, "good", "2026-09-21")

    engine.assert_not_called()
    assert result == {"publicadas": 0, "fallidas": 0}


def test_publish_now_skips_unapproved_and_other_clients_or_dates(publication):
    engine, _ = publication
    db = Database(stories=[
        story(aprobado=False),
        story("other-client", client_id="other"),
        story("other-date", fecha_publicacion="2026-09-22"),
    ])

    result = jobs.publish_now(db, "good", "2026-09-21")

    engine.assert_not_called()
    assert result == {"publicadas": 0, "fallidas": 0}


def test_publish_now_counts_failures(publication):
    engine, _ = publication
    engine.side_effect = MetaPublishError("Meta failed")
    db = Database(stories=[story()])

    result = jobs.publish_now(db, "good", "2026-09-21")

    assert result == {"publicadas": 0, "fallidas": 1}
    assert db.rows["stories"][0]["estado"] == "error"


def _tag(persona, escena): return {"persona": persona, "escena": escena}


def _rows(*ids): return [(i, f"{i}.jpg", b"x") for i in ids]


def test_elegir_hilo_puts_the_person_photo_last_and_varies_scenes():
    examinadas = _rows("a", "b", "c", "d", "e", "f")
    tags = {"a": _tag(True, "ambiente"), "b": _tag(False, "producto"), "c": _tag(False, "producto"),
            "d": _tag(False, "detalle"), "e": _tag(False, "exterior"), "f": _tag(True, "producto")}
    elegidas = jobs.elegir_hilo(examinadas, tags, frescas={r[0] for r in examinadas})
    assert [r[0] for r in elegidas] == ["b", "d", "e", "a"]  # c repite escena con b; a (persona) va última


def test_elegir_hilo_avoids_scenes_used_this_week_when_it_can():
    examinadas = _rows("a", "b", "c", "d", "e")
    tags = {"a": _tag(False, "producto"), "b": _tag(False, "ambiente"), "c": _tag(False, "detalle"),
            "d": _tag(False, "exterior"), "e": _tag(True, "grafico")}
    elegidas = jobs.elegir_hilo(examinadas, tags, {r[0] for r in examinadas}, escenas_recientes={"producto"})
    assert [r[0] for r in elegidas] == ["b", "c", "d", "e"]


def test_elegir_hilo_still_returns_four_when_nothing_is_tagged_or_there_is_no_person():
    examinadas = _rows("a", "b", "c", "d", "e")
    assert [r[0] for r in jobs.elegir_hilo(examinadas, {}, {r[0] for r in examinadas})] == ["a", "b", "c", "d"]
    tags = {r[0]: _tag(False, "producto") for r in examinadas}
    assert len(jobs.elegir_hilo(examinadas, tags, {r[0] for r in examinadas})) == 4


def test_elegir_hilo_prefers_fresh_photos_over_recently_used_ones():
    examinadas = _rows("a", "b", "c", "d", "e", "f")
    tags = {"a": _tag(False, "producto"), "b": _tag(False, "ambiente"), "c": _tag(False, "detalle"),
            "d": _tag(False, "exterior"), "e": _tag(True, "grafico"), "f": _tag(True, "ambiente")}
    elegidas = jobs.elegir_hilo(examinadas, tags, frescas={"a", "b", "c", "d", "f"})
    assert [r[0] for r in elegidas] == ["a", "c", "d", "f"]  # f es fresca; b repite su escena


def test_elegir_hilo_keeps_all_fresh_photos_when_only_three_are_available():
    examinadas = _rows("a", "b", "c", "d", "e", "f")
    tags = {"a": _tag(False, "producto"), "b": _tag(False, "producto"),
            "c": _tag(False, "producto"), "d": _tag(False, "detalle"),
            "e": _tag(False, "exterior"), "f": _tag(True, "ambiente")}

    elegidas = jobs.elegir_hilo(examinadas, tags, frescas={"a", "b", "c"})

    assert [row[0] for row in elegidas] == ["a", "b", "c", "f"]


def test_elegir_hilo_reserves_the_cta_scene_before_choosing_the_other_three():
    examinadas = _rows("a", "b", "c", "d", "e")
    tags = {"a": _tag(False, "producto"), "b": _tag(False, "ambiente"),
            "c": _tag(False, "detalle"), "d": _tag(False, "exterior"),
            "e": _tag(True, "producto")}

    elegidas = jobs.elegir_hilo(examinadas, tags, frescas={row[0] for row in examinadas})

    assert [row[0] for row in elegidas] == ["b", "c", "d", "e"]


@pytest.mark.parametrize("failed_index", [4, 8])
def test_generate_for_client_survives_optional_photo_download_errors(generation, monkeypatch, failed_index):
    metadata, engine = generation
    metadata.return_value = image_metadata(12)

    def download(rows, **kwargs):
        downloaded = []
        for file_id, name in rows:
            if file_id == f"file-{failed_index}":
                raise RuntimeError("optional photo unavailable")
            downloaded.append((file_id, name, b"image"))
        return downloaded

    monkeypatch.setattr(jobs.drive, "download_images", download)
    monkeypatch.setattr(jobs.image_tags, "classify_many", lambda rows: {
        row[0]: _tag(False, "producto") for row in rows})
    db = Database([client()])

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result["created"] is True
    assert [image.drive_file_id for image in engine.call_args.args[1]] == ["file-0", "file-1", "file-2", "file-3"]


@pytest.mark.parametrize("failed_index", [0, 3])
def test_generate_for_client_still_propagates_required_photo_download_errors(generation, monkeypatch, failed_index):
    _, engine = generation

    def download(rows, **kwargs):
        downloaded = []
        for file_id, name in rows:
            if file_id == f"file-{failed_index}":
                raise RuntimeError("required photo unavailable")
            downloaded.append((file_id, name, b"image"))
        return downloaded

    monkeypatch.setattr(jobs.drive, "download_images", download)
    db = Database([client()])

    with pytest.raises(RuntimeError, match="required photo unavailable"):
        jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    engine.assert_not_called()
    assert db.saved == []


@pytest.mark.parametrize("candidate_cap", [24, 10])
def test_image_candidate_cap_applies_even_when_all_downloaded_images_are_unreadable(monkeypatch, candidate_cap):
    attempted = []

    def download(rows, **kwargs):
        attempted.extend(file_id for file_id, _ in rows)
        return []

    monkeypatch.setattr(jobs, "IMAGE_EXAMINE_MAX", candidate_cap)
    monkeypatch.setattr(jobs.drive, "download_images", download)
    rows = jobs._elegir_y_descargar_hilo(
        Database(), "good", image_metadata(40), [], datetime.now(timezone.utc), None)

    assert rows == []
    assert attempted == [f"file-{index}" for index in range(candidate_cap)]


def test_generate_for_client_orders_photos_by_tags_and_only_classifies_new_ones(generation, monkeypatch):
    drive, engine = generation
    drive.return_value = image_metadata(6)
    engine.return_value = HiloGenerado(
        historias=[f"Story {index}" for index in range(4)],
        imagenes_originales_url=[f"raw-{index}" for index in range(4)],
        imagenes_editadas_url=[f"edited-{index}" for index in range(4)],
        drive_file_ids_usados=["file-1", "file-2", "file-3", "file-5"],
    )
    # Sin enfoque ni temas: es el escenario de a74 (con enfoque o tema de la semana, a79/a80
    # re-etiquetan las fotos sin producto).
    db = Database([client(weekly_focus=None, topics=None)])
    db.rows["image_tags"] = [
        {"client_id": "good", "drive_file_id": "file-0", "has_person": True, "scene": "ambiente"},
        {"client_id": "good", "drive_file_id": "file-1", "has_person": False, "scene": "producto"},
        {"client_id": "good", "drive_file_id": "file-2", "has_person": False, "scene": "ambiente"},
    ]
    classified = []

    def classify_many(rows):
        classified.extend(row[0] for row in rows)
        return {"file-3": _tag(False, "detalle"), "file-4": _tag(False, "producto"), "file-5": _tag(True, "exterior")}

    monkeypatch.setattr(jobs.image_tags, "classify_many", classify_many)

    jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    ids = [image.drive_file_id for image in engine.call_args.args[1]]
    # 3 sin personas de escenas distintas (file-4 repite "producto") y al final una con persona
    # de escena nueva (file-0 repite "ambiente", file-5 es "exterior").
    assert ids == ["file-1", "file-2", "file-3", "file-5"]
    assert sorted(classified) == ["file-3", "file-4", "file-5"]


def test_generate_for_client_works_when_tags_cannot_be_read_or_saved(generation):
    _, engine = generation
    db = Database([client()])  # sin tabla image_tags: la lectura falla y se sigue sin etiquetas

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result["created"] is True
    assert [image.drive_file_id for image in engine.call_args.args[1]] == ["file-0", "file-1", "file-2", "file-3"]


# ---------------------------------------------------------------------------
# a79: fotos que coinciden con el producto del enfoque
# ---------------------------------------------------------------------------
def test_es_relevante_compara_el_enfoque_con_la_ruta_y_el_producto():
    enfoque = "Llegó la nueva Osmo Pocket"
    assert jobs.es_relevante(enfoque, "Osmo Pocket/IMG_1.jpg", "")
    assert jobs.es_relevante(enfoque, "IMG_9.jpg", "drone DJI Osmo")
    assert not jobs.es_relevante(enfoque, "Garmin/IMG_2.jpg", "reloj Garmin")


def test_es_relevante_ignora_las_palabras_genericas():
    enfoque = "Black Friday: 6 cuotas sin interés en toda la web"
    assert not jobs.es_relevante(enfoque, "Osmo Pocket/IMG_1.jpg", "")
    assert not jobs.es_relevante(enfoque, "promo-black-friday.jpg", "cuotas sin interés")
    assert not jobs.es_relevante(None, "Osmo Pocket/IMG_1.jpg", "osmo")
    assert not jobs.es_relevante("", "Osmo Pocket/IMG_1.jpg", "osmo")


def test_es_relevante_no_distingue_tildes_ni_mayusculas():
    assert jobs.es_relevante("OSMO", "osmo/img.jpg", "")
    assert jobs.es_relevante("cámara nueva", "Camara/IMG_1.jpg", "")


def test_es_relevante_cuenta_codigos_pero_no_numeros_sueltos():
    assert jobs.es_relevante("Llegó el X5", "Insta360 X5/IMG_1.jpg", "")
    assert not jobs.es_relevante("Nuevo modelo 2031", "IMG_2031.jpg", "")


def test_elegir_hilo_prefiere_las_fotos_del_producto_aunque_sean_menos_frescas():
    examinadas = _rows("f1", "f2", "f3", "r1", "r2", "r3", "p")
    tags = {"f1": _tag(False, "producto"), "f2": _tag(False, "detalle"), "f3": _tag(False, "exterior"),
            "r1": _tag(False, "producto"), "r2": _tag(False, "detalle"), "r3": _tag(False, "exterior"),
            "p": _tag(True, "ambiente")}
    frescas = {"f1", "f2", "f3", "p"}

    elegidas = jobs.elegir_hilo(examinadas, tags, frescas, relevantes={"r1", "r2", "r3"})

    assert [r[0] for r in elegidas] == ["r1", "r2", "r3", "p"]
    # Sin relevantes, como en a74, ganan las frescas.
    assert [r[0] for r in jobs.elegir_hilo(examinadas, tags, frescas)] == ["f1", "f2", "f3", "p"]


def test_elegir_hilo_con_relevantes_mantiene_la_regla_de_persona():
    examinadas = _rows("r1", "r2", "r3", "r4", "p")
    tags = {"r1": _tag(False, "producto"), "r2": _tag(False, "detalle"), "r3": _tag(False, "exterior"),
            "r4": _tag(False, "ambiente"), "p": _tag(True, "ambiente")}
    relevantes = {"r1", "r2", "r3", "r4"}

    elegidas = jobs.elegir_hilo(examinadas, tags, {r[0] for r in examinadas}, relevantes=relevantes)

    ids = [r[0] for r in elegidas]
    # 4 relevantes sin persona: igual la del CTA es la de persona, aunque no sea relevante.
    assert ids[3] == "p"
    assert all(tags[i]["persona"] is False for i in ids[:3])


def test_elegir_hilo_con_relevantes_vacias_se_comporta_como_antes():
    examinadas = _rows("a", "b", "c", "d", "e", "f")
    tags = {"a": _tag(True, "ambiente"), "b": _tag(False, "producto"), "c": _tag(False, "producto"),
            "d": _tag(False, "detalle"), "e": _tag(False, "exterior"), "f": _tag(True, "producto")}
    frescas = {"a", "b", "c", "d"}

    assert jobs.elegir_hilo(examinadas, tags, frescas, relevantes=frozenset()) == jobs.elegir_hilo(
        examinadas, tags, frescas)


class _TagsQuery(Query):
    def select(self, fields="", *args):
        self.fields = fields
        return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.values = "upsert", rows
        return self

    def execute(self):
        db = self.db
        if self.op == "upsert":
            db.upserts.append(deepcopy(self.values))
            if db.fail_upserts:
                db.fail_upserts -= 1
                raise RuntimeError("column subject does not exist")
            return SimpleNamespace(data=self.values)
        if self.name == "image_tags" and self.op is None:
            db.tag_selects.append(self.fields)
            if db.fail_selects:
                db.fail_selects -= 1
                raise RuntimeError("column subject does not exist")
        return super().execute()


class _TagsDatabase(Database):
    """Database with image_tags upsert support and injectable failures for the
    a79 'subject' column (simulating the migration not applied yet)."""
    def __init__(self, *args, fail_selects=0, fail_upserts=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_selects, self.fail_upserts = fail_selects, fail_upserts
        self.tag_selects, self.upserts = [], []

    def table(self, name): return _TagsQuery(self, name)


def test_load_tags_devuelve_el_producto_desde_subject():
    db = _TagsDatabase()
    db.rows["image_tags"] = [
        {"client_id": "good", "drive_file_id": "f1", "has_person": False, "scene": "producto", "subject": "drone DJI"},
        {"client_id": "good", "drive_file_id": "f2", "has_person": True, "scene": "ambiente", "subject": None},
    ]

    tags = jobs._load_tags(db, "good")

    assert tags == {"f1": {"persona": False, "escena": "producto", "producto": "drone DJI"},
                    "f2": {"persona": True, "escena": "ambiente", "producto": None}}
    assert db.tag_selects == ["drive_file_id,has_person,scene,subject"]


def test_load_tags_sin_columna_subject_reintenta_sin_ella():
    db = _TagsDatabase(fail_selects=1)
    db.rows["image_tags"] = [
        {"client_id": "good", "drive_file_id": "f1", "has_person": False, "scene": "producto"},
    ]

    tags = jobs._load_tags(db, "good")

    assert tags == {"f1": {"persona": False, "escena": "producto", "producto": None}}
    assert db.tag_selects == ["drive_file_id,has_person,scene,subject", "drive_file_id,has_person,scene"]


def test_load_tags_si_falla_todo_devuelve_vacio():
    assert jobs._load_tags(_TagsDatabase(fail_selects=2), "good") == {}


def test_save_tags_incluye_subject():
    db = _TagsDatabase()

    jobs._save_tags(db, "good", {"f1": {"persona": False, "escena": "producto", "producto": "drone DJI"},
                                 "f2": {"persona": True, "escena": "ambiente"}})

    assert db.upserts == [[
        {"client_id": "good", "drive_file_id": "f1", "has_person": False, "scene": "producto", "subject": "drone DJI"},
        {"client_id": "good", "drive_file_id": "f2", "has_person": True, "scene": "ambiente", "subject": ""},
    ]]


def test_save_tags_sin_columna_subject_reintenta_sin_ella():
    db = _TagsDatabase(fail_upserts=1)

    jobs._save_tags(db, "good", {"f1": {"persona": False, "escena": "producto", "producto": "drone"}})

    assert len(db.upserts) == 2
    assert "subject" in db.upserts[0][0]
    assert db.upserts[1] == [{"client_id": "good", "drive_file_id": "f1", "has_person": False, "scene": "producto"}]


def test_save_tags_nunca_levanta():
    jobs._save_tags(_TagsDatabase(fail_upserts=2), "good", {"f1": {"persona": False, "escena": "otro"}})


def _echo_engine(engine):
    """The engine mock returns the IDs of the photos it was given, so the
    persistence check accepts whatever selection the job made."""
    engine.side_effect = lambda config, imagenes: HiloGenerado(
        historias=[f"Story {index}" for index in range(4)],
        imagenes_originales_url=[f"raw-{index}" for index in range(4)],
        imagenes_editadas_url=[f"edited-{index}" for index in range(4)],
        drive_file_ids_usados=[image.drive_file_id for image in imagenes],
    )


def _osmo_folder():
    # 4 fotos generales primero (en el orden de Drive) y 4 de la subcarpeta del producto.
    return ([(f"gen-{i}", f"General/IMG_{i}.jpg") for i in range(4)]
            + [(f"osmo-{i}", f"Osmo Pocket/IMG_{i}.jpg") for i in range(4)])


def test_generate_for_client_con_enfoque_prioriza_fotos_del_producto(generation, monkeypatch):
    drive_mock, engine = generation
    _echo_engine(engine)
    drive_mock.return_value = _osmo_folder()
    db = _TagsDatabase([client(weekly_focus="Llegó la nueva Osmo Pocket")])
    escenas = {"osmo-0": "producto", "osmo-1": "detalle", "osmo-2": "exterior", "osmo-3": "ambiente"}
    # Etiquetadas con a74 (sin subject): con enfoque se vuelven a etiquetar.
    db.rows["image_tags"] = [
        {"client_id": "good", "drive_file_id": file_id, "has_person": file_id == "osmo-3", "scene": scene}
        for file_id, scene in escenas.items()
    ]
    classified = []

    def classify_many(rows):
        classified.extend(row[0] for row in rows)
        return {row[0]: {"persona": row[0] == "osmo-3", "escena": escenas.get(row[0], "otro"),
                         "producto": "cámara DJI Osmo Pocket" if row[0] in escenas else ""}
                for row in rows}

    monkeypatch.setattr(jobs.image_tags, "classify_many", classify_many)

    result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result["created"] is True
    assert [image.drive_file_id for image in engine.call_args.args[1]] == ["osmo-0", "osmo-1", "osmo-2", "osmo-3"]
    # Las gen-* no tenían etiqueta (se clasifican siempre); las osmo-* sí tenían, pero de a74
    # (sin producto): se re-etiquetan porque hay enfoque.
    assert sorted(classified) == [f"gen-{i}" for i in range(4)] + [f"osmo-{i}" for i in range(4)]
    assert "generation_error" not in db.rows["clients"][0]


def test_generate_for_client_sin_enfoque_no_reetiqueta_fotos_viejas(generation, monkeypatch):
    drive_mock, engine = generation
    _echo_engine(engine)
    drive_mock.return_value = [("old", "old.jpg"), ("n1", "n1.jpg"), ("n2", "n2.jpg"), ("n3", "n3.jpg")]
    db = _TagsDatabase([client(weekly_focus=None, topics=None)])  # sin enfoque ni tema de la semana
    db.rows["image_tags"] = [{"client_id": "good", "drive_file_id": "old", "has_person": False, "scene": "producto"}]
    classified = []

    def classify_many(rows):
        classified.extend(row[0] for row in rows)
        return {}

    monkeypatch.setattr(jobs.image_tags, "classify_many", classify_many)

    jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert "old" not in classified
    assert sorted(classified) == ["n1", "n2", "n3"]


def test_generate_for_client_sin_coincidencias_usa_fotos_generales_sin_error(generation, caplog):
    drive_mock, engine = generation
    _echo_engine(engine)
    drive_mock.return_value = _osmo_folder()
    db = Database([client(weekly_focus="Llegó el nuevo Kindle Paperwhite")])

    with caplog.at_level("INFO", logger=jobs.logger.name):
        result = jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert result["created"] is True
    assert len(engine.call_args.args[1]) == 4
    assert "generation_error" not in db.rows["clients"][0]
    assert "Sin fotos que coincidan con el enfoque" in caplog.text


# ---------------------------------------------------------------------------
# a80: el portal elige el tema de la semana antes de elegir las fotos
# ---------------------------------------------------------------------------
_TEMAS = ["Garmin para running", "DJI y creadores", "Kindle para leer"]
_LUNES = date(2026, 11, 9)


def test_tema_de_la_semana_rota_y_no_repite_dos_semanas_seguidas():
    semanas = [jobs.tema_de_la_semana(_TEMAS, "abc", _LUNES + timedelta(weeks=n)) for n in range(4)]

    assert len(set(semanas[:3])) == 3
    assert semanas[3] == semanas[0]
    assert all(jobs.tema_de_la_semana(["Único"], "abc", _LUNES + timedelta(weeks=n)) == "Único" for n in range(5))
    assert jobs.tema_de_la_semana([], "abc", _LUNES) is None
    assert jobs.tema_de_la_semana(None, "abc", _LUNES) is None
    assert jobs.tema_de_la_semana(["  ", "", None], "abc", _LUNES) is None
    assert jobs.tema_de_la_semana(["  ", " Garmin "], "abc", _LUNES) == "Garmin"


def test_tema_de_la_semana_no_repite_al_cambiar_de_ano():
    # 2027 tiene 52 semanas ISO: año*53 + semana saltaría de a 2 entre el 27/12/2027 y el
    # 3/1/2028 y, con 2 temas, repetiría el mismo tema dos semanas seguidas.
    temas = ["Garmin para running", "DJI y creadores"]
    lunes = date(2027, 12, 6)
    seguidas = [jobs.tema_de_la_semana(temas, "abc", lunes + timedelta(weeks=n)) for n in range(6)]

    assert all(a != b for a, b in zip(seguidas, seguidas[1:]))


def test_tema_de_la_semana_es_estable_para_el_mismo_cliente_y_semana():
    temas = {jobs.tema_de_la_semana(_TEMAS, "abc", _LUNES + timedelta(days=d)) for d in range(7)}

    assert len(temas) == 1
    assert jobs.tema_de_la_semana(_TEMAS, "abc", _LUNES) == jobs.tema_de_la_semana(_TEMAS, "abc", _LUNES)
    assert jobs.tema_de_la_semana(_TEMAS, "abc", _LUNES + timedelta(days=7)) not in temas


def test_tema_de_la_semana_difiere_entre_clientes():
    # "a" y "b" suman 97 y 98 bytes: con 3 temas caen en posiciones distintas.
    assert jobs.tema_de_la_semana(_TEMAS, "a", _LUNES) != jobs.tema_de_la_semana(_TEMAS, "b", _LUNES)


def test_generate_for_client_pasa_el_tema_elegido_al_motor(generation):
    _, engine = generation
    db = Database([client(weekly_focus=None, topics=_TEMAS)])

    jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    config = engine.call_args.args[0]
    assert config.tema_elegido == jobs.tema_de_la_semana(_TEMAS, "good", date(2026, 9, 18))
    assert config.weekly_focus is None
    # El tema elegido no es un enfoque: no se persiste ni se limpia como tal.
    assert db.saved[0]["p_used_focus"] is None


def test_generate_for_client_con_enfoque_no_elige_tema(generation):
    _, engine = generation
    db = Database([client(weekly_focus="Launch", topics=_TEMAS)])

    jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert engine.call_args.args[0].tema_elegido is None
    assert db.saved[0]["p_used_focus"] == "Launch"


def test_generate_for_client_sin_topics_ni_enfoque_no_elige_tema(generation):
    _, engine = generation
    db = Database([client(weekly_focus=None, topics=None)])

    jobs.generate_for_client(db, db.rows["clients"][0], date(2026, 9, 18))

    assert engine.call_args.args[0].tema_elegido is None


def _fecha_con_tema(temas, client_id, tema):
    for n in range(len(temas)):
        dia = _LUNES + timedelta(weeks=n)
        if jobs.tema_de_la_semana(temas, client_id, dia) == tema:
            return dia
    raise AssertionError(f"ninguna semana da {tema!r}")


@pytest.mark.parametrize("tema, carpeta", [("Garmin para running", "Garmin"), ("DJI y creadores", "DJI")])
def test_generate_for_client_sin_enfoque_elige_fotos_del_tema(generation, monkeypatch, tema, carpeta):
    drive_mock, engine = generation
    _echo_engine(engine)
    temas = ["Garmin para running", "DJI y creadores"]
    drive_mock.return_value = ([(f"garmin-{i}", f"Garmin/IMG_{i}.jpg") for i in range(4)]
                               + [(f"dji-{i}", f"DJI/IMG_{i}.jpg") for i in range(4)])
    db = Database([client(weekly_focus=None, topics=temas)])
    prefijo = carpeta.lower()

    def classify_many(rows):
        # Persona solo en la última foto de cada carpeta; escenas distintas entre sí.
        escenas = ("producto", "detalle", "exterior", "ambiente")
        return {row[0]: {"persona": row[0].endswith("-3"), "escena": escenas[int(row[0][-1])], "producto": ""}
                for row in rows}

    monkeypatch.setattr(jobs.image_tags, "classify_many", classify_many)

    jobs.generate_for_client(db, db.rows["clients"][0], _fecha_con_tema(temas, "good", tema))

    assert engine.call_args.args[0].tema_elegido == tema
    assert [image.drive_file_id for image in engine.call_args.args[1]] == [f"{prefijo}-{i}" for i in range(4)]
    assert db.saved[0]["p_used_focus"] is None
