import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from backend.prediction import (ApprovalSnapshot, CandidateFact, build_prediction_bundle,
                                load_snapshot, save_snapshot)
from backend.prediction.evaluation import (AccessPurpose, AnswerKey, build_evaluation_inputs,
                                           load_answer_key)
from backend.prediction.models import Availability, FactApproval, Reference, Section, Source
from backend.prediction.snapshots import digest


SHA = 'a'*64
SECTION_SHA = 'b'*64
BEFORE = '2025-01-01T10:00:00Z'
CUTOFF = '2025-01-02T10:00:00Z'
AFTER = '2025-01-03T10:00:00Z'


def fixture(*, state='present', key='monthly_rent', kind='lease', content='neutral',
            received_at=BEFORE, verified=True, cutoff=CUTOFF):
    availability = Availability(state='verified', received_at=received_at,
                                proof_kind='submission_email', proof_sha256=SHA) if verified else Availability()
    section = Section(section_id='rent', sha256=SECTION_SHA, content=content, availability=availability)
    source = Source(source_id='lease-1', version='revision-1', sha256=SHA, kind=kind,
                    availability=availability, sections=(section,))
    ref = Reference(source_id=source.source_id, version=source.version, sha256=SHA,
                    section_id=section.section_id, section_sha256=SECTION_SHA)
    fact = CandidateFact(fact_id='rent-1', key=key, state=state,
                         value='1200.00' if state == 'present' else None, references=(ref,))
    snapshot = ApprovalSnapshot(claim_id='1241', cutoff=cutoff,
                                cutoff_evidence_sha256=SHA if cutoff else None,
                                approved_by='fixture-reviewer', approved_at=AFTER,
                                sources=(source,), facts=(FactApproval(fact_id=fact.fact_id, sha256=digest(fact)),))
    return snapshot, fact


def inputs(bundle):
    return {fact.key: fact for fact in bundle.facts}


def replace(model, **updates):
    return type(model).model_validate({**model.model_dump(), **updates})


