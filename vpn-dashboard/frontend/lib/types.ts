/** Response types mirroring docs/CONTRACT.md — keep in sync with the backend. */

// ---- overview ----
export interface SparkPoint {
  date: string;
  value: number;
}
export interface OverviewSummary {
  online_users: number | null;
  remnawave_available: boolean;
  active_subs: number;
  revenue_today: number;
  revenue_7d: number;
  revenue_30d: number;
  registrations_today: number;
  sparklines: {
    revenue: SparkPoint[];
    registrations: SparkPoint[];
    online: SparkPoint[];
    active_subs: SparkPoint[];
  };
}
export interface ProviderStatus {
  source: string;
  last_payment_at: string | null;
  payments_24h: number;
  payments_7d: number;
  median_gap_hours: number | null;
  silence_hours: number | null;
  threshold_hours: number;
  status: "ok" | "warning" | "down";
  /** data source: provider webhooks (payments_flat) or balance credits */
  via?: "webhook" | "balance";
  commission_30d?: number | null;
  failed_24h?: number | null;
}
export interface ProvidersStatusResponse {
  providers: ProviderStatus[];
}

// ---- live events (WS) ----
export interface LiveEvent {
  id: number;
  ts: string;
  event:
    | "topup"
    | "registration"
    | "promo"
    | "ref_income"
    | "purchase"
    | "segment"
    | "alert";
  replay?: boolean;
  user_id?: number | null;
  username?: string | null;
  amount?: number;
  source?: string | null;
  bonus?: number;
  promo_code?: string | null;
  kind?: string;
  product?: string | null;
  segment?: string;
  severity?: string;
  title?: string;
}

// ---- revenue ----
export interface RevenueSeriesRow {
  bucket: string;
  source: string;
  revenue: number;
  count: number;
}
export interface RevenueTimeseries {
  series: RevenueSeriesRow[];
  sources: string[];
}
export interface RevenueKpis {
  mrr: number;
  device_mrr: number;
  arpu: number;
  avg_check: number;
  median_check: number;
  paying_users: number;
  payments_count: number;
  revenue: number;
  prev_revenue: number;
  revenue_change_pct: number | null;
}
export interface LtvCohort {
  cohort: string;
  users: number;
  revenue: number;
  ltv: number;
}
export interface RevenueByTypeRow {
  kind: string;
  label: string;
  amount: number;
  count: number;
}
export interface BonusShare {
  series: { bucket: string; net: number; bonus: number; promo: number }[];
  totals: { net: number; bonus: number; promo: number; share_pct: number };
}
export interface RevenueCompare {
  month: { current: number; previous: number; change_pct: number | null };
  week: { current: number; previous: number; change_pct: number | null };
  wow: { week: string; revenue: number }[];
}

// ---- users ----
export interface SegmentRow {
  segment: string;
  count: number;
}
export interface SegmentsResponse {
  segments: SegmentRow[];
  total: number;
}
export interface SegmentFlows {
  nodes: string[];
  links: { source: string; target: string; value: number }[];
}
export interface RetentionCohort {
  cohort: string;
  size: number;
  retention: number[];
}
export interface FunnelStep {
  step: string;
  users: number;
  conversion_pct: number;
  median_hours_from_prev: number | null;
}
export interface ChurnResponse {
  monthly: {
    month: string;
    churned: number;
    active_start: number;
    churn_rate_pct: number;
  }[];
  at_risk: {
    user_id: number;
    username: string | null;
    segment: string | null;
    days_to_expire: number | null;
    balance: number;
    renewals_count: number;
  }[];
  at_risk_count: number;
}
export interface UserSearchRow {
  user_id: number;
  username: string | null;
  segment: string | null;
  joined_at: string | null;
  topup_total: number;
  last_tx_at: string | null;
}
export interface UserCardTx {
  dt: string | null;
  direction: "credit" | "debit";
  kind: string;
  amount: number;
  source: string | null;
  bonus: number;
  promo_code: string | null;
  desc: string | null;
}
export interface UserCard {
  user: Record<string, unknown> & {
    _id: number;
    username: string | null;
    segment: string | null;
    joined_at: string | null;
    balance: number;
    topup_total: number;
    spend_total: number;
    renewals_count: number;
    extra_devices_active: number;
    preferred_client: string | null;
    days_to_expire: number | null;
    segment_history: { segment: string; dt: string }[];
  };
  transactions: UserCardTx[];
  referrals: {
    user_id: number;
    username: string | null;
    topup_total: number;
    joined_at: string | null;
  }[];
  recent_logs: { dt: string | null; text: string }[];
}

// ---- referrals ----
export interface ReferrerRow {
  user_id: number;
  username: string | null;
  turnover_total: number;
  earned_total: number;
  referrals: number;
  paying_referrals: number;
  conversion_pct: number;
  payout_pending: number;
}
export interface ReferralsSummary {
  total_referrers: number;
  total_referrals: number;
  total_paying: number;
  conversion_pct: number;
  payout_pending_total: number;
  earned_total: number;
  turnover_total: number;
}
export interface PayoutsResponse {
  pending_total: number;
  queue: { user_id: number; username: string | null; payout_pending: number }[];
  history: Record<string, unknown>[];
}
export interface RefTimeseriesRow {
  bucket: string;
  amount: number;
  count: number;
}

