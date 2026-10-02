import React, { useEffect, useState } from 'react';
import { Plus, Play, ShoppingBag, LineChart, TrendingUp, ShieldCheck } from 'lucide-react';

export default function Dashboard({ setActiveTab, setSelectedStrategyId }) {
  const [strategies, setStrategies] = useState([]);

  useEffect(() => {
    fetch('/api/strategies')
      .then((res) => res.json())
      .then((data) => setStrategies(data))
      .catch(() => {
        setStrategies([
          {
            id: 1,
            name: 'Dual Group Momentum Alpha',
            allocated_capital: 2000000,
            cash_balance: 2000000,
            age_days: 57,
          }
        ]);
      });
  }, []);

  return (
    <div className="space-y-6">
      {/* Top Portfolio Stats */}
      <div className="bg-slate-800/50 p-6 rounded-2xl border border-slate-700/80 shadow-lg">
        <h2 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-4">Portfolio Overview</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-slate-900/80 p-4 rounded-xl border border-slate-800">
            <div className="text-xs text-slate-400 font-medium">Allocated Capital</div>
            <div className="text-2xl font-bold text-white mt-1 font-mono">₹ 20,00,000.00</div>
          </div>
          <div className="bg-slate-900/80 p-4 rounded-xl border border-slate-800">
            <div className="text-xs text-slate-400 font-medium">Realized P&L</div>
            <div className="text-2xl font-bold text-emerald-400 mt-1 font-mono">+₹ 2,350.91 (+2.52%)</div>
          </div>
          <div className="bg-slate-900/80 p-4 rounded-xl border border-slate-800">
            <div className="text-xs text-slate-400 font-medium">Total Returns</div>
            <div className="text-2xl font-bold text-emerald-400 mt-1 font-mono">+₹ 3,610.56 (+3.87%)</div>
          </div>
        </div>
      </div>

      {/* Strategies List Header */}
      <div className="flex justify-between items-center">
        <h2 className="text-lg font-bold text-white">Active Investing Strategies</h2>
        <button
          onClick={() => setActiveTab('strategy-builder')}
          className="bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold px-4 py-2 rounded-xl transition shadow-md flex items-center space-x-1.5"
        >
          <Plus className="w-4 h-4" />
          <span>New Strategy</span>
        </button>
      </div>

      {/* Strategy Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {strategies.map((strat) => (
          <div
            key={strat.id}
            className="bg-slate-800/50 rounded-2xl border border-slate-700/80 p-5 space-y-4 hover:border-blue-500/50 transition"
          >
            <div className="flex justify-between items-start border-b border-slate-700/60 pb-3">
              <div>
                <span className="text-[11px] font-semibold text-slate-400">Age: {strat.age_days || 57} Days</span>
                <h3 className="text-lg font-bold text-white mt-0.5">{strat.name}</h3>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs bg-slate-900/80 p-3.5 rounded-xl border border-slate-800 font-mono">
              <div>
                <span className="text-slate-400">Allocated:</span>
                <span className="font-bold text-white ml-1">₹ {strat.allocated_capital?.toLocaleString()}</span>
              </div>
              <div>
                <span className="text-slate-400">Current P&L:</span>
                <span className="font-bold text-emerald-400 ml-1">+3.86%</span>
              </div>
            </div>

            <div className="flex items-center space-x-2 pt-1">
              <button
                onClick={() => {
                  setSelectedStrategyId(strat.id);
                  setActiveTab('scanner');
                }}
                className="flex-1 bg-blue-600/20 border border-blue-500/40 text-blue-300 hover:bg-blue-600/30 text-xs font-bold py-2.5 rounded-xl transition flex justify-center items-center space-x-1.5"
              >
                <ShoppingBag className="w-3.5 h-3.5" />
                <span>Rebalance Basket</span>
              </button>
              <button
                onClick={() => {
                  setSelectedStrategyId(strat.id);
                  setActiveTab('positions');
                }}
                className="flex-1 bg-slate-900 border border-slate-700 text-slate-300 hover:bg-slate-800 text-xs font-bold py-2.5 rounded-xl transition flex justify-center items-center space-x-1.5"
              >
                <LineChart className="w-3.5 h-3.5" />
                <span>Positions</span>
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}