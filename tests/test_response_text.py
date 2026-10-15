import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"skill/copilot-consult/scripts"))
from response_text import code_from_accessible_lines


class CodeExtractionTests(unittest.TestCase):
    def rows(self,lines):
        return [row for i,line in enumerate(lines,1) for row in ({"type":"Text","name":str(i)},{"type":"Text","name":line})]

    def test_indentation_unicode_empty_lines_and_numeric_source(self):
        rows=self.rows(["def ação():","    return 'coração'","\u00a0","123"])
        result=code_from_accessible_lines(rows)
        self.assertEqual(result,"def ação():\n    return 'coração'\n\n123")
        compile(result,"synthetic.py","exec")

    def test_reject_partial_or_virtualized_lines(self):
        rows=self.rows(["first","second"])
        rows[2]["name"]="3"
        with self.assertRaises(ValueError):
            code_from_accessible_lines(rows)
        with self.assertRaises(ValueError):
            code_from_accessible_lines(rows[:-1])


if __name__=="__main__":
    unittest.main()
