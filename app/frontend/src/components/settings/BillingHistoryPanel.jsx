import {
  AlertTriangle, ArrowLeft, ArrowRight, Calendar, FileText, Loader2,
} from 'lucide-react'
import { StatusBadge, InvoiceRow, InvoiceCard, InvoiceDetailModal } from './InvoiceHistory'
import { formatCurrency, formatDate } from './invoiceFormatters'
import { Section } from './SettingsPrimitives'

export default function BillingHistoryPanel({
  currentPlan,
  invoicesLoading,
  invoicesError,
  invoices,
  invoicesTotal,
  invoicesPage,
  invoicesPerPage,
  fetchInvoices,
  expandedInvoice,
  setExpandedInvoice,
  invoiceDetail,
  fetchInvoiceDetail,
  selectedInvoice,
  setSelectedInvoice,
  setInvoiceDetail,
  invoiceDetailLoading,
}) {
  return (
    <>
              {/* Upcoming Billing */}
              <Section
                title="Upcoming Billing"
                icon={Calendar}
                description="Your next scheduled payment"
              >
                {currentPlan?.price > 0 ? (
                  <div className="p-4 bg-gradient-to-br from-brand-50 to-brand-100/50 rounded-2xl ring-1 ring-brand-200">
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium text-slate-600">Next billing date</p>
                        <p className="font-bold text-brand-900 text-lg mt-0.5">
                          {currentPlan?.current_period_end
                            ? new Date(currentPlan.current_period_end).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
                            : 'N/A'}
                        </p>
                      </div>
                      <div className="sm:text-right">
                        <p className="text-sm font-medium text-slate-600">Amount</p>
                        <p className="font-bold text-brand-900 text-lg mt-0.5">
                          ${((currentPlan?.price || 0) / 100).toFixed(2)}
                          <span className="text-sm font-medium text-slate-500">/{currentPlan?.billing_cycle === 'monthly' ? 'mo' : 'yr'}</span>
                        </p>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="p-4 bg-slate-50 rounded-2xl ring-1 ring-slate-200 text-center">
                    <p className="text-slate-600 text-sm">You're on the free plan. No upcoming charges.</p>
                  </div>
                )}
              </Section>

              {/* Invoice List */}
              <Section
                title="Invoice History"
                icon={FileText}
                description="View and download your past invoices"
              >
                {invoicesLoading ? (
                  <div className="flex justify-center py-12">
                    <Loader2 className="w-6 h-6 animate-spin text-brand-600" />
                  </div>
                ) : invoicesError ? (
                  <div className="p-4 bg-red-50 rounded-2xl ring-1 ring-red-200 text-center">
                    <AlertTriangle className="w-6 h-6 text-red-500 mx-auto mb-2" />
                    <p className="text-red-700 text-sm">{invoicesError}</p>
                    <button
                      onClick={() => fetchInvoices(0)}
                      className="mt-3 px-3 py-1.5 bg-white text-red-600 text-xs font-semibold rounded-xl hover:bg-red-50 transition-colors"
                    >
                      Retry
                    </button>
                  </div>
                ) : invoices.length === 0 ? (
                  <div className="py-12 text-center">
                    <FileText className="w-10 h-10 text-slate-300 mx-auto mb-3" />
                    <p className="text-slate-500 font-medium">No invoices yet</p>
                    <p className="text-slate-400 text-sm mt-1">Invoices will appear here once you make a payment</p>
                  </div>
                ) : (
                  <>
                    {/* Desktop Table */}
                    <div className="hidden sm:block overflow-x-auto">
                      <table className="w-full">
                        <thead>
                          <tr className="border-b border-brand-100">
                            <th className="text-left py-3 px-3 text-xs font-bold text-slate-500 uppercase tracking-wider">Invoice #</th>
                            <th className="text-left py-3 px-3 text-xs font-bold text-slate-500 uppercase tracking-wider">Date</th>
                            <th className="text-left py-3 px-3 text-xs font-bold text-slate-500 uppercase tracking-wider">Description</th>
                            <th className="text-right py-3 px-3 text-xs font-bold text-slate-500 uppercase tracking-wider">Amount</th>
                            <th className="text-center py-3 px-3 text-xs font-bold text-slate-500 uppercase tracking-wider">Status</th>
                            <th className="text-right py-3 px-3 text-xs font-bold text-slate-500 uppercase tracking-wider">Actions</th>
                          </tr>
                        </thead>
                        <tbody>
                          {invoices.map((inv) => (
                            <InvoiceRow
                              key={inv.id}
                              invoice={inv}
                              isExpanded={expandedInvoice === inv.id}
                              onToggle={() => {
                                if (expandedInvoice === inv.id) {
                                  setExpandedInvoice(null)
                                } else {
                                  setExpandedInvoice(inv.id)
                                  if (!invoiceDetail || invoiceDetail?.id !== inv.id) {
                                    fetchInvoiceDetail(inv.id)
                                  }
                                }
                              }}
                              onViewDetail={() => {
                                setSelectedInvoice(inv)
                                fetchInvoiceDetail(inv.id)
                              }}
                            />
                          ))}
                        </tbody>
                      </table>
                    </div>

                    {/* Mobile Cards */}
                    <div className="sm:hidden space-y-3">
                      {invoices.map((inv) => (
                        <InvoiceCard
                          key={inv.id}
                          invoice={inv}
                          onViewDetail={() => {
                            setSelectedInvoice(inv)
                            fetchInvoiceDetail(inv.id)
                          }}
                        />
                      ))}
                    </div>

                    {/* Pagination */}
                    {invoicesTotal > invoicesPerPage && (
                      <div className="flex items-center justify-between mt-6 pt-4 border-t border-brand-100">
                        <p className="text-xs text-slate-500">
                          Showing {invoicesPage * invoicesPerPage + 1}–{Math.min((invoicesPage + 1) * invoicesPerPage, invoicesTotal)} of {invoicesTotal}
                        </p>
                        <div className="flex gap-2">
                          <button
                            onClick={() => fetchInvoices(invoicesPage - 1)}
                            disabled={invoicesPage === 0}
                            className="flex items-center gap-1 px-3 py-1.5 bg-brand-50 text-brand-700 text-xs font-semibold rounded-xl hover:bg-brand-100 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            <ArrowLeft className="w-3 h-3" />
                            Prev
                          </button>
                          <button
                            onClick={() => fetchInvoices(invoicesPage + 1)}
                            disabled={(invoicesPage + 1) * invoicesPerPage >= invoicesTotal}
                            className="flex items-center gap-1 px-3 py-1.5 bg-brand-50 text-brand-700 text-xs font-semibold rounded-xl hover:bg-brand-100 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            Next
                            <ArrowRight className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </Section>

              {/* Expanded Invoice Detail (inline) */}
              {expandedInvoice && invoiceDetail && invoiceDetail.id === expandedInvoice && (
                <Section
                  title={`Invoice ${invoiceDetail.invoice_number}`}
                  icon={FileText}
                  description="Invoice details"
                >
                  <div className="space-y-4">
                    <div className="grid sm:grid-cols-2 gap-4">
                      <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Status</p>
                        <StatusBadge status={invoiceDetail.status} className="mt-1" />
                      </div>
                      <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Amount</p>
                        <p className="font-bold text-brand-900 mt-1">{formatCurrency(invoiceDetail.amount, invoiceDetail.currency)}</p>
                      </div>
                      <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Period</p>
                        <p className="text-sm text-slate-700 mt-1">
                          {formatDate(invoiceDetail.period_start)} — {formatDate(invoiceDetail.period_end)}
                        </p>
                      </div>
                      <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Payment Provider</p>
                        <p className="text-sm text-slate-700 mt-1 capitalize">{invoiceDetail.payment_provider || 'N/A'}</p>
                      </div>
                      {invoiceDetail.issued_at && (
                        <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                          <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Issued</p>
                          <p className="text-sm text-slate-700 mt-1">{formatDate(invoiceDetail.issued_at)}</p>
                        </div>
                      )}
                      {invoiceDetail.paid_at && (
                        <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                          <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Paid</p>
                          <p className="text-sm text-slate-700 mt-1">{formatDate(invoiceDetail.paid_at)}</p>
                        </div>
                      )}
                    </div>

                    {/* Line Items */}
                    {invoiceDetail.line_items && invoiceDetail.line_items.length > 0 && (
                      <div>
                        <h5 className="font-bold text-slate-800 text-sm mb-2">Line Items</h5>
                        <div className="space-y-2">
                          {invoiceDetail.line_items.map((item, i) => (
                            <div key={i} className="flex justify-between items-center p-3 bg-slate-50 rounded-xl ring-1 ring-slate-200">
                              <div>
                                <p className="text-sm font-medium text-slate-700">{item.description}</p>
                                {item.quantity > 1 && <p className="text-xs text-slate-400">Qty: {item.quantity}</p>}
                              </div>
                              <p className="font-semibold text-slate-900">{formatCurrency(item.amount, invoiceDetail.currency)}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="flex justify-end mt-4">
                    <button
                      onClick={() => setExpandedInvoice(null)}
                      className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-800 transition-colors"
                    >
                      Close Details
                    </button>
                  </div>
                </Section>
              )}

              {/* Invoice Detail Modal */}
              {selectedInvoice && (
                <InvoiceDetailModal
                  invoice={invoiceDetail || selectedInvoice}
                  loading={invoiceDetailLoading}
                  onClose={() => {
                    setSelectedInvoice(null)
                    setInvoiceDetail(null)
                  }}
                />
              )}

    </>
  )
}
