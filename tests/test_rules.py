import unittest

from backend.prediction.rules import RuleCitation, ReviewedFact, ReviewRule, evaluate_rule


def citation():
    return RuleCitation(source_id='synthetic-policy', version='1', sha256='a' * 64,
                        page=2, quote='Synthetic rule and evidence, not governing terms')


def fact(key='possession_ended', value='no', state='present', **changes):
    return ReviewedFact(**{'claim_id': '100', 'key': key, 'value': value if state == 'present' else None,
                           'state': state, 'reviewed_by': 'synthetic-reviewer',
                           'citations': (citation(),), **changes})


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.rule = ReviewRule(rule_id='synthetic-only', version='1',
            candidate_reason='Synthetic possession condition', required_facts=('possession_ended',),
            matches={'possession_ended': 'no'}, terms=(citation(),), reviewed_by='synthetic-reviewer')

    def evaluate(self, **changes):
        return evaluate_rule(**{'rule': self.rule, 'claim_id': '100', 'reserved_ids': ('200',),
            'applicability': fact('synthetic-only:applicable', 'yes'), 'facts': (fact(),), **changes})

    def test_candidate_requires_rule_applicability_and_source_facts(self):
        result = self.evaluate()
        self.assertEqual(result['status'], 'candidate_reason')
        self.assertEqual(len(result['citations']), 3)
        self.assertEqual(result['decision'], 'needs_review')
        self.assertIsNone(result['approved_ceiling'])
        self.assertFalse(result['training_eligible'])
        self.assertFalse(result['prediction_eligible'])
        self.assertEqual(self.evaluate(facts=(fact(value='yes'),))['status'], 'reason_not_supported')
        self.assertEqual(self.evaluate(applicability=fact('synthetic-only:applicable', 'no'))['status'], 'not_applicable')

    def test_every_unknown_and_duplicate_withholds_candidate(self):
        for state in ('missing', 'conflicting', 'unreadable', 'unreviewed', 'failed',
                      'not_applicable', 'availability_unverified', 'excluded'):
            with self.subTest(state=state):
                result = self.evaluate(facts=(fact(state=state),))
                self.assertEqual(result['status'], 'needs_review')
                self.assertIsNone(result['candidate_reason'])
                self.assertIn('possession_ended:' + state, result['review_reasons'])
        for facts in ((), (fact(), fact()), (fact(reviewed_by=None),), (fact(citations=()),)):
            self.assertEqual(self.evaluate(facts=facts)['status'], 'needs_review')

    def test_unverified_terms_or_applicability_withholds(self):
        for field, value in (('terms', ()), ('reviewed_by', None), ('matches', {})):
            rule = self.rule.model_copy(update={field: value})
            self.assertEqual(self.evaluate(rule=rule)['status'], 'needs_review')
        for applicability in (fact('synthetic-only:applicable', state='conflicting'),
                              fact('synthetic-only:applicable', 'yes', citations=()), fact()):
            self.assertEqual(self.evaluate(applicability=applicability)['status'], 'needs_review')

    def test_reserved_and_placeholder_inventory_boundaries(self):
        for identity in ('200', '00200', '53', '00053'):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.evaluate(claim_id=identity)
        with self.assertRaises(ValueError):
            self.evaluate(reserved_ids=())

    def test_conditions_must_have_required_facts(self):
        with self.assertRaises(ValueError):
            self.evaluate(rule=self.rule.model_copy(update={'required_facts': ('unrelated',)}))

    def test_cross_claim_evidence_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(facts=(fact(claim_id='101'),))

    def test_duplicate_evidence_keeps_both_source_citations(self):
        other = citation().model_copy(update={'source_id': 'competing-evidence'})
        result = self.evaluate(facts=(fact(), fact(value='yes', citations=(other,))))
        self.assertEqual(result['status'], 'needs_review')
        self.assertIn('competing-evidence', [item['source_id'] for item in result['citations']])
