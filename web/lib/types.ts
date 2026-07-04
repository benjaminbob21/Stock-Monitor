export interface Driver {
  feature: string;
  value: number;
  shap: number;
  direction: string;
}

export interface ScoreResponse {
  ticker: string;
  as_of: string;
  conviction: number;
  recommendation: string;
  calibrated: boolean;
  model_version: string;
  fundamentals_known_on: string | null;
  drivers: Driver[];
  risk_flags: string[];
  disclaimer: string;
}

export interface ApiError {
  detail: string;
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
}

export interface OpportunitiesResponse {
  scanned_at: string | null;
  opportunities: Opportunity[];
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
  conviction_change?: number;
  since_sold_pct?: number | null;
  sold_at?: string | null;
  sold_price?: number | null;
  signal: string;
  expert_view: string;
}

export interface PositionsResponse {
  positions: PositionView[];
}
