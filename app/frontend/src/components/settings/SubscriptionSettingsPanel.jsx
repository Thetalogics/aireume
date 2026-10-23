import {
  AlertTriangle, BarChart3, Check, CreditCard, Loader2, RefreshCw, Sparkles, Zap,
} from 'lucide-react'
import { sanitizePlanFeatures } from '../../lib/uxLabels'
import { formatPlanPrice, isSalesLedPlan, SALES_CONTACT_EMAIL } from '../../lib/planDisplay'
import { Section, UsageCard } from './SettingsPrimitives'

export default function SubscriptionSettingsPanel({
  loading,
  error,
  onRetry,
  currentPlan,
  usageStats,
  remainingAnalyses,
  planFeatures,
  availablePlans,
  user,
  actionLoading,
  onResetUsage,
  onChangePlan,
}) {
  return (
    <>
              {loading ? (
                <div className="flex justify-center py-16">
                  <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
                </div>
              ) : error ? (
                <div className="p-6 bg-red-50 rounded-2xl ring-1 ring-red-200 text-center">
                  <AlertTriangle className="w-8 h-8 text-red-500 mx-auto mb-2" />
                  <p className="text-red-700">{error}</p>
                  <button
                    onClick={onRetry}
                    className="mt-4 px-4 py-2 bg-white text-red-600 text-sm font-semibold rounded-xl hover:bg-red-50 transition-colors"
                  >
                    Retry
                  </button>
                </div>
              ) : (
                <>
                  {/* Current Plan */}
                  <Section
                    title="Current Plan"
                    icon={Zap}
                    description={currentPlan?.plan?.description || `You're on the ${currentPlan?.plan?.display_name || 'Free'} plan.`}
                  >
                    <div className="flex items-center justify-between p-4 bg-gradient-to-br from-brand-50 to-brand-100/50 rounded-2xl ring-1 ring-brand-200 mb-6">
                      <div>
                        <div className="flex items-center gap-2">
                          <h4 className="font-extrabold text-brand-900 text-xl">{currentPlan?.plan?.display_name}</h4>
                          <span className="px-2 py-0.5 bg-green-100 text-green-700 text-xs font-bold rounded-full ring-1 ring-green-200">
                            {currentPlan?.status === 'active' ? 'Active' : currentPlan?.status}
                          </span>
                        </div>
                        <p className="text-sm text-slate-600 mt-1">
                          {currentPlan?.price > 0
                            ? `$${(currentPlan.price / 100).toFixed(0)}/${currentPlan?.billing_cycle === 'monthly' ? 'mo' : 'yr'}`
                            : 'Free plan'
                          }
                        </p>
                      </div>
                      <div className="text-right">
                        {usageStats?.daysUntilReset !== undefined && (
                          <p className="text-xs text-slate-500">
                            Resets in <span className="font-medium text-slate-700">{usageStats.daysUntilReset} days</span>
                          </p>
                        )}
                        {currentPlan?.current_period_end && (
                          <p className="text-xs text-slate-500 mt-1">
                            Period ends: <span className="font-medium text-slate-700">{new Date(currentPlan.current_period_end).toLocaleDateString()}</span>
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Usage Stats */}
                    <h5 className="font-bold text-slate-800 text-sm mb-3 flex items-center gap-2">
                      <BarChart3 className="w-4 h-4 text-brand-600" />
                      Usage This Month
                    </h5>
                    <div className="grid sm:grid-cols-2 gap-3">
                      <UsageCard
                        label="Resume Analyses"
                        used={usageStats?.analysesUsed || 0}
                        limit={usageStats?.analysesLimit || 20}
                      />
                      <UsageCard
                        label="Storage Used"
                        used={usageStats?.storageUsedMB || 0}
                        limit={(usageStats?.storageLimitGB || 1) * 1024}
                        unit="MB"
                      />
                      <UsageCard
                        label="Team Members"
                        used={usageStats?.teamMembers || 1}
                        limit={usageStats?.teamMembersLimit || 1}
                      />
                      <UsageCard
                        label="Remaining Analyses"
                        used={remainingAnalyses === Infinity ? 0 : (usageStats?.analysesLimit || 20) - (usageStats?.analysesUsed || 0)}
                        limit={remainingAnalyses === Infinity ? -1 : usageStats?.analysesLimit || 20}
                      />
                    </div>

                    {/* Features */}
                    <h5 className="font-bold text-slate-800 text-sm mt-6 mb-3 flex items-center gap-2">
                      <Check className="w-4 h-4 text-brand-600" />
                      Plan Features
                    </h5>
                    <div className="grid sm:grid-cols-2 gap-2">
                      {planFeatures.map((feature, i) => (
                        <div key={i} className="flex items-center gap-2 text-sm text-slate-700">
                          <div className="w-5 h-5 rounded-full bg-brand-50 flex items-center justify-center shrink-0">
                            <Check className="w-3 h-3 text-brand-600" />
                          </div>
                          {feature}
                        </div>
                      ))}
                    </div>

                    {/* Admin Controls */}
                    {import.meta.env.DEV && user?.role === 'admin' && (
                      <>
                        <h5 className="font-bold text-slate-800 text-sm mt-6 mb-3 flex items-center gap-2">
                          <Sparkles className="w-4 h-4 text-brand-600" />
                          Admin Testing Controls
                        </h5>
                        <div className="flex gap-2 flex-wrap">
                          <button
                            onClick={onResetUsage}
                            disabled={actionLoading === 'resetUsage'}
                            className="flex items-center gap-2 px-3 py-2 bg-amber-50 text-amber-700 text-xs font-semibold rounded-xl hover:bg-amber-100 transition-colors disabled:opacity-50"
                          >
                            <RefreshCw className={`w-3 h-3 ${actionLoading === 'resetUsage' ? 'animate-spin' : ''}`} />
                            {actionLoading === 'resetUsage' ? 'Resetting...' : 'Reset Usage'}
                          </button>
                        </div>
                      </>
                    )}
                  </Section>

                  {/* Available Plans */}
                  <Section
                    title="Available Plans"
                    icon={CreditCard}
                    description="Upgrade or change your plan at any time"
                  >
                    <div className="grid sm:grid-cols-3 gap-4">
                      {availablePlans.map((plan, index) => {
                        const isCurrent = currentPlan?.plan?.id === plan.id
                        const isPopular = plan.name === 'growth' || plan.name === 'pro'
                        return (
                          <div
                            key={plan.id}
                            className={`relative p-5 rounded-2xl ring-1 transition-all ${
                              isCurrent
                                ? 'bg-brand-50 ring-brand-300'
                                : isPopular
                                  ? 'bg-white ring-brand-200 shadow-brand'
                                  : 'bg-white ring-brand-100'
                            } ${isCurrent ? '' : 'hover:shadow-brand-lg'}`}
                          >
                            {isPopular && !isCurrent && (
                              <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-brand-600 text-white text-xs font-bold rounded-full shadow-brand-sm">
                                Most Popular
                              </div>
                            )}
                            {isCurrent && (
                              <div className="absolute top-3 right-3 w-6 h-6 rounded-full bg-brand-600 text-white flex items-center justify-center">
                                <Check className="w-4 h-4" />
                              </div>
                            )}
                            <h4 className="font-extrabold text-brand-900 text-lg">{plan.display_name}</h4>
                            <p className={`mt-1 font-bold text-brand-900 ${isSalesLedPlan(plan) ? 'text-lg' : 'text-2xl'}`}>
                              {formatPlanPrice(plan)}
                            </p>
                            <p className="text-xs text-slate-500 mt-1">{plan.description}</p>
                            <ul className="mt-4 space-y-2">
                              {sanitizePlanFeatures(plan.features).slice(0, 5).map((feature, i) => (
                                <li key={i} className="flex items-center gap-2 text-xs text-slate-700">
                                  <div className="w-4 h-4 rounded-full bg-brand-50 flex items-center justify-center shrink-0">
                                    <Check className="w-2.5 h-2.5 text-brand-600" />
                                  </div>
                                  {feature}
                                </li>
                              ))}
                            </ul>
                            {user?.role === 'admin' ? (
                              isSalesLedPlan(plan) ? (
                                <a
                                  href={`mailto:${SALES_CONTACT_EMAIL}?subject=ARIA Enterprise Plan Inquiry`}
                                  className={`w-full mt-4 py-2.5 rounded-xl text-sm font-bold transition-all block text-center ${
                                    isCurrent
                                      ? 'bg-brand-200 text-brand-700 cursor-default pointer-events-none'
                                      : 'btn-brand text-white shadow-brand-sm'
                                  }`}
                                >
                                  {isCurrent ? 'Current Plan' : 'Contact Sales'}
                                </a>
                              ) : (
                                <button
                                  onClick={() => onChangePlan(plan)}
                                  disabled={isCurrent || actionLoading?.startsWith('changePlan')}
                                  className={`w-full mt-4 py-2.5 rounded-xl text-sm font-bold transition-all ${
                                    isCurrent
                                      ? 'bg-brand-200 text-brand-700 cursor-default'
                                      : 'btn-brand text-white shadow-brand-sm disabled:opacity-50'
                                  }`}
                                >
                                  {isCurrent ? 'Current Plan' : actionLoading === `changePlan-${plan.id}` ? 'Changing...' : actionLoading?.startsWith('changePlan') ? 'Please wait...' : 'Switch Plan'}
                                </button>
                              )
                            ) : (
                              <button
                                disabled={isCurrent}
                                className={`w-full mt-4 py-2.5 rounded-xl text-sm font-bold transition-all ${
                                  isCurrent
                                    ? 'bg-brand-200 text-brand-700 cursor-default'
                                    : isSalesLedPlan(plan)
                                      ? 'btn-brand text-white shadow-brand-sm'
                                      : 'bg-slate-100 text-slate-400 cursor-not-allowed'
                                }`}
                                onClick={isSalesLedPlan(plan) && !isCurrent
                                  ? () => { window.location.href = `mailto:${SALES_CONTACT_EMAIL}?subject=ARIA Enterprise Plan Inquiry` }
                                  : undefined}
                              >
                                {isCurrent ? 'Current Plan' : isSalesLedPlan(plan) ? 'Contact Sales' : 'Contact Admin'}
                              </button>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </Section>
                </>
              )}

    </>
  )
}
