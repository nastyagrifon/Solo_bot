"""Прогноз в сводке за день: выручка дня против скользящих окон.

Логика лежит в ``handlers/admin/stats/forecast.py`` отдельно от обработчика,
поэтому гоняется без aiogram и без базы. Разбор методик - в FORECAST.md.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import importlib.util
import pathlib
import unittest

# Грузим модуль по пути, а не через пакет: ``handlers/__init__.py`` тянет
# aiogram, а эта логика в нём не нуждается - значит и тест не должен.
_SPEC = importlib.util.spec_from_file_location(
    "stats_forecast",
    pathlib.Path(__file__).resolve().parent.parent
    / "handlers" / "admin" / "stats" / "forecast.py",
)
_forecast = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_forecast)

forecast = _forecast.forecast
LABEL_DAY = _forecast.LABEL_DAY
LABEL_ROLLING = _forecast.LABEL_ROLLING

DAY = 100.0
#: Ровный ряд в 30 дней: сумма недели 700, сумма месяца 3000 - считается в уме.
FLAT = [100.0] * 30


class LegacyMode(unittest.TestCase):
    def test_takes_day_revenue(self):
        week, month, label = forecast(False, DAY, FLAT)
        self.assertEqual(week, 700.0)
        self.assertEqual(month, 3000.0)
        self.assertEqual(label, LABEL_DAY)

    def test_ignores_series(self):
        """Режим по умолчанию смотрит только на день, ряд не влияет."""
        week, month, _ = forecast(False, DAY, [1.0] * 30)
        self.assertEqual((week, month), (700.0, 3000.0))

    def test_missing_flag_keeps_old_behaviour(self):
        for value in (None, "", 0, [], {}):
            with self.subTest(value=value):
                week, month, label = forecast(value, DAY, FLAT)
                self.assertEqual((week, month, label), (700.0, 3000.0, LABEL_DAY))


class RollingMode(unittest.TestCase):
    def test_windows_are_independent(self):
        """Неделя по семи дням, месяц по тридцати - окна разной длины."""
        series = [10.0] * 23 + [100.0] * 7      # последняя неделя богаче
        week, month, label = forecast(True, 0.0, series)
        self.assertEqual(week, 700.0)
        self.assertEqual(month, 230.0 + 700.0)
        self.assertEqual(label, LABEL_ROLLING)

    def test_day_revenue_not_used(self):
        """Аномальный день не сдвигает прогноз - в этом и смысл режима."""
        week, month, _ = forecast(True, 99999.0, FLAT)
        self.assertEqual((week, month), (700.0, 3000.0))

    def test_short_history_scaled_not_truncated(self):
        """Пять дней истории: месяц достраивается средним, а не обрезается."""
        week, month, _ = forecast(True, 0.0, [100.0] * 5)
        self.assertEqual(week, 700.0)
        self.assertEqual(month, 3000.0)

    def test_empty_series(self):
        week, month, _ = forecast(True, 500.0, [])
        self.assertEqual((week, month), (0.0, 0.0))

    def test_longer_series_uses_only_windows(self):
        """Ряд длиннее месяца: в расчёт идут только последние дни."""
        series = [1.0] * 100 + [100.0] * 30
        week, month, _ = forecast(True, 0.0, series)
        self.assertEqual(week, 700.0)
        self.assertEqual(month, 3000.0)


class Labels(unittest.TestCase):
    def test_labels_differ(self):
        self.assertNotEqual(LABEL_DAY, LABEL_ROLLING)


if __name__ == "__main__":
    unittest.main()
