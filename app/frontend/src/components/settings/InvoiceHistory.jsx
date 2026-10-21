import { formatCurrency, formatDate } from './invoiceFormatters'
import { ChevronDown, ChevronUp, ExternalLink, FileText, Loader2, X } from 'lucide-react'
import ModalOverlay from '../motion/ModalOverlay'



const STATUS_STYLES = {
  paid:      'bg-green-100 text-green-700 ring-green-200',
  pending:   'bg-amber-100 text-amber-700 ring-amber-200',
  draft:     'bg-slate-100 text-slate-600 ring-slate-200',
  void:      'bg-slate-100 text-slate-500 ring-slate-200',
  refunded:  'bg-blue-100 text-blue-700 ring-blue-200',
}

function StatusBadge({ status, className = '' }) {
  const style = STATUS_STYLES[status] || STATUS_STYLES.draft
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 text-xs font-bold rounded-full ring-1 ${style} ${className}`}>
      {status?.charAt(0).toUpperCase() + status?.slice(1) || 'Unknown'}
    </span>
  )
}

function InvoiceRow({ invoice, isExpanded, onToggle, onViewDetail }) {
  return (
    <>
      <tr className="border-b border-brand-50 hover:bg-brand-50/30 transition-colors">
        <td className="py-3 px-3 text-sm font-semibold text-brand-900">{invoice.invoice_number}</td>
        <td className="py-3 px-3 text-sm text-slate-600">{formatDate(invoice.issued_at)}</td>
        <td className="py-3 px-3 text-sm text-slate-700 max-w-[200px] truncate">{invoice.description || '—'}</td>
        <td className="py-3 px-3 text-sm font-semibold text-slate-900 text-right">{formatCurrency(invoice.amount, invoice.currency)}</td>
        <td className="py-3 px-3 text-center"><StatusBadge status={invoice.status} /></td>
        <td className="py-3 px-3 text-right">
          <div className="flex items-center justify-end gap-1">
            <button
              onClick={onViewDetail}
              className="p-1.5 text-slate-400 hover:text-brand-600 hover:bg-brand-50 rounded-lg transition-colors"
              title="View details"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={onToggle}
              className="p-1.5 text-slate-400 hover:text-brand-600 hover:bg-brand-50 rounded-lg transition-colors"
              title={isExpanded ? 'Collapse' : 'Expand'}
            >
              {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </button>
          </div>
        </td>
      </tr>
    </>
  )
}

function InvoiceCard({ invoice, onViewDetail }) {
  return (
    <div className="p-4 bg-brand-50/30 rounded-2xl ring-1 ring-brand-100">
      <div className="flex items-start justify-between mb-2">
        <div>
          <p className="font-semibold text-brand-900 text-sm">{invoice.invoice_number}</p>
          <p className="text-xs text-slate-500 mt-0.5">{formatDate(invoice.issued_at)}</p>
        </div>
        <StatusBadge status={invoice.status} />
      </div>
      <p className="text-sm text-slate-700 mb-2">{invoice.description || '—'}</p>
      <div className="flex items-center justify-between">
        <p className="font-bold text-brand-900">{formatCurrency(invoice.amount, invoice.currency)}</p>
        <button
          onClick={onViewDetail}
          className="flex items-center gap-1 px-3 py-1.5 bg-white text-brand-700 text-xs font-semibold rounded-xl hover:bg-brand-50 transition-colors ring-1 ring-brand-200"
        >
          <ExternalLink className="w-3 h-3" />
          Details
        </button>
      </div>
    </div>
  )
}

function InvoiceDetailModal({ invoice, loading, onClose }) {
  return (
    <ModalOverlay isOpen onClose={onClose}>
      <div
        className="bg-white dark:bg-dark-card rounded-3xl ring-1 ring-brand-100 shadow-brand-lg max-w-lg w-full max-h-[80vh] overflow-y-auto"
      >
        <div className="p-6">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-2xl bg-brand-50 ring-1 ring-brand-100 flex items-center justify-center">
                <FileText className="w-5 h-5 text-brand-600" />
              </div>
              <div>
                <h3 className="font-extrabold text-brand-900 text-lg">{invoice.invoice_number || 'Invoice'}</h3>
                <p className="text-xs text-slate-500">Invoice Details</p>
              </div>
            </div>
            <button
              onClick={onClose}
              aria-label="Close dialog"
              className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-xl transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {loading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="w-6 h-6 animate-spin text-brand-600" />
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                  <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Status</p>
                  <div className="mt-1"><StatusBadge status={invoice.status} /></div>
                </div>
                <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                  <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Amount</p>
                  <p className="font-bold text-brand-900 mt-1">{formatCurrency(invoice.amount, invoice.currency)}</p>
                </div>
                <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                  <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Period</p>
                  <p className="text-sm text-slate-700 mt-1">
                    {formatDate(invoice.period_start)} — {formatDate(invoice.period_end)}
                  </p>
                </div>
                <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                  <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Provider</p>
                  <p className="text-sm text-slate-700 mt-1 capitalize">{invoice.payment_provider || 'N/A'}</p>
                </div>
                {invoice.issued_at && (
                  <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                    <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Issued</p>
                    <p className="text-sm text-slate-700 mt-1">{formatDate(invoice.issued_at)}</p>
                  </div>
                )}
                {invoice.paid_at && (
                  <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                    <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Paid</p>
                    <p className="text-sm text-slate-700 mt-1">{formatDate(invoice.paid_at)}</p>
                  </div>
                )}
              </div>

              {invoice.description && (
                <div className="p-3 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                  <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Description</p>
                  <p className="text-sm text-slate-700 mt-1">{invoice.description}</p>
                </div>
              )}

              {invoice.line_items && invoice.line_items.length > 0 && (
                <div>
                  <h5 className="font-bold text-slate-800 text-sm mb-2">Line Items</h5>
                  <div className="space-y-2">
                    {invoice.line_items.map((item, i) => (
                      <div key={i} className="flex justify-between items-center p-3 bg-slate-50 rounded-xl ring-1 ring-slate-200">
                        <div>
                          <p className="text-sm font-medium text-slate-700">{item.description}</p>
                          {item.quantity > 1 && <p className="text-xs text-slate-400">Qty: {item.quantity}</p>}
                        </div>
                        <p className="font-semibold text-slate-900">{formatCurrency(item.amount, invoice.currency)}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </ModalOverlay>
  )
}

export { StatusBadge, InvoiceRow, InvoiceCard, InvoiceDetailModal }
