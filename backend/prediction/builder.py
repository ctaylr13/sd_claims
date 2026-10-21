from collections import Counter

from .models import (FACT_KEYS, SOURCE_KINDS, ApprovalSnapshot, CandidateFact,
                     InputFact, PredictionBundle, Rejection)
from .snapshots import digest


def _rejection(snapshot, fact):
    if fact.key not in FACT_KEYS:
        return 'fact_not_allowed'
    approval = next((item for item in snapshot.facts if item.fact_id == fact.fact_id), None)
    if approval is None or approval.sha256 != digest(fact):
        return 'fact_not_approved'
    if snapshot.cutoff is None:
        return 'cutoff_unknown'
    for ref in fact.references:
        source = next((s for s in snapshot.sources if s.source_id == ref.source_id), None)
        if source is None or source.kind not in SOURCE_KINDS:
            return 'source_not_allowed'
        if (source.version, source.sha256) != (ref.version, ref.sha256):
            return 'version_mismatch'
        section = next((s for s in source.sections if s.section_id == ref.section_id), None)
        if section is None or section.content != 'neutral':
            return 'section_not_allowed'
        if section.sha256 != ref.section_sha256:
            return 'version_mismatch'
        for availability in (source.availability, section.availability):
            if availability.state != 'verified':
                return 'availability_unverified'
            if availability.received_at >= snapshot.cutoff:
                return 'post_cutoff'
    return None


def build_prediction_bundle(snapshot: ApprovalSnapshot, candidates: tuple[CandidateFact, ...]):
    snapshot = ApprovalSnapshot.model_validate(snapshot)
    candidates = tuple(CandidateFact.model_validate(fact) for fact in candidates)
    counts = Counter(fact.key for fact in candidates)
    accepted, rejected, unknowns = {}, [], {}
    for index, fact in enumerate(candidates):
        reason = _rejection(snapshot, fact)
        if reason is None and counts[fact.key] > 1:
            reason = 'duplicate_fact'
        if reason:
            rejected.append(Rejection(candidate_index=index, reason=reason))
            if fact.key in FACT_KEYS:
                unknowns[fact.key] = 'availability_unverified' if reason in {
                    'availability_unverified', 'cutoff_unknown'} else 'excluded'
        else:
            accepted[fact.key] = InputFact(key=fact.key, state=fact.state, value=fact.value,
                                           references=fact.references)
    facts = tuple(accepted.get(key) or InputFact(key=key, state=unknowns.get(key, 'unreviewed'))
                  for key in sorted(FACT_KEYS))
    return PredictionBundle(claim_id=snapshot.claim_id, snapshot_id=digest(snapshot),
                            cutoff=snapshot.cutoff, facts=facts, rejections=tuple(rejected),
                            eligible=any(fact.state == 'present' for fact in facts))