// ---- promos ----
export interface PromoRow {
  code: string;
  activations: number;
  unique_users: number;
  amount_total: number;
  repeat_activations: number;
  max_by_one_user: number;
  abuse_suspects: number;
  last_used_at: string | null;
}
export interface AbuseCase {
  user_id: number;
  username: string | null;
  code: string;
  count: number;
  amount_total: number;
  first_at: string | null;
  last_at: string | null;
}
export interface CampaignRow {
  campaign: string;
  users: number;
  converted: number;
  conversion_pct: number;
  converted_amount: number;
}
export interface BroadcastImpact {
  available: boolean;
  broadcasts: {
    name: string;
    at: string;
    topups_after: number;
    revenue_after: number;
    topups_before: number;
    revenue_before: number;
    uplift_pct: number | null;
  }[];
}

// ---- product ----
export interface DevicesResponse {
  active_slots: number;
  users_with_devices: number;
  device_mrr: number;
  churned_slots: number;
  revenue_total: number;
  timeseries: { bucket: string; amount: number; count: number }[];
}
export interface BypassResponse {
  purchases: number;
  revenue: number;
  unique_buyers: number;
  repeat_buyers: number;
  repeat_rate_pct: number;
  avg_days_between: number | null;
  top_consumers: {
    user_id: number;
    username: string | null;
    purchases: number;
    amount: number;
  }[];
  timeseries: { bucket: string; amount: number; count: number }[];
}
export interface ClientsResponse {
  clients: { client: string; count: number }[];
}

// ---- infra ----
export interface NodeInfo {
  uuid: string | null;
  name: string;
  country: string | null;
  address: string | null;
  is_online: boolean;
  users_online: number;
  traffic_used_bytes: number;
  traffic_limit_bytes: number;
  cpu_percent: number | null;
  mem_percent: number | null;
}
export interface NodesResponse {
  available: boolean;
  nodes: NodeInfo[];
  online_total: number;
  realtime:
    | {
        node_uuid: string | null;
        node_name: string | null;
        download_bytes: number;
        upload_bytes: number;
        download_speed_bps: number;
        upload_speed_bps: number;
      }[]
    | null;
}
export interface HeatmapResponse {
  cells: { dow: number; hour: number; count: number }[];
  computed_at: string | null;
}
export interface PeakHoursResponse {
  hours: { hour: number; count: number }[];
}

// ---- pace ----
export interface PaceFactor {
  key: string;
  label: string;
  unit: string;
  today: number;
  expected: number;
  delta_pct: number | null;
  gap_rub?: number;
}
export interface PaceResponse {
  now_hour: number;
  baseline_days: number;
  today_so_far: number;
  expected_so_far: number;
  expected_full_day: number;
  projected_today: number | null;
  deviation_pct: number | null;
  status: "behind" | "on_track" | "ahead" | "no_data";
  series: { hour: number; today: number | null; expected: number }[];
  factors: PaceFactor[];
  verdict: string;
}

// ---- renewal outlook ----
export interface ExpiringUser {
  user_id: number;
  username: string | null;
  segment: string | null;
  balance: number;
  personal_cost: number;
  needed: number;
  expected_topup: number;
  sub_until: string | null;
}
export interface RenewalOutlook {
  min_topup: number;
  monthly_sub_cost: number;
  churn_reasons: { reason: string; count: number }[];
  today: {
    expiring: number;
    can_renew_from_balance: number;
    need_topup: number;
    potential_topup_rub: number;
    potential_monthly_rub: number;
    users: ExpiringUser[];
  };
  history: {
    day: string;
    churned: number;
    returned: number;
    lost_monthly_rub: number;
  }[];
}

// ---- experiments ----
export interface AbGroup {
  group: string;
  users: number;
  paying: number;
  conversion_pct: number;
  arpu: number;
  avg_ltv_paying: number;
  renewal_share_pct: number;
  vs_control: {
    z: number;
    p_value: number;
    significant: boolean;
    conversion_diff_pp: number;
  } | null;
}
export interface AbResponse {
  experiment: string;
  control: string | null;
  groups: AbGroup[];
  untagged_users: number;
}
export interface Opportunity {
  key: string;
  title: string;
  description: string;
  users: number;
  potential_rub: number;
  assumption: string;
}
export interface OpportunitiesResponse {
  /** median per-user renewal spend over 30 days (billing-model agnostic) */
  monthly_sub_cost: number;
  median_check: number;
  opportunities: Opportunity[];
  computed_at: string | null;
}

// ---- registration economics ----
export interface RegHorizon {
  users: number;
  value_per_reg: number;
  paying_share_pct: number;
  value_per_paying: number;
}
export interface RegEconomics {
  horizons: { d7: RegHorizon; d30: RegHorizon; d90: RegHorizon };
  trend: {
    cohort: string;
    users: number;
    value_per_reg_30d: number;
    paying_share_pct: number;
    complete: boolean;
  }[];
  regs_per_day_14d: number;
  current_monthly_value: number;
  churned_30d: number;
  churn_lost_monthly_rub: number;
  regs_per_day_to_offset_churn: number | null;
  monthly_sub_cost: number;
}

// ---- registration sources ----
export interface RegSourceRow {
  source: string;
  regs_30d: number;
  share_pct: number;
  last7: number;
  prev7: number;
  trend_pct: number | null;
  value_per_reg_30d: number | null;
  paying_share_pct: number | null;
  quality_users: number;
}
export interface RegSources {
  window_days: number;
  total_regs_30d: number;
  keys: string[];
  series: Record<string, unknown>[];
  sources: RegSourceRow[];
  recommendations: { priority: string; text: string }[];
}

// ---- alerts ----
export interface AlertRow {
  key: string;
  severity: "critical" | "warning" | "info";
  title: string;
  details: Record<string, unknown>;
  created_at: string;
}
export interface AlertsStatus {
  telegram_configured: boolean;
  checks: string[];
  cooldown_hours: number;
}
