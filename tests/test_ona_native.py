"""Native ONA boundary tests; OMEGA_onaBinary selects an installed test binary."""
import os
from pathlib import Path
import tempfile
import unittest
from src.ona import ONAEngine, binary_path


class NativeONATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.binary = binary_path()
        except Exception as exc:
            raise unittest.SkipTest(str(exc))

    def test_real_deduction_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ona.sqlite3'
            engine = ONAEngine(path, self.binary)
            try:
                engine.run('deduction', '<robin --> bird>.\n<bird --> animal>.', 100)
                answer = engine.run('deduction', '<robin --> animal>?', 100)
                self.assertTrue(answer['answers'], answer['raw'])
                self.assertGreater(answer['answers'][0]['truth']['confidence'], .5)
            finally:
                engine.close()
            engine = ONAEngine(path, self.binary)
            try:
                answer = engine.run('deduction', '<robin --> animal>?', 20)
                self.assertTrue(answer['answers'], answer['raw'])
                engine.reset('deduction')
                self.assertFalse(engine.run('deduction', '<robin --> animal>?', 20)['answers'])
            finally:
                engine.close()

    def test_goal_operation_proposal_and_observed_feedback(self):
        engine = ONAEngine(':memory:', self.binary)
        try:
            engine.run('door', '*setopname 1 ^go\n*motorbabbling=false\n<(<door --> [centered]> &/ ^go) =/> <door --> [open]>>.', 20)
            result = engine.run('door', '<door --> [centered]>. :|:\n<door --> [open]>! :|:', 20)
            self.assertEqual(result['operations'][0]['operator'], '^go', result['raw'])
            self.assertTrue(any('!' in item['text'] for item in result['inputs']))
            observed = engine.run('door', '<door --> [open]>. :|:\n<door --> [open]>? :|:', 20)
            self.assertTrue(observed['answers'], observed['raw'])
            self.assertGreater(observed['answers'][0]['truth']['confidence'], .5)
            with self.assertRaises(ValueError):
                engine.run('door', '*setopstdin 1', 20)
        finally:
            engine.close()

    def test_temporal_events_and_budget_validation(self):
        engine = ONAEngine(':memory:', self.binary)
        try:
            result = engine.run('temporal', '<sensor --> bright>. :|:\n<switch --> on>. :|:', 20)
            self.assertEqual(result['engine'], 'ona')
            self.assertTrue(any(e['occurrence_time'] != 'eternal' for e in result['inputs']))
            with self.assertRaises(ValueError):
                engine.run('temporal', '100000000', 20)
            with self.assertRaises(ValueError):
                engine.run('temporal', '', 1001)
        finally:
            engine.close()


if __name__ == '__main__':
    unittest.main()
