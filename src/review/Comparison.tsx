import { label, percentage } from './api';
import { amountDifference, decisionMatch } from './comparisonFormatting';
import { useResource } from './useResource';
import type { Claim, Evaluation, HistoricalComparison } from './types';
import { Badge, Copy, Definition, Details, Grid, Heading, Notice, Panel, Secondary, Small, Spread, Stack, Stat, StatValue, Summary, Term, Value } from './styles';

export const Comparison = ({ comparison }: { comparison: HistoricalComparison }) => <Panel>
  <Spread><Heading>Prediction vs recorded outcome</Heading><Badge data-state={comparison.decision_match ? 'passed' : 'Needs review'}>{decisionMatch(comparison.decision_match)}</Badge></Spread>
  <Definition><Term>Recorded status</Term><Value>{comparison.recorded_status ?? 'Missing'}</Value><Term>Recorded approved amount</Term><Value>{comparison.recorded_amount_raw ?? 'Missing'}</Value><Term>Ceiling difference</Term><Value>{amountDifference(comparison.difference_dollars)}</Value><Term>Absolute amount error</Term><Value>{comparison.absolute_error_percent === null ? 'Unscored' : `${comparison.absolute_error_percent.toFixed(1)}%`}</Value></Definition>
  {comparison.reasons.map(reason => <Small key={reason}>{reason}</Small>)}
  <Copy>{comparison.interpretation}</Copy>
</Panel>;

export const RecordedSummary = ({ claim }: { claim: Claim }) => <Definition>
  <Term>Recorded status</Term><Value>{claim.historical_outcome.Status || 'Missing'}</Value>
  <Term>Recorded approved amount</Term><Value>{claim.historical_outcome['Approved Benefit Amount'] || 'Missing'}</Value>
</Definition>;

export const ModelAgreement = () => {
  const { data, error, reload } = useResource<Evaluation>('/api/review/metrics');
  if (error) return <Notice><Spread><Copy>Model accuracy estimate unavailable: {error}</Copy><Secondary onClick={reload}>Reload model metrics</Secondary></Spread></Notice>;
  if (!data) return <Copy role="status">Loading measured model agreement…</Copy>;
  return <Stack><Grid>
    <Stat><Small>Historical decision agreement · development evaluation</Small><StatValue>{percentage(data.decision.research_decision.accuracy)}</StatValue><Small>Across {data.coverage.qualified_outcomes} qualified outcomes</Small></Stat>
    <Stat><Small>Majority baseline agreement</Small><StatValue>{percentage(data.decision.majority_decision.accuracy)}</StatValue><Small>Comparison with always selecting the most common decision</Small></Stat>
    <Stat><Small>Decline recall · development evaluation</Small><StatValue>{percentage(data.decision.research_decision.decline_recall)}</StatValue><Small>Share of recorded declines identified by the model</Small></Stat>
  </Grid><Details><Summary>How to read these percentages</Summary><Stack><Copy>This is measured agreement with historical records on reused development folds. It is model-level evidence, not a probability that any individual claim is correct. Per-claim confidence has not been calibrated, and independent correctness has not been established.</Copy><Copy>{data.method}</Copy><Small>Report: {new Date(data.created_at).toLocaleString()} · {label(data.scope)}</Small></Stack></Details></Stack>;
};
