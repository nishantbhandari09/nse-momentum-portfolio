import React, { useState } from 'react';
import { Table, Download, TrendingUp, Check, DollarSign } from 'lucide-react';

export default function PositionsTracker() {
  const [positions, setPositions] = useState([
    { id: 1, symbol: 'GILT5YBEES', buyQty: 239, buyPrice: 65.39, entryDate: '2026-06-10', cmp: 65.78, pnl: 93.21, pnlPct: 0.60, status: 'OPEN' },
    { id: 2, symbol: 'LOWVOLIETF', buyQty: 1072, buyPrice: 14.50, entryDate: '2026-06-10', cmp: 14.50, pnl: 0.00, pnlPct: 0.00, status: 'OPEN' }
  ]);

  const handleMarkSell = (id) => {
    setPositions(positions.map((p) => (p.id === id ? { ...p, status: 'CLOSED' } : p)));
  };

  return (
    <div className="space-y-6">
      {/* Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-slate-800/50 p-5 rounded-2xl border border-slate-700/80">
          <div className="text-xs font-semibold text-slate-400">Total Holdings Value</div>
          <div className="text-2xl font-bold text-white mt-1 font-mono">₹ 31,233.42</div>
        </div>
        <div className="bg-slate-800/50 p-5 rounded-2xl border border-slate-700/80">
          <div className="text-xs font-semibold text-slate-400">Realized P&L</div>
          <div className="text-2xl font-bold text-emerald-400 mt-1 font-mono">+₹ 2,350.91</div>
        </div>
        <div className="bg-slate-800/50 p-5 rounded-2xl border border-slate-700/80">
          <div className="text-xs font-semibold text-slate-400">Unrealized P&L</div>
          <div className="text-2xl font-bold text-emerald-400 mt-1 font-mono">+₹ 93.21 (+0.30%)</div>
        </div>
      </div>

      {/* Main Table Card */}
      <div className="bg-slate-800/50 rounded-2xl border border-slate-700/80 p-5 space-y-4">
        <div className="flex justify-between items-center border-b border-slate-700/60 pb-3">
          <h2 className="text-lg font-bold text-white flex items-center space-x-2">
            <Table className="w-5 h-5 text-blue-400" />
            <span>Active Positions & Portfolio Tracker</span>
          </h2>

          <button className="flex items-center space-x-1.5 bg-slate-900 border border-slate-700 hover:bg-slate-800 text-slate-300 px-3 py-1.5 rounded-lg text-xs font-semibold transition">
            <Download className="w-4 h-4" />
            <span>Export CSV</span>
          </button>
        </div>

        <div className="overflow-x-auto rounded-xl border border-slate-700/60 bg-slate-900/60">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-800/80 text-slate-400 uppercase text-[11px] font-semibold tracking-wider">
              <tr>
                <th className="py-3 px-4">#</th>
                <th className="py-3 px-4">Symbol</th>
                <th className="py-3 px-4">Buy Qty</th>
                <th className="py-3 px-4">Buy Price</th>
                <th className="py-3 px-4">Entry Date</th>
                <th className="py-3 px-4">CMP</th>
                <th className="py-3 px-4">P&L (₹)</th>
                <th className="py-3 px-4 text-center">Status</th>
                <th className="py-3 px-4 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 text-slate-200">
              {positions.map((pos, idx) => (
                <tr key={pos.id} className="hover:bg-slate-800/40 transition">
                  <td className="py-3 px-4 font-mono text-slate-500 text-xs">{idx + 1}</td>
                  <td className="py-3 px-4 font-bold text-white tracking-wide">{pos.symbol}</td>
                  <td className="py-3 px-4 font-mono">{pos.buyQty}</td>
                  <td className="py-3 px-4 font-mono">₹ {pos.buyPrice.toFixed(2)}</td>
                  <td className="py-3 px-4 text-slate-400 text-xs">{pos.entryDate}</td>
                  <td className="py-3 px-4 font-mono font-semibold">₹ {pos.cmp.toFixed(2)}</td>
                  <td className={`py-3 px-4 font-mono font-bold ${pos.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {pos.pnl >= 0 ? '+' : ''}₹ {pos.pnl.toFixed(2)} ({pos.pnlPct.toFixed(2)}%)
                  </td>
                  <td className="py-3 px-4 text-center">
                    <span className={`text-[10px] font-extrabold px-2 py-0.5 rounded-full uppercase ${
                      pos.status === 'OPEN' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-slate-800 text-slate-400'
                    }`}>
                      {pos.status}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-right">
                    {pos.status === 'OPEN' ? (
                      <button
                        onClick={() => handleMarkSell(pos.id)}
                        className="bg-red-950/60 hover:bg-red-900 border border-red-800 text-red-300 font-bold text-xs px-2.5 py-1 rounded-lg transition"
                      >
                        Mark Sell
                      </button>
                    ) : (
                      <span className="text-xs text-slate-500 italic">Closed</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}