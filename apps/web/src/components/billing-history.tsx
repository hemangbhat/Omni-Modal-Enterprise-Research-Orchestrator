"use client";

import { useEffect, useState } from "react";
import { formatMoney, getInvoices, type Invoice } from "@/lib/workspace-api";

/** Past invoices from Stripe; says plainly when billing is not connected. */
export function BillingHistory() {
  const [state, setState] = useState<{ available: boolean; invoices: Invoice[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getInvoices()
      .then(setState)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <section aria-labelledby="billing-history" className="mt-xl flex flex-col gap-sm">
      <h3 id="billing-history" className="text-[19px] font-semibold text-on-surface">Billing history</h3>
      {error ? (
        <p role="alert" className="text-[14px] text-error">{error}</p>
      ) : state === null ? (
        <p className="text-[14px] text-on-surface-variant">Loading invoices…</p>
      ) : !state.available ? (
        <p className="text-[14px] leading-relaxed text-on-surface-variant">
          Invoices appear here once Stripe billing is connected. This workspace uses demo billing, so no payments
          have been taken.
        </p>
      ) : state.invoices.length === 0 ? (
        <p className="text-[14px] text-on-surface-variant">No invoices yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-outline-variant/30">
          <table className="w-full text-left text-[14px]">
            <thead className="text-on-surface-variant">
              <tr className="border-b border-outline-variant/20">
                <th scope="col" className="px-md py-sm font-medium">Date</th>
                <th scope="col" className="px-md py-sm font-medium">Invoice</th>
                <th scope="col" className="px-md py-sm font-medium">Amount</th>
                <th scope="col" className="px-md py-sm font-medium">Status</th>
                <th scope="col" className="px-md py-sm font-medium"><span className="sr-only">Links</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant/15 text-on-surface">
              {state.invoices.map((inv) => (
                <tr key={inv.id}>
                  <td className="px-md py-sm">
                    {inv.created ? new Date(inv.created * 1000).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" }) : "—"}
                  </td>
                  <td className="px-md py-sm">{inv.number ?? inv.id}</td>
                  <td className="px-md py-sm">{formatMoney(inv.status === "paid" ? inv.amount_paid : inv.amount_due, inv.currency)}</td>
                  <td className="px-md py-sm capitalize">{inv.status ?? "—"}</td>
                  <td className="px-md py-sm text-right">
                    {inv.hosted_invoice_url ? (
                      <a href={inv.hosted_invoice_url} target="_blank" rel="noopener noreferrer" className="text-primary-fixed-dim hover:underline">
                        View
                      </a>
                    ) : null}
                    {inv.invoice_pdf ? (
                      <a href={inv.invoice_pdf} target="_blank" rel="noopener noreferrer" className="ml-md text-primary-fixed-dim hover:underline">
                        PDF
                      </a>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
