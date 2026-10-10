import unittest
from html.parser import HTMLParser

from profile_scanned import transform_html


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.text = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden -= 1
    def handle_data(self, text):
        if not self.hidden:
            self.text.append(text)


class ScannedProfileTests(unittest.TestCase):
    requirement = '''<!doctype html><html><body><h2>Current REV-C requirement</h2>
<a href="/requirements/req-b">Next sheet</a><table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Diameter (bore_mm)</td><td>45 mm</td></tr>
<tr><td>Stroke (stroke_mm)</td><td>987 mm</td></tr>
<tr><td>Mounting (mounting)</td><td>Flange</td></tr>
</table></body></html>'''

    def test_requirements_are_visual_not_dom_text(self):
        result = transform_html(self.requirement, 'specification')
        self.assertIn('<canvas ', result)
        self.assertNotIn('<table>', result)
        self.assertIn('987 mm', result)  # normal rendering code contains source
        parser = TextOnly()
        parser.feed(result)
        text = ''.join(parser.text)
        self.assertNotIn('987 mm', text)
        self.assertIn('Current REV-C', text)
        self.assertIn('href="/requirements/req-b"', result)
        self.assertIn('aria-label="Scanned document attachment"', result)
        self.assertNotIn('bore_mm', result)

    def test_historical_and_index_tables_unchanged(self):
        html = '<table><tr><td>REV-A superseded</td><td>135 mm</td></tr></table>'
        self.assertEqual(transform_html(html, 'specification'), html)

    def test_historical_requirement_schema_is_not_converted(self):
        html = self.requirement.replace('Current REV-C requirement', 'Historical REV-A superseded')
        result = transform_html(html, 'specification')
        self.assertNotIn('<canvas', result)
        self.assertIn('987 mm', result)
        self.assertIn('<table>', result)

    def test_forms_and_other_origins_unchanged(self):
        html = '<form action="/proposal"><input name="quantity" value="10"><button>Submit</button></form>'
        for role in ('tender', 'mail', 'erp', 'specification'):
            self.assertEqual(transform_html(html, role), html)
        self.assertEqual(transform_html(self.requirement, 'tender'), self.requirement)

    def test_prices_preserve_original_number_formats_and_dates(self):
        html = '''<section role="tabpanel" style="display:none"><table><thead><tr>
<th>Status</th><th>Validity</th><th>Currency</th><th>Price unit</th><th>FX-Schlüssel</th><th>Format</th><th>Quantity tiers</th></tr></thead>
<tbody><tr><td>Released</td><td><time datetime="2044-01-01">2044-01-01</time> bis 2044-12-31</td><td>USD</td><td>10</td><td>FX-USD</td><td>en-US</td><td>ab 1 Stk: 1,200.00 USD<br>ab 5 Stk: 1,100.00 USD</td></tr></tbody></table></section>'''
        result = transform_html(html, 'erp')
        self.assertIn('<canvas ', result)
        for value in ('1,200.00', '1,100.00', '2044-01-01', 'en-US', 'FX-USD'):
            self.assertIn(value, result)
        self.assertIn('style="display:none"', result)
        parser = TextOnly()
        parser.feed(result)
        self.assertNotIn('1,200.00', ''.join(parser.text))

    def test_markup_in_values_cannot_escape_script(self):
        html = self.requirement.replace('987 mm', '&lt;/script&gt; &lt;img src=x&gt;')
        result = transform_html(html, 'specification')
        self.assertEqual(result.count('</script>'), 1)
        self.assertNotIn('<img src=x>', result)

    def test_stock_stays_readable_without_schema_keys(self):
        html = '<table><tr><td>Free stock (unrestricted_stock)</td><td>19</td></tr></table>'
        result = transform_html(html, 'erp')
        self.assertIn('Free stock', result)
        self.assertNotIn('unrestricted_stock', result)
        self.assertIn('>19<', result)
        self.assertNotIn('<canvas', result)


if __name__ == '__main__':
    unittest.main()
