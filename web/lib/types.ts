export interface Driver {
  feature: string;
  value: number;
  shap: number;
  direction: string;
}

export interface ScoreResponse {
  ticker: string;
  name?: string | null;
  as_of: string;
  conviction: number;
  recommendation: string;
  calibrated: boolean;
  model_version: string;
  fundamentals_known_on: string | null;
  drivers: Driver[];
  risk_flags: string[];
  disclaimer: string;
  price?: number;
  price_is_live?: boolean;
  last_close?: number;
  conviction_3m?: number | null;
  recommendation_3m?: string | null;
  near_term_note?: string | null;
  days_to_earnings?: number | null;
}

export interface AnalystOpinion {
  opinion: "BUY" | "HOLD" | "SELL";
  confidence: "low" | "medium" | "high" | "unknown";
  rationale: string;
  key_risks: string[];
  agrees_with_model: boolean;
  model: string;
  disclaimer: string;
}

export interface AnalystResponse {
  ticker: string;
  opinion: AnalystOpinion | null;
  note: string | null;
}

export interface ExplainResponse {
  ticker: string;
  summary: string | null;
  note?: string | null;
}

export interface ApiError {
  detail: string;
}

export interface SymbolMatch {
  ticker: string;
  name: string;
}

export interface SearchResponse {
  query: string;
  results: SymbolMatch[];
}

export interface ScanStatus {
  status?: "started" | "already_running";
  running: boolean;
  last_started: string | null;
  last_finished: string | null;
  last_count: number | null;
  last_error: string | null;
  progress?: { done: number; total: number } | null;
}

export interface NewsStatus {
  status?: "started" | "already_running";
  running: boolean;
  last_started: string | null;
  last_finished: string | null;
  last_archived: number | null;
  last_error: string | null;
  progress?: { done: number; total: number } | null;
  last_news_date?: string | null;
  days_since?: number | null;
}

export interface Opportunity {
  rank: number;
  ticker: string;
  conviction: number;
  capped_conviction: number;
  recommendation: string;
  as_of: string | null;
  risk_flags: string[];
  model_version: string | null;
  /** "scan" (nightly universe) or "on_demand" (scored via a manual lookup). */
  source?: "scan" | "on_demand";
}

export interface OpportunitiesResponse {
  scanned_at: string | null;
  opportunities: Opportunity[];
  on_demand_count?: number;
  note: string | null;
}

export interface Recommendation extends Opportunity {
  rationale: string;
}

export interface RecommendationsResponse {
  scanned_at: string | null;
  recommendations: Recommendation[];
  note: string | null;
}

export interface PositionView {
  id: string;
  ticker: string;
  status: "open" | "sold";
  added_at: string | null;
  entry_price: number;
  entry_conviction: number;
  entry_recommendation: string;
  current_price?: number;
  current_conviction?: number;
  current_recommendation?: string;
  current_flags?: string[];
  price_change_pct?: number | null;
  price_is_live?: boolean;
  conviction_change?: number;
  since_sold_pct?: number | null;
  sold_at?: string | null;
  sold_price?: number | null;
  signal: string;
  expert_view: string;
  sentiment_score?: number | null;
  sentiment_label?: string | null;
}

export type SkewQuadrant = "Contrarian Bid" | "Chase" | "Hedged Rally" | "Fear";

export interface SkewRecordView {
  snapshot_date: string;
  ticker: string;
  sector: string;
  spot: number;
  ret_1m: number;
  rel_ret_spy: number;
  rvol: number;
  expiration: string;
  dte_days: number;
  atm_iv: number;
  call_25d_iv: number;
  put_25d_iv: number;
  raw_skew: number;
  normalized_skew: number;
  quadrant: SkewQuadrant;
  earnings_date: string | null;
  is_earnings_near: boolean;
  sanity_passed: boolean;
  sanity_warning: string | null;
  sector_avg_raw_skew: number;
  sector_avg_norm_skew: number;
  sector_agreement: number;
  verdict: string;
  ret_1d: number;
  ret_1w: number;
  thin_chain: boolean;
}

export interface SkewSectorSummary {
  snapshot_date: string;
  sector: string;
  ticker_count: number;
  avg_raw_skew: number;
  avg_norm_skew: number;
  avg_ret_1m: number;
  agreement: number;
  dominant_lean: string;
}

export interface SkewLatestResponse {
  date: string | null;
  records: SkewRecordView[];
  counts: Record<SkewQuadrant, number>;
  sectors: SkewSectorSummary[];
}

