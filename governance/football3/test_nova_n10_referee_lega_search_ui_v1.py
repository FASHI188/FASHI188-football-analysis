from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_lega_search_ui_v1 import (
    StructureParser, domain_ok, form_parameter_names, script_contract, select_route_scripts
)

REG=Path(__file__).with_name("nova_n10_referee_lega_search_ui_registry_v1.json")

class T(unittest.TestCase):
    def test_registry_contract(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"cb6c94ea0ee13a1fb00820d31e1356ee83ea13f1")
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["search_result_text_read"])
        self.assertFalse(p["hard_rules"]["search_result_links_read"])
        self.assertFalse(p["hard_rules"]["article_body_read"])
        self.assertFalse(p["hard_rules"]["parameter_guessing_allowed"])
        self.assertFalse(p["hard_rules"]["hidden_endpoint_bruteforce_allowed"])

    def test_structure_parser_ignores_text_nodes(self):
        html='''<html><body><form action="/search" method="get">
        SECRET RESULT TEXT
        <input type="search" name="q" placeholder="Search">
        <button type="submit" name="submit">Go</button>
        <script src="/_next/static/chunks/app/search/page-abc.js"></script>
        </form></body></html>'''
        p=StructureParser({"action","method","name","type","placeholder","value","src"})
        p.feed(html)
        self.assertEqual(p.forms,[{"action":"/search","method":"get"}])
        self.assertEqual(p.inputs[0]["name"],"q")
        self.assertEqual(p.scripts[0]["src"],"/_next/static/chunks/app/search/page-abc.js")
        self.assertFalse(hasattr(p,"text"))

    def test_script_contract_mechanical_parameters(self):
        raw=b'''const q=searchParams.get("query");
        const p=new URLSearchParams(); p.set("page","1");
        router.push("/search?term="+q);'''
        c=script_contract(raw)
        self.assertIn("query",c["parameter_names"])
        self.assertIn("page",c["parameter_names"])
        self.assertIn("term",c["parameter_names"])
        self.assertGreaterEqual(c["relevant_context_marker_n"],2)

    def test_route_script_selection_prefers_search_app(self):
        scripts=[
            "https://www.legaseriea.it/_next/static/chunks/common-search-utils.js",
            "https://www.legaseriea.it/_next/static/chunks/app/search/page-abc.js",
            "https://www.legaseriea.it/_next/static/chunks/main.js",
        ]
        out=select_route_scripts(scripts,["/app/search/","search"],8)
        self.assertEqual(out,["https://www.legaseriea.it/_next/static/chunks/app/search/page-abc.js"])

    def test_form_names_and_domain(self):
        self.assertEqual(form_parameter_names([],[
            {"name":"q","type":"search"},{"name":"page","type":"hidden"},{"type":"submit"}
        ]),["page","q"])
        self.assertTrue(domain_ok("https://www.legaseriea.it/search","legaseriea.it"))
        self.assertFalse(domain_ok("https://legaseriea.it.evil.example/search","legaseriea.it"))

if __name__=="__main__":
    unittest.main()
