import React, { useState, useEffect } from 'react';
import { Sliders, Save, Info, Plus, Settings, ShieldCheck, CheckCircle2 } from 'lucide-react';

export default function StrategyBuilder({ setActiveTab }) {
  const [groups, setGroups] = useState([]);
  const [showMtfModal, setShowMtfModal] = useState(false);

  // Form State
  const [strategyName, setStrategyName] = useState('');
  const [group1Id, setGroup1Id] = useState(1);
  const [group2Id, setGroup2Id] = useState(2);
  const [defensiveMultiplier, setDefensiveMultiplier] = useState(1.5);
  const [allocatedCapital, setAllocatedCapital] = useState(2000000);
  const [maxHoldings, setMaxHoldings] = useState(5);
  const [lookbackPeriod, setLookbackPeriod] = useState(252);
  const [stopLossPct, setStopLossPct] = useState(20.0);

  // MTF State
  const [mtfEnabled, setMtfEnabled] = useState(true);
  const [mtfChartType, setMtfChartType] = useState('OHLC');
  const [mtfFilterAction, setMtfFilterAction] = useState('Exit as per Strategy. No New entry.');
  const [mtfMode, setMtfMode] = useState('Index Filter');
  const [mtfScrip, setMtfScrip] = useState('NSE:NIFTY50-INDEX');
  const [mtfIndicator, setMtfIndicator] = useState('EMA'); // 'EMA' or 'VSTOP'
  const [mtfIndicatorPeriod, setMtfIndicatorPeriod] = useState(20);
  const [mtfVstopMultiplier, setMtfVstopMultiplier] = useState(3.0);

  // Stock EMA 200 Filter
  const [stockEmaFilter, setStockEmaFilter] = useState(true);

  useEffect(() => {
    fetch('/api/groups')
      .then((res) => res.json())
      .then((data) => {
        setGroups(data);
        if (data.length > 0) {
          setGroup1Id(data[0].id);
          if (data.length > 1) setGroup2Id(data[1].id);
        }
      });
  }, []);

  const handleSaveStrategy = () => {
    if (!strategyName.trim()) return alert('Please specify a Strategy Name');

    const payload = {
      name: strategyName.trim(),
      group1_id: parseInt(group1Id),
      group2_id: parseInt(group2Id),
      defensive_multiplier: parseFloat(defensiveMultiplier),
      mtf_enabled: mtfEnabled,
      mtf_chart_type: mtfChartType,
      mtf_filter_action: mtfFilterAction,
      mtf_mode: mtfMode,
      mtf_scrip: mtfScrip,
      mtf_indicator: mtfIndicator,
      mtf_indicator_period: parseInt(mtfIndicatorPeriod),
      mtf_vstop_multiplier: parseFloat(mtfVstopMultiplier),
      stock_ema_filter: stockEmaFilter,
      lookback_period: parseInt(lookbackPeriod),
      max_holdings: parseInt(maxHoldings),
      stop_loss_pct: parseFloat(stopLossPct),
      target_pct: 0.0,
      rebalance_frequency: 'Monthly',
      allocated_capital: parseFloat(allocatedCapital),
    };

    fetch('/api/strategies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then((res) => res.json())
      .then(() => {
        alert('Strategy successfully saved!');
        setActiveTab('dashboard');
      });
  };

  return (
    <div className="space-y-6">
      {/* Top Banner */}
      <div className="bg-slate-800/80 p-5 rounded-2xl border border-slate-700/80 flex flex-col md:flex-row justify-between md:items-center gap-4">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center space-x-2">
            <Sliders className="w-5 h-5 text-blue-400" />
            <span>Strategy Parameter Engine</span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">Configure dual universe allocation, Market Trend Filters (MTF), and stock-level EMA rules.</p>
        </div>

        <button
          onClick={() => setShowMtfModal(true)}
          className="bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 px-4 py-2 rounded-xl text-xs font-bold flex items-center space-x-2 transition"
        >
          <Settings className="w-4 h-4 text-amber-400" />
          <span>Configure Market Trend Filter (MTF)</span>
        </button>
      </div>

      {/* Main Form Box */}
      <div className="bg-slate-800/50 p-6 rounded-2xl border border-slate-700/80 space-y-6">
        {/* Strategy Name & Capital */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="md:col-span-2">
            <label className="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Strategy Name</label>
            <input
              type="text"
              placeholder="e.g. Dual Group Momentum Alpha"
              value={strategyName}
              onChange={(e) => setStrategyName(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 text-white px-4 py-2.5 rounded-xl text-sm focus:outline-none focus:border-blue-500"
            />
          </div>

          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Allocated Capital (₹)</label>
            <input
              type="number"
              value={allocatedCapital}
              onChange={(e) => setAllocatedCapital(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 text-white px-4 py-2.5 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono"
            />
          </div>
        </div>

        {/* Dual Universe Setup */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
          <div className="bg-slate-900/60 p-4 rounded-xl border border-slate-800 space-y-2">
            <label className="block text-xs font-bold text-blue-400 uppercase tracking-wider">Group 1 (Normal Momentum Universe)</label>
            <select
              value={group1Id}
              onChange={(e) => setGroup1Id(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg p-2.5 text-sm focus:outline-none"
            >
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name} ({g.group_type})
                </option>
              ))}
            </select>
            <p className="text-[11px] text-slate-500">Selected during Bullish MTF market regimes.</p>
          </div>

          <div className="bg-slate-900/60 p-4 rounded-xl border border-slate-800 space-y-2">
            <label className="block text-xs font-bold text-amber-400 uppercase tracking-wider">Group 2 (Defensive / Cash Proxy)</label>
            <div className="flex space-x-2">
              <select
                value={group2Id}
                onChange={(e) => setGroup2Id(e.target.value)}
                className="flex-1 bg-slate-800 border border-slate-700 text-white rounded-lg p-2.5 text-sm focus:outline-none"
              >
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name} ({g.group_type})
                  </option>
                ))}
              </select>
              <select
                value={defensiveMultiplier}
                onChange={(e) => setDefensiveMultiplier(e.target.value)}
                className="bg-slate-800 border border-slate-700 text-amber-300 font-bold rounded-lg p-2.5 text-sm focus:outline-none"
              >
                <option value={1.0}>1.0x</option>
                <option value={1.5}>1.5x</option>
                <option value={2.0}>2.0x</option>
              </select>
            </div>
            <p className="text-[11px] text-slate-500">Allocated during Bearish MTF shifts with selected defensive multiplier.</p>
          </div>
        </div>

        {/* Quant Parameters Row */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Max Holdings</label>
            <input
              type="number"
              value={maxHoldings}
              onChange={(e) => setMaxHoldings(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 text-white px-3 py-2 rounded-lg text-sm font-mono"
            />
          </div>

          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Lookback Period (Days)</label>
            <input
              type="number"
              value={lookbackPeriod}
              onChange={(e) => setLookbackPeriod(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 text-white px-3 py-2 rounded-lg text-sm font-mono"
            />
          </div>

          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Trailing Stop Loss (%)</label>
            <input
              type="number"
              value={stopLossPct}
              onChange={(e) => setStopLossPct(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 text-white px-3 py-2 rounded-lg text-sm font-mono"
            />
          </div>
        </div>

        {/* Stock 200 EMA Checkbox */}
        <div className="bg-slate-900/80 p-4 rounded-xl border border-slate-800 flex items-center justify-between">
          <div>
            <div className="text-sm font-bold text-white flex items-center space-x-2">
              <ShieldCheck className="w-4 h-4 text-emerald-400" />
              <span>Stock-Level Trend Filter (200 EMA)</span>
            </div>
            <div className="text-xs text-slate-400 mt-0.5">
              Strictly exclude any stock from Group 1 whose Current Price is below its 200 Exponential Moving Average.
            </div>
          </div>
          <input
            type="checkbox"
            checked={stockEmaFilter}
            onChange={(e) => setStockEmaFilter(e.target.checked)}
            className="w-5 h-5 accent-blue-600 rounded cursor-pointer"
          />
        </div>

        {/* Save Button */}
        <button
          onClick={handleSaveStrategy}
          className="w-full bg-blue-600 hover:bg-blue-500 text-white font-bold py-3.5 rounded-xl flex items-center justify-center space-x-2 transition shadow-lg shadow-blue-600/30"
        >
          <Save className="w-5 h-5" />
          <span>Save Strategy Configuration</span>
        </button>
      </div>

      {/* MTF Configuration Modal */}
      {showMtfModal && (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex justify-center items-center p-4 z-50">
          <div className="bg-slate-900 rounded-2xl max-w-lg w-full border border-slate-700 shadow-2xl p-6 space-y-5">
            <div className="flex justify-between items-center border-b border-slate-800 pb-3">
              <h2 className="text-lg font-bold text-white flex items-center space-x-2">
                <Settings className="w-5 h-5 text-amber-400" />
                <span>Market Trend Filter (MTF) Settings</span>
              </h2>
              <button onClick={() => setShowMtfModal(false)} className="text-slate-500 hover:text-white font-bold text-lg">✕</button>
            </div>

            {/* Enable MTF Toggle */}
            <div className="flex items-center justify-between bg-slate-800/60 p-3 rounded-xl border border-slate-700">
              <span className="text-sm font-bold text-slate-200">Enable Market Trend Filter</span>
              <input
                type="checkbox"
                checked={mtfEnabled}
                onChange={(e) => setMtfEnabled(e.target.checked)}
                className="w-5 h-5 accent-amber-500 cursor-pointer"
              />
            </div>

            {/* Modal Controls */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-semibold text-slate-400">Chart Type</label>
                <select
                  value={mtfChartType}
                  onChange={(e) => setMtfChartType(e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 text-white p-2 rounded-lg text-sm mt-1 focus:outline-none"
                >
                  <option value="OHLC">OHLC</option>
                  <option value="HeikinAshi">Heikin Ashi</option>
                </select>
              </div>

              <div>
                <label className="text-xs font-semibold text-slate-400">Filter Action</label>
                <select
                  value={mtfFilterAction}
                  onChange={(e) => setMtfFilterAction(e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 text-white p-2 rounded-lg text-sm mt-1 focus:outline-none"
                >
                  <option value="Exit as per Strategy. No New entry.">Exit as per Strategy. No New entry.</option>
                </select>
              </div>
            </div>

            <div>
              <label className="text-xs font-semibold text-slate-400">Index Scrip Ticker</label>
              <input
                type="text"
                value={mtfScrip}
                onChange={(e) => setMtfScrip(e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 text-white p-2 rounded-lg text-sm mt-1 font-mono focus:outline-none"
              />
            </div>

            {/* Indicator Toggle (EMA / VSTOP) */}
            <div className="bg-slate-800/80 p-4 rounded-xl border border-slate-700 space-y-3">
              <label className="text-xs font-bold text-amber-400 uppercase tracking-wider block">
                Select Trend Indicator Filter
              </label>

              <div className="flex items-center space-x-6">
                {/* EMA Option */}
                <label className="flex items-center space-x-2 text-sm text-slate-200 cursor-pointer">
                  <input
                    type="radio"
                    name="mtfIndicator"
                    checked={mtfIndicator === 'EMA'}
                    onChange={() => setMtfIndicator('EMA')}
                    className="accent-amber-500"
                  />
                  <span className="font-semibold">EMA</span>
                </label>

                {/* VSTOP Option */}
                <label className="flex items-center space-x-2 text-sm text-slate-200 cursor-pointer">
                  <input
                    type="radio"
                    name="mtfIndicator"
                    checked={mtfIndicator === 'VSTOP'}
                    onChange={() => setMtfIndicator('VSTOP')}
                    className="accent-amber-500"
                  />
                  <span className="font-semibold">VSTOP</span>
                </label>
              </div>

              {/* Dynamic Inputs based on selected indicator */}
              {mtfIndicator === 'EMA' ? (
                <div className="pt-2 flex items-center space-x-3">
                  <span className="text-xs text-slate-400">EMA Period:</span>
                  <input
                    type="number"
                    value={mtfIndicatorPeriod}
                    onChange={(e) => setMtfIndicatorPeriod(e.target.value)}
                    className="w-24 bg-slate-900 border border-slate-700 text-white p-1.5 rounded-lg text-sm font-mono"
                  />
                </div>
              ) : (
                <div className="pt-2 grid grid-cols-2 gap-3">
                  <div>
                    <span className="text-xs text-slate-400 block mb-1">VSTOP Period:</span>
                    <input
                      type="number"
                      value={mtfIndicatorPeriod}
                      onChange={(e) => setMtfIndicatorPeriod(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 text-white p-1.5 rounded-lg text-sm font-mono"
                    />
                  </div>
                  <div>
                    <span className="text-xs text-slate-400 block mb-1">Multiplier:</span>
                    <input
                      type="number"
                      step="0.1"
                      value={mtfVstopMultiplier}
                      onChange={(e) => setMtfVstopMultiplier(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 text-white p-1.5 rounded-lg text-sm font-mono"
                    />
                  </div>
                </div>
              )}
            </div>

            <button
              onClick={() => setShowMtfModal(false)}
              className="w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold py-3 rounded-xl transition shadow"
            >
              Apply MTF Settings
            </button>
          </div>
        </div>
      )}
    </div>
  );
}