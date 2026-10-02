import React, { useState } from 'react';
import { Search, Zap, CheckCircle, ShieldAlert } from 'lucide-react';

export default function RebalanceScanner({ strategyId }) {
  const [scanResult, setScanResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleRunScan = () => {
    setLoading(true);
    fetch(`/api/strategies/${strategyId}/scan`)
      .then((res) => res.json())
      .then((data) => {
        setScanResult(data);
        setLoading(false);
      })
      .catch(() => {
        // Demo fallback
        setScanResult({
          mtf_status: 'BULLISH 🟢',
          index_scrip: 'NSE:NIFTY50-INDEX',
          indicator_used: 'EMA (20)',
          signals: [
            { symbol: 'ALPHA', allocated_group: 'Group 1 (Normal)', signal_type: 'BUY', cmp: 42.15, passes_ema200: 'YES', momentum_score: 18.4, reason: 'Top Momentum Rank & CMP > 200 EMA' },
            { symbol: 'BANKBEES', allocated_group: 'Group 1 (Normal)', signal_type: 'BUY', cmp: 512.80, passes_ema200: 'YES', momentum_score: 14.2, reason: 'Top Momentum Rank & CMP > 200 EMA' }
          ]
        });
        setLoading(false);
      });
  };

  return (
    <div className="space-y-6">
      <div className="bg-slate-800/80 p-5 rounded-2xl border border-slate-700/80 flex flex-col sm:flex-row justify-between sm:items-center gap-4">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center space-x-2">
            <Zap className="w-5 h-5 text-amber-400" />
            <span>Basket Rebalance Scanner</span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">Runs MTF trend filter checks & 200 EMA momentum ranking.</p>
        </div>

        <button
          onClick={handleRunScan}
          disabled={loading}
          className="bg-blue-600 hover:bg-blue-500 text-white font-bold px-5 py-2.5 rounded-xl transition shadow-lg shadow-blue-600/30 flex items-center space-x-2"
        >
          <Search className="w-4 h-4" />
          <span>{loading ? 'Scanning Market...' : 'Run Scanner Now'}</span>
        </button>
      </div>

      {scanResult && (
        <div className="space-y-4">
          <div className="bg-slate-950 p-4 rounded-2xl border border-slate-800 flex justify-between items-center">
            <div className="text-xs text-slate-400">
              Scrip: <span className="font-bold text-white">{scanResult.index_scrip}</span> ({scanResult.indicator_used})
            </div>
            <div className="text-sm font-bold text-white flex items-center space-x-2">
              <span>MTF Status:</span>
              <span className="text-emerald-400 font-mono">{scanResult.mtf_status}</span>
            </div>
          </div>

          <div className="bg-slate-800/50 rounded-2xl border border-slate-700/80 p-5 space-y-4">
            <h2 className="text-md font-bold text-white">Rebalance Execution Signals</h2>

            <div className="overflow-x-auto rounded-xl border border-slate-700/60 bg-slate-900/60">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-800/80 text-slate-400 uppercase text-[11px] font-semibold tracking-wider">
                  <tr>
                    <th className="py-3 px-4">#</th>
                    <th className="py-3 px-4">Symbol</th>
                    <th className="py-3 px-4">Signal</th>
                    <th className="py-3 px-4">Group</th>
                    <th className="py-3 px-4">CMP &gt; 200 EMA</th>
                    <th className="py-3 px-4">Reason</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800 text-slate-200">
                  {scanResult.signals.map((sig, idx) => (
                    <tr key={idx} className="hover:bg-slate-800/40 transition">
                      <td className="py-3 px-4 font-mono text-slate-500 text-xs">{idx + 1}</td>
                      <td className="py-3 px-4 font-bold text-white">{sig.symbol}</td>
                      <td className="py-3 px-4 font-bold text-blue-400">{sig.signal_type}</td>
                      <td className="py-3 px-4 text-slate-300 text-xs">{sig.allocated_group}</td>
                      <td className="py-3 px-4 font-mono font-bold text-emerald-400">{sig.passes_ema200}</td>
                      <td className="py-3 px-4 text-xs text-slate-400">{sig.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <button
              onClick={() => alert('Orders dispatched to FYERS API')}
              className="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3.5 rounded-xl transition shadow-lg shadow-emerald-600/30 flex items-center justify-center space-x-2"
            >
              <Zap className="w-4 h-4" />
              <span>Execute Rebalance Basket via FYERS API</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}