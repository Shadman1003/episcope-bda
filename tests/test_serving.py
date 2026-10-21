"""Disclosure, endpoint access control and bounded-release regression tests."""
import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from serving.export import MAX_BYTES, _json_bytes, simplify_geojson, validate_release
from serving.privacy import count_bin, coupled_bins, quality_release
from serving.server import DashboardServer, filter_payload


def release_fixture():
    return {"districts": [{"district_id":"01","district_name":"North","division":"One","population":10000},{"district_id":"02","district_name":"South","division":"Two","population":20000}],"observed":[[0,0,0,None,.9],[1,0,0,10,1.0]],"forecasts":[[0,0,"2026-01-05",1,10,5,20,10],[1,0,"2026-01-05",1,20,10,30,15]],"signals":[[0,0,"2025-12-31",None,None,None,None,False,True],[1,0,"2025-12-31",None,None,None,6,True,True]],"allocations":[[0,0,1000,.2,None,None,None,None,None],[1,0,1000,.2,20,5,10,5,10]],"calendar":[["One",0,1,1.2,"Prepare",2026],["Two",0,1,1.1,"Monitor",2026]],"geography":{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"district_id":"01"},"geometry":{"type":"Polygon","coordinates":[[[90,23],[91,23],[91,24],[90,23]]]}},{"type":"Feature","properties":{"district_id":"02"},"geometry":{"type":"Polygon","coordinates":[[[91,23],[92,23],[92,24],[91,23]]]}}]},"tradeoff":[{"sensitivity":.8}],"scenarios":[]}


