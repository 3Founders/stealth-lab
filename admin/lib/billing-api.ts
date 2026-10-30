/**
 * Typed client for the billing endpoints of the admin dashboard (backend/app/api/admin_billing.py). Amounts are whole paise as
 * the backend stores them; `rupees()` is the only place they become rupees, and it never rounds a paisa away.
 */
import { apiGet, apiPost, apiPut, type ApiState } from "@/lib/api";

export type { ApiState };

export interface BillingOverview {
  orders: number; ordered_paise: number; payments: number; paid_paise: number; stale_orders: number; stale_after_minutes: number;
  invoices: number; credit_notes: number; invoiced_paise: number; credit_noted_paise: number; payments_without_invoice: number; mfa_required: boolean;
}
export interface OrderRow { order_id: string; organization_id: string; organization: string | null; amount_paise: number; created_by: string; created_at: string; credited: boolean }
export interface PaymentRow {
  payment_ref: string; order_id: string | null; source: string; organization_id: string; organization: string | null; amount_paise: number;
  amount_usd: string; created_at: string; invoice_id: string | null; invoice_number: string | null;
}
export interface Reconciliation {
  uncredited_orders: Array<Pick<OrderRow, "order_id" | "organization_id" | "organization" | "amount_paise" | "created_at">>;
  payments_without_invoice: Array<Pick<PaymentRow, "payment_ref" | "organization_id" | "organization" | "amount_paise" | "created_at">>;
  stale_after_minutes: number; note: string;
}
export interface SellerProfile {
  legal_name: string; trade_name: string | null; gstin: string | null; state_code: string | null; address: string | null; sac_code: string | null;
  gst_rate_pct: string; invoice_prefix: string; updated_by: string; updated_at: string;
}
export interface SellerState { profile: SellerProfile | null; problems: string[]; e_invoicing?: string }
export interface Invoice {
  id: string; number: string; fy: string; kind: "invoice" | "credit_note"; organization_id: string | null; payment_ref: string | null; credit_for: string | null;
  seller_name: string; seller_gstin: string; seller_state_code: string; seller_address: string;
  buyer_name: string; buyer_gstin: string | null; buyer_state_code: string; buyer_address: string | null; place_of_supply: string;
  description: string; sac_code: string; rate_pct: string; taxable_paise: number; cgst_paise: number; sgst_paise: number; igst_paise: number; total_paise: number;
  issued_by: string; issued_at: string; already_issued?: boolean;
}
export interface BuyerInput { name: string; gstin?: string; state_code?: string; address?: string }

const B = "/v1/admin/billing";
export const getOverview = (s?: AbortSignal) => apiGet<BillingOverview>(`${B}/overview`, s);
export const getOrders = (s?: AbortSignal) => apiGet<{ orders: OrderRow[] }>(`${B}/orders?limit=200`, s);
export const getPayments = (s?: AbortSignal) => apiGet<{ payments: PaymentRow[] }>(`${B}/payments?limit=200`, s);
export const getReconciliation = (s?: AbortSignal) => apiGet<Reconciliation>(`${B}/reconciliation`, s);
export const getSeller = (s?: AbortSignal) => apiGet<SellerState>(`${B}/seller`, s);
export const saveSeller = (body: Partial<SellerProfile>) => apiPut<SellerState>(`${B}/seller`, body);
export const getInvoices = (s?: AbortSignal) => apiGet<{ invoices: Invoice[] }>(`${B}/invoices?limit=200`, s);
export const issueInvoice = (payment_ref: string, buyer: BuyerInput) => apiPost<Invoice>(`${B}/invoices`, { payment_ref, buyer });
export const issueCreditNote = (invoiceId: string) => apiPost<Invoice>(`${B}/invoices/${encodeURIComponent(invoiceId)}/credit-note`, {});

/** Whole paise to a rupee string with two decimals, in Indian digit grouping. Exact: no floating point on the amount. */
export function rupees(paise: number): string {
  const neg = paise < 0;
  const p = Math.abs(Math.trunc(paise));
  const whole = Math.floor(p / 100).toLocaleString("en-IN");
  const frac = String(p % 100).padStart(2, "0");
  return `${neg ? "-" : ""}₹${whole}.${frac}`;
}

/** The Indian state names for the two-digit GST state codes, for the buyer's place of supply. */
export const STATES: Array<[string, string]> = [
  ["01", "Jammu and Kashmir"], ["02", "Himachal Pradesh"], ["03", "Punjab"], ["04", "Chandigarh"], ["05", "Uttarakhand"], ["06", "Haryana"],
  ["07", "Delhi"], ["08", "Rajasthan"], ["09", "Uttar Pradesh"], ["10", "Bihar"], ["11", "Sikkim"], ["12", "Arunachal Pradesh"], ["13", "Nagaland"],
  ["14", "Manipur"], ["15", "Mizoram"], ["16", "Tripura"], ["17", "Meghalaya"], ["18", "Assam"], ["19", "West Bengal"], ["20", "Jharkhand"],
  ["21", "Odisha"], ["22", "Chhattisgarh"], ["23", "Madhya Pradesh"], ["24", "Gujarat"], ["26", "Dadra and Nagar Haveli and Daman and Diu"],
  ["27", "Maharashtra"], ["29", "Karnataka"], ["30", "Goa"], ["31", "Lakshadweep"], ["32", "Kerala"], ["33", "Tamil Nadu"], ["34", "Puducherry"],
  ["35", "Andaman and Nicobar Islands"], ["36", "Telangana"], ["37", "Andhra Pradesh"], ["38", "Ladakh"],
];
export const stateName = (code: string): string => STATES.find(([c]) => c === code)?.[1] ?? code;
