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
