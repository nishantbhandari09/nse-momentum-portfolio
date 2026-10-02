import React from 'react';
import { LayoutDashboard, Zap, FolderKanban, SlidersHorizontal, Table } from 'lucide-react';

export default function Navbar({ activeTab, setActiveTab }) {
  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'strategy-builder', label: 'Strategy Builder', icon: SlidersHorizontal },
    { id: 'group-manager', label: 'Group Manager', icon: FolderKanban },
    { id: 'scanner', label: 'Rebalance Scanner', icon: Zap },
    { id: 'positions', label: 'Positions', icon: Table },
  ];

  return (
    <header className="bg-slate-950 border-b border-slate-800 sticky top-0 z-40">
      <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
        {/* Logo */}
        <div 
          className="flex items-center space-x-3 cursor-pointer"
          onClick={() => setActiveTab('dashboard')}
        >
          <div className="bg-gradient-to-tr from-blue-600 to-indigo-500 text-white p-2 rounded-lg font-bold text-lg shadow-lg shadow-blue-500/20">
            QI
          </div>
          <div>
            <span className="font-black text-xl tracking-wider text-white">QUANT</span>
            <span className="font-light text-xl tracking-wider text-blue-400 ml-1">INVESTOR</span>
          </div>
        </div>

        {/* Navigation Tabs */}
        <nav className="flex items-center space-x-1 bg-slate-900/80 p-1 rounded-xl border border-slate-800">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
                  isActive
                    ? 'bg-blue-600 text-white shadow-md shadow-blue-600/30'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        {/* Status Indicator */}
        <div className="hidden md:flex items-center space-x-2 bg-emerald-950/60 border border-emerald-800/50 px-3 py-1 rounded-full text-xs font-semibold text-emerald-400">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping"></span>
          <span>FYERS Live Connected</span>
        </div>
      </div>
    </header>
  );
}