class PredictionInputTests(unittest.TestCase):
    def test_permitted_fact_and_no_whole_source_text(self):
        snapshot, fact = fixture()
        bundle = build_prediction_bundle(snapshot, (fact,))
        self.assertTrue(bundle.eligible)
        self.assertEqual(inputs(bundle)['monthly_rent'].value, '1200.00')
        self.assertEqual(inputs(bundle)['monthly_rent'].references, fact.references)
        self.assertEqual(bundle.rejections, ())
        self.assertEqual(inputs(bundle)['confirmed_possession_end'].state, 'unreviewed')
        for forbidden in ('answer_key', 'approved_ceiling', 'proof_kind', 'approved_by', 'source_url'):
            self.assertNotIn(forbidden, bundle.model_dump_json())

    def assert_rejected(self, snapshot, fact, reason):
        bundle = build_prediction_bundle(snapshot, (fact,))
        self.assertFalse(bundle.eligible)
        self.assertEqual(bundle.rejections[0].reason, reason)
        self.assertNotIn('1200.00', bundle.model_dump_json())
        return bundle

    def test_outcome_and_rationale_fields_even_if_approved(self):
        for key in ('Status', 'Approved Benefit Amount', 'approved_ceiling', 'Approval Date',
                    'Posted Date', 'PM Explanation', 'rationale', 'Send to Collections'):
            with self.subTest(key=key):
                self.assert_rejected(*fixture(key=key), 'fact_not_allowed')

    def test_decision_and_derived_sources_even_with_neutral_section(self):
        for kind in ('decision_letter', 'denial_letter', 'approval_letter', 'outcome',
                     'adjudicator_rationale', 'collection_letter', 'reconciliation', 'unknown_mixed'):
            with self.subTest(kind=kind):
                self.assert_rejected(*fixture(kind=kind), 'source_not_allowed')

    def test_section_review_does_not_inherit_source_permission(self):
        for content in ('answer_revealing', 'unreviewed'):
            self.assert_rejected(*fixture(content=content), 'section_not_allowed')
        snapshot, fact = fixture()
        source = replace(snapshot.sources[0], sections=())
        self.assert_rejected(replace(snapshot, sources=(source,)), fact, 'section_not_allowed')

    def test_current_sheet_or_ocr_without_receipt_is_excluded(self):
        for kind in ('claim_intake', 'lease', 'ledger'):
            snapshot, fact = fixture(kind=kind, verified=False)
            bundle = self.assert_rejected(snapshot, fact, 'availability_unverified')
            self.assertEqual(inputs(bundle)['monthly_rent'].state, 'availability_unverified')

    def test_source_and_section_receipt_are_independent(self):
        snapshot, fact = fixture()
        source = snapshot.sources[0]
        for altered in (replace(source, availability=Availability()),
                        replace(source, sections=(replace(source.sections[0], availability=Availability()),))):
            self.assert_rejected(replace(snapshot, sources=(altered,)), fact, 'availability_unverified')

    def test_post_cutoff_and_same_instant_excluded(self):
        for received_at in (AFTER, CUTOFF, '2025-01-02T05:00:00-05:00'):
            self.assert_rejected(*fixture(received_at=received_at), 'post_cutoff')

    def test_unknown_cutoff_cannot_use_approval_date_as_exact_time(self):
        bundle = self.assert_rejected(*fixture(cutoff=None), 'cutoff_unknown')
        self.assertEqual(inputs(bundle)['monthly_rent'].state, 'availability_unverified')
        with self.assertRaises(ValidationError):
            fixture(cutoff='2025-01-02')
        with self.assertRaises(ValidationError):
            fixture(received_at='2025-01-01T10:00:00')

    def test_receipt_and_cutoff_need_proof(self):
        for fields in ({'state': 'verified'}, {'state': 'verified', 'received_at': BEFORE},
                       {'state': 'verified', 'received_at': BEFORE, 'proof_kind': 'upload_audit'}):
            with self.assertRaises(ValidationError):
                Availability(**fields)
        snapshot, _ = fixture()
        with self.assertRaises(ValidationError):
            replace(snapshot, cutoff_evidence_sha256=None)

    def test_exact_fact_allowlist_prevents_new_or_changed_values(self):
        snapshot, fact = fixture()
        self.assert_rejected(replace(snapshot, facts=()), fact, 'fact_not_approved')
        for altered in (replace(fact, value='1300.00'), replace(fact, fact_id='new-id'),
                        replace(fact, key='requested_amount')):
            self.assert_rejected(snapshot, altered, 'fact_not_approved')

    def test_source_version_and_section_hash_changes(self):
        snapshot, fact = fixture()
        source = snapshot.sources[0]
        for altered in (replace(source, version='revision-2'), replace(source, sha256='c'*64),
                        replace(source, sections=(replace(source.sections[0], sha256='c'*64),))):
            self.assert_rejected(replace(snapshot, sources=(altered,)), fact, 'version_mismatch')
        self.assert_rejected(replace(snapshot, sources=()), fact, 'source_not_allowed')

    def test_all_lineage_must_be_permitted(self):
        snapshot, fact = fixture()
        fact = replace(fact, references=(*fact.references, replace(fact.references[0], source_id='denial')))
        snapshot = replace(snapshot, facts=(FactApproval(fact_id=fact.fact_id, sha256=digest(fact)),))
        self.assert_rejected(snapshot, fact, 'source_not_allowed')

    def test_explicit_unknown_states_never_become_zero_or_present(self):
        for state in ('missing', 'conflicting', 'unreadable', 'unreviewed', 'failed',
                      'not_applicable', 'availability_unverified'):
            snapshot, fact = fixture(state=state)
            bundle = build_prediction_bundle(snapshot, (fact,))
            self.assertEqual(inputs(bundle)['monthly_rent'].state, state)
            self.assertIsNone(inputs(bundle)['monthly_rent'].value)
            self.assertFalse(bundle.eligible)
            with self.assertRaises(ValidationError):
                replace(fact, value='0.00')
        with self.assertRaises(ValidationError):
            replace(fixture()[1], references=())

    def test_duplicate_candidates_do_not_choose_a_value(self):
        snapshot, fact = fixture()
        bundle = build_prediction_bundle(snapshot, (fact, fact))
        self.assertEqual([r.reason for r in bundle.rejections], ['duplicate_fact']*2)
        self.assertEqual(inputs(bundle)['monthly_rent'].state, 'excluded')

    def test_unreviewed_inputs_default_to_empty_unknown_bundle(self):
        snapshot, _ = fixture(cutoff=None)
        bundle = build_prediction_bundle(replace(snapshot, sources=(), facts=()), ())
        self.assertFalse(bundle.eligible)
        self.assertTrue(all(fact.state == 'unreviewed' and fact.value is None for fact in bundle.facts))

    def test_each_supported_source_still_requires_fact_and_section_approval(self):
        for kind in ('claim_intake', 'lease', 'renewal', 'ledger', 'move_out_statement',
                     'policy', 'possession_evidence'):
            with self.subTest(kind=kind):
                snapshot, fact = fixture(kind=kind)
                self.assertTrue(build_prediction_bundle(snapshot, (fact,)).eligible)
                self.assert_rejected(replace(snapshot, facts=()), fact, 'fact_not_approved')

    def test_cli_loads_snapshot_and_keeps_unverified_value_out(self):
        snapshot, fact = fixture(verified=False)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot_id = save_snapshot(root, snapshot)
            candidates = root / 'candidates.json'
            candidates.write_text('['+fact.model_dump_json()+']')
            output = root / 'bundle.json'
            result = subprocess.run([sys.executable, '-m', 'backend.prediction', str(root),
                                     snapshot_id, str(candidates), '--output', str(output)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('1200.00', output.read_text())
            self.assertIn('availability_unverified', output.read_text())

    def test_extra_raw_content_rejected(self):
        snapshot, fact = fixture()
        for model, field in ((snapshot, 'historical_outcome'), (fact, 'raw_text'),
                             (snapshot.sources[0], 'filename')):
            with self.assertRaises(ValidationError):
                replace(model, **{field: 'SECRET ANSWER'})

    def test_snapshots_are_nested_immutable_and_content_addressed(self):
        snapshot, fact = fixture()
        for model, field, value in ((snapshot, 'claim_id', '564'),
                                    (snapshot.sources[0], 'kind', 'decision_letter'),
                                    (snapshot.sources[0].availability, 'state', 'unverified')):
            with self.assertRaises(ValidationError):
                setattr(model, field, value)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot_id = save_snapshot(root, snapshot)
            self.assertEqual(load_snapshot(root, snapshot_id), snapshot)
            self.assertEqual(save_snapshot(root, snapshot), snapshot_id)
            new = replace(snapshot, approved_by='another-reviewer')
            self.assertNotEqual(save_snapshot(root, new), snapshot_id)
            (root / f'{snapshot_id}.json').write_text('{}')
            with self.assertRaises(ValueError):
                load_snapshot(root, snapshot_id)
            with self.assertRaises(ValueError):
                save_snapshot(root, snapshot)
            with self.assertRaises(ValueError):
                load_snapshot(root, '../key')
        self.assertEqual(inputs(build_prediction_bundle(snapshot, (fact,)))['monthly_rent'].value, '1200.00')

    def test_construct_or_copy_validation_bypass_revalidated_at_boundary(self):
        snapshot, fact = fixture()
        with self.assertRaises(ValidationError):
            build_prediction_bundle(snapshot.model_copy(update={'claim_id': '53'}), (fact,))
        with self.assertRaises(ValidationError):
            build_prediction_bundle(snapshot, (fact.model_copy(update={'state': 'missing'}),))

    def test_candidate_53_reserved_outside_historical_prediction(self):
        snapshot, _ = fixture()
        for claim_id in ('53', ' 53 ', '053'):
            with self.assertRaises(ValidationError):
                replace(snapshot, claim_id=claim_id)


class EvaluationInputTests(unittest.TestCase):
    def key(self, **changes):
        return AnswerKey(claim_id='1241', decision='approved', ceiling_state='unknown',
                         source_sha256=SHA, reviewed_by='fixture-reviewer', **changes)

    def test_denied_before_reading_answer_file(self):
        for access in (AccessPurpose.PREDICTION, AccessPurpose.ROBUSTNESS, None, 'evaluation'):
            with patch.object(Path, 'read_bytes') as read:
                with self.assertRaises(PermissionError):
                    load_answer_key(Path('answer.json'), access=access)
                read.assert_not_called()
            with self.assertRaises(PermissionError):
                build_evaluation_inputs(fixture()[0], (), self.key(), access=access)

    def test_evaluator_can_load_and_join_key_without_changing_prediction(self):
        snapshot, fact = fixture()
        key = self.key()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'key.json'
            path.write_text(key.model_dump_json())
            self.assertEqual(load_answer_key(path, access=AccessPurpose.EVALUATION), key)
        evaluated = build_evaluation_inputs(snapshot, (fact,), key, access=AccessPurpose.EVALUATION)
        self.assertEqual(evaluated.prediction, build_prediction_bundle(snapshot, (fact,)))
        self.assertIsNone(evaluated.answer_key.approved_ceiling)
        with self.assertRaises(ValueError):
            build_evaluation_inputs(snapshot, (fact,), replace(key, claim_id='564'), access=AccessPurpose.EVALUATION)
        with self.assertRaises(ValidationError):
            replace(key, claim_id='53')

    def test_nonfinal_and_ambiguous_labels_are_not_fabricated(self):
        key = self.key()
        with self.assertRaises(ValidationError):
            replace(key, decision='pending')
        with self.assertRaises(ValidationError):
            replace(key, ceiling_state='present')
        unknown = replace(key, decision='unknown')
        self.assertIsNone(unknown.approved_ceiling)
        self.assertEqual(unknown.ceiling_state, 'unknown')