export interface SkewChangeView {
  ticker: string;
  sector: string;
  spot: number;
  ret_1m: number;
  current_norm_skew: number;
  prev_norm_skew: number;
  skew_change_norm: number;
  current_raw_skew: number;
  prev_raw_skew: number;
  skew_change_raw: number;
  current_quadrant: SkewQuadrant;
  prev_quadrant: SkewQuadrant;
  quadrant_changed: boolean;
  sector_agreement: number;
  is_earnings_near: boolean;
  verdict: string;
}

export interface SkewChangesResponse {
  changes: SkewChangeView[];
}


export interface PositionsResponse {
  positions: PositionView[];
}

export interface BasketLeg {
  id: string;
  ticker: string;
  pct: number;
  budget: number;
  shares: number;
  entry_price: number;
  status: "open" | "sold";
  current_price?: number | null;
  leg_return_pct?: number | null;
  current_value?: number;
  pnl?: number;
  contribution_points?: number | null;
}

export interface BriefAllocation {
  ticker: string;
  target_pct: number;
  current_pct: number;
  delta_pct: number;
  conviction: number;
  reasons: string[];
}

export interface BriefContext {
  as_of: string;
  total_value: number;
  cash_pct: number;
  allocations: BriefAllocation[];
  warnings: string[];
  unpriceable_positions?: string[];
}

export interface BriefResponse {
  as_of: string;
  context: BriefContext;
  brief: string | null;
  model: string | null;
  llm_available: boolean;
  note: string | null;
  cached: boolean;
}

export interface ReviewOpinion {
  ticker: string;
  opinion: "BUY" | "HOLD" | "SELL";
  confidence: string;
  rationale: string;
  key_risks: string[];
  model: string;
  disclaimer: string;
  cached?: boolean;
}

export interface ReviewResponse {
  ticker: string;
  opinion: ReviewOpinion | null;
  note: string | null;
}

export interface DcfFlow {
  year: number;
  fcf: number;
  pv: number;
}

export interface DcfInputs {
  base_fcf: number | null;
  fcf_years: string | null;
  growth_pct: number | null;
  growth_source: string | null;
  wacc_pct: number | null;
  terminal_growth_pct: number | null;
  shares: number | null;
  shares_known_on: string | null;
  net_debt: number | null;
  bridge: string | null;
  cash_known_on: string | null;
  price: number | null;
  fundamentals_age_days: number | null;
}

export interface DcfResponse {
  ticker: string;
  price: number | null;
  as_of: string;
  value: number | null;
  upside_pct: number | null;
  confidence: "good" | "rough" | "none";
  reasons: string[];
  inputs: DcfInputs;
  pv_explicit?: number;
  pv_terminal?: number;
  terminal_weight?: number | null;
  flows?: DcfFlow[];
  verdict?: "undervalued" | "overvalued" | "fairly valued" | null;
}

export interface BasketView {
  id: string;
  name: string;
  created_at: string;
  total_budget: number;
  status: "open" | "closed";
  closed_at?: string | null;
  current_value?: number;
  pnl?: number;
  return_pct?: number | null;
  benchmark_return_pct?: number | null;
  excess_vs_spy_pct?: number | null;
  complete?: boolean;
  legs: BasketLeg[];
}

export interface BasketsResponse {
  baskets: BasketView[];
}

export interface NewsItem {
  headline: string;
  url: string;
  source: string;
  published: string | null;
  sentiment: number | null;
}

export interface NewsResponse {
  ticker: string;
  score: number;
  label: string;
  count: number;
  backend: string;
  items: NewsItem[];
}

export type SignalStatus = "pass" | "fail" | "pending";

export interface ScorecardBacktest {
  status: SignalStatus;
  message: string;
  excess_return?: number | null;
  hit_rate?: number | null;
  strategy_total_return?: number | null;
  benchmark_total_return?: number | null;
  n_periods?: number | null;
  universe_size?: number | null;
  created_at?: string | null;
}

export interface ScorecardPaper {
  status: SignalStatus;
  message: string;
  closed: number;
  open: number;
  hit_rate: number | null;
  avg_excess_return: number | null;
  progress: number;
}

export interface Scorecard {
  verdict: "confirmed" | "no_edge" | "building";
  verdict_label: string;
  message: string;
  thresholds: { min_closed_picks: number; min_hit_rate: number };
  backtest: ScorecardBacktest;
  paper: ScorecardPaper;
  note?: string;
}
