import { amountDifference, decisionMatch } from './comparisonFormatting';
import type { Claim, HistoricalComparison } from './types';
import { Badge, Copy, Definition, Heading, Small, Spread, Stack, Term, Value } from './styles';

export const Comparison = ({ comparison }: { comparison: HistoricalComparison }) => <Stack>
  <Spread><Heading>Prediction vs recorded outcome</Heading><Badge data-state={comparison.decision_match ? 'passed' : 'Needs review'}>{decisionMatch(comparison.decision_match)}</Badge></Spread>
  <Definition><Term>Recorded status</Term><Value>{comparison.recorded_status ?? 'Missing'}</Value><Term>Recorded approved amount</Term><Value>{comparison.recorded_amount_raw ?? 'Missing'}</Value><Term>Ceiling difference</Term><Value>{amountDifference(comparison.difference_dollars)}</Value><Term>Absolute amount error</Term><Value>{comparison.absolute_error_percent === null ? 'Unscored' : `${comparison.absolute_error_percent.toFixed(1)}%`}</Value></Definition>
  {comparison.reasons.map(reason => <Small key={reason}>{reason}</Small>)}
  <Copy>{comparison.interpretation}</Copy>
</Stack>;

export const RecordedSummary = ({ claim }: { claim: Claim }) => <Definition>
  <Term>Recorded status</Term><Value>{claim.historical_outcome.Status || 'Missing'}</Value>
  <Term>Recorded approved amount</Term><Value>{claim.historical_outcome['Approved Benefit Amount'] || 'Missing'}</Value>
</Definition>;
