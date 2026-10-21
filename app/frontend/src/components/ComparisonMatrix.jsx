import React, { useState, useEffect } from 'react';
import { compareCandidates } from '../lib/api';
import { CheckCircle2, XCircle, AlertTriangle, Users, ArrowUpDown } from 'lucide-react';

export default function ComparisonMatrix({ candidateIds, jdAnalysis, screeningResultId, teamGaps }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [sortBy, setSortBy] = useState('match_percentage'); // or 'gaps_filled' or 'fit_score'

  useEffect(() => {
    if (candidateIds?.length < 2 || !candidateIds) return;
    let cancelled = false;
    async function fetchComparison() {
      setLoading(true);
      setError(null);
      try {
        const result = await compareCandidates({
          candidate_ids: candidateIds,
          jd_analysis: jdAnalysis || undefined,
          screening_result_id: screeningResultId || undefined,
          team_gaps: teamGaps || [],
        });
        if (!cancelled) setData(result);
      } catch (e) {
        if (!cancelled) setError(e.message || 'Failed to load comparison');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    fetchComparison();
    return () => { cancelled = true; };
  }, [candidateIds, jdAnalysis, screeningResultId, teamGaps]);

  if (loading) return <div className="flex justify-center p-8"><div className="animate-spin h-8 w-8 border-4 border-brand-500 border-t-transparent rounded-full" /></div>;
  if (error) return <div className="p-4 bg-red-50 text-red-700 rounded-lg">{error}</div>;
  if (!data) return null;

  const { skills_matrix, summary, metadata } = data;
  const candidateEntries = Object.entries(summary);

  // Sort candidates
  const sortedCandidates = [...candidateEntries].sort((a, b) => {
    return (b[1][sortBy] || 0) - (a[1][sortBy] || 0);
  });

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
      <div className="p-4 border-b border-gray-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Users className="w-5 h-5 text-brand-600" />
          <h3 className="font-semibold text-gray-900">Candidate Comparison</h3>
          <span className="text-sm text-gray-500">({candidateEntries.length} candidates)</span>
        </div>
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          className="text-sm border border-gray-200 rounded-lg px-3 py-1.5"
        >
          <option value="match_percentage">Sort by Match %</option>
          <option value="fit_score">Sort by Fit Score</option>
          <option value="gaps_filled">Sort by Gaps Filled</option>
        </select>
      </div>

      {/* Summary Cards */}
      <div className="p-4 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 border-b border-gray-100">
        {sortedCandidates.map(([id, s]) => (
          <div key={id} className="p-3 rounded-lg border border-gray-200 bg-gray-50">
            <div className="font-medium text-sm text-gray-900 truncate">{s.name}</div>
            <div className="mt-1 flex items-center gap-2">
              <span className="text-lg font-bold text-brand-600">{s.match_percentage}%</span>
              {s.fit_score && <span className="text-xs text-gray-500">Score: {s.fit_score}</span>}
            </div>
            <div className="mt-1 text-xs text-gray-500">
              {s.required_matched}/{s.required_total} required &bull; {s.gaps_filled} gaps filled
            </div>
          </div>
        ))}
      </div>

      {/* Skills Matrix Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="text-left p-3 font-medium text-gray-700 sticky left-0 bg-gray-50">Skill</th>
              <th className="text-center p-3 font-medium text-gray-700 w-20">Type</th>
              {sortedCandidates.map(([id, s]) => (
                <th key={id} className="text-center p-3 font-medium text-gray-700 min-w-[100px]">
                  {s.name.split(' ')[0]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {skills_matrix.map((row, idx) => (
              <tr key={idx} className={`border-t border-gray-100 ${row.is_team_gap ? 'bg-amber-50' : ''}`}>
                <td className="p-3 sticky left-0 bg-white font-medium text-gray-900">
                  <div className="flex items-center gap-1.5">
                    {row.skill}
                    {row.is_team_gap && <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />}
                  </div>
                </td>
                <td className="text-center p-3">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${row.is_required ? 'bg-red-100 text-red-700' : 'bg-blue-100 text-blue-700'}`}>
                    {row.is_required ? 'Required' : 'Nice'}
                  </span>
                </td>
                {sortedCandidates.map(([id]) => {
                  const cell = row.candidates[id];
                  if (!cell) return <td key={id} className="text-center p-3">&mdash;</td>;

                  return (
                    <td key={id} className="text-center p-3">
                      {cell.matched ? (
                        <div className="flex items-center justify-center gap-1">
                          <CheckCircle2 className={`w-4 h-4 ${cell.confidence >= 0.9 ? 'text-green-500' : cell.confidence >= 0.7 ? 'text-amber-500' : 'text-orange-400'}`} />
                          {cell.confidence < 1.0 && (
                            <span className="text-xs text-gray-400">{Math.round(cell.confidence * 100)}%</span>
                          )}
                        </div>
                      ) : (
                        <XCircle className="w-4 h-4 text-red-400 mx-auto" />
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
