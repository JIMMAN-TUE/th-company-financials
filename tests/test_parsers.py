import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from tests.helpers import STATEMENT_HTML
from thfin.parsers.numbers import parse_number
from thfin.parsers.statement import extract_from_rows, parse_excel, parse_html


class NumberTests(unittest.TestCase):
    def test_plain_and_thousands(self):
        self.assertEqual(parse_number("1,234,567.89"), 1234567.89)
        self.assertEqual(parse_number(" 42 "), 42.0)

    def test_negative_forms(self):
        self.assertEqual(parse_number("(1,000.50)"), -1000.5)
        self.assertEqual(parse_number("-250"), -250.0)

    def test_empty_markers(self):
        for s in ["", "-", "—", "N/A", None, "abc", "12abc"]:
            self.assertIsNone(parse_number(s), s)

    def test_thai_digits(self):
        self.assertEqual(parse_number("๑,๒๓๔"), 1234.0)


class StatementTests(unittest.TestCase):
    def test_html_fixture(self):
        data = parse_html(STATEMENT_HTML.read_text(encoding="utf-8"))
        self.assertEqual(sorted(data), [2566, 2567])
        y = data[2567]
        self.assertEqual(y["revenue_total"], 1_200_000)
        self.assertEqual(y["revenue_sales"], 1_150_000)
        self.assertEqual(y["cost_of_sales"], 700_000)
        self.assertEqual(y["total_expenses"], 1_100_000)
        self.assertEqual(y["net_profit"], 100_000)
        self.assertEqual(y["total_assets"], 12_500_000)
        self.assertEqual(data[2566]["net_profit"], -50_000)

    def test_other_income_is_not_revenue(self):
        data = parse_html(STATEMENT_HTML.read_text(encoding="utf-8"))
        self.assertNotEqual(data[2567]["revenue_total"], 50_000)

    def test_english_labels_and_gregorian_headers(self):
        rows = [
            ["Item", "FY2024", "FY2023"],
            ["Total revenues", "500", "400"],
            ["Cost of sales", "300", "250"],
            ["Net profit (loss)", "(20)", "10"],
        ]
        data = extract_from_rows(rows)
        self.assertEqual(data[2567]["revenue_total"], 500)  # 2024 CE -> 2567 BE
        self.assertEqual(data[2566]["cost_of_sales"], 250)
        self.assertEqual(data[2567]["net_profit"], -20)

    def test_money_that_looks_like_a_year_is_not_a_header(self):
        rows = [["รายการ", "2567", "2566"], ["รายได้รวม", "2,567", "2,566"]]
        data = extract_from_rows(rows)
        self.assertEqual(data[2567]["revenue_total"], 2567)

    def test_no_header_means_nothing_extracted(self):
        self.assertEqual(extract_from_rows([["รายได้รวม", "100"]]), {})

    def test_no_tables(self):
        self.assertEqual(parse_html("<html><body><p>nothing</p></body></html>"), {})

    def test_excel(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["รายการ", 2567, 2566])
        ws.append(["รายได้รวม", 900, 800])
        ws.append(["ต้นทุนขาย", 500, 450])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.xlsx"
            wb.save(p)
            data = parse_excel(p)
        self.assertEqual(data[2567]["revenue_total"], 900)
        self.assertEqual(data[2566]["cost_of_sales"], 450)


if __name__ == "__main__":
    unittest.main()
