export type Confidence = "high" | "medium" | "low" | "insufficient";

export interface Metrics {
  subscriber_count: number | null;
  subscribers_hidden: boolean;
  median_views: number | null;
  engagement_rate: number | null;
  view_per_sub: number | null;
  consistency: number | null;
  momentum?: number | null;
  sponsor_ratio: number | null;
  sponsored_videos?: number;
  declared_paid_videos?: number;
  last_sponsored_days?: number | null;
  activity: number;
  paid_placement_hits: number;
  videos_surfaced: number;
  eligible_video_count: number;
  format?: "long" | "shorts";
  last_upload_days: number | null;
  channel_age_days: number | null;
  likes_visible: boolean;
}

export interface BreakdownTerm {
  term: string;
  group?: "relevance" | "quality";
  value: number;
  weight: number;
  contribution: number;
}

export interface Cultural {
  cultural_relevance?: number;
  fame_tier?: "household_name" | "scene_famous" | "niche_known" | "unknown";
  persona?: string;
  audience_generation?: "gen_z" | "millennial" | "mixed" | "older" | "unclear";
  sustained_or_spike?: "sustained" | "rising" | "spike" | "fading" | "unknown";
  notable_context?: string;
  brand_fit_note?: string;
  evidence_found?: boolean;
}

export interface AgentView {
  relevance?: number | null;
  relevance_reason?: string;
  match_type?: "direct" | "adjacent" | "lifestyle" | "weak";
  audience_fit?: number | null;
  inferred_viewer_profile?: string;
  purchase_intent_signal?: "high" | "medium" | "low";
  safety_flag?: boolean;
  safety_severity?: "low" | "medium" | "high";
  safety_category?: string;
  safety_reason?: string;
  sponsor_confidence?: number | null;
  observed_format?: string;
  cadence?: string;
  sponsor_evidence?: string;
}

export type FitLevel = "match" | "neutral" | "clash";

export interface AudienceField {
  similarity: number;
  adjusted: number;
}

export interface RelevanceDetail {
  audience_fields?: Record<string, AudienceField>;
  audience_level?: "strong" | "good" | "partial" | "weak" | null;
  audience_evidence?: string[];
  product_fit?: "natural" | "plausible" | "forced" | "none" | null;
  product_moments?: string[];
  brand_fit?: { tone?: FitLevel; price_tier?: FitLevel; values?: FitLevel };
  brand_evidence?: string;
}

export interface Video {
  title: string;
  video_id: string;
  views: string | number | null;
  url: string;
  paid_placement: boolean;
}

export interface Creator {
  channel_id: string;
  found_via?: string[];
  below_floor?: boolean;
  title: string;
  description?: string;
  country?: string | null;
  url: string;
  fit_score: number;
  confidence: Confidence;
  partial: boolean;
  trending: boolean;
  trending_days?: number;
  popularity_bonus?: number;
  archetype?: string;
  relevance_score?: number | null;
  relevance_gate?: number;
  relevance_detail?: RelevanceDetail;
  cultural_bonus?: number;
  cultural_reason?: string;
  cultural?: Cultural;
  metrics: Metrics;
  breakdown: BreakdownTerm[];
  dropped_terms: string[];
  top_videos: Video[];
  agent: AgentView;
  headline?: string;
  rationale?: string;
  caveat?: string | null;
  rationale_status?: "verified" | "summary";
  unsupported_claims?: string[];
  /** Supporting numbers, rounded for people, each with what it means. */
  key_numbers?: KeyNumber[];
  /** top: clear recommendation · limited_data: strong but few videos ·
   *  broader: a wider, popular pick whose viewers overlap the buyer. */
  tier?: "top" | "limited_data" | "broader";
}

export interface KeyNumber {
  label: string;
  value: string;
  note?: string | null;
}

export interface Intent {
  brand?: string;
  product?: string;
  target_audience?: string;
  target_generation?: string;
  cultural_angle?: string;
  geo_raw?: string;
  geo_granularity?: "country" | "sub_country" | "none";
  region_code?: string | null;
  ambiguities?: string[];
}

export interface Review {
  verdict?: "ship" | "ship_with_caveat" | "weak";
  headline?: string;
  what_we_found?: string;
  how_to_use_this?: string;
  gaps?: string[];
  suggested_refinements?: string[];
  flagged_channel_ids?: string[];
  diversity_note?: string;
}

export interface Market {
  brand_known?: boolean;
  brand_profile?: string;
  brand_positioning?: string;
  known_competitors?: string[];
  category_landscape?: string;
  audience_watch_habits?: string[];
  confidence?: "high" | "medium" | "low";
}

export interface Archetype {
  name: string;
  why_audience_matches?: string;
  uses_product_words?: boolean;
}

export interface Persona {
  brand_profile?: {
    tone?: string;
    price_tier?: string;
    values?: string[];
    avoid?: string[];
  };
  product_moments?: string[];
  archetypes?: Archetype[];
}

export interface Results {
  intent: Intent;
  ranked: Creator[];
  review_manually: Creator[];
  excluded: { channel_id: string; title: string; exclusion: string }[];
  cultural_sources: string[];
  market?: Market;
  market_sources?: string[];
  persona?: Persona;
  review?: Review;
  held_back?: { held_back: number; note: string };
  floor_note?: { below_floor?: boolean; note?: string };
  agent_failures: string[];
  notices: string[];
  demo: boolean;
}

export interface Progress {
  node: string;
  label: string;
  done: number;
  total: number;
  widening?: boolean;
}
