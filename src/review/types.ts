export type Claim = {
  tracking_number: string;
  claims_source: { id?: string; name?: string; version?: string; read_at?: string };
  prediction_inputs: Record<string, { recorded_value: string; source_column: string; availability: string }>;
  historical_outcome: Record<string, string>;
  evidence: { state: string; folder: DriveItem | null; items: DriveItem[] };
};
export type DriveItem = { id: string; name: string; mimeType?: string; webViewLink?: string };
export type LookupResult = { tracking_number: string } & (
  { state: 'found'; claim: Claim } | { state: 'error'; error: { code: string; message: string } }
);
export type Batch = {
  batch_id: string; state: 'queued' | 'running' | 'completed' | 'failed';
  total: number; completed: number; succeeded: number; failed: number; error: string | null;
  results: { claim_id: string; state: 'queued' | 'running' | 'succeeded' | 'failed'; error: string | null }[];
};
export type Citation = {
  document_id?: string; sha256?: string; page?: number; quote: string;
  bbox?: number[]; method?: string; source_id?: string; row?: number; column?: string;
};
export type Fact = { state: string; reason?: string; observations: {
  raw_value: string; value: string | null; limitations: string[]; citations: Citation[];
}[] };
export type ReviewRow = { row_id: string; status: string; amount: string | null; reasons: string[]; citations: Citation[] };
export type Report = {
  claim_id: string; created_at: string; parser_version: string; limitations: string[];
  effective_monthly_rent: Fact; requested_insurer_reimbursement: Fact;
  documents: { document: { document_id: string; kind: string; sha256: string | null; errors: string[]; pages: { number: number; width: number; height: number; method: string; state: string }[] };
    fields: Record<string, Fact>; limitations: string[] }[];
  validation: { documents: { document_id: string; rows: ReviewRow[];
    checks: { check: string; status: string; reason?: string }[]; issues: { code: string; field?: string; reason?: string }[] }[] };
};
export type Recommendation = {
  historical_comparison: HistoricalComparison;
  claim_id: string; research_decision: string; conditional_ceiling: number | null;
  ceiling_condition: string; ceiling_state: string; ceiling_bound_state: string;
  review_reasons: string[]; withholding_reasons: string[]; model_sha256: string;
  benchmarks: { majority_decision: string; training_median_ceiling: number; recorded_limit: number | null; minimum_request_limit: number | null };
  inputs: { column: string; value: string | null; source_reference: string; availability: string }[];
};
export type DecisionMetrics = { accuracy: number | null; balanced_accuracy: number | null; decline_recall: number | null; confusion_matrix: number[][] };
export type Evaluation = {
  created_at: string; scope: string; method: string;
  decision: { research_decision: DecisionMetrics; majority_decision: DecisionMetrics };
  ceiling_on_same_approved_advisory_subset: Record<string, { count: number; metrics: { mae_dollars: number; within_tolerance_rate: number } | null }>;
  coverage: { development_rows: number; qualified_outcomes: number; conditional_estimates: number; scoreable_approved_advisories: number; withheld: number };
  independent_correctness: { status: string; reviewed_claim_count: number; required: string };
};

export type HistoricalComparison = {
  recorded_status: string | null;
  recorded_decision: string | null;
  recorded_amount_raw: string | null;
  recorded_ceiling: number | null;
  decision_match: boolean | null;
  difference_dollars: number | null;
  absolute_error_percent: number | null;
  reasons: string[];
  interpretation: string;
};
export type RecommendationState =
  | { state: 'loading' }
  | { state: 'error'; error: string }
  | { state: 'succeeded'; data: Recommendation };
export type RecommendationResponse = { results: ({ tracking_number: string } & (
  { state: 'succeeded'; recommendation: Recommendation } |
  { state: 'error'; error: { code: string; message: string } }
))[] };
