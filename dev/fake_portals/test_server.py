"""Unit and integration tests for Mini RFQ Portals."""

import json
import re
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

from server import PortalCluster, load_fixture_cases

class HTTPClient:
    def __init__(self):
        self.cj = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def request(self, method, url, data=None):
        req = urllib.request.Request(url, method=method)
        if data is not None:
            encoded = urllib.parse.urlencode(data).encode('utf-8')
            req.data = encoded
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        try:
            resp = self.opener.open(req)
            return resp.status, resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode('utf-8')

    def get(self, url):
        return self.request('GET', url)

    def post(self, url, data):
        return self.request('POST', url, data)

    def login(self, origin, login_path='/login', user='demo', pwd='synthetic-only'):
        return self.post(f"{origin}{login_path}", {'username': user, 'password': pwd})

class TestPortalCluster(unittest.TestCase):
    def test_login_success_and_wrong_password(self):
        with PortalCluster(case_name='baseline', profile='classic') as cluster:
            client = HTTPClient()
            origin = cluster.origins['tender']
            # Unauthenticated -> redirect to login
            st, body = client.get(f"{origin}/uebersicht")
            self.assertIn("Anmeldung", body)
            # Wrong password
            st, body = client.login(origin, '/anmeldung', 'demo', 'wrong-pass')
            self.assertIn("Ungültige Anmeldedaten", body)
            # Correct login
            st, body = client.login(origin, '/anmeldung', 'demo', 'synthetic-only')
            self.assertEqual(st, 200)
            self.assertIn("Ausschreibungsübersicht", body)

    def test_profile_specific_paths(self):
        with PortalCluster(case_name='baseline', profile='classic') as c_classic:
            cl = HTTPClient()
            cl.login(c_classic.origins['tender'], '/anmeldung')
            st, _ = cl.get(f"{c_classic.origins['tender']}/uebersicht")
            self.assertEqual(st, 200)
            st, _ = cl.get(f"{c_classic.origins['tender']}/dashboard")
            self.assertEqual(st, 404)

        with PortalCluster(case_name='baseline', profile='modern') as c_modern:
            cl = HTTPClient()
            cl.login(c_modern.origins['tender'], '/login')
            st, _ = cl.get(f"{c_modern.origins['tender']}/dashboard")
            self.assertEqual(st, 200)
            st, _ = cl.get(f"{c_modern.origins['tender']}/uebersicht")
            self.assertEqual(st, 404)

        with PortalCluster(case_name='baseline', profile='legacy') as c_legacy:
            cl = HTTPClient()
            cl.login(c_legacy.origins['specification'], '/auth.cgi')
            st, body = cl.get(f"{c_legacy.origins['specification']}/frameset.html")
            self.assertEqual(st, 200)
            self.assertIn("<iframe", body)
            st, body = cl.get(f"{c_legacy.origins['specification']}/legacy_spec/docs.html")
            self.assertEqual(st, 200)

    def test_paginated_qa(self):
        with PortalCluster(case_name='baseline', profile='classic') as cluster:
            cl = HTTPClient()
            cl.login(cluster.origins['tender'], '/anmeldung')
            st, body = cl.get(f"{cluster.origins['tender']}/fragen-antworten?seite=1")
            self.assertEqual(st, 200)
            self.assertIn("Antwort #99", body)
            self.assertIn("Antwort #2", body)
            self.assertIn("<time datetime=", body)
            self.assertIn("seite=2", body)
            st, body2 = cl.get(f"{cluster.origins['tender']}/fragen-antworten?seite=2")
            self.assertEqual(st, 200)
            self.assertIn("Antwort #3", body2)
            self.assertIn("Antwort #4", body2)

    def test_erp_group_search_and_detail(self):
        with PortalCluster(case_name='baseline', profile='classic') as cluster:
            cl = HTTPClient()
            cl.login(cluster.origins['erp'], '/anmeldung')
            st, body = cl.get(f"{cluster.origins['erp']}/katalog/suche?group=CAT-A")
            self.assertEqual(st, 200)
            self.assertIn("SYN-MAT-AX", body)
            self.assertIn("SYN-MAT-DECOY", body)
            self.assertNotIn("SYN-MAT-BX", body)
            # Detail view
            st, det = cl.get(f"{cluster.origins['erp']}/artikel/SYN-MAT-AX")
            self.assertEqual(st, 200)
            self.assertIn("Trunnion", det)
            self.assertIn("TEST-CERT-A", det)
            self.assertIn("Frei verfügbarer Bestand", det)
            self.assertIn("Status-Legende: R = active / released, B = blocked", det)
            self.assertIn("110,50", det)

    def test_specification_revisions(self):
        with PortalCluster(case_name='baseline', profile='classic') as cluster:
            cl = HTTPClient()
            cl.login(cluster.origins['specification'], '/anmeldung')
            st, body = cl.get(f"{cluster.origins['specification']}/revisionen")
            self.assertEqual(st, 200)
            self.assertIn("Gültig / Current", body)
            self.assertIn("Überholt / Superseded", body)
            st, req1 = cl.get(f"{cluster.origins['specification']}/anforderungen/teil1")
            self.assertEqual(st, 200)
            self.assertIn("REQ-A", req1)
            self.assertIn("120 mm", req1)
            st, decoy = cl.get(f"{cluster.origins['specification']}/alt-revision")
            self.assertEqual(st, 200)
            self.assertIn("135 mm", decoy)
            self.assertIn("WARNUNG", decoy)

    def test_mail_search_and_pagination(self):
        with PortalCluster(case_name='baseline', profile='classic') as cluster:
            cl = HTTPClient()
            cl.login(cluster.origins['mail'], '/anmeldung')
            st, body = cl.get(f"{cluster.origins['mail']}/postfach/posteingang")
            self.assertEqual(st, 200)
            self.assertIn("sales@boreal.example", body)
            self.assertIn("Discount approval", body)
            st, search_body = cl.get(f"{cluster.origins['mail']}/postfach/posteingang?q=discount+approval")
            self.assertEqual(st, 200)
            self.assertIn("Discount approval", search_body)
            st, msg = cl.get(f"{cluster.origins['mail']}/nachricht/approval")
            self.assertEqual(st, 200)
            self.assertIn("director@boreal.example", msg)
            self.assertIn("8 percent", msg)

    def test_tender_draft_review_and_tan_flow(self):
        with PortalCluster(case_name='baseline', profile='classic') as cluster:
            cl = HTTPClient()
            cl.login(cluster.origins['tender'], '/anmeldung')
            cl_mail = HTTPClient()
            cl_mail.login(cluster.origins['mail'], '/anmeldung')

            # 1. Draft submission with arbitrary invented numbers
            draft_data = {
                'bidder_company': 'Arbitrary Corp',
                'bidder_vat': 'VAT-999',
                'offer_type': 'bid',
                'discount_percent': '7,5',
                'total_net': '1234,50',
                'accept_documents': '1',
                'own_terms': '0',
                'pos_0_position': 'X-01',
                'pos_0_material': 'SYN-MAT-AX',
                'pos_0_quantity': '10',
                'pos_0_unit_price': '110,50',
                'pos_0_base_unit_price': '90,00',
                'pos_0_surcharge_per_unit': '5,00',
                'pos_0_packaging_per_unit': '2,50',
                'pos_0_rebate_total': '0,00',
                'pos_0_lead_time_weeks': '2',
                'pos_1_position': 'X-02',
                'pos_1_omit': '1'
            }
            st, body = cl.post(f"{cluster.origins['tender']}/angebot-einreichen", draft_data)
            self.assertEqual(st, 200)
            self.assertIn("Arbitrary Corp", body)
            self.assertIn("1234.5", body)

            # 2. Request TAN
            st, _ = cl.post(f"{cluster.origins['tender']}/tan-anfordern", {})
            self.assertEqual(st, 200)
            snap = cluster.snapshot()
            self.assertEqual(snap['tan_requests'], 1)

            # 3. Read TAN from mail origin
            st, mail_body = cl_mail.get(f"{cluster.origins['mail']}/postfach/posteingang")
            m = re.search(r"TAN-\d+", mail_body)
            if not m:
                tan_msg = cluster.messages[0]
                m = re.search(r"TAN-\d+", tan_msg['body'])
            self.assertIsNotNone(m)
            tan_code = m.group(0)

            # 4. Reject invalid TAN
            st, _ = cl.post(f"{cluster.origins['tender']}/absenden", {'tan': 'TAN-000000'})
            self.assertEqual(st, 400)

            # 5. Accept valid TAN and check snapshot
            st, body = cl.post(f"{cluster.origins['tender']}/absenden", {'tan': tan_code})
            self.assertEqual(st, 200)
            self.assertIn("Bestätigung", body)
            snap2 = cluster.snapshot()
            self.assertIsNotNone(snap2['submitted'])
            self.assertIsNotNone(snap2['receipt'])
            self.assertEqual(snap2['submitted']['decision'], 'bid')
            self.assertEqual(snap2['submitted']['discount_percent'], 7.5)
            self.assertEqual(snap2['submitted']['total_net'], 1234.5)
            self.assertEqual(len(snap2['submitted']['positions']), 1)
            self.assertEqual(snap2['submitted']['positions'][0]['quantity'], 10)

            # 6. Duplicate POST returns existing receipt
            st, body_dup = cl.post(f"{cluster.origins['tender']}/absenden", {'tan': tan_code})
            self.assertEqual(st, 200)
            self.assertIn(snap2['receipt'], body_dup)

    def test_declarations_and_malformed_numeric_rejection(self):
        with PortalCluster(case_name='baseline', profile='classic') as cluster:
            cl = HTTPClient()
            cl.login(cluster.origins['tender'], '/anmeldung')
            # Malformed numbers
            bad_data = {
                'bidder_company': 'Bad Corp',
                'bidder_vat': 'VAT-001',
                'offer_type': 'bid',
                'discount_percent': 'invalid_num',
                'total_net': '100',
                'accept_documents': '1'
            }
            st, _ = cl.post(f"{cluster.origins['tender']}/angebot-einreichen", bad_data)
            self.assertEqual(st, 400)

            # Own terms checked
            terms_data = {
                'bidder_company': 'Terms Corp',
                'bidder_vat': 'VAT-002',
                'offer_type': 'bid',
                'discount_percent': '5',
                'total_net': '100',
                'accept_documents': '1',
                'own_terms': '1'
            }
            cl.post(f"{cluster.origins['tender']}/angebot-einreichen", terms_data)
            cl.post(f"{cluster.origins['tender']}/tan-anfordern", {})
            m = re.search(r"TAN-\d+", cluster.messages[0]['body'])
            tan = m.group(0)
            st, _ = cl.post(f"{cluster.origins['tender']}/absenden", {'tan': tan})
            self.assertEqual(st, 400)

    def test_cluster_lifecycle_and_isolation(self):
        c1 = PortalCluster(case_name='baseline', profile='classic')
        c2 = PortalCluster(case_name='blocked', profile='modern')
        c1.start()
        c2.start()
        try:
            self.assertNotEqual(c1.origins['tender'], c2.origins['tender'])
            with self.assertRaises(RuntimeError):
                c1.start()
            # Session isolation across origins
            cl = HTTPClient()
            cl.login(c1.origins['tender'], '/anmeldung')
            st, body = cl.get(f"{c1.origins['mail']}/postfach/posteingang")
            self.assertIn("Anmeldung", body)
        finally:
            c1.stop()
            c2.stop()

if __name__ == '__main__':
    unittest.main()
