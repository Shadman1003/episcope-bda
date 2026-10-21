"""Small Spark integration fixture, including a report-date artefact."""
from datetime import date, timedelta
import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec("pyspark"), "PySpark runtime is not installed")
class SparkSurveillanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark = (SparkSession.builder.master("local[2]").appName("EpiScope analytics tests")
                     .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "2").getOrCreate())
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def test_delayed_backfill_is_not_a_realtime_onset_outbreak(self):
        from analytics.detection import detect
        start = date(2021, 1, 1)
        encounters = [("01", "dengue", start+timedelta(days=i), start+timedelta(days=i)) for i in range(60)]
        # Old reports arrive together; none was available by its onset+7 decision.
        encounters += [("01", "dengue", start+timedelta(days=i), start+timedelta(days=28))
                       for i in range(10, 21) for _ in range(4)]
        # A separate, genuine promptly reported rise should still be detected.
        encounters += [("01", "dengue", start+timedelta(days=36), start+timedelta(days=36)) for _ in range(20)]
        records = self.spark.createDataFrame(encounters, "district_id string, disease string, onset_date date, report_date date")
        daily = self.spark.createDataFrame([
            (start+timedelta(days=i), "01", "dengue", sum(1 for row in encounters if row[2] == start+timedelta(days=i)),
             sum(1 for row in encounters if row[3] == start+timedelta(days=i)), 1.) for i in range(60)
        ], "date date, district_id string, disease string, cases long, report_count long, completeness double")
        output = detect(self.spark, daily, records).collect()
        artifact = [row for row in output if row.method == "EARS_C2" and row.as_of_date == start+timedelta(days=28)]
        self.assertEqual(len(artifact), 1)
        self.assertFalse(artifact[0].alert)
        self.assertEqual(artifact[0].observed, 1)
        real = [row for row in output if row.method == "EARS_C2" and row.date == start+timedelta(days=36)][0]
        self.assertTrue(real.alert)
        self.assertEqual(real.as_of_date, start+timedelta(days=43))


if __name__ == "__main__":
    unittest.main()
