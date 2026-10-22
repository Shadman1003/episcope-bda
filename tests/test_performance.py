"""Regression checks for independent timing samples; no Spark JVM is required."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration.performance import measure_query_samples


class CachedQuery:
    """Model one physical plan whose expensive shuffle is reused by collect."""
    def __init__(self, clock, identity, *, value=17, plan_identity=None):
        self.clock, self.identity, self.value = clock, identity, value
        self.plan_identity = identity if plan_identity is None else plan_identity
        self.calls = 0
        self._jdf = self

    def queryExecution(self):
        return self

    def id(self):
        return self.identity

    def collect(self):
        self.clock[0] += 5. if self.calls == 0 else .01
        self.calls += 1
        return [SimpleNamespace(asDict=lambda: {'encounters':self.value})]

    def executedPlan(self):
        return SimpleNamespace(id=lambda:self.plan_identity,
                               toString=lambda:f'Aggregate plan {self.plan_identity}')

    def explain(self, **unused):
        pass


class FreshQuerySampleTests(unittest.TestCase):
    def setUp(self):
        self.clock = [0.]
        self.spark = SimpleNamespace(sparkContext=SimpleNamespace(
            setJobGroup=lambda *args:None, setJobDescription=lambda *args:None))

    def measure(self, factory, directory):
        with patch('orchestration.performance.time.monotonic', side_effect=lambda:self.clock[0]):
            return measure_query_samples(self.spark, 'pruning_after_native_predicate', factory, directory)

    def test_each_sample_executes_fresh_expensive_work_instead_of_cached_shuffle(self):
        queries = []
        def factory():
            query = CachedQuery(self.clock, len(queries) + 1)
            queries.append(query)
            return query
        with TemporaryDirectory() as directory:
            result = self.measure(factory, directory)
            self.assertEqual(result['seconds'], [5., 5., 5.])
            self.assertEqual([query.calls for query in queries], [1, 1, 1])
            self.assertEqual(len({sample['query_execution_id'] for sample in result['samples']}), 3)
            for sample in result['samples']:
                self.assertTrue((Path(directory) / sample['plan_file']).is_file())

    def test_reusing_same_query_is_rejected_before_cheap_repeat(self):
        query = CachedQuery(self.clock, 1)
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'fresh QueryExecution'):
                self.measure(lambda:query, directory)
        self.assertEqual(query.calls, 1)

    def test_distinct_query_wrappers_cannot_share_one_physical_plan(self):
        queries = []
        def factory():
            query = CachedQuery(self.clock, len(queries) + 1, plan_identity=7)
            queries.append(query)
            return query
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'fresh physical plan'):
                self.measure(factory, directory)

    def test_different_sample_results_fail_instead_of_publishing_median(self):
        queries = []
        def factory():
            query = CachedQuery(self.clock, len(queries) + 1, value=len(queries))
            queries.append(query)
            return query
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'different aggregate results'):
                self.measure(factory, directory)


if __name__ == '__main__':
    unittest.main()
