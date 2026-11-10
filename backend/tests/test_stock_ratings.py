import unittest
from app.services.stock_ratings import parse_ratings


class RatingTests(unittest.TestCase):
    def html(self, opinion='매수'):
        return f'''<input id="hd_cmp_cd" value="005930"><table id="cTB24"><tr>
        <td>증권사</td><td>26/11/06</td><td>440,000</td><td>-</td><td><span>0.00</span></td>
        <td>{opinion}</td><td>중립</td></tr></table>'''

    def test_real_opinions_and_units_are_preserved(self):
        for opinion in ['매수', '매도', '중립', 'Not Rated', 'Trading Buy']:
            row = parse_ratings(self.html(opinion), '005930')['rows'][0]
            self.assertEqual(row['opinion'], opinion)
            self.assertEqual(row['previous_opinion'], '중립')
            self.assertEqual(row['target_price'], 440000)
            self.assertIsNone(row['previous_target_price'])
            self.assertEqual(row['date'], '2026-11-06')

    def test_wrong_stock_and_changed_schema_rejected(self):
        with self.assertRaises(ValueError):
            parse_ratings(self.html(), '000660')
        with self.assertRaises(ValueError):
            parse_ratings(self.html().replace('cTB24', 'other'), '005930')
        self.assertEqual(parse_ratings('<input id="hd_cmp_cd" value="005930"><table id="cTB24"></table>', '005930')['rows'], [])
