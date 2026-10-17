"""База прогноза в сводке за день: доход дня против среднего за 14 дней.

Логика лежит в ``handlers/admin/stats/forecast.py`` отдельно от обработчика,
поэтому гоняется без aiogram и без базы.
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

forecast_base = _forecast.forecast_base
LABEL_DAY = _forecast.LABEL_DAY
LABEL_AVG = _forecast.LABEL_AVG

DAY = 13629.0        # доход аномального дня 15.10.2026
AVG14 = 5194.0       # среднее по той же серии за 14 дней


class ForecastBase(unittest.TestCase):
    def test_flag_off_takes_day_revenue(self):
        base, label = forecast_base(False, DAY, AVG14)
        self.assertEqual(base, DAY)
        self.assertEqual(label, LABEL_DAY)

    def test_flag_on_takes_series_average(self):
        base, label = forecast_base(True, DAY, AVG14)
        self.assertEqual(base, AVG14)
        self.assertEqual(label, LABEL_AVG)

    def test_missing_flag_keeps_old_behaviour(self):
        """Настройки нет - поведение существующих установок не меняется."""
        for value in (None, "", 0, [], {}):
            with self.subTest(value=value):
                base, label = forecast_base(value, DAY, AVG14)
                self.assertEqual(base, DAY)
                self.assertEqual(label, LABEL_DAY)

    def test_truthy_values_enable_average(self):
        for value in (1, "1", "yes", True):
            with self.subTest(value=value):
                base, _ = forecast_base(value, DAY, AVG14)
                self.assertEqual(base, AVG14)

    def test_month_forecast_difference(self):
        """Ровно та разница, из-за которой режим и понадобился."""
        legacy, _ = forecast_base(False, DAY, AVG14)
        avg, _ = forecast_base(True, DAY, AVG14)
        self.assertEqual(round(legacy * 30), 408870)
        self.assertEqual(round(avg * 30), 155820)
        self.assertEqual(round(legacy * 7), 95403)
        self.assertEqual(round(avg * 7), 36358)

    def test_labels_differ(self):
        self.assertNotEqual(LABEL_DAY, LABEL_AVG)


if __name__ == "__main__":
    unittest.main()
