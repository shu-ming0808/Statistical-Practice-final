import csv
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from import_metro_distances import parse_fares, derive_routes, DISTANCE_SCHEMA
from metro_operations import import_sql


class DistanceTests(unittest.TestCase):
    def fixture(self):
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['起站','訖站','全票票價[金額]',
                         '敬老卡愛心卡愛心陪伴卡及臺北市與新北市兒童優惠票價[金額]','距離'])
        for a in ['甲','北門','乙']:
            for b in ['甲','北門','乙']:
                # The endpoint shortcut must NOT replace the chosen line's two legs.
                km = 0 if a==b else (3 if {a,b}=={'甲','乙'} else 2)
                writer.writerow([a,b,20,8,km])
        return out.getvalue().encode('cp950')

    def links(self):
        return [dict(source_id='links',link_no=1,route_name='甲-乙',route_seq=1,station_a='甲',station_b='北門'),
                dict(source_id='links',link_no=2,route_name='甲-乙',route_seq=2,station_a='北門',station_b='乙')]

    def test_route_uses_adjacent_pairs_not_endpoint_shortcut(self):
        fares = parse_fares(self.fixture(), 'fare')
        segments, routes, paths = derive_routes(fares, self.links(), ['甲-乙'])
        self.assertEqual(routes[0]['service_km'], '4.00')
        self.assertEqual(routes[0]['shortest_endpoint_km'], '3.00')
        self.assertEqual([r['along_line_km'] for r in paths], ['2.00','2.00'])
        self.assertTrue(all(r['depot_km'] is None and r['return_km'] is None for r in paths))
        self.assertEqual(len(segments),2)

    def test_duplicate_and_asymmetry_rejected(self):
        content = self.fixture()
        self.assertEqual(len(parse_fares(content + content.splitlines(keepends=True)[1], 'fare')), 9)
        with self.assertRaises(ValueError):
            parse_fares(content + '甲,北門,25,8,2\r\n'.encode('cp950'), 'fare')
        with self.assertRaises(ValueError):
            parse_fares(content.replace('甲,北門,20,8,2'.encode('cp950'), '甲,北門,20,8,1'.encode('cp950')), 'fare')

    def test_disconnected_route_rejected(self):
        links = self.links(); links[1]['station_a'] = '甲'
        with self.assertRaises(ValueError):
            derive_routes(parse_fares(self.fixture(),'fare'), links, ['甲-乙'])

    def test_zero_distance_preserved_but_not_used_as_rail_segment(self):
        content = self.fixture().decode('cp950').replace('甲,北門,20,8,2','甲,北門,20,8,0').replace('北門,甲,20,8,2','北門,甲,20,8,0')
        fares = parse_fares(content.encode('cp950'),'fare')
        self.assertTrue(any(r['origin']=='甲' and r['destination']=='北門' and r['shortest_km']=='0.00' for r in fares))
        with self.assertRaises(ValueError):
            derive_routes(fares,self.links(),['甲-乙'])

    def test_import_guards_conflicts_and_uses_explicit_schema(self):
        rows = parse_fares(self.fixture(),'fare')
        sql = import_sql({'metro_od_distance':rows}, DISTANCE_SCHEMA)
        self.assertIn('START TRANSACTION;',sql)
        self.assertIn('metro_od_distance_conflict',sql)
        self.assertIn('d.origin=s.origin',sql)
        self.assertNotIn('REPLACE INTO',sql)
        self.assertNotIn('ON DUPLICATE KEY UPDATE',sql)


if __name__ == '__main__':
    unittest.main()