class PrivacyTests(unittest.TestCase):
    def test_quality_counts_and_pass_rates_cannot_expose_small_failures(self):
        row={'batch_id':'a'*20,'loaded_at':'2026-01-01T00:00:00Z','rows_in':100,'rows_out':97,'quarantined':3,'duplicates':0,'rule_counts_json':'{"invalid_age":3,"invalid_sex":0}'}
        release=quality_release(row)
        self.assertTrue(all(release[key] is None for key in ('rows_in','rows_out','quarantined','duplicates')))
        self.assertIsNone(release['rules'][0]['failed'])
        self.assertTrue(all(rule['approximate_pass_rate'] is None for rule in release['rules']))
        payload=release_fixture();payload['quality']=[release]
        self.assertTrue(validate_release(payload))
        payload['quality'][0]['rows_out']=95
        with self.assertRaisesRegex(ValueError,'Quality balance'):
            validate_release(payload)

    def test_quality_public_rates_derive_from_binned_counts(self):
        row={'batch_id':'b'*20,'rows_in':207,'rows_out':180,'quarantined':17,'duplicates':10,'rule_counts_json':'{"invalid_age":17}'}
        release=quality_release(row)
        self.assertEqual(release['rows_in'],205)
        self.assertEqual(release['rules'][0]['failed'],15)
        self.assertEqual(release['rules'][0]['approximate_pass_rate'],.95)
        payload=release_fixture();payload['quality']=[release]
        self.assertTrue(validate_release(payload))

    def test_zero_small_and_nonfinite(self):
        self.assertEqual(count_bin(0),0)
        for value in [None,1,2,3,4,4.99,-1,float("nan"),float("inf")]:
            self.assertIsNone(count_bin(value))
        self.assertEqual(count_bin(9),5)
        self.assertEqual(count_bin(10),10)

    def test_coupled_balance_cannot_reveal_hidden_small_value(self):
        for hidden in range(1,5):
            self.assertEqual(coupled_bins([100,80,20-hidden,hidden,15]),[None]*5)
        self.assertEqual(coupled_bins([101,83,17,0,13]),[100,80,15,0,10])

    def test_overlapping_totals_and_partial_suppression_rejected(self):
        payload=release_fixture()
        self.assertTrue(validate_release(payload))
        payload["allocations"][0][4]=10
        with self.assertRaisesRegex(ValueError,"partial suppression"):
            validate_release(payload)
        payload=release_fixture();payload["totals"]=[12]
        with self.assertRaisesRegex(ValueError,"totals"):
            validate_release(payload)

    def test_no_exact_parent_total_for_subtraction_attack(self):
        payload=release_fixture()
        self.assertNotIn("totals",payload)
        self.assertNotIn("daily",payload)
        self.assertNotIn("admissions",payload)
        self.assertTrue(all(row[3] is None and row[4] is None and row[5] is None for row in payload["signals"]))

    def test_disjoint_observations_and_private_daily_values_enforced(self):
        payload=release_fixture();payload["observed"].append(list(payload["observed"][0]))
        with self.assertRaisesRegex(ValueError,"disjoint"):
            validate_release(payload)
        payload=release_fixture();payload["signals"][0][3]=25
        with self.assertRaisesRegex(ValueError,"Daily numerator"):
            validate_release(payload)

    def test_no_small_public_counts(self):
        for table,position in [("observed",3),("forecasts",4),("signals",3),("allocations",4)]:
            for value in range(1,5):
                payload=release_fixture();payload[table][0][position]=value
                with self.assertRaisesRegex(ValueError,"Unsafe public count"):
                    validate_release(payload)

    def test_district_filter_covers_all_tables_and_geography(self):
        payload=release_fixture();before=copy.deepcopy(payload)
        filtered=filter_payload(payload,{"role":"district","district_id":"02"})
        self.assertEqual(payload,before)
        self.assertEqual([d["district_id"] for d in filtered["districts"]],["02"])
        for table in ["observed","forecasts","signals","allocations"]:
            self.assertEqual(len(filtered[table]),1)
            self.assertEqual(filtered[table][0][0],0)
        self.assertEqual(filtered["observed"][0][3],10)
        self.assertEqual(filtered["geography"]["features"][0]["properties"]["district_id"],"02")
        self.assertEqual([r[0] for r in filtered["calendar"]],["Two"])
        self.assertEqual(filtered["tradeoff"],[])
        with self.assertRaises(PermissionError):
            filter_payload(payload,{"role":"district","district_id":"64"})

    def test_national_scale_compact_payload_bound(self):
        payload=release_fixture()
        payload["observed"]=[[district,disease,week,125,.95] for district in range(64) for disease in range(4) for week in range(262)]
        self.assertEqual(len(payload["observed"]),67072)
        # Leave substantial headroom for geography, forecasts and scenario grids.
        self.assertLess(len(_json_bytes(payload)),MAX_BYTES//2)

    def test_geometry_simplification_keeps_closed_rings_and_safe_properties(self):
        payload=release_fixture();result=simplify_geojson(payload["geography"],payload["districts"])
        self.assertEqual(len(result["features"]),2)
        ring=result["features"][0]["geometry"]["coordinates"][0]
        self.assertEqual(ring[0],ring[-1])
        self.assertLess(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(ring,ring[1:])),0)
        self.assertEqual(set(result["features"][0]["properties"]),{"district_id"})


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        root=Path(self.directory.name);(root/"data").mkdir()
        (root/"data"/"dashboard.json").write_text(json.dumps(release_fixture()))
        (root/"index.html").write_text("<html>test assets</html>")
        self.server=DashboardServer(("127.0.0.1",0),root,{"north-access-token-long-enough":{"role":"district","district_id":"01"},"national-access-token-long-enough":{"role":"national"}})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.directory.cleanup()

    def request(self,path,token=None):
        return urllib.request.urlopen(urllib.request.Request(self.url+path,headers={"Authorization":"Bearer "+token} if token else {}))

    def test_no_auth_no_data_and_static_release_cannot_bypass_role(self):
        for path,code in [("/api/data",401),("/data/dashboard.json",404),("/data/manifest.json",404),("/%2e%2e/data/dashboard.json",404),("/../data/dashboard.json",404),("/serving/server.py",404)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code,code,path)

    def test_district_token_receives_only_own_district(self):
        with self.request("/api/data","north-access-token-long-enough") as response:
            payload=json.load(response)
            self.assertEqual(len(payload["districts"]),1)
            self.assertEqual(payload["districts"][0]["district_id"],"01")
            self.assertEqual(len(payload["observed"]),1)
            self.assertIsNone(payload["observed"][0][3])
            self.assertEqual(response.headers["Cache-Control"],"no-store")
            self.assertIn("frame-ancestors 'none'",response.headers["Content-Security-Policy"])

    def test_national_token_sees_both_districts(self):
        with self.request("/api/data","national-access-token-long-enough") as response:
            self.assertEqual(len(json.load(response)["districts"]),2)


if __name__=="__main__":
    unittest.main()
