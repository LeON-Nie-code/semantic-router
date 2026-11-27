import math
import unittest
from qualify import close, finite, panel, percentile, questions, validate


class QualificationChecks(unittest.TestCase):
    def test_missing_answers_fail(self):
        self.assertEqual(len(validate(200, {'answers': {}}, questions())), 3)

    def test_http_failure_cannot_pass(self):
        self.assertIn('http_error', validate(503, {}, questions()))

    def test_nan_is_detected_recursively(self):
        self.assertFalse(finite({'answers': [{'score': math.nan}]}))
        self.assertFalse(finite({'value': math.inf}))

    def test_stability_checks_strings_and_probability_drift(self):
        self.assertTrue(close({'noul': 0.25}, {'noul': 0.250001}))
        self.assertFalse(close({'noul': 0.25}, {'noul': 0.26}))
        self.assertFalse(close({'choice': 'code'}, {'choice': 'math'}))
        self.assertFalse(close({'choice': 'code'}, {}))

    def test_fixed_panel_has_all_question_types(self):
        self.assertEqual(len(panel()), 24)
        self.assertEqual(panel(), panel())
        for case in panel():
            self.assertEqual({q['type'] for q in case['body']['questions'].values()},
                             {'choice', 'noul', 'score'})

    def test_nearest_rank_p95(self):
        self.assertEqual(percentile(list(range(1, 101)), .95), 95)

    def test_answer_type_and_numeric_value_are_checked(self):
        body = {'answers': {'x': {'type': 'choice', 'score': 'high'}}}
        self.assertTrue(validate(200, body, {'x': {'type': 'score', 'criteria': ['Low', 'High']}}))

    def test_probabilities_must_be_normalized(self):
        body = {'answers': {'x': {'type': 'choice', 'choice': 'a',
                                 'probabilities': {'a': .1, 'b': .1}}}}
        self.assertTrue(validate(200, body, {'x': {'type': 'choice', 'criteria': {'a': 'A', 'b': 'B'}}}))


if __name__ == '__main__':
    unittest.main()